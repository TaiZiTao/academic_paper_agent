"""文献检索源: arXiv API / Semantic Scholar API / OpenAlex API。

统一输出 `SearchResult`(见 schemas.py)。httpx.AsyncClient 可注入,
测试用 MockTransport 不访问真实网络; 生产默认创建真实 client。
解析类异常统一归一化为 `SearchSourceError`, 单源失败可在下游降级。

OpenAlexSearcher 同时承担两条职责:
1. 独立检索源(覆盖 IEEE/Springer/Elsevier 等正式发表元数据, 无需 VPN);
2. enrich_arxiv: 用 arXiv ID 批量反查, 把 arXiv 预印本补全为正式发表信息。
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.research.ccf import classify_ccf
from app.research.free_pdf import is_free_source_venue
from app.research.schemas import SearchResult

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

ARXIV_API = "https://export.arxiv.org/api/query"

# submittedDate 范围过滤默认上下界(仅给一侧时补齐)
DEFAULT_YEAR_MIN = 1991  # arXiv 建站年份
DEFAULT_YEAR_MAX = 2030  # 与 SearchRequest 校验上界一致
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,year,authors,venue,externalIds,citationCount,openAccessPdf,url"

OPENALEX_API = "https://api.openalex.org/works"
OPENALEX_SELECT = (
    "id,title,publication_year,primary_location,locations,authorships,doi,ids,open_access,type,biblio,"
    "abstract_inverted_index,"  # 缺失则响应不含摘要字段, _parse_work 还原恒为空
    "cited_by_count"  # Google Scholar 式排序: 分组内按被引量降序, 经典论文优先
)
OPENALEX_PER_PAGE_MAX = 50  # OpenAlex 每页硬上限
# enrich 补全性能保护: ids.arxiv filter 带几十个 ID 会超时/400 → 退化逐条串行搜索(几十秒)。
# 钳制单次 ID 数与总补全条数, 并按批并行反查; 退化路径也并行, 把补全耗时从数十秒压到秒级。
ENRICH_BATCH_MAX = 25  # 单次 ids.arxiv filter 的 ID 数上限
# 每页最多补全的 arXiv 结果数: 与结果集缓存深度(100/组合)匹配, 让多数经典论文
# 都拿到 published/CCF/被引量, 否则未补全的 arXiv 孪生条目会在排序中被低估
ENRICH_TARGETS_MAX = 120

# S2 轻量重试: 429/5xx 等状态码 sleep 2s 重试一次(最多一次)
RETRY_STATUSES = (429, 500, 502, 503, 504)


class SearchSourceError(Exception):
    """单源检索/解析失败(含非 HTTP 的解析异常), 下游统一捕获后降级该源。"""


class _OpenAlexBadFilter(Exception):
    """OpenAlex filter 语法错误(400) — 触发 enrich 的标题搜索退化路径。"""


def _arxiv_id_from_url(url: str) -> str:
    """从 arXiv 链接(abs/pdf)提取规范 arXiv ID: 去版本号后缀、转小写。

    支持新式(2401.12345)与旧式含分类前缀(cs.LG/2301.12345)的 ID。
    """
    if not url:
        return ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#\s]+)", url)
    if not m:
        return ""
    return re.sub(r"v\d+$", "", m.group(1)).lower()


def _sanitize_arxiv_query(query: str) -> str:
    """清理 arXiv 查询语法字符, 保留字母/数字/空格(arXiv 默认按空格分词做隐式 AND)。

    不带引号: 整句短语匹配(all:"a b c")对 LLM 规划出的多词查询经常召回归零
    (实测 all:"industrial anomaly detection total recall" → 0 条,
    all:industrial anomaly detection total recall → 43 万条), 改为分词 AND 检索。
    """
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (query or "").lower()).strip()
    if not cleaned:
        # 全符号等极端输入: 去掉引号防语法错误, 保留原文
        cleaned = re.sub(r'["()]', " ", (query or "")).strip()
    return cleaned


def _is_arxiv_venue(venue: str) -> bool:
    """venue 是否为 arXiv 本身(OpenAlex 把 arXiv 当做一个 venue 收录)。

    归一化后包含 "arxiv" 即视为 arXiv 自身(如 "arXiv (Cornell University)" / "arXiv")。
    这类 venue 不代表正式发表, enrich 补全时不得据其把纯 arXiv 预印本标为 published。
    """
    return "arxiv" in (venue or "").lower()


def _work_has_arxiv(item: Any) -> bool:
    """work 的 ids.arxiv 是否存在(有 arXiv 预印本 → 可免费获取)。"""
    ids = item.get("ids")
    return bool(isinstance(ids, dict) and ids.get("arxiv"))


def _restore_abstract(inverted: Any) -> str:
    """OpenAlex abstract_inverted_index(词 -> 位置列表) 还原为原文。"""
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        if isinstance(idxs, list):
            for i in idxs:
                if isinstance(i, int):
                    positions.append((i, str(word)))
    if not positions:
        return ""
    positions.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positions)


def _norm_doi(doi: Any) -> str | None:
    """"https://doi.org/10.xxxx" / "https://dx.doi.org/10.xxxx" → "10.xxxx"; 其余原样。"""
    if not isinstance(doi, str) or not doi.strip():
        return None
    value = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if value.lower().startswith(prefix):
            return value[len(prefix):]
    return value or None

def _make_client(proxy: str, timeout: float) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "headers": {"User-Agent": "research-agent/1.0 (academic search)"},
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


class ArxivSearcher:
    """arXiv API 检索(Atom XML)。"""

    SOURCE_NAME = "arxiv"

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 15.0, proxy: str = ""):
        self._owns_client = client is None
        self.client = client or _make_client(proxy, timeout)

    async def aclose(self) -> None:
        """关闭自建 client(注入的 client 由调用方管理生命周期)。"""
        if self._owns_client:
            await self.client.aclose()

    async def search(
        self,
        query: str,
        top_k: int = 20,
        start: int = 0,
        year_min: int | None = None,
        year_max: int | None = None,
    ) -> tuple[list[SearchResult], int]:
        """分页检索: 返回 (results, total)。

        total 从 Atom feed 的 opensearch:totalResults 解析; 解析失败则回退 len(results)。
        start 透传为 arXiv API 的 start 参数, 支持 Google Scholar 式分页翻页。
        year_min/year_max 按 arXiv 语法追加 submittedDate 范围过滤(按提交日期匹配);
        只给一侧时另一侧取默认值补齐。
        """
        search_query = f"all:{_sanitize_arxiv_query(query)}"
        if year_min is not None or year_max is not None:
            lo = year_min if year_min is not None else DEFAULT_YEAR_MIN
            hi = year_max if year_max is not None else DEFAULT_YEAR_MAX
            search_query += f" AND submittedDate:[{lo}0101 TO {hi}1231]"
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": top_k,
            "sortBy": "relevance",
        }
        resp = await self.client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise SearchSourceError(f"arXiv Atom 解析失败: {exc}") from exc
        total_el = root.find(f"{OPENSEARCH}totalResults")
        total: int | None = None
        if total_el is not None and total_el.text and total_el.text.strip().isdigit():
            total = int(total_el.text.strip())
        results: list[SearchResult] = []
        for entry in root.findall(f"{ATOM}entry"):
            abs_url = (entry.findtext(f"{ATOM}id") or "").strip()
            arxiv_id = abs_url.rsplit("/abs/", 1)[-1] if "/abs/" in abs_url else ""
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)  # 去掉版本号后缀(v1/v2/...), 得到规范 ID
            doi_el = entry.find(f"{ARXIV}doi")
            journal_el = entry.find(f"{ARXIV}journal_ref")
            published = (entry.findtext(f"{ATOM}published") or "").strip()
            year = int(published[:4]) if published[:4].isdigit() else None
            venue = (journal_el.text or "").strip() if journal_el is not None else ""
            ccf = classify_ccf(venue)
            authors = [
                (a.findtext(f"{ATOM}name") or "").strip()
                for a in entry.findall(f"{ATOM}author")
                if a.findtext(f"{ATOM}name")
            ]
            results.append(
                SearchResult(
                    source="arxiv",
                    title=(entry.findtext(f"{ATOM}title") or "").strip().replace("\n", " "),
                    authors=authors,
                    year=year,
                    venue=venue,
                    abstract=(entry.findtext(f"{ATOM}summary") or "").strip(),
                    doi=(doi_el.text or "").strip() if doi_el is not None else None,
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
                    page_url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (abs_url or ""),
                    published=ccf["published"],
                    ccf_level=ccf["ccf_level"],
                )
            )
        return results, (total if total is not None else len(results))


class SemanticScholarSearcher:
    """Semantic Scholar Graph API 检索(JSON)。"""

    SOURCE_NAME = "semantic_scholar"

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 15.0, proxy: str = ""):
        self._owns_client = client is None
        self.client = client or _make_client(proxy, timeout)

    async def aclose(self) -> None:
        """关闭自建 client(注入的 client 由调用方管理生命周期)。"""
        if self._owns_client:
            await self.client.aclose()

    async def search(self, query: str, top_k: int = 20) -> list[SearchResult]:
        params = {"query": query, "limit": top_k, "fields": S2_FIELDS}
        resp = await self.client.get(S2_API, params=params)
        if resp.status_code in RETRY_STATUSES:
            await asyncio.sleep(2)
            resp = await self.client.get(S2_API, params=params)
        resp.raise_for_status()
        try:
            data = (resp.json() or {}).get("data") or []
        except ValueError as exc:
            raise SearchSourceError(f"Semantic Scholar JSON 解析失败: {exc}") from exc
        results: list[SearchResult] = []
        for item in data:
            try:
                if not isinstance(item, dict):
                    continue
                ext = item.get("externalIds")
                ext = ext if isinstance(ext, dict) else {}
                oa = item.get("openAccessPdf")
                oa = oa if isinstance(oa, dict) else {}
                authors = [
                    a.get("name", "")
                    for a in (item.get("authors") or [])
                    if isinstance(a, dict) and a.get("name")
                ]
                venue = item.get("venue") or ""
                ccf = classify_ccf(venue)
                results.append(
                    SearchResult(
                        source="semantic_scholar",
                        title=(item.get("title") or "").strip(),
                        authors=authors,
                        year=item.get("year"),
                        venue=venue,
                        abstract=item.get("abstract") or "",
                        doi=ext.get("DOI") or None,
                        pdf_url=(oa.get("url") or None) if oa else None,
                        page_url=item.get("url") or "",
                        citations=int(item.get("citationCount") or 0),
                        published=ccf["published"],
                        ccf_level=ccf["ccf_level"],
                    )
                )
            except Exception:
                continue  # 单条脏数据跳过, 不拖垮整批
        return results


class OpenAlexSearcher:
    """OpenAlex Works API 检索(JSON; 免费覆盖各出版社正式发表元数据)。

    分页: OpenAlex 用 page(每页最多 50), per-page=min(top_k, 50);
    top_k>50 时循环翻页拉够, total 从 meta.count 解析。
    start 为 Google Scholar 式偏移: 首页 = start // per_page + 1。
    year_min/year_max → filter=publication_year:{lo}-{hi}(只给一侧时用默认补齐)。
    """

    SOURCE_NAME = "openalex"

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 15.0, proxy: str = ""):
        self._owns_client = client is None
        self.client = client or _make_client(proxy, timeout)

    async def aclose(self) -> None:
        """关闭自建 client(注入的 client 由调用方管理生命周期)。"""
        if self._owns_client:
            await self.client.aclose()

    async def _get_json(self, params: dict[str, Any]) -> dict:
        """带 429/5xx 轻量重试(最多一次)的 GET, 解析 JSON; 失败归一化 SearchSourceError。"""
        resp = await self.client.get(OPENALEX_API, params=params)
        if resp.status_code in RETRY_STATUSES:
            await asyncio.sleep(2)
            resp = await self.client.get(OPENALEX_API, params=params)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise SearchSourceError(f"OpenAlex JSON 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise SearchSourceError("OpenAlex 响应结构异常: 非 JSON 对象")
        return data

    async def search(
        self,
        query: str,
        top_k: int = 20,
        start: int = 0,
        year_min: int | None = None,
        year_max: int | None = None,
    ) -> tuple[list[SearchResult], int]:
        """分页检索: 返回 (results, total)。"""
        per_page = min(top_k, OPENALEX_PER_PAGE_MAX)
        filter_parts: list[str] = []
        if year_min is not None or year_max is not None:
            lo = year_min if year_min is not None else DEFAULT_YEAR_MIN
            hi = year_max if year_max is not None else DEFAULT_YEAR_MAX
            filter_parts.append(f"publication_year:{lo}-{hi}")
        results: list[SearchResult] = []
        total = 0
        page = start // per_page + 1
        needed = top_k
        while needed > 0:
            params: dict[str, Any] = {
                "search": query,
                "per-page": min(per_page, needed),
                "page": page,
                "select": OPENALEX_SELECT,
            }
            if filter_parts:
                params["filter"] = ",".join(filter_parts)
            data = await self._get_json(params)
            meta = data.get("meta")
            total = int(meta.get("count") or 0) if isinstance(meta, dict) else 0
            works = data.get("results") or []
            if not works:
                break
            take = min(per_page, needed)
            for item in works:
                try:
                    r = self._parse_work(item)
                    if r is not None:
                        results.append(r)
                except Exception:
                    continue  # 单条脏数据跳过, 不拖垮整批
            needed -= len(works)
            if len(works) < take:
                break  # 已到末尾
            page += 1
        return results, total

    def _parse_work(self, item: Any) -> SearchResult | None:
        """OpenAlex work JSON → SearchResult(单条解析失败返回 None)。"""
        if not isinstance(item, dict):
            return None
        title = (item.get("title") or "").strip()
        if not title:
            return None
        authors: list[str] = []
        for a in item.get("authorships") or []:
            if isinstance(a, dict):
                author = a.get("author")
                if isinstance(author, dict) and author.get("display_name"):
                    authors.append(str(author["display_name"]).strip())
        primary = item.get("primary_location")
        venue = ""
        if isinstance(primary, dict):
            src = primary.get("source")
            if isinstance(src, dict) and src.get("display_name"):
                venue = str(src["display_name"]).strip()
        if not venue:
            # primary_location 缺 source(常见于预印本主记录)时, 从 locations 里找
            # 非 arXiv 的正式发表 venue 兜底(如 CVPR/期刊正式版)
            for loc in item.get("locations") or []:
                if not isinstance(loc, dict):
                    continue
                src = loc.get("source")
                if isinstance(src, dict) and src.get("display_name"):
                    name = str(src["display_name"]).strip()
                    if name and not _is_arxiv_venue(name):
                        venue = name
                        break
        doi = _norm_doi(item.get("doi"))
        oa = item.get("open_access")
        oa_status = "unknown"
        oa_url = None
        if isinstance(oa, dict):
            is_oa = oa.get("is_oa")
            status = oa.get("oa_status")
            if is_oa is True or (isinstance(status, str) and status in ("gold", "green", "hybrid", "bronze", "diamond")):
                oa_status = "open"
            elif is_oa is False or status == "closed":
                oa_status = "closed"
                # IEEE/Springer 出版社把正式版标 closed, 但属 CVF/ACL/NeurIPS/PMLR/
                # OpenReview/AAAI 等官方开放站点会议, 或存在 arXiv 预印本时, 免费 PDF
                # 实际可获取(下载链路 L1.5 可直接拿到), 检索阶段即视为开放获取
                if is_free_source_venue(venue) or _work_has_arxiv(item):
                    oa_status = "open"
            raw_url = oa.get("oa_url")
            if isinstance(raw_url, str) and raw_url.strip():
                oa_url = raw_url.strip()
        openalex_id = None
        raw_id = item.get("id")
        if isinstance(raw_id, str) and "/W" in raw_id:
            openalex_id = raw_id.rsplit("/", 1)[-1]
        if oa_url:
            page_url = oa_url
        elif doi:
            page_url = f"https://doi.org/{doi}"
        else:
            page_url = ""
        ccf = classify_ccf(venue)
        published = ccf["published"]
        ccf_level = ccf["ccf_level"]
        if not published and doi and (item.get("type") or "") != "preprint":
            # venue 缺失(OpenAlex 部分会议论文主记录无 source, 如 EDSR 挂在 CVPR
            # workshop DOI 下)但有 DOI 且类型非预印本 → 视为已发表, 无 CCF 级别
            published = True
            ccf_level = None
        return SearchResult(
            source="openalex",
            title=title,
            authors=authors,
            year=item.get("publication_year"),
            venue=venue,
            abstract=_restore_abstract(item.get("abstract_inverted_index")),
            doi=doi,
            pdf_url=oa_url,
            page_url=page_url,
            citations=int(item.get("cited_by_count") or 0),
            published=published,
            ccf_level=ccf_level,
            oa_status=oa_status,
            openalex_id=openalex_id,
        )

    # ---------- arXiv 发表信息补全(批量反查) ----------

    async def enrich_arxiv(self, results: list[SearchResult]) -> list[SearchResult]:
        """用 arXiv ID 批量反查 OpenAlex, 补全正式发表 venue/published/ccf_level。

        只处理 source=="arxiv" 且能从 page_url 提取 arXiv ID 的结果(原地更新);
        一次请求(filter=ids.arxiv:a|b|...)补全整页; ids.arxiv 400 时退化逐条标题+年份搜索;
        任何失败都不阻断(保留 arXiv 原始值)。
        """
        if not results:
            return results
        targets = [r for r in results if r.source == "arxiv" and _arxiv_id_from_url(r.page_url)]
        if not targets:
            return results
        # 钳制: 只补全合并顺序靠前(源相关度最高)的 arXiv 结果, 防一次反查数十个 ID
        targets = targets[:ENRICH_TARGETS_MAX]
        chunks = [targets[i : i + ENRICH_BATCH_MAX] for i in range(0, len(targets), ENRICH_BATCH_MAX)]

        async def _process(chunk: list[SearchResult]) -> dict[str, SearchResult]:
            chunk_ids = [_arxiv_id_from_url(r.page_url) for r in chunk]
            try:
                return await self._batch_lookup_arxiv(chunk_ids)
            except _OpenAlexBadFilter:
                return await self._fallback_title_lookup(chunk)
            except Exception:
                return {}  # 补全失败不阻断

        lookup: dict[str, SearchResult] = {}
        partials = await asyncio.gather(*(_process(c) for c in chunks))
        for partial in partials:
            lookup.update(partial or {})
        for r in targets:
            oa = lookup.get(_arxiv_id_from_url(r.page_url))
            if oa is None:
                continue
            # venue 是 arXiv 本身(如 "arXiv (Cornell University)")→ 视为未正式发表:
            # 不更新 venue/published/ccf_level, 仅补全其他字段
            if oa.venue and not _is_arxiv_venue(oa.venue):
                r.venue = oa.venue
                ccf = classify_ccf(oa.venue)
                r.published = ccf["published"]
                r.ccf_level = ccf["ccf_level"]
            elif oa.published and not r.published and not _is_arxiv_venue(oa.venue):
                # 孪生条目无 venue 但按 DOI/类型判定为已发表(OpenAlex 会议论文主记录缺 venue);
                # venue 是 arXiv 自身的孪生条目不适用(预印本不算正式发表)
                r.published = True
                r.ccf_level = None
            if oa.doi and not r.doi:
                r.doi = oa.doi
            if oa.oa_status != "unknown":
                r.oa_status = oa.oa_status
            if oa.pdf_url and not r.pdf_url:
                r.pdf_url = oa.pdf_url
            if oa.openalex_id:
                r.openalex_id = oa.openalex_id
            if oa.citations and not r.citations:
                r.citations = oa.citations
        return results

    async def _batch_lookup_arxiv(self, arxiv_ids: list[str]) -> dict[str, SearchResult]:
        """filter=ids.arxiv:a|b|c 一次请求批量反查(OpenAlex OR 用 | 连接)。

        返回 {规范 arXiv ID: SearchResult}; 400 抛 _OpenAlexBadFilter 触发退化路径。
        """
        filter_val = "ids.arxiv:" + "|".join(arxiv_ids)
        params: dict[str, Any] = {
            "filter": filter_val,
            "per-page": OPENALEX_PER_PAGE_MAX,
            "select": OPENALEX_SELECT,
        }
        resp = await self.client.get(OPENALEX_API, params=params)
        if resp.status_code in RETRY_STATUSES:
            await asyncio.sleep(2)
            resp = await self.client.get(OPENALEX_API, params=params)
        if resp.status_code == 400:
            raise _OpenAlexBadFilter(f"OpenAlex ids.arxiv filter 400: {filter_val}")
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise SearchSourceError(f"OpenAlex JSON 解析失败: {exc}") from exc
        works = data.get("results") or [] if isinstance(data, dict) else []
        out: dict[str, SearchResult] = {}
        for item in works:
            try:
                r = self._parse_work(item)
                if r is None:
                    continue
                ids_field = item.get("ids")
                raw = ids_field.get("arxiv") if isinstance(ids_field, dict) else None
                aid = _arxiv_id_from_url(str(raw)) if raw else ""
                if aid:
                    out[aid] = r
            except Exception:
                continue  # 单条脏数据跳过
        return out

    async def _fallback_title_lookup(self, results: list[SearchResult]) -> dict[str, SearchResult]:
        """ids.arxiv 400 退化路径: 逐条按标题(+年份过滤)取 top 1, 收集命中。

        并发受限(信号量)批量执行, 避免几十条串行标题搜索造成数十秒延迟。
        """
        sem = asyncio.Semaphore(10)

        async def _one(r: SearchResult) -> tuple[str | None, SearchResult | None]:
            aid = _arxiv_id_from_url(r.page_url)
            if not aid or not r.title:
                return aid, None
            async with sem:
                try:
                    params: dict[str, Any] = {"search": r.title, "per-page": 1, "select": OPENALEX_SELECT}
                    if r.year:
                        params["filter"] = f"publication_year:{r.year}"
                    data = await self._get_json(params)
                    works = data.get("results") or []
                    if works:
                        hit = self._parse_work(works[0])
                        if hit is not None:
                            return aid, hit
                except Exception:
                    pass  # 单条失败跳过, 不拖垮整批
            return aid, None

        pairs = await asyncio.gather(*(_one(r) for r in results))
        return {aid: hit for aid, hit in pairs if aid and hit is not None}

