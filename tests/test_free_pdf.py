"""free_pdf 免费论文源查找测试(全部 mock httpx)。

L1.5 免费 PDF 兜底扩展: arXiv 预印本(通用) + ACL Anthology + PMLR(ICML/ECML)
+ NeurIPS Proceedings + OpenReview(ICLR) + AAAI OJS, 统一入口 find_free_pdf
按 venue 归一化路由。所有网络请求以 MockTransport 模拟, 不触网。

Fixtures 均按真实站点结构构造(实测确认):
- arXiv API Atom XML(entry/link type=application/pdf)
- ACL Anthology 卷页(未加引号属性, 标题链接 href 形如 /2023.acl-long.N/)
- PMLR 主索引(<li><a href="v202">...Proceedings of ICML 2023</li>)与卷页(div.paper)
- NeurIPS 年索引(hash-...-Abstract-Conference.html)与论文页(citation_pdf_url meta)
- OpenReview /notes/search JSON(content.title.value / content.venueid.value / content.pdf.value)
- AAAI OJS issue/archive(AAAI-{yy})与 issue 页(obj_article_summary 文章块 + obj_galley_link pdf)
"""

import json

import httpx
import pytest

from app.research import cvf
from app.research.free_pdf import (
    _cache_clear,
    _normalize_title,
    _parse_neurips_index,
    _route_venue,
    _titles_match,
    find_aaai_pdf,
    find_acl_pdf,
    find_arxiv_pdf,
    find_free_pdf,
    find_neurips_pdf,
    find_openreview_pdf,
    find_pmlr_pdf,
)

TARGET = "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale"

# ---------------------------------------------------------------- fixtures

ARXIV_XML = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
<opensearch:totalResults>2</opensearch:totalResults>
<entry>
  <id>http://arxiv.org/abs/2103.13915v2</id>
  <title>An Image is Worth 16x16 Words, What is a Video Worth?</title>
  <link href="https://arxiv.org/abs/2103.13915v2" rel="alternate" type="text/html"/>
  <link href="https://arxiv.org/pdf/2103.13915v2" rel="related" type="application/pdf" title="pdf"/>
</entry>
<entry>
  <id>http://arxiv.org/abs/2010.11929v2</id>
  <title>An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale</title>
  <link href="https://arxiv.org/abs/2010.11929v2" rel="alternate" type="text/html"/>
  <link href="https://arxiv.org/pdf/2010.11929v2" rel="related" type="application/pdf" title="pdf"/>
</entry>
</feed>"""

ARXIV_XML_NO_MATCH = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>An Unrelated Paper About Something Else</title>
  <link href="https://arxiv.org/pdf/1234.5678v1" rel="related" type="application/pdf"/>
</entry>
</feed>"""

# ACL Anthology 卷页: 未加引号属性 + 标题链接 href 形如 /2023.acl-long.N/(实测结构)
ACL_VOLUME_HTML = """<!doctype html><html><body>
<span class=d-block><strong><a class=align-middle href=/2023.acl-long.1/>Unrelated First Paper in the Volume</a></strong><br>
<a class="badge text-bg-primary align-middle me-1" href=https://aclanthology.org/2023.acl-long.1.pdf title="Open PDF">pdf</a></span>
<span class=d-block><strong><a class=align-middle href=/2023.acl-long.2/>""" + TARGET + """</a></strong><br>
<a class="badge text-bg-primary align-middle me-1" href=https://aclanthology.org/2023.acl-long.2.pdf title="Open PDF">pdf</a></span>
</body></html>"""

# PMLR 主索引: <li><a href="vNNN"><b>Volume NNN</b></a> Proceedings of ... {year}</li>(实测结构)
PMLR_MAIN_HTML = """<html><body>
<ul>
<li><a href="v195"><b>Volume 195</b></a> Proceedings of COLT 2023</li>
<li><a href="v202"><b>Volume 202</b></a> Proceedings of ICML 2023</li>
<li><a href="v216"><b>Volume 216</b></a> Proceedings of UAI 2023</li>
<li><a href="v219"><b>Volume 219</b></a> Proceedings of ECML PKDD 2022</li>
</ul>
</body></html>"""

