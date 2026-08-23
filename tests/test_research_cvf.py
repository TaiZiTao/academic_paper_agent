"""CVF Open Access 免费 PDF 兜底 find_cvf_pdf 测试(全部 mock httpx)。

方案: 直抓 CVF 官方按年会议页(https://openaccess.thecvf.com/{CONF}{year}?day=all)
→ 标题归一化匹配 → 访问论文页提取 PDF 直链。不依赖任何搜索引擎。

实测说明: CVF 仅收录 CVPR(每年)与 ICCV(奇数年); ECCV 不在 CVF(全 404), 不生成候选。
"""

import httpx
import pytest

from app.research.cvf import (
    _cache_clear,
    _candidate_pages,
    _extract_pdf_url,
    _find_paper_page,
    _normalize_title,
    _parse_conference_page,
    find_cvf_pdf,
)

EXPECTED_PDF = "https://openaccess.thecvf.com/content/CVPR2023/papers/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.pdf"

TARGET_TITLE = "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale"

# CVPR2023?day=all 会议页(真实结构: /content/{CONF}{year}/html/..._paper.html)
CVPR2023_ALL_HTML = """<html><body>
<div id="content">
<h3>Papers</h3>
<dl>
<dt class="ptitle"><br><a href="/content/CVPR2023/html/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.html">An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale</a></dt>
<dd>Alexey Dosovitskiy, Lucas Beyer, Alexander Kolesnikov, ...<br>
<a href="/content/CVPR2023/papers/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.pdf">[pdf]</a></dd>
<dt class="ptitle"><br><a href="/content/CVPR2023/html/Kirkpatrick_Unrelated_Topic_CVPR_2023_paper.html">Unrelated Topic Detection in the Wild</a></dt>
<dd>Jane Kirkpatrick<br><a href="/content/CVPR2023/papers/Kirkpatrick_Unrelated_Topic_CVPR_2023_paper.pdf">[pdf]</a></dd>
</dl>
</div>
</body></html>"""

# ICCV2023?day=all: 含同一篇论文(跨会议重复出现, 验证下个候选可命中)
ICCV2023_ALL_HTML = """<html><body>
<dl>
<dt class="ptitle"><br><a href="/content/ICCV2023/html/Dosovitskiy_An_Image_Is_Worth_ICCV_2023_paper.html">An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale</a></dt>
<dd>Alexey Dosovitskiy<br><a href="/content/ICCV2023/papers/Dosovitskiy_An_Image_Is_Worth_ICCV_2023_paper.pdf">[pdf]</a></dd>
</dl>
</body></html>"""

# CVF 论文页: 含 PDF 直链
PAPER_HTML = """<html><head><title>An Image is Worth 16x16 Words</title></head>
<body>
<div class="ptitle"><a href="/content/CVPR2023/html/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.html">An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale</a></div>
<div class="abstract">While the Transformer architecture has become the de-facto standard for NLP tasks...</div>
<p class="text-center"><a class="btn" href="/content/CVPR2023/papers/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.pdf">PDF</a></p>
</body></html>"""

