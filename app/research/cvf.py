"""CVF Open Access 免费 PDF 兜底查找(L1.5 下载源)。

CVPR/ICCV 论文在 openaccess.thecvf.com 有官方免费 PDF(无需 VPN)。
但 OpenAlex 对这类论文只认 IEEE DOI(is_oa=False), 被误标付费墙。
本模块直接抓取 CVF 官方按年会议页(https://openaccess.thecvf.com/{CONF}{year}?day=all,
单页含该会议全部论文), 解析论文标题 + 论文页链接, 标题归一化匹配命中后
访问论文页提取 PDF 直链, 作为 L1 直链失败后的补充下载源。

实测: CVF Open Access 仅收录 CVPR(每年)与 ICCV(奇数年); ECCV 由 Springer
出版(LNCS), 不在 CVF(openaccess.thecvf.com/ECCV* 均 404)。

不依赖任何搜索引擎(DuckDuckGo/Google 在部分网络环境被墙, 实测
html.duckduckgo.com ConnectTimeout, 而 openaccess.thecvf.com 直连 200)。

容错约定: 单个会议页失败/超时跳过继续下个候选; 全部失败/未找到返回 None,
不抛异常(下载降级链路上游兜底)。
"""

import re
import time
import urllib.parse
from datetime import datetime

import httpx

CVF_BASE = "https://openaccess.thecvf.com"

# 浏览器 UA(CVF 对默认 httpx UA 可能返回 403)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 会议页解析结果缓存: page_url -> (fetched_at, [(title, paper_page_href), ...])
_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}
CACHE_TTL = 3600.0  # 1 小时

_ANCHOR_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)


def _new_client() -> httpx.AsyncClient:
    """独立 client: 超时 15s + 浏览器 UA + 自动跟随跳转。"""
    return httpx.AsyncClient(
        timeout=15.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# 候选会议页
# ---------------------------------------------------------------------------

def _recent_years(n: int = 3) -> list[int]:
    """最近 n 个年份(含当年), 供缺失 year 时兜底遍历。"""
    now = datetime.now().year
    return [now - i for i in range(n)]


def _candidate_pages(year: int | None) -> list[str]:
    """候选会议页路径列表: 有年份精准定位, 无年份近 3 年兜底。

    CVF Open Access 实测仅收录 CVPR(每年)与 ICCV(奇数年); ECCV 不在 CVF,
    不生成候选(避免无效请求)。会议页用 ?day=all 单页取全部论文。
    """
    pages: list[str] = []
    if year:
        pages.append(f"CVPR{year}?day=all")
        if year % 2 == 1:
            pages.append(f"ICCV{year}?day=all")
        return pages
    for y in _recent_years():
        pages.append(f"CVPR{y}?day=all")
        if y % 2 == 1:
            pages.append(f"ICCV{y}?day=all")
    return pages


# ---------------------------------------------------------------------------
# 解析与匹配
# ---------------------------------------------------------------------------

def _normalize_title(s: str) -> str:
    """标题归一化: 小写 + 去标点 + 折叠空白(字母数字之外全部视为分隔)。"""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _parse_conference_page(html: str) -> list[tuple[str, str]]:
    """解析会议页: 提取 (论文标题, 论文页 href) 列表。

    只收 href 指向 content/{CONF}{year}/html/xxx.html 的论文条目
    (会议页导航/作者索引等其它 .html 链接会被排除)。
    """
    entries: list[tuple[str, str]] = []
    for href, raw_text in _ANCHOR_RE.findall(html or ""):
        href_l = href.lower()
        if "/html/" not in href_l or not href_l.endswith(".html"):
            continue
        # 锚文本内可能残留标签(如 <br>), 先剥掉再折叠空白
        title = re.sub(r"<[^>]+>", "", raw_text)
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        entries.append((title, href))
    return entries


def _find_paper_page(entries: list[tuple[str, str]], title: str) -> str | None:
    """标题归一化匹配: 先精确, 再允许包含/被包含模糊匹配。"""
    target = _normalize_title(title)
    if not target:
        return None
    for paper_title, href in entries:
        if _normalize_title(paper_title) == target:
            return href
    for paper_title, href in entries:
        norm = _normalize_title(paper_title)
        if norm and (target in norm or norm in target):
            return href
    return None


def _extract_pdf_url(html: str) -> str | None:
    """从 CVF 论文页提取 PDF 直链: 优先 .pdf href, 其次含 'papers' 路径的 href。

    相对路径用 CVF 站点基准补全为绝对 URL。
    """
    hrefs = [href for href, _ in _ANCHOR_RE.findall(html or "")]
    for href in hrefs:
        if re.search(r"\.pdf", href, re.IGNORECASE):
            return urllib.parse.urljoin(CVF_BASE, href)
    for href in hrefs:
        if re.search(r"papers", href, re.IGNORECASE):
            return urllib.parse.urljoin(CVF_BASE, href)
    return None


# ---------------------------------------------------------------------------
# 会议页缓存(TTL 1 小时, 防重复抓取)
# ---------------------------------------------------------------------------

def _cache_get(page_url: str) -> list[tuple[str, str]] | None:
    hit = _cache.get(page_url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _cache_set(page_url: str, entries: list[tuple[str, str]]) -> None:
    _cache[page_url] = (time.monotonic(), entries)


def _cache_clear() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def find_cvf_pdf(
    title: str,
    year: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """按论文标题(可选年份)在 CVF 找免费 PDF 直链。

    流程: 按年份生成候选会议页(CVPR + 奇数年 ICCV, 无年份则近 3 年兜底)
    → 抓 ?day=all 会议页解析论文条目(带缓存) → 标题归一化匹配 → 访问论文页提取 PDF 直链。

    Args:
        title: 论文标题。
        year: 发表年份(精准定位会议页); None 时遍历近 3 年候选。
        client: 可注入的 httpx.AsyncClient(测试用 MockTransport); 缺省自建
            15s 超时 + 浏览器 UA 的 client, 用完关闭。

    Returns:
        PDF 直链 URL; 所有候选失败/未找到返回 None(不抛异常)。
    """
    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        for page_path in _candidate_pages(year):
            page_url = f"{CVF_BASE}/{page_path}"
            entries = _cache_get(page_url)
            if entries is None:
                try:
                    resp = await client.get(page_url)
                    resp.raise_for_status()
                    entries = _parse_conference_page(resp.text)
                    _cache_set(page_url, entries)
                except Exception:
                    continue  # 单页失败/超时: 跳过继续下个候选
            paper_href = _find_paper_page(entries, title)
            if paper_href is None:
                continue
            paper_url = urllib.parse.urljoin(CVF_BASE, paper_href)
            try:
                page_resp = await client.get(paper_url)
                page_resp.raise_for_status()
                pdf_url = _extract_pdf_url(page_resp.text)
                if pdf_url:
                    return pdf_url
            except Exception:
                continue  # 论文页失败: 尝试下个会议候选
        return None
    finally:
        if owns_client:
            await client.aclose()