# PMLR 卷页: <div class="paper"> 内 <p class="title"> 与 links 内 .pdf(实测结构)
PMLR_VOLUME_HTML = """<html><body>
<div class="paper">
  <p class="title">Unrelated Paper Title</p>
  <p class="links">[<a href="https://proceedings.mlr.press/v202/other23a.html">abs</a>][<a href="https://proceedings.mlr.press/v202/other23a/other23a.pdf">Download PDF</a>]</p>
</div>
<div class="paper">
  <p class="title">""" + TARGET + """</p>
  <p class="links">[<a href="https://proceedings.mlr.press/v202/dosovitskiy23a.html">abs</a>][<a href="https://proceedings.mlr.press/v202/dosovitskiy23a/dosovitskiy23a.pdf">Download PDF</a>]</p>
</div>
</body></html>"""

# NeurIPS 年索引: <a href="/paper_files/paper/{year}/hash/{hash}-Abstract-Conference.html">标题</a>(实测结构)
NEURIPS_INDEX_HTML = """<html><body>
<ul class="list-unstyled">
<li><a href="/paper_files/paper/2023/hash/0001ca33ba34ce0351e4612b744b3936-Abstract-Conference.html">""" + TARGET + """</a> <span class="paper-authors">Alexey Dosovitskiy, Lucas Beyer</span></li>
<li><a href="/paper_files/paper/2023/hash/000262941c9edfd472a79298b2ac5e17-Abstract-Conference.html">Unrelated NeurIPS Paper</a> <span class="paper-authors">Someone Else</span></li>
</ul>
</body></html>"""

# NeurIPS 论文页: citation_pdf_url meta(实测结构)
NEURIPS_PAPER_HTML = """<html><head>
<meta name="citation_pdf_url" content="https://proceedings.neurips.cc/paper_files/paper/2023/file/0001ca33ba34ce0351e4612b744b3936-Paper-Conference.pdf">
</head><body><h2>""" + TARGET + """</h2></body></html>"""

# NeurIPS 2017 索引: 部分论文链接为 -Abstract.html(无 -Conference 后缀,
# 实测 Attention is All you Need 链接即此结构), 另含一条 -Abstract-Conference.html 对照
NEURIPS_INDEX_2017_HTML = """<html><body>
<ul class="list-unstyled">
<li><a href="/paper_files/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa-Abstract.html">Attention Is All You Need</a> <span class="paper-authors">Ashish Vaswani, Noam Shazeer</span></li>
<li><a href="/paper_files/paper/2017/hash/000262941c9edfd472a79298b2ac5e17-Abstract-Conference.html">Another Paper With Conference Suffix</a></li>
</ul>
</body></html>"""

NEURIPS_PAPER_2017_HTML = """<html><head>
<meta name="citation_pdf_url" content="https://proceedings.neurips.cc/paper_files/paper/2017/file/3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf">
</head><body><h2>Attention Is All You Need</h2></body></html>"""

# OpenReview /notes/search 响应(实测: content.title.value / venueid.value / pdf.value 可为相对路径)
OPENREVIEW_JSON = json.dumps({
    "count": 3,
    "notes": [
        {"id": "n-comment", "content": {
            "title": {"value": "Thank you for the response!"},
            "venueid": {"value": "ICLR.cc/2023/Conference/Paper1/Review"},
        }},
        {"id": "n-iclr", "content": {
            "title": {"value": TARGET},
            "venueid": {"value": "ICLR.cc/2023/Conference"},
            "venue": {"value": "ICLR 2023 Conference"},
            "pdf": {"value": "/pdf/23033fb559ea69645c04072f0f3ca7ae4edacc8a.pdf"},
        }},
        {"id": "n-archive", "content": {
            "title": {"value": TARGET},
            "venueid": {"value": "OpenReview.net/Archive"},
            "pdf": {"value": "http://arxiv.org/pdf/2406.09415v1"},
        }},
    ],
})