ICCV_PAPER_HTML = """<html><body>
<div class="ptitle"><a href="/content/ICCV2023/html/Dosovitskiy_An_Image_Is_Worth_ICCV_2023_paper.html">An Image is Worth 16x16 Words</a></div>
<p><a class="btn" href="/content/ICCV2023/papers/Dosovitskiy_An_Image_Is_Worth_ICCV_2023_paper.pdf">PDF</a></p>
</body></html>"""


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前后清空模块级会议页缓存, 避免跨用例污染。"""
    _cache_clear()
    yield
    _cache_clear()


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------- 候选会议页 ----------

def test_candidate_pages_with_year_cvpr_and_odd_iccv():
    """奇数年 CVPR + ICCV; 偶数年仅 CVPR(ECCV 不在 CVF, 不生成候选)。"""
    assert _candidate_pages(2023) == ["CVPR2023?day=all", "ICCV2023?day=all"]
    assert _candidate_pages(2022) == ["CVPR2022?day=all"]


def test_candidate_pages_no_year_recent_years(monkeypatch):
    monkeypatch.setattr("app.research.cvf._recent_years", lambda n=3: [2023, 2022, 2021])
    assert _candidate_pages(None) == [
        "CVPR2023?day=all", "ICCV2023?day=all",
        "CVPR2022?day=all",
        "CVPR2021?day=all", "ICCV2021?day=all",
    ]


# ---------- 标题归一化与会议页解析 ----------

def test_normalize_title_strips_case_punct_whitespace():
    assert _normalize_title("  An Image is Worth 16x16 Words: Transformers! ") == "an image is worth 16x16 words transformers"
    assert _normalize_title("") == ""
    assert _normalize_title(None) == ""


def test_parse_conference_page_extracts_title_href_pairs():
    entries = _parse_conference_page(CVPR2023_ALL_HTML)
    assert entries == [
        ("An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale",
         "/content/CVPR2023/html/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.html"),
        ("Unrelated Topic Detection in the Wild",
         "/content/CVPR2023/html/Kirkpatrick_Unrelated_Topic_CVPR_2023_paper.html"),
    ]


def test_parse_conference_page_skips_non_paper_links():
    html = """<html>
    <a href="https://openaccess.thecvf.com/CVPR2023">CVPR 2023</a>
    <a href="CVPR2023_title.html">title index</a>
    <a href="content/CVPR2023/html/Real_Paper_CVPR_2023_paper.html">Real Paper</a>
    <a href="/content/CVPR2023/bibtex/Real_Paper_CVPR_2023.bib">bibtex</a>
    </html>"""
    assert _parse_conference_page(html) == [("Real Paper", "content/CVPR2023/html/Real_Paper_CVPR_2023_paper.html")]


def test_find_paper_page_exact_then_containment():
    entries = _parse_conference_page(CVPR2023_ALL_HTML)
    # 精确匹配
    assert _find_paper_page(entries, TARGET_TITLE) ==         "/content/CVPR2023/html/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.html"
    # 包含/被包含模糊匹配(只传前半段标题)
    assert _find_paper_page(entries, "An Image is Worth 16x16 Words") ==         "/content/CVPR2023/html/Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.html"
    # 未命中
    assert _find_paper_page(entries, "Totally Different Paper") is None


# ---------- PDF 直链提取 ----------

def test_extract_pdf_url_prefers_dot_pdf_link():
    assert _extract_pdf_url(
        '<a href="https://openaccess.thecvf.com/content/CVPR2023/papers/A_B_CVPR_2023_paper.pdf">PDF</a>'
    ) == "https://openaccess.thecvf.com/content/CVPR2023/papers/A_B_CVPR_2023_paper.pdf"


def test_extract_pdf_url_resolves_relative_link():
    """页面内相对/站内绝对路径的 .pdf 链接用 urljoin 补全为绝对 URL。"""
    assert _extract_pdf_url('<a href="/content/CVPR2023/papers/A_B_CVPR_2023_paper.pdf">PDF</a>') ==         "https://openaccess.thecvf.com/content/CVPR2023/papers/A_B_CVPR_2023_paper.pdf"
    assert _extract_pdf_url('<a href="content/CVPR2023/papers/A_B_CVPR_2023_paper.pdf">PDF</a>') ==         "https://openaccess.thecvf.com/content/CVPR2023/papers/A_B_CVPR_2023_paper.pdf"


def test_extract_pdf_url_none_when_no_pdf_or_papers_link():
    assert _extract_pdf_url("<html><body>no links</body></html>") is None


# ---------- find_cvf_pdf 集成 ----------

@pytest.mark.asyncio
async def test_find_cvf_pdf_exact_title_match():
    """年份精准定位: CVPR2023?day=all 命中标题 → 论文页 → PDF 直链。"""
    def handler(request):
        url = str(request.url)
        if "CVPR2023?day=all" in url:
            return httpx.Response(200, text=CVPR2023_ALL_HTML)
        if "CVPR2023/html" in url:
            assert "Dosovitskiy_An_Image_Is_Worth_CVPR_2023_paper.html" in url
            return httpx.Response(200, text=PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_cvf_pdf(TARGET_TITLE, year=2023, client=make_client(handler))
    assert pdf == EXPECTED_PDF


@pytest.mark.asyncio
async def test_find_cvf_pdf_fuzzy_title_still_matches():
    """标题归一化 + 包含匹配: 传入不带副标题的标题也能命中。"""
    def handler(request):
        url = str(request.url)
        if "CVPR2023?day=all" in url:
            return httpx.Response(200, text=CVPR2023_ALL_HTML)
        if "CVPR2023/html" in url:
            return httpx.Response(200, text=PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_cvf_pdf("An Image is Worth 16x16 Words", year=2023, client=make_client(handler))
    assert pdf == EXPECTED_PDF


@pytest.mark.asyncio
async def test_find_cvf_pdf_odd_year_iccv_fallback():
    """奇数年: CVPR2023?day=all 404 → ICCV2023?day=all 命中。"""
    def handler(request):
        url = str(request.url)
        if "CVPR2023?day=all" in url:
            return httpx.Response(404)
        if "ICCV2023?day=all" in url:
            return httpx.Response(200, text=ICCV2023_ALL_HTML)
        if "ICCV2023/html" in url:
            return httpx.Response(200, text=ICCV_PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_cvf_pdf(TARGET_TITLE, year=2023, client=make_client(handler))
    assert pdf == "https://openaccess.thecvf.com/content/ICCV2023/papers/Dosovitskiy_An_Image_Is_Worth_ICCV_2023_paper.pdf"


@pytest.mark.asyncio
async def test_find_cvf_pdf_no_year_tries_recent_years(monkeypatch):
    """year=None → 近 3 年候选依次尝试, 命中 2022 CVPR。"""
    monkeypatch.setattr("app.research.cvf._recent_years", lambda n=3: [2023, 2022, 2021])
    fetched = []

    def handler(request):
        url = str(request.url)
        fetched.append(url)
        if "CVPR2023?day=all" in url or "ICCV2023?day=all" in url:
            return httpx.Response(404)
        if "CVPR2022?day=all" in url:
            return httpx.Response(200, text=CVPR2023_ALL_HTML)
        if "CVPR2023/html" in url:  # CVPR2023_ALL_HTML 内论文页 href 指向 /content/CVPR2023/html/
            return httpx.Response(200, text=PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_cvf_pdf(TARGET_TITLE, year=None, client=make_client(handler))
    assert pdf == EXPECTED_PDF
    assert fetched[0] == "https://openaccess.thecvf.com/CVPR2023?day=all"  # 从最近年份开始
    assert any("CVPR2022?day=all" in u for u in fetched)


@pytest.mark.asyncio
async def test_find_cvf_pdf_miss_returns_none():
    """所有候选页都无匹配标题 → None。"""
    def handler(request):
        url = str(request.url)
        if "CVPR2023?day=all" in url:
            return httpx.Response(200, text=CVPR2023_ALL_HTML)
        if "ICCV2023?day=all" in url:
            return httpx.Response(200, text=ICCV2023_ALL_HTML)
        return httpx.Response(404)

    assert await find_cvf_pdf("A Paper That Does Not Exist", year=2023, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_find_cvf_pdf_page_failure_skips_to_next_candidate():
    """CVPR2023?day=all 500 → 跳过, ICCV2023?day=all 命中。"""
    def handler(request):
        url = str(request.url)
        if "CVPR2023?day=all" in url:
            return httpx.Response(500)
        if "ICCV2023?day=all" in url:
            return httpx.Response(200, text=ICCV2023_ALL_HTML)
        if "ICCV2023/html" in url:
            return httpx.Response(200, text=ICCV_PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_cvf_pdf(TARGET_TITLE, year=2023, client=make_client(handler))
    assert pdf == "https://openaccess.thecvf.com/content/ICCV2023/papers/Dosovitskiy_An_Image_Is_Worth_ICCV_2023_paper.pdf"


@pytest.mark.asyncio
async def test_find_cvf_pdf_paper_page_failure_tries_next_candidate():
    """CVPR 论文页 500 → 继续下个候选会议(ICCV)命中。"""
    def handler(request):
        url = str(request.url)
        if "CVPR2023?day=all" in url:
            return httpx.Response(200, text=CVPR2023_ALL_HTML)
        if "CVPR2023/html" in url:
            return httpx.Response(500)
        if "ICCV2023?day=all" in url:
            return httpx.Response(200, text=ICCV2023_ALL_HTML)
        if "ICCV2023/html" in url:
            return httpx.Response(200, text=ICCV_PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_cvf_pdf(TARGET_TITLE, year=2023, client=make_client(handler))
    assert pdf == "https://openaccess.thecvf.com/content/ICCV2023/papers/Dosovitskiy_An_Image_Is_Worth_ICCV_2023_paper.pdf"


@pytest.mark.asyncio
async def test_find_cvf_pdf_caches_conference_page():
    """同一会议页只抓一次, 第二次走模块级缓存。"""
    day_fetches = {"n": 0}
    paper_fetches = {"n": 0}

    def handler(request):
        url = str(request.url)
        if "CVPR2023?day=all" in url:
            day_fetches["n"] += 1
            return httpx.Response(200, text=CVPR2023_ALL_HTML)
        if "CVPR2023/html" in url:
            paper_fetches["n"] += 1
            return httpx.Response(200, text=PAPER_HTML)
        return httpx.Response(404)

    client = make_client(handler)
    pdf1 = await find_cvf_pdf(TARGET_TITLE, year=2023, client=client)
    pdf2 = await find_cvf_pdf(TARGET_TITLE, year=2023, client=client)
    assert pdf1 == pdf2 == EXPECTED_PDF
    assert day_fetches["n"] == 1  # 会议页命中缓存
    assert paper_fetches["n"] == 2  # 论文页不缓存


@pytest.mark.asyncio
async def test_find_cvf_pdf_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    assert await find_cvf_pdf("Some Title", year=2023, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_find_cvf_pdf_all_pages_missing_returns_none():
    def handler(request):
        return httpx.Response(404)

    assert await find_cvf_pdf("Some Title", year=2023, client=make_client(handler)) is None
