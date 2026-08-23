"""免费论文源查找: CCF-A/B 类论文免费获取渠道的统一 L1.5 下载兜底。

在 cvf.py(CVPR/ICCV 官方 Open Access)基础上扩展 6 类可直连的免费源:
- arXiv 预印本: 通用兜底(覆盖 TPAMI/IJCV/TIP 等期刊 + 多数会议), arXiv API ti: 标题查询
- ACL Anthology(aclanthology.org): ACL/EMNLP/NAACL/CoNLL/COLING 免费 PDF
- PMLR(proceedings.mlr.press): ICML/ECML 免费 PDF
- NeurIPS Proceedings(proceedings.neurips.cc): NeurIPS 免费 PDF
- OpenReview(openreview.net): ICLR 免费 PDF
- AAAI OJS(ojs.aaai.org): AAAI 免费 PDF

统一入口 find_free_pdf(title, venue, year, client):
按 venue 归一化路由到对应查找器; 路由未识别或命中失败时, 最后统一试
find_arxiv_pdf 兜底(预印本几乎覆盖所有 CCF-A/B 论文的免费版本);
全部失败返回 None, 不抛异常(下载降级链路上游兜底)。

容错约定(与 cvf.py 一致): 单个源失败/超时跳过继续下个候选; 全部失败/未找到
返回 None 不抛异常。各源页面解析均采用「抓索引页 + 正则/结构匹配」的通用
启发式, 优先保证能跑通 + 标题匹配正确 + 容错返回 None。

注意: ACL Anthology(aclanthology.org)在某些网络环境不可达(实测持续
ConnectError), 属网络可达性问题而非解析问题; find_acl_pdf 失败返回 None,
find_free_pdf 自动继续尝试 arXiv 兜底, 不影响其它源。
"""

import difflib
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from app.research import cvf

# 浏览器 UA(部分站点对默认 httpx UA 可能返回 403)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 站点常量
ARXIV_API = "https://export.arxiv.org/api/query"
ACL_BASE = "https://aclanthology.org"
PMLR_BASE = "https://proceedings.mlr.press"
NEURIPS_BASE = "https://proceedings.neurips.cc"
OPENREVIEW_API = "https://api2.openreview.net"
OPENREVIEW_WEB = "https://openreview.net"
AAAI_BASE = "https://ojs.aaai.org/index.php/AAAI"

# 索引页解析结果缓存: url -> (fetched_at, entries); TTL 1 小时(与 cvf 一致)
_cache: dict[str, tuple[float, list]] = {}
CACHE_TTL = 3600.0

# 通用锚点正则: href 值可不带引号(ACL Anthology 实测未加引号属性),
# 捕获后统一 strip 引号
_ANCHOR_RE = re.compile(r'<a[^>]*href=([^\s>]+)[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)

# arXiv Atom XML 命名空间
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _new_client() -> httpx.AsyncClient:
    """独立 client: 超时 45s + 浏览器 UA + 自动跟随跳转。

    CVF/ACL 索引页较大(单页含整年论文), 20s 在代理下偶发超时导致免费源
    查找失败而误判付费墙; 放宽到 45s。
    """
    return httpx.AsyncClient(
        timeout=45.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    )


def _recent_years(n: int = 3) -> list[int]:
    """最近 n 个年份(含当年), 供缺失 year 时兜底遍历。"""
    now = datetime.now().year
    return [now - i for i in range(n)]


def _normalize_title(s: str) -> str:
    """标题归一化: 小写 + 去标点 + 折叠空白(字母数字之外全部视为分隔)。"""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _venue_key(s: str) -> str:
    """venue 归一化(路由用): 小写 + 去全部非字母数字(不留空格, 便于子串匹配)。"""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _titles_match(target: str, candidate: str) -> bool:
    """标题匹配: 归一化后 精确 / 互相包含 / difflib 相似度 > 0.8。"""
    t = _normalize_title(target)
    c = _normalize_title(candidate)
    if not t or not c:
        return False
    if t == c:
        return True
    if t in c or c in t:
        return True
    return difflib.SequenceMatcher(None, t, c).ratio() > 0.8


def _cache_get(page_url: str) -> list | None:
    hit = _cache.get(page_url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _cache_set(page_url: str, entries: list) -> None:
    _cache[page_url] = (time.monotonic(), entries)


def _cache_clear() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# venue 归一化路由
# ---------------------------------------------------------------------------

# (源 key, 命中关键词); 同源内关键词按长度降序检查(更具体者优先,
# 如 naacl 先于 acl 子串), 源间顺序: 会议专属源在前, 通用 arXiv 兜底在后
_ROUTE_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "cvf",
        (
            "conferenceoncomputervisionandpatternrecognition",  # CVPR 全称
            "cvpr",
            "internationalconferenceoncomputervision",  # ICCV 全称
            "iccv",
        ),
    ),
    (
        "acl",
        (
            "annualmeetingoftheassociationforcomputationallinguistics",  # ACL 全称
            "empiricalmethodsinnaturallanguageprocessing",  # EMNLP 全称
            "northamericanchapteroftheassociationforcomputationallinguistics",  # NAACL 全称
            "computationalnaturallanguagelearning",  # CoNLL 全称
            "internationalconferenceoncomputationallinguistics",  # COLING 全称
            "naacl",
            "emnlp",
            "conll",
            "coling",
            "acl",
        ),
    ),
    (
        "pmlr",
        (
            "internationalconferenceonmachinelearning",  # ICML 全称
            "europeanconferenceonmachinelearning",  # ECML 全称
            "icml",
            "ecml",
        ),
    ),
    (
        "neurips",
        (
            "advancesinneuralinformationprocessingsystems",  # NeurIPS 论文集名
            "neuralinformationprocessingsystems",
            "neurips",
            "nips",
        ),
    ),
    (
        "openreview",
        (
            "internationalconferenceonlearningrepresentations",  # ICLR 全称
            "iclr",
        ),
    ),
    (
        "aaai",
        (
            "associationfortheadvancementofartificialintelligence",  # AAAI 全称
            "aaai",
        ),
    ),
]