OPENREVIEW_JSON_NO_PDF_KEY = json.dumps({
    "count": 1,
    "notes": [
        {"id": "n-iclr2", "content": {
            "title": {"value": TARGET},
            "venueid": {"value": "ICLR.cc/2023/Conference"},
        }},
    ],
})

# AAAI OJS 归档页: <a class="title" href=".../issue/view/N">AAAI-{yy} Technical Tracks {k}</a>(实测结构)
AAAI_ARCHIVE_HTML = """<html><body>
<ul class="issues_archive">
  <li><div class="obj_issue_summary">
    <h2><a class="title" href="https://ojs.aaai.org/index.php/AAAI/issue/view/703">AAAI-23 Technical Tracks 1</a></h2>
    <div class="series">Vol. 37 No. 1</div>
  </div></li>
  <li><div class="obj_issue_summary">
    <h2><a class="title" href="https://ojs.aaai.org/index.php/AAAI/issue/view/704">AAAI-23 Technical Tracks 2</a></h2>
  </div></li>
  <li><div class="obj_issue_summary">
    <h2><a class="title" href="https://ojs.aaai.org/index.php/AAAI/issue/view/683">AAAI-26 Technical Tracks 1</a></h2>
  </div></li>
</ul>
</body></html>"""

# AAAI OJS issue 页: <div class="obj_article_summary"> 文章块 + obj_galley_link pdf(实测结构)
AAAI_ISSUE_HTML = """<html><body>
<ul class="cmp_article_list articles">
<li><div class="obj_article_summary">
  <h3 class="title"><a id="article-12345" href="https://ojs.aaai.org/index.php/AAAI/article/view/12345">Unrelated AAAI Article</a></h3>
  <ul class="galleys_links"><li><a class="obj_galley_link pdf" href="https://ojs.aaai.org/index.php/AAAI/article/view/12345/galley1">PDF</a></li></ul>
</div></li>
<li><div class="obj_article_summary">
  <h3 class="title"><a id="article-36958" href="https://ojs.aaai.org/index.php/AAAI/article/view/36958">""" + TARGET + """</a></h3>
  <ul class="galleys_links"><li><a class="obj_galley_link pdf" href="https://ojs.aaai.org/index.php/AAAI/article/view/36958/galley2">PDF</a></li></ul>
</div></li>
</ul>
</body></html>"""


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前后清空模块级缓存, 避免跨用例污染。"""
    _cache_clear()
    yield
    _cache_clear()


# ---------------------------------------------------------------- 工具函数

def test_normalize_title_strips_case_punct_whitespace():
    assert _normalize_title("  An Image is Worth 16x16 Words: Transformers! ") ==         "an image is worth 16x16 words transformers"
    assert _normalize_title("") == ""
    assert _normalize_title(None) == ""


def test_titles_match_exact_containment_similarity():
    assert _titles_match(TARGET, TARGET)  # 精确
    assert _titles_match(TARGET, "An Image is Worth 16x16 Words")  # 目标包含候选
    assert _titles_match("An Image is Worth 16x16 Words", TARGET)  # 候选包含目标
    assert _titles_match("An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale!",
                         TARGET)  # 相似度 > 0.8
    assert not _titles_match(TARGET, "An Image is Worth 16x16 Words, What is a Video Worth?")
    assert not _titles_match(TARGET, "Totally Different Paper About Nothing")
    assert not _titles_match("", TARGET)
    assert not _titles_match(TARGET, "")


def test_route_venue_maps_sources():
    assert _route_venue("CVPR 2023") == "cvf"
    assert _route_venue("Proceedings of the IEEE/CVF International Conference on Computer Vision (ICCV)") == "cvf"
    assert _route_venue("ACL 2023") == "acl"
    assert _route_venue("EMNLP 2022") == "acl"
    assert _route_venue("NAACL 2022") == "acl"  # naacl 优先于 acl 子串
    assert _route_venue("CoNLL 2023") == "acl"
    assert _route_venue("COLING 2022") == "acl"
    assert _route_venue("Proceedings of the 61st Annual Meeting of the Association for Computational Linguistics") == "acl"
    assert _route_venue("ICML 2023") == "pmlr"
    assert _route_venue("ECML PKDD 2022") == "pmlr"
    assert _route_venue("NeurIPS 2023") == "neurips"
    assert _route_venue("Advances in Neural Information Processing Systems 36") == "neurips"
    assert _route_venue("ICLR 2023") == "openreview"
    assert _route_venue("AAAI-23") == "aaai"
    # 期刊/未知 → None(走 arXiv 通用兜底)
    assert _route_venue("IEEE Transactions on Pattern Analysis and Machine Intelligence") is None
    assert _route_venue("International Journal of Computer Vision") is None
    assert _route_venue("") is None
    assert _route_venue(None) is None


# ---------------------------------------------------------------- arXiv

@pytest.mark.asyncio
async def test_arxiv_pdf_found_and_strips_version():
    """命中 arXiv 预印本: 相似度匹配 + PDF 链接去掉版本号后缀(v2 → 空)。"""

    def handler(request):
        assert "export.arxiv.org" in str(request.url)
        assert request.url.params["search_query"].startswith('ti:"')
        return httpx.Response(200, text=ARXIV_XML)

    pdf = await find_arxiv_pdf(TARGET, client=make_client(handler))
    assert pdf == "https://arxiv.org/pdf/2010.11929"  # v2 后缀已去除


@pytest.mark.asyncio
async def test_arxiv_pdf_no_match_returns_none():
    def handler(request):
        return httpx.Response(200, text=ARXIV_XML_NO_MATCH)

    assert await find_arxiv_pdf(TARGET, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_arxiv_pdf_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    assert await find_arxiv_pdf(TARGET, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_arxiv_pdf_http_error_returns_none():
    def handler(request):
        return httpx.Response(503)

    assert await find_arxiv_pdf(TARGET, client=make_client(handler)) is None


# ---------------------------------------------------------------- ACL Anthology

@pytest.mark.asyncio
async def test_acl_pdf_found():
    def handler(request):
        url = str(request.url)
        assert "aclanthology.org/volumes/2023.acl-long/" in url
        return httpx.Response(200, text=ACL_VOLUME_HTML)

    pdf = await find_acl_pdf(TARGET, year=2023, client=make_client(handler), venue="acl")
    assert pdf == "https://aclanthology.org/2023.acl-long.2.pdf"


@pytest.mark.asyncio
async def test_acl_pdf_volume_404_tries_next_candidate():
    """首个卷页 404 → 尝试下个候选卷(2023.acl-short)命中。"""
    fetched = []

    def handler(request):
        url = str(request.url)
        fetched.append(url)
        if "2023.acl-long/" in url:
            return httpx.Response(404)
        if "2023.acl-short/" in url:
            return httpx.Response(200, text=ACL_VOLUME_HTML.replace("acl-long", "acl-short"))
        return httpx.Response(404)

    pdf = await find_acl_pdf(TARGET, year=2023, client=make_client(handler), venue="acl")
    assert pdf == "https://aclanthology.org/2023.acl-short.2.pdf"
    assert any("acl-long" in u for u in fetched)
    assert any("acl-short" in u for u in fetched)


@pytest.mark.asyncio
async def test_acl_pdf_miss_returns_none():
    def handler(request):
        return httpx.Response(200, text=ACL_VOLUME_HTML)

    assert await find_acl_pdf("A Paper Not In This Volume", year=2023, client=make_client(handler), venue="acl") is None


@pytest.mark.asyncio
async def test_acl_pdf_emnlp_uses_emnlp_volume_patterns():
    """EMNLP 路由: 卷 id 用 emnlp-main 而非 acl-long。"""
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if "2023.emnlp-main/" in url:
            return httpx.Response(200, text=ACL_VOLUME_HTML.replace("acl-long", "emnlp-main"))
        return httpx.Response(404)

    pdf = await find_acl_pdf(TARGET, year=2023, client=make_client(handler), venue="emnlp")
    assert pdf == "https://aclanthology.org/2023.emnlp-main.2.pdf"
    assert any("emnlp-main" in u for u in seen)


# ---------------------------------------------------------------- PMLR

@pytest.mark.asyncio
async def test_pmlr_pdf_found():
    """主索引定位 ICML 卷(v202) → 卷页标题匹配 → PDF 直链。"""
    fetched = []

    def handler(request):
        url = str(request.url)
        fetched.append(url)
        if url.rstrip("/") == "https://proceedings.mlr.press":
            return httpx.Response(200, text=PMLR_MAIN_HTML)
        if "v202/" in url:
            return httpx.Response(200, text=PMLR_VOLUME_HTML)
        return httpx.Response(404)

    pdf = await find_pmlr_pdf(TARGET, year=2023, client=make_client(handler))
    assert pdf == "https://proceedings.mlr.press/v202/dosovitskiy23a/dosovitskiy23a.pdf"
    assert any("v202/" in u for u in fetched)
    assert not any("v219/" in u for u in fetched)  # ECML 2022 卷被年份过滤


@pytest.mark.asyncio
async def test_pmlr_pdf_ecml_year_filter():
    def handler(request):
        url = str(request.url)
        if url.rstrip("/") == "https://proceedings.mlr.press":
            return httpx.Response(200, text=PMLR_MAIN_HTML)
        if "v219/" in url:
            return httpx.Response(200, text=PMLR_VOLUME_HTML.replace("v202", "v219"))
        return httpx.Response(404)

    pdf = await find_pmlr_pdf(TARGET, year=2022, client=make_client(handler))
    assert pdf == "https://proceedings.mlr.press/v219/dosovitskiy23a/dosovitskiy23a.pdf"


@pytest.mark.asyncio
async def test_pmlr_pdf_miss_returns_none():
    def handler(request):
        url = str(request.url)
        if url.rstrip("/") == "https://proceedings.mlr.press":
            return httpx.Response(200, text=PMLR_MAIN_HTML)
        return httpx.Response(200, text=PMLR_VOLUME_HTML)

    assert await find_pmlr_pdf("A Paper Not In PMLR", year=2023, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_pmlr_pdf_index_failure_returns_none():
    def handler(request):
        return httpx.Response(500)

    assert await find_pmlr_pdf(TARGET, year=2023, client=make_client(handler)) is None


# ---------------------------------------------------------------- NeurIPS

@pytest.mark.asyncio
async def test_neurips_pdf_found():
    """年索引命中标题 → 论文页提取 citation_pdf_url。"""
    def handler(request):
        url = str(request.url)
        if "paper_files/paper/2023" in url and "-Abstract-Conference" not in url:
            return httpx.Response(200, text=NEURIPS_INDEX_HTML)
        if "hash/0001ca33ba34ce0351e4612b744b3936-Abstract-Conference.html" in url:
            return httpx.Response(200, text=NEURIPS_PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_neurips_pdf(TARGET, year=2023, client=make_client(handler))
    assert pdf == "https://proceedings.neurips.cc/paper_files/paper/2023/file/0001ca33ba34ce0351e4612b744b3936-Paper-Conference.pdf"


@pytest.mark.asyncio
async def test_neurips_pdf_miss_returns_none():
    def handler(request):
        return httpx.Response(200, text=NEURIPS_INDEX_HTML)

    assert await find_neurips_pdf("A Paper Not In NeurIPS", year=2023, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_neurips_pdf_year_none_tries_recent_years(monkeypatch):
    monkeypatch.setattr("app.research.free_pdf._recent_years", lambda n=3: [2023, 2022, 2021])

    def handler(request):
        url = str(request.url)
        if "paper/2023" in url and "-Abstract-Conference" not in url:
            return httpx.Response(200, text=NEURIPS_INDEX_HTML)
        if "hash/0001ca33ba34ce0351e4612b744b3936-Abstract-Conference.html" in url:
            return httpx.Response(200, text=NEURIPS_PAPER_HTML)
        return httpx.Response(404)

    pdf = await find_neurips_pdf(TARGET, client=make_client(handler))
    assert pdf == "https://proceedings.neurips.cc/paper_files/paper/2023/file/0001ca33ba34ce0351e4612b744b3936-Paper-Conference.pdf"


@pytest.mark.asyncio
async def test_neurips_pdf_abstract_suffix_without_conference():
    """NeurIPS 索引链接为 -Abstract.html(无 -Conference 后缀, 实测 2017 年结构)也能解析命中。"""
    def handler(request):
        url = str(request.url)
        if "paper_files/paper/2017" in url and "-Abstract" not in url:
            return httpx.Response(200, text=NEURIPS_INDEX_2017_HTML)
        if "hash/3f5ee243547dee91fbd053c1c4a845aa-Abstract.html" in url:
            return httpx.Response(200, text=NEURIPS_PAPER_2017_HTML)
        return httpx.Response(404)

    pdf = await find_neurips_pdf("Attention Is All You Need", year=2017, client=make_client(handler))
    assert pdf == "https://proceedings.neurips.cc/paper_files/paper/2017/file/3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf"


def test_parse_neurips_index_accepts_abstract_and_conference_suffix():
    """索引正则兼容 -Abstract.html 与 -Abstract-Conference.html 两种链接后缀。"""
    entries = _parse_neurips_index(NEURIPS_INDEX_2017_HTML)
    titles = [t for t, _href in entries]
    assert "Attention Is All You Need" in titles
    assert "Another Paper With Conference Suffix" in titles
    assert len(entries) == 2


# ---------------------------------------------------------------- OpenReview

@pytest.mark.asyncio
async def test_openreview_pdf_found_relative_pdf():
    """ICLR 论文命中: content.pdf 为相对路径 → 回退到 openreview.net/pdf?id= 直链。"""
    def handler(request):
        assert "api2.openreview.net/notes/search" in str(request.url)
        return httpx.Response(200, json=json.loads(OPENREVIEW_JSON))

    pdf = await find_openreview_pdf(TARGET, client=make_client(handler))
    assert pdf == "https://openreview.net/pdf?id=n-iclr"


@pytest.mark.asyncio
async def test_openreview_pdf_no_pdf_key_uses_note_id():
    def handler(request):
        return httpx.Response(200, json=json.loads(OPENREVIEW_JSON_NO_PDF_KEY))

    pdf = await find_openreview_pdf(TARGET, client=make_client(handler))
    assert pdf == "https://openreview.net/pdf?id=n-iclr2"


@pytest.mark.asyncio
async def test_openreview_pdf_no_iclr_returns_none():
    payload = json.dumps({"count": 1, "notes": [{"id": "x", "content": {
        "title": {"value": TARGET}, "venueid": {"value": "OpenReview.net/Archive"}}}]})

    def handler(request):
        return httpx.Response(200, json=json.loads(payload))

    assert await find_openreview_pdf(TARGET, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_openreview_pdf_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    assert await find_openreview_pdf(TARGET, client=make_client(handler)) is None


# ---------------------------------------------------------------- AAAI

@pytest.mark.asyncio
async def test_aaai_pdf_found():
    """归档页定位 AAAI-23 卷 → issue 页文章匹配 → galley PDF 直链。"""
    fetched = []

    def handler(request):
        url = str(request.url)
        fetched.append(url)
        if url.endswith("/AAAI/issue/archive"):
            return httpx.Response(200, text=AAAI_ARCHIVE_HTML)
        if "issue/view/703" in url:
            return httpx.Response(200, text=AAAI_ISSUE_HTML)
        return httpx.Response(404)

    pdf = await find_aaai_pdf(TARGET, year=2023, client=make_client(handler))
    assert pdf == "https://ojs.aaai.org/index.php/AAAI/article/view/36958/galley2"
    assert any("issue/view/703" in u for u in fetched)
    assert not any("issue/view/683" in u for u in fetched)  # AAAI-26 被年份过滤


@pytest.mark.asyncio
async def test_aaai_pdf_miss_returns_none():
    def handler(request):
        url = str(request.url)
        if url.endswith("/AAAI/issue/archive"):
            return httpx.Response(200, text=AAAI_ARCHIVE_HTML)
        return httpx.Response(200, text=AAAI_ISSUE_HTML)

    assert await find_aaai_pdf("A Paper Not In AAAI", year=2023, client=make_client(handler)) is None


@pytest.mark.asyncio
async def test_aaai_pdf_archive_failure_returns_none():
    def handler(request):
        return httpx.Response(500)

    assert await find_aaai_pdf(TARGET, year=2023, client=make_client(handler)) is None


# ---------------------------------------------------------------- 统一路由 find_free_pdf


class FakeFinder:
    """可配置的假查找器: 记录调用, 返回预设 URL / None / 抛异常。"""

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    async def __call__(self, title, year, client, **kwargs):
        self.calls.append((title, year))
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.fixture
def fake_finders(monkeypatch):
    """把 free_pdf 模块内各源查找器换成 FakeFinder, 返回 name -> fake 映射。"""
    import app.research.free_pdf as fp
    names = ["cvf", "find_acl_pdf", "find_pmlr_pdf", "find_neurips_pdf",
             "find_openreview_pdf", "find_aaai_pdf", "find_arxiv_pdf"]
    fakes = {n: FakeFinder() for n in names}
    for n in names:
        if n == "cvf":
            monkeypatch.setattr(fp.cvf, "find_cvf_pdf", fakes[n])  # cvf 是模块引用
        else:
            monkeypatch.setattr(fp, n, fakes[n])
    return fakes


@pytest.mark.asyncio
async def test_find_free_pdf_cvpr_routes_to_cvf(fake_finders):
    fake_finders["cvf"].result = "https://openaccess.thecvf.com/content/CVPR2023/papers/x.pdf"
    url = await find_free_pdf(TARGET, venue="CVPR 2023", year=2023, client=None)
    assert url == "https://openaccess.thecvf.com/content/CVPR2023/papers/x.pdf"
    assert fake_finders["cvf"].calls == [(TARGET, 2023)]
    assert fake_finders["find_arxiv_pdf"].calls == []  # cvf 命中不再 arXiv 兜底


@pytest.mark.asyncio
async def test_find_free_pdf_acl_routes_to_acl(fake_finders):
    fake_finders["find_acl_pdf"].result = "https://aclanthology.org/2023.acl-long.1.pdf"
    url = await find_free_pdf(TARGET, venue="EMNLP 2022", year=2022, client=None)
    assert url == "https://aclanthology.org/2023.acl-long.1.pdf"
    assert fake_finders["find_acl_pdf"].calls == [(TARGET, 2022)]


@pytest.mark.asyncio
async def test_find_free_pdf_acl_unreachable_falls_back_to_arxiv(fake_finders):
    """ACL Anthology 网络不可达(find_acl_pdf 返回 None)→ 继续 arXiv 兜底并返回结果。"""
    fake_finders["find_acl_pdf"].result = None
    fake_finders["find_arxiv_pdf"].result = "https://arxiv.org/pdf/1706.03762"
    url = await find_free_pdf("Attention Is All You Need", venue="ACL 2023", year=2023, client=None)
    assert url == "https://arxiv.org/pdf/1706.03762"
    assert fake_finders["find_acl_pdf"].calls == [("Attention Is All You Need", 2023)]
    assert fake_finders["find_arxiv_pdf"].calls == [("Attention Is All You Need", 2023)]


@pytest.mark.asyncio
async def test_find_free_pdf_icml_routes_to_pmlr(fake_finders):
    fake_finders["find_pmlr_pdf"].result = "https://proceedings.mlr.press/v202/x/x.pdf"
    url = await find_free_pdf(TARGET, venue="ICML 2023", year=2023, client=None)
    assert url == "https://proceedings.mlr.press/v202/x/x.pdf"
    assert fake_finders["find_pmlr_pdf"].calls == [(TARGET, 2023)]


@pytest.mark.asyncio
async def test_find_free_pdf_neurips_routes_to_neurips(fake_finders):
    fake_finders["find_neurips_pdf"].result = "https://proceedings.neurips.cc/paper_files/paper/2023/file/x-Paper-Conference.pdf"
    url = await find_free_pdf(TARGET, venue="NeurIPS 2023", year=2023, client=None)
    assert url == "https://proceedings.neurips.cc/paper_files/paper/2023/file/x-Paper-Conference.pdf"
    assert fake_finders["find_neurips_pdf"].calls == [(TARGET, 2023)]


@pytest.mark.asyncio
async def test_find_free_pdf_iclr_routes_to_openreview(fake_finders):
    fake_finders["find_openreview_pdf"].result = "https://openreview.net/pdf?id=abc"
    url = await find_free_pdf(TARGET, venue="ICLR 2023", year=2023, client=None)
    assert url == "https://openreview.net/pdf?id=abc"
    assert fake_finders["find_openreview_pdf"].calls == [(TARGET, 2023)]


@pytest.mark.asyncio
async def test_find_free_pdf_aaai_routes_to_aaai(fake_finders):
    fake_finders["find_aaai_pdf"].result = "https://ojs.aaai.org/index.php/AAAI/article/view/1/g"
    url = await find_free_pdf(TARGET, venue="AAAI-23", year=2023, client=None)
    assert url == "https://ojs.aaai.org/index.php/AAAI/article/view/1/g"
    assert fake_finders["find_aaai_pdf"].calls == [(TARGET, 2023)]


@pytest.mark.asyncio
async def test_find_free_pdf_unknown_venue_uses_arxiv(fake_finders):
    fake_finders["find_arxiv_pdf"].result = "https://arxiv.org/pdf/2010.11929"
    url = await find_free_pdf(TARGET, venue="IEEE Transactions on Pattern Analysis and Machine Intelligence", year=None, client=None)
    assert url == "https://arxiv.org/pdf/2010.11929"
    assert fake_finders["find_arxiv_pdf"].calls == [(TARGET, None)]


@pytest.mark.asyncio
async def test_find_free_pdf_route_miss_falls_back_to_arxiv(fake_finders):
    """路由源未命中(返回 None) → arXiv 兜底。"""
    fake_finders["find_pmlr_pdf"].result = None
    fake_finders["find_arxiv_pdf"].result = "https://arxiv.org/pdf/2010.11929"
    url = await find_free_pdf(TARGET, venue="ICML 2023", year=2023, client=None)
    assert url == "https://arxiv.org/pdf/2010.11929"
    assert fake_finders["find_arxiv_pdf"].calls == [(TARGET, 2023)]


@pytest.mark.asyncio
async def test_find_free_pdf_route_exception_falls_back_to_arxiv(fake_finders):
    """路由源抛异常 → 捕获后 arXiv 兜底。"""
    fake_finders["find_neurips_pdf"].exc = RuntimeError("boom")
    fake_finders["find_arxiv_pdf"].result = "https://arxiv.org/pdf/2010.11929"
    url = await find_free_pdf(TARGET, venue="NeurIPS 2023", year=2023, client=None)
    assert url == "https://arxiv.org/pdf/2010.11929"
    assert fake_finders["find_arxiv_pdf"].calls == [(TARGET, 2023)]


@pytest.mark.asyncio
async def test_find_free_pdf_all_fail_returns_none(fake_finders):
    fake_finders["find_aaai_pdf"].result = None
    fake_finders["find_arxiv_pdf"].result = None
    assert await find_free_pdf(TARGET, venue="AAAI-23", year=2023, client=None) is None


@pytest.mark.asyncio
async def test_find_free_pdf_arxiv_failure_returns_none(fake_finders):
    fake_finders["find_arxiv_pdf"].exc = RuntimeError("boom")
    assert await find_free_pdf(TARGET, venue="", year=None, client=None) is None