def _route_venue(venue: str) -> str | None:
    """venue 归一化后按关键词路由到免费源 key; 期刊/未知返回 None(走 arXiv 通用兜底)。"""
    norm = _venue_key(venue)
    if not norm:
        return None
    for source, keywords in _ROUTE_RULES:
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in norm:
                return source
    return None


def is_free_source_venue(venue: str) -> bool:
    """venue 是否属于已知官方开放站点托管的会议(CVF/ACL/NeurIPS/PMLR/OpenReview/AAAI)。

    IEEE/Springer 等出版社把这类会议论文的正式版标为 closed(如 CVPR 论文 DOI
    挂在 IEEE Xplore), 但官方开放站点提供免费 PDF, 下载链路 L1.5 可直接获取
    → 检索阶段即可视为开放获取, 避免"付费墙"误报。
    """
    return _route_venue(venue) is not None


# ---------------------------------------------------------------------------
# arXiv 预印本(通用兜底)
# ---------------------------------------------------------------------------

def _parse_arxiv_feed(xml: str) -> list[tuple[str, str]]:
    """解析 arXiv API Atom XML → [(标题, pdf href)]。"""
    root = ET.fromstring(xml or "")
    out: list[tuple[str, str]] = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        title = (entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").strip()
        pdf_href = ""
        for link in entry.findall("atom:link", _ATOM_NS):
            if link.get("type") == "application/pdf":
                pdf_href = link.get("href") or ""
        if title and pdf_href:
            out.append((title, pdf_href))
    return out


def _strip_arxiv_version(url: str) -> str:
    """去掉 arXiv PDF 链接的版本号后缀(v1/v2...), 指向该论文最新版。"""
    return re.sub(r"v\d+$", "", url)


async def find_arxiv_pdf(
    title: str,
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """按标题在 arXiv 找预印本 PDF 直链(通用兜底)。

    arXiv API ti:"{title}" 查询 → 标题归一化匹配(相似度/互相包含) →
    取 application/pdf 链接并去掉版本号后缀。

    Args:
        title: 论文标题。
        year: 忽略(arXiv 无年份索引需求), 仅保持统一签名。
        client: 可注入的 httpx.AsyncClient(测试用 MockTransport)。

    Returns:
        PDF 直链 URL; 未命中/失败返回 None(不抛异常)。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        params = {
            "search_query": f'ti:"{title}"',
            "max_results": "10",
            "sortBy": "relevance",
        }
        resp = await client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        for entry_title, pdf_href in _parse_arxiv_feed(resp.text):
            if _titles_match(title, entry_title):
                return _strip_arxiv_version(pdf_href)
        return None
    except Exception:
        return None
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# ACL Anthology(ACL/EMNLP/NAACL/CoNLL/COLING)
# ---------------------------------------------------------------------------

# 各 ACL 系会议在 Anthology 的卷 id 候选(按命中概率排序; 缺失的卷 404 跳过)
_ACL_VOLUME_PATTERNS: dict[str, tuple[str, ...]] = {
    "acl": ("acl-long", "acl-short", "acl-main"),
    "emnlp": ("emnlp-main", "emnlp-short", "emnlp"),
    "naacl": ("naacl-main", "naacl-short", "naacl-long"),
    "conll": ("conll-1", "conll-main", "conll"),
    "coling": ("coling-1", "coling-2", "coling-main"),
}


def _parse_acl_volume(html: str, vol_id: str) -> list[tuple[str, str]]:
    """解析 ACL Anthology 卷页 → [(标题, pdf_url)]。

    ACL 卷页每篇论文的标题链接 href 形如 /{vol_id}.{n}/(实测未加引号属性),
    PDF 直链为 {vol_id}.{n}.pdf(Anthology 固定 URL 模式, 无需再访问论文页)。
    """
    entries: list[tuple[str, str]] = []
    pat = re.compile(rf"/{re.escape(vol_id)}.\d+/$")
    for href_raw, raw_text in _ANCHOR_RE.findall(html or ""):
        href = href_raw.strip("'\"")
        if not pat.search(href):
            continue
        title = re.sub(r"<[^>]+>", "", raw_text)
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        pdf_url = urllib.parse.urljoin(ACL_BASE, href.rstrip("/") + ".pdf")
        entries.append((title, pdf_url))
    return entries


async def find_acl_pdf(
    title: str,
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
    venue: str = "acl",
) -> str | None:
    """按标题在 ACL Anthology 找免费 PDF 直链。

    按年份 + 会议(venue 参数: acl/emnlp/naacl/conll/coling)生成候选卷页
    https://aclanthology.org/volumes/{year}.{pattern}/, 标题匹配命中后
    返回 {paper_id}.pdf 直链。

    Args:
        title: 论文标题。
        year: 发表年份; None 时遍历近 3 年候选。
        client: 可注入的 httpx.AsyncClient(测试用 MockTransport)。
        venue: ACL 系会议简称(决定卷 id 候选), 默认 acl。

    Returns:
        PDF 直链 URL; 未命中/失败返回 None(不抛异常)。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        years = [year] if year else _recent_years()
        patterns = _ACL_VOLUME_PATTERNS.get(venue, _ACL_VOLUME_PATTERNS["acl"])
        for y in years:
            for pattern in patterns:
                vol_id = f"{y}.{pattern}"
                page_url = f"{ACL_BASE}/volumes/{vol_id}/"
                entries = _cache_get(page_url)
                if entries is None:
                    try:
                        resp = await client.get(page_url)
                        resp.raise_for_status()
                        entries = _parse_acl_volume(resp.text, vol_id)
                        _cache_set(page_url, entries)
                    except Exception:
                        continue  # 404/超时: 尝试下个候选卷
                for paper_title, pdf_url in entries:
                    if _titles_match(title, paper_title):
                        return pdf_url
        return None
    except Exception:
        return None
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# PMLR(ICML/ECML)
# ---------------------------------------------------------------------------

def _parse_pmlr_index(html: str) -> list[tuple[str, str]]:
    """解析 PMLR 主索引 → [(卷号如 v202, 卷标题文本)]。

    实测结构: <li><a href="v202"><b>Volume 202</b></a> Proceedings of ICML 2023</li>
    """
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"<li>\s*<a href=\"?(v\d+)\"?[^>]*>.*?</a>\s*(.*?)</li>", html or "", re.IGNORECASE | re.DOTALL):
        vol_id = m.group(1)
        text = re.sub(r"<[^>]+>", "", m.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        out.append((vol_id, text))
    return out


def _pmlr_volume_matches(vol: tuple[str, str], year: int | None) -> bool:
    """卷是否属于 ICML/ECML(且年份匹配, 可选)。"""
    _vol_id, text = vol
    norm = _venue_key(text)
    if "icml" not in norm and "ecml" not in norm:
        return False
    if year is not None and str(year) not in text:
        return False
    return True


def _parse_pmlr_volume(html: str) -> list[tuple[str, str]]:
    """解析 PMLR 卷页 → [(标题, pdf_url)]。

    实测结构: 每篇论文 <div class="paper"> 内 <p class="title"> 标题,
    <p class="links"> 内含 .pdf 下载直链。
    """
    entries: list[tuple[str, str]] = []
    for block in re.split(r'<div class="paper">', html or "")[1:]:
        title_m = re.search(r'<p class="title">(.*?)</p>', block, re.S)
        if not title_m:
            continue
        title = re.sub(r"<[^>]+>", "", title_m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        pdf_m = re.search(r'href="([^"]+\.pdf)"', block, re.S)
        if title and pdf_m:
            entries.append((title, pdf_m.group(1)))
    return entries


async def find_pmlr_pdf(
    title: str,
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """按标题在 PMLR(proceedings.mlr.press)找 ICML/ECML 免费 PDF 直链。

    流程: 抓主索引(https://proceedings.mlr.press/) → 按会议关键词 + 年份
    定位卷号(vNNN) → 抓卷页标题匹配 → 返回 .pdf 直链。

    PMLR 卷号与年份无简单映射(如 ICML2023=v202), 故经主索引动态定位。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        index_url = f"{PMLR_BASE}/"
        volumes = _cache_get(index_url)
        if volumes is None:
            try:
                resp = await client.get(index_url)
                resp.raise_for_status()
                volumes = _parse_pmlr_index(resp.text)
                _cache_set(index_url, volumes)
            except Exception:
                volumes = []
        if not volumes:
            return None
        candidates = [v for v in volumes if _pmlr_volume_matches(v, year)]
        for vol_id, _text in candidates:
            page_url = f"{PMLR_BASE}/{vol_id}/"
            entries = _cache_get(page_url)
            if entries is None:
                try:
                    resp = await client.get(page_url)
                    resp.raise_for_status()
                    entries = _parse_pmlr_volume(resp.text)
                    _cache_set(page_url, entries)
                except Exception:
                    continue
            for paper_title, pdf_url in entries:
                if _titles_match(title, paper_title):
                    return pdf_url
        return None
    except Exception:
        return None
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# NeurIPS Proceedings
# ---------------------------------------------------------------------------

def _parse_neurips_index(html: str) -> list[tuple[str, str]]:
    """解析 NeurIPS 年索引 → [(标题, 论文页 href)]。

    实测结构: <a href="/paper_files/paper/{year}/hash/{hash}-Abstract-Conference.html">标题</a>;
    部分年份(如 2017)链接为 -Abstract.html 后缀(无 -Conference), 正则兼容两种。
    """
    out: list[tuple[str, str]] = []
    for m in re.finditer(
        r'href="(/paper_files/paper/\d+/hash/[^"]+?-Abstract(?:-Conference)?\.html)"[^>]*>(.*?)</a>',
        html or "",
        re.S,
    ):
        title = re.sub(r"<[^>]+>", "", m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            out.append((title, m.group(1)))
    return out


def _extract_neurips_pdf(html: str) -> str | None:
    """从 NeurIPS 论文页提取 PDF 直链: 优先 citation_pdf_url meta, 其次 .pdf href。

    实测论文页含 <meta name="citation_pdf_url" content="https://...-Paper-Conference.pdf">。
    """
    m = re.search(r'citation_pdf_url"\s+content="([^"]+)"', html or "")
    if m:
        return m.group(1)
    for href_raw, _text in _ANCHOR_RE.findall(html or ""):
        href = href_raw.strip("'\"")
        if re.search(r"\.pdf", href, re.IGNORECASE):
            return urllib.parse.urljoin(NEURIPS_BASE, href)
    return None


async def find_neurips_pdf(
    title: str,
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """按标题在 NeurIPS Proceedings 找免费 PDF 直链。

    流程: 抓 https://proceedings.neurips.cc/paper_files/paper/{year} 年索引
    → 标题匹配 → 访问论文页提取 citation_pdf_url / .pdf 直链。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        years = [year] if year else _recent_years()
        for y in years:
            page_url = f"{NEURIPS_BASE}/paper_files/paper/{y}"
            entries = _cache_get(page_url)
            if entries is None:
                try:
                    resp = await client.get(page_url)
                    resp.raise_for_status()
                    entries = _parse_neurips_index(resp.text)
                    _cache_set(page_url, entries)
                except Exception:
                    continue  # 单年失败: 尝试下一年
            for paper_title, paper_page in entries:
                if not _titles_match(title, paper_title):
                    continue
                try:
                    page_resp = await client.get(urllib.parse.urljoin(NEURIPS_BASE, paper_page))
                    page_resp.raise_for_status()
                    pdf_url = _extract_neurips_pdf(page_resp.text)
                    if pdf_url:
                        return pdf_url
                except Exception:
                    continue  # 论文页失败: 尝试下一候选
        return None
    except Exception:
        return None
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# OpenReview(ICLR)
# ---------------------------------------------------------------------------

def _is_iclr_note(note: dict) -> bool:
    """OpenReview note 是否为 ICLR 论文(有标题且 venueid 含 ICLR.cc)。"""
    content = note.get("content") or {}
    title = (content.get("title") or {}).get("value", "")
    venueid = (content.get("venueid") or {}).get("value", "")
    return bool(title) and "iclr.cc" in venueid.lower()


def _openreview_pdf_url(note: dict) -> str:
    """构造 OpenReview PDF 直链: 显式 http 链接直接用; 否则按 note id 走 /pdf?id=。

    实测 content.pdf.value 可为相对路径/文件名, 而 /pdf?id={note_id} 为
    任意 note 的稳定 PDF 直链。
    """
    pdf = ((note.get("content") or {}).get("pdf") or {}).get("value", "")
    if pdf.startswith("http"):
        return pdf
    return f"{OPENREVIEW_WEB}/pdf?id={note.get('id', '')}"


async def find_openreview_pdf(
    title: str,
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """按标题在 OpenReview API 找 ICLR 免费 PDF 直链。

    流程: GET https://api2.openreview.net/notes/search?term={title} → 过滤
    ICLR 论文(venueid 含 ICLR.cc, Conference 优先) → 标题匹配 → PDF 直链。

    OpenReview 的搜索结果含评论/归档等噪声, 先按 venueid 过滤再匹配标题,
    避免把讨论串当论文。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        resp = await client.get(
            f"{OPENREVIEW_API}/notes/search",
            params={"term": title, "limit": "50"},
        )
        resp.raise_for_status()
        notes = resp.json().get("notes") or []
        iclr_notes = [n for n in notes if _is_iclr_note(n)]
        # 正式录用(venueid 以 /Conference 结尾)优先于 Withdrawn/Rejected
        iclr_notes.sort(
            key=lambda n: 0
            if ((n.get("content") or {}).get("venueid") or {}).get("value", "").endswith("/Conference")
            else 1
        )
        for note in iclr_notes:
            note_title = ((note.get("content") or {}).get("title") or {}).get("value", "")
            if _titles_match(title, note_title):
                return _openreview_pdf_url(note)
        return None
    except Exception:
        return None
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# AAAI OJS
# ---------------------------------------------------------------------------

def _parse_aaai_archive(html: str) -> list[tuple[str, str]]:
    """解析 AAAI OJS 归档页 → [(issue_url, issue 标题)]。

    实测结构: <a class="title" href=".../issue/view/N">AAAI-{yy} Technical Tracks {k}</a>
    """
    out: list[tuple[str, str]] = []
    for m in re.finditer(
        r'<a class="title" href="([^"]*issue/view/[^"]+)">\s*(.*?)\s*</a>',
        html or "",
        re.S,
    ):
        label = re.sub(r"<[^>]+>", "", m.group(2))
        label = re.sub(r"\s+", " ", label).strip()
        out.append((m.group(1), label))
    return out


def _aaai_issue_matches(label: str, year: int | None) -> bool:
    """issue 标题(如 AAAI-23 Technical Tracks 1)是否对应目标年份。"""
    m = re.search(r"AAAI-(\d+)", label)
    if not m:
        return False
    issue_year = 2000 + int(m.group(1))
    return year is None or issue_year == year


def _parse_aaai_issue(html: str) -> list[tuple[str, str]]:
    """解析 AAAI OJS issue 页 → [(标题, pdf_url)]。

    实测结构: <div class="obj_article_summary"> 文章块, 内含
    <h3 class="title"><a href=".../article/view/N">标题</a> 与
    <a class="obj_galley_link pdf" href=".../article/view/N/{galley}">PDF</a>
    (article/view/{id}/{galley} 直接返回 PDF 文件)。
    """
    entries: list[tuple[str, str]] = []
    blocks = re.split(r'<div class="obj_article_summary">', html or "")[1:]
    for block in blocks:
        title_m = re.search(r'<h3 class="title">\s*<a[^>]*href="([^"]+)">\s*(.*?)\s*</a>', block, re.S)
        if not title_m:
            continue
        title = re.sub(r"<[^>]+>", "", title_m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        pdf_m = re.search(r'class="obj_galley_link pdf"[^>]*href="([^"]+)"', block, re.S)
        if not title:
            continue
        if pdf_m:
            entries.append((title, pdf_m.group(1)))
    return entries


async def find_aaai_pdf(
    title: str,
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """按标题在 AAAI OJS 找免费 PDF 直链。

    流程: 抓归档页(issue/archive)定位 AAAI-{yy} 卷 → 抓 issue 页文章标题匹配
    → 返回 obj_galley_link pdf 直链。

    AAAI OJS 的 issue id 为顺序号(非年份), 故先经归档页按 AAAI-{yy} 映射年份。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        archive_url = f"{AAAI_BASE}/issue/archive"
        issues = _cache_get(archive_url)
        if issues is None:
            try:
                resp = await client.get(archive_url)
                resp.raise_for_status()
                issues = _parse_aaai_archive(resp.text)
                _cache_set(archive_url, issues)
            except Exception:
                issues = []
        if not issues:
            return None
        candidates = [u for u, label in issues if _aaai_issue_matches(label, year)]
        for issue_url in candidates:
            entries = _cache_get(issue_url)
            if entries is None:
                try:
                    resp = await client.get(issue_url)
                    resp.raise_for_status()
                    entries = _parse_aaai_issue(resp.text)
                    _cache_set(issue_url, entries)
                except Exception:
                    continue
            for paper_title, pdf_url in entries:
                if _titles_match(title, paper_title):
                    return pdf_url
        return None
    except Exception:
        return None
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def _make_acl_finder(sub_venue: str):
    """生成绑定子会议(acl/emnlp/naacl/conll/coling)的 ACL 查找器。"""

    async def _finder(title, year, client):
        return await find_acl_pdf(title, year=year, client=client, venue=sub_venue)

    return _finder


# 源 key -> 查找器(统一签名: (title, year, client) -> str | None)。
# 全部用 lambda/闭包间接调用模块全局, 使查找器可在调用时被替换(测试 monkeypatch 友好)。
_FINDERS: dict[str, object] = {
    "cvf": lambda title, year, client: cvf.find_cvf_pdf(title, year=year, client=client),
    "acl": _make_acl_finder("acl"),
    "emnlp": _make_acl_finder("emnlp"),
    "naacl": _make_acl_finder("naacl"),
    "conll": _make_acl_finder("conll"),
    "coling": _make_acl_finder("coling"),
    "pmlr": lambda title, year, client: find_pmlr_pdf(title, year=year, client=client),
    "neurips": lambda title, year, client: find_neurips_pdf(title, year=year, client=client),
    "openreview": lambda title, year, client: find_openreview_pdf(title, year=year, client=client),
    "aaai": lambda title, year, client: find_aaai_pdf(title, year=year, client=client),
}


async def find_free_pdf(
    title: str,
    venue: str = "",
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """统一免费论文源查找入口(L1.5 下载兜底)。

    流程: venue 归一化路由到对应查找器(CVF/ACL Anthology/PMLR/NeurIPS/
    OpenReview/AAAI); 路由未识别(期刊/未知)或路由源未命中/失败时, 最后统一
    试 arXiv 预印本兜底; 全部失败返回 None 不抛异常。

    Args:
        title: 论文标题。
        venue: 发表 venue(空/未知 → 直接走 arXiv 通用兜底)。
        year: 发表年份(精准定位会议/卷页); None 时各源近 3 年兜底。
        client: 可注入的 httpx.AsyncClient(测试用 MockTransport); 缺省自建
            20s 超时 + 浏览器 UA 的 client, 用完关闭。

    Returns:
        免费 PDF 直链 URL; 未找到/失败返回 None。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        source = _route_venue(venue)
        if source is not None:
            finder = _FINDERS[source]
            try:
                url = await finder(title, year, client)  # type: ignore[operator]
                if url:
                    return url
            except Exception:
                pass  # 路由源失败: 落到 arXiv 兜底
        # 路由未识别或命中失败: 统一 arXiv 预印本兜底
        try:
            return await find_arxiv_pdf(title, year=year, client=client)
        except Exception:
            return None
    finally:
        if owns_client:
            await client.aclose()
