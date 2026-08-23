"""多源检索器测试(全部 mock, 不访问真实网络)。"""

import httpx
import pytest

import app.research.searchers as searchers_mod
from app.research.schemas import SearchResult
from app.research.searchers import (
    ArxivSearcher,
    OpenAlexSearcher,
    SearchSourceError,
    SemanticScholarSearcher,
)

OPENSEARCH_NS = 'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"'

ARXIV_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom" {OPENSEARCH_NS}>
  <opensearch:totalResults>42</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>Lightweight Attention for Super-Resolution</title>
    <summary>We propose a lightweight attention module.</summary>
    <published>2024-01-10T00:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Wang</name></author>
    <arxiv:doi>10.1000/arxiv.2401.12345</arxiv:doi>
    <arxiv:journal_ref>CVPR 2024</arxiv:journal_ref>
  </entry>
</feed>"""

S2_JSON = {
    "data": [
        {
            "title": "Efficient Super-Resolution Networks",
            "abstract": "An efficient approach.",
            "year": 2023,
            "authors": [{"name": "Carol Li"}],
            "venue": "ICCV",
            "externalIds": {"DOI": "10.1000/xyz", "ArXiv": "2301.99999"},
            "citationCount": 42,
            "openAccessPdf": {"url": "https://arxiv.org/pdf/2301.99999"},
            "url": "https://www.semanticscholar.org/paper/abc",
        }
    ]
}


def client_with(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://example.com")


@pytest.mark.asyncio
async def test_arxiv_search_parses_atom():
    def handler(request):
        assert "export.arxiv.org" in str(request.url)
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("lightweight super-resolution attention", top_k=5)
    assert len(results) == 1
    assert total == 42  # 从 opensearch:totalResults 解析
    r = results[0]
    assert r.source == "arxiv"
    assert r.title == "Lightweight Attention for Super-Resolution"
    assert r.authors == ["Alice Zhang", "Bob Wang"]
    assert r.year == 2024
    assert r.venue == "CVPR 2024"
    assert r.doi == "10.1000/arxiv.2401.12345"
    assert r.pdf_url == "https://arxiv.org/pdf/2401.12345"
    assert r.page_url == "https://arxiv.org/abs/2401.12345"


@pytest.mark.asyncio
async def test_arxiv_start_param_passthrough():
    """start 参数透传到 arXiv API 的 start 查询参数。"""
    seen = {}

    def handler(request):
        seen["start"] = request.url.params.get("start")
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("query", top_k=5, start=20)
    assert seen["start"] == "20"
    assert total == 42
    assert len(results) == 1


@pytest.mark.asyncio
async def test_arxiv_total_falls_back_to_result_count():
    """无 opensearch:totalResults 时 total 回退为 len(results)。"""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>No Total</title>
    <summary>s</summary>
    <published>2024-01-10T00:00:00Z</published>
    <author><name>Alice Zhang</name></author>
  </entry>
</feed>"""

    def handler(request):
        return httpx.Response(200, text=xml)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("query", top_k=5)
    assert len(results) == 1
    assert total == 1


@pytest.mark.asyncio
async def test_s2_search_parses_json():
    def handler(request):
        assert "api.semanticscholar.org" in str(request.url)
        return httpx.Response(200, json=S2_JSON)

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    results = await searcher.search("super-resolution", top_k=5)
    assert len(results) == 1
    r = results[0]
    assert r.source == "semantic_scholar"
    assert r.year == 2023
    assert r.citations == 42
    assert r.pdf_url == "https://arxiv.org/pdf/2301.99999"
    assert r.doi == "10.1000/xyz"


@pytest.mark.asyncio
async def test_arxiv_timeout_raises():
    async def handler(request):
        raise httpx.ConnectTimeout("timeout")

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    with pytest.raises(httpx.HTTPError):
        await searcher.search("query", top_k=5)


@pytest.mark.asyncio
async def test_s2_empty_results():
    def handler(request):
        return httpx.Response(200, json={"data": []})

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    assert await searcher.search("nothing", top_k=5) == []


# ---- 审查修复: Issue 1 解析异常归一化为 SearchSourceError ----

@pytest.mark.asyncio
async def test_arxiv_parse_error_raises_search_source_error():
    def handler(request):
        return httpx.Response(200, text="<feed>unclosed tag")

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    with pytest.raises(SearchSourceError):
        await searcher.search("query", top_k=5)


@pytest.mark.asyncio
async def test_s2_parse_error_raises_search_source_error():
    def handler(request):
        return httpx.Response(200, text="not json at all")

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    with pytest.raises(SearchSourceError):
        await searcher.search("query", top_k=5)


# ---- 审查修复: Issue 2 proxy 参数不再死代码 ----

def test_constructor_forwards_proxy_to_make_client(monkeypatch):
    captured = {}

    def fake_make_client(proxy, timeout):
        captured["proxy"] = proxy
        captured["timeout"] = timeout
        return object()

    monkeypatch.setattr(searchers_mod, "_make_client", fake_make_client)
    searcher = ArxivSearcher(timeout=3.0, proxy="http://127.0.0.1:9999")
    assert searcher.client is not None
    assert captured == {"proxy": "http://127.0.0.1:9999", "timeout": 3.0}


# ---- 审查修复: Issue 3 S2 单条脏数据不拖垮整批 ----

@pytest.mark.asyncio
async def test_s2_dirty_item_skipped():
    dirty = {
        "title": "Broken Paper",
        "openAccessPdf": "not-a-dict",  # 非 dict: 类型守卫按空 dict 处理
        "year": [2023],  # 非法类型: 触发 Pydantic ValidationError, 整条跳过
        "authors": [{"name": "X"}],
    }
    normal = S2_JSON["data"][0]

    def handler(request):
        return httpx.Response(200, json={"data": [dirty, normal]})

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    results = await searcher.search("dirty", top_k=5)
    assert len(results) == 1
    assert results[0].title == "Efficient Super-Resolution Networks"


# ---- 审查修复: Issue 5 S2 429/5xx 轻量重试(最多一次) ----

@pytest.mark.asyncio
async def test_s2_retry_on_429():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json=S2_JSON)

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    results = await searcher.search("super-resolution", top_k=5)
    assert len(calls) == 2
    assert len(results) == 1


# ---- 审查修复: Issue 4 aclose 只关闭自建 client ----

@pytest.mark.asyncio
async def test_searcher_aclose_owns_client():
    owned = ArxivSearcher(timeout=5.0)
    await owned.aclose()
    assert owned.client.is_closed is True

    injected = client_with(lambda r: httpx.Response(200, text=ARXIV_XML))
    try:
        searcher = SemanticScholarSearcher(client=injected, timeout=5.0)
        await searcher.aclose()
        assert injected.is_closed is False
    finally:
        await injected.aclose()

# ---- 新增: 年份筛选 + 发表状态/CCF 分级 ----

@pytest.mark.asyncio
async def test_arxiv_search_query_unquoted_tokens():
    """多词查询不带引号(分词 AND): 避免整句短语匹配召回归零。"""
    seen = {}

    def handler(request):
        seen["search_query"] = request.url.params.get("search_query")
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("Industrial Anomaly Detection Total Recall", top_k=5)
    assert seen["search_query"] == "all:industrial anomaly detection total recall"
    assert '"' not in seen["search_query"]


@pytest.mark.asyncio
async def test_arxiv_search_query_sanitizes_syntax_chars():
    """查询中的引号/括号/布尔词等 arXiv 语法字符被清洗, 不破坏检索语法。"""
    seen = {}

    def handler(request):
        seen["search_query"] = request.url.params.get("search_query")
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search('图像编辑 "AND" (diffusion)??', top_k=5)
    sq = seen["search_query"]
    assert sq.startswith("all:")
    assert '"' not in sq and "(" not in sq and ")" not in sq
    assert "diffusion" in sq


@pytest.mark.asyncio
async def test_arxiv_year_range_appended_to_search_query():
    """year_min/year_max 透传为 arXiv submittedDate 范围过滤。"""
    seen = {}

    def handler(request):
        seen["search_query"] = request.url.params.get("search_query")
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("query", top_k=5, year_min=2020, year_max=2024)
    assert "submittedDate:[20200101 TO 20241231]" in seen["search_query"]


@pytest.mark.asyncio
async def test_arxiv_year_min_only_appends_lower_bound():
    """只给 year_min → submittedDate 下界为 year_min, 上界用默认。"""
    seen = {}

    def handler(request):
        seen["search_query"] = request.url.params.get("search_query")
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("query", top_k=5, year_min=2021)
    assert "submittedDate:[20210101 TO " in seen["search_query"]


@pytest.mark.asyncio
async def test_arxiv_year_max_only_appends_upper_bound():
    """只给 year_max → submittedDate 上界为 year_max, 下界用默认。"""
    seen = {}

    def handler(request):
        seen["search_query"] = request.url.params.get("search_query")
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("query", top_k=5, year_max=2022)
    assert "submittedDate:[19910101 TO 20221231]" in seen["search_query"]


@pytest.mark.asyncio
async def test_arxiv_no_year_no_submitted_date():
    """不传年份 → search_query 不含 submittedDate。"""
    seen = {}

    def handler(request):
        seen["search_query"] = request.url.params.get("search_query")
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("query", top_k=5)
    assert "submittedDate" not in seen["search_query"]


@pytest.mark.asyncio
async def test_arxiv_result_published_and_ccf_filled():
    """venue 非空 → published=True; CCF 目录命中 → ccf_level。"""
    def handler(request):
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    results, _ = await searcher.search("query", top_k=5)
    r = results[0]
    assert r.venue == "CVPR 2024"
    assert r.published is True
    assert r.ccf_level == "A"


@pytest.mark.asyncio
async def test_arxiv_preprint_published_false():
    """无 journal_ref → venue 空 → published=False, ccf_level=None。"""
    xml = ARXIV_XML.replace("<arxiv:journal_ref>CVPR 2024</arxiv:journal_ref>", "")

    def handler(request):
        return httpx.Response(200, text=xml)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    results, _ = await searcher.search("query", top_k=5)
    r = results[0]
    assert r.venue == ""
    assert r.published is False
    assert r.ccf_level is None


@pytest.mark.asyncio
async def test_s2_result_published_and_ccf_filled():
    """S2 结果的 venue 同样填充 published/ccf_level。"""
    def handler(request):
        return httpx.Response(200, json=S2_JSON)

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    results = await searcher.search("super-resolution", top_k=5)
    r = results[0]
    assert r.venue == "ICCV"
    assert r.published is True
    assert r.ccf_level == "A"

# ==================== OpenAlex: 解析 / 分页 / 年份过滤 / 重试 / 错误归一化 ====================

OPENALEX_JSON = {
    "meta": {"count": 137},
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "title": "Lightweight Attention for Super-Resolution",
            "publication_year": 2024,
            "primary_location": {"source": {"display_name": "CVPR"}},
            "authorships": [
                {"author": {"display_name": "Alice Zhang"}},
                {"author": {"display_name": "Bob Wang"}},
            ],
            "doi": "https://doi.org/10.1000/cvpr.2024.123",
            "ids": {"openalex": "https://openalex.org/W2741809807", "arxiv": "https://arxiv.org/abs/2401.12345"},
            "open_access": {"is_oa": True, "oa_status": "gold", "oa_url": "https://openaccess.cvpr.org/p.pdf"},
            "cited_by_count": 1234,
            "abstract_inverted_index": {
                "module": [5],
                "We": [0],
                "propose": [1],
                "lightweight": [3],
                "attention": [4],
                "a": [2],
            },
        },
        {
            "id": "https://openalex.org/W2999999999",
            "title": "Closed Access Paper",
            "publication_year": 2023,
            "primary_location": {"source": {"display_name": "IEEE Transactions on Image Processing"}},
            "authorships": [{"author": {"display_name": "Carol Li"}}],
            "doi": "https://doi.org/10.1000/ieee.tip",
            "ids": {"openalex": "https://openalex.org/W2999999999"},
            "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
            "abstract_inverted_index": None,
        },
    ],
}


def _make_openalex_work(i: int) -> dict:
    """最小可解析的 work(分页循环测试用)。"""
    return {
        "id": f"https://openalex.org/W{i}",
        "title": f"Paper {i}",
        "publication_year": 2024,
        "primary_location": None,
        "authorships": [],
        "doi": None,
        "ids": {},
        "open_access": None,
        "abstract_inverted_index": None,
    }


@pytest.mark.asyncio
async def test_openalex_search_parses_json():
    """OpenAlex 解析: 标题/作者/年份/venue/DOI 归一化/oa_url/abstract 还原/oa_status/openalex_id。"""
    def handler(request):
        assert "api.openalex.org" in str(request.url)
        return httpx.Response(200, json=OPENALEX_JSON)

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("lightweight super-resolution attention", top_k=20)
    assert len(results) == 2
    assert total == 137  # meta.count
    r = results[0]
    assert r.source == "openalex"
    assert r.title == "Lightweight Attention for Super-Resolution"
    assert r.authors == ["Alice Zhang", "Bob Wang"]
    assert r.year == 2024
    assert r.venue == "CVPR"
    assert r.abstract == "We propose a lightweight attention module"  # inverted index 按位置还原
    assert r.doi == "10.1000/cvpr.2024.123"  # https://doi.org/ 前缀剥除
    assert r.pdf_url == "https://openaccess.cvpr.org/p.pdf"
    assert r.page_url == "https://openaccess.cvpr.org/p.pdf"
    assert r.oa_status == "open"
    assert r.openalex_id == "W2741809807"
    assert r.published is True
    assert r.ccf_level == "A"
    assert r.citations == 1234  # Google Scholar 式: cited_by_count 透传


@pytest.mark.asyncio
async def test_openalex_doi_type_published_without_venue():
    """venue 缺失但有 DOI 且类型非 preprint → 视为已发表(无 CCF 级别)。"""
    work = {
        "id": "https://openalex.org/W88888",
        "title": "Enhanced Deep Residual Networks for Single Image Super-Resolution",
        "publication_year": 2017,
        "primary_location": None,  # 无 venue source(OpenAlex 数据缺失)
        "locations": [{"source": None}, {"source": {"display_name": "arXiv (Cornell University)"}}],
        "authorships": [],
        "doi": "https://doi.org/10.1109/cvprw.2017.151",
        "ids": {"openalex": "https://openalex.org/W88888"},
        "open_access": None,
        "abstract_inverted_index": None,
        "cited_by_count": 615,
        "type": "conference-paper",
    }

    def handler(request):
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, _ = await searcher.search("q", top_k=20)
    r = results[0]
    assert r.published is True
    assert r.ccf_level is None
    assert r.citations == 615


@pytest.mark.asyncio
async def test_openalex_closed_access_doi_landing_page():
    """付费墙论文: oa_status=closed, pdf_url 为空, page_url 回退 DOI 落地页。"""
    def handler(request):
        return httpx.Response(200, json=OPENALEX_JSON)

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, _ = await searcher.search("q", top_k=20)
    r = results[1]
    assert r.oa_status == "closed"
    assert r.pdf_url is None
    assert r.page_url == "https://doi.org/10.1000/ieee.tip"
    assert r.published is True
    assert r.ccf_level == "B"  # IEEE Transactions on Image Processing


@pytest.mark.asyncio
async def test_openalex_closed_free_venue_promoted_to_open():
    """CVPR 等官方开放站点会议被 IEEE 标 closed → 检索阶段提升为开放获取(免费 PDF 实际可获取)。"""
    work = {
        "id": "https://openalex.org/W55555",
        "title": "Towards Total Recall in Industrial Anomaly Detection",
        "publication_year": 2022,
        "primary_location": {"source": {"display_name": "2022 IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)"}},
        "authorships": [],
        "doi": "https://doi.org/10.1109/CVPR52688.2022.01326",
        "ids": {"openalex": "https://openalex.org/W55555"},
        "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
        "abstract_inverted_index": None,
    }

    def handler(request):
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, _ = await searcher.search("q", top_k=20)
    r = results[0]
    assert r.oa_status == "open"
    assert r.pdf_url is None  # 直链仍无: 下载时经 L1.5 CVF 查找
    assert r.page_url == "https://doi.org/10.1109/CVPR52688.2022.01326"
    assert r.published is True
    assert r.ccf_level == "A"


@pytest.mark.asyncio
async def test_openalex_closed_with_arxiv_id_promoted_to_open():
    """closed + 存在 arXiv 预印本 → 提升为开放获取。"""
    work = {
        "id": "https://openalex.org/W66666",
        "title": "Some Closed Journal Paper",
        "publication_year": 2023,
        "primary_location": {"source": {"display_name": "IEEE Transactions on Pattern Analysis and Machine Intelligence"}},
        "authorships": [],
        "doi": "https://doi.org/10.1000/tpami.2023",
        "ids": {"openalex": "https://openalex.org/W66666", "arxiv": "https://arxiv.org/abs/2301.12345"},
        "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
        "abstract_inverted_index": None,
    }

    def handler(request):
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, _ = await searcher.search("q", top_k=20)
    r = results[0]
    assert r.oa_status == "open"
    assert r.published is True
    assert r.ccf_level == "A"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_openalex_year_range_filter():
    """year_min/year_max → filter=publication_year:{lo}-{hi}。"""
    seen = {}

    def handler(request):
        seen["filter"] = request.url.params.get("filter")
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("q", top_k=5, year_min=2020, year_max=2024)
    assert seen["filter"] == "publication_year:2020-2024"


@pytest.mark.asyncio
async def test_openalex_year_min_only_uses_default_max():
    """只给 year_min → 上界用默认(DEFAULT_YEAR_MAX=2030)。"""
    seen = {}

    def handler(request):
        seen["filter"] = request.url.params.get("filter")
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("q", top_k=5, year_min=2021)
    assert seen["filter"] == "publication_year:2021-2030"


@pytest.mark.asyncio
async def test_openalex_no_year_no_filter():
    seen = {}

    def handler(request):
        seen["filter"] = request.url.params.get("filter")
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("q", top_k=5)
    assert seen["filter"] is None


@pytest.mark.asyncio
async def test_openalex_start_maps_to_page():
    """start=20, top_k=20 → page=2(Google Scholar 式偏移), per-page=top_k。"""
    seen = {}

    def handler(request):
        seen["page"] = request.url.params.get("page")
        seen["per_page"] = request.url.params.get("per-page")
        return httpx.Response(200, json={"meta": {"count": 100}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("q", top_k=20, start=20)
    assert seen["page"] == "2"
    assert seen["per_page"] == "20"
    assert total == 100


@pytest.mark.asyncio
async def test_openalex_per_page_capped_at_50():
    """top_k>50 → per-page 钳制 50(OpenAlex 每页上限)。"""
    seen = {}

    def handler(request):
        seen["per_page"] = request.url.params.get("per-page")
        return httpx.Response(200, json={"meta": {"count": 200}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("q", top_k=200)
    assert seen["per_page"] == "50"


@pytest.mark.asyncio
async def test_openalex_pagination_loops_when_top_k_gt_50():
    """top_k=120 → 3 次请求(per-page 50/50/20)循环拉够, total 取 meta.count。"""
    calls: list[int] = []

    def handler(request):
        page = int(request.url.params.get("page"))
        per_page = int(request.url.params.get("per-page"))
        calls.append(page)
        works = [_make_openalex_work(i) for i in range(per_page)]
        return httpx.Response(200, json={"meta": {"count": 200}, "results": works})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("q", top_k=120)
    assert calls == [1, 2, 3]
    assert len(results) == 120
    assert total == 200


@pytest.mark.asyncio
async def test_openalex_retry_on_429():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json=OPENALEX_JSON)

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("q", top_k=5)
    assert len(calls) == 2
    assert len(results) == 2
    assert total == 137


@pytest.mark.asyncio
async def test_openalex_parse_error_raises_search_source_error():
    def handler(request):
        return httpx.Response(200, text="not json at all")

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    with pytest.raises(SearchSourceError):
        await searcher.search("q", top_k=5)


@pytest.mark.asyncio
async def test_openalex_dirty_item_skipped():
    """单条脏数据(非法年份类型)跳过, 不拖垮整批。"""
    dirty = {"title": "Broken Paper", "publication_year": [2024], "open_access": "not-a-dict"}
    normal = OPENALEX_JSON["results"][0]

    def handler(request):
        return httpx.Response(200, json={"meta": {"count": 2}, "results": [dirty, normal]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results, total = await searcher.search("q", top_k=5)
    assert len(results) == 1
    assert results[0].title == "Lightweight Attention for Super-Resolution"
    assert total == 2


@pytest.mark.asyncio
async def test_openalex_aclose_owns_client():
    owned = OpenAlexSearcher(timeout=5.0)
    await owned.aclose()
    assert owned.client.is_closed is True

    injected = client_with(lambda r: httpx.Response(200, json=OPENALEX_JSON))
    try:
        searcher = OpenAlexSearcher(client=injected, timeout=5.0)
        await searcher.aclose()
        assert injected.is_closed is False
    finally:
        await injected.aclose()


@pytest.mark.asyncio
async def test_openalex_select_includes_abstract_inverted_index():
    """select 参数必须包含 abstract_inverted_index(缺失时线上响应恒无摘要字段)。

    OpenAlex 的 select 只返回列出的字段; mock 直接构造了摘要字段会绕过该限制,
    故在 handler 里断言真实请求的 select 参数。
    """
    seen = {}

    def handler(request):
        seen["select"] = request.url.params.get("select")
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    await searcher.search("q", top_k=5)
    assert "abstract_inverted_index" in seen["select"].split(",")


# ==================== OpenAlex: arXiv 发表信息补全(enrich) ====================

ARXIV_ENRICH_WORK = {
    "id": "https://openalex.org/W2741809807",
    "title": "Lightweight Attention for Super-Resolution",
    "publication_year": 2024,
    "primary_location": {"source": {"display_name": "CVPR 2024"}},
    "authorships": [],
    "doi": "https://doi.org/10.1000/real.doi",
    "ids": {"openalex": "https://openalex.org/W2741809807", "arxiv": "https://arxiv.org/abs/2401.12345"},
    "open_access": {"is_oa": True, "oa_status": "green", "oa_url": "https://repo.example.com/p.pdf"},
    "abstract_inverted_index": None,
}


def _arxiv_result(**overrides) -> SearchResult:
    """arXiv 预印本结果(page_url 含 arXiv ID, venue 空)。"""
    defaults = {
        "source": "arxiv",
        "title": "Lightweight Attention for Super-Resolution",
        "authors": [],
        "year": 2024,
        "venue": "",
        "abstract": "",
        "page_url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
        "published": False,
        "ccf_level": None,
    }
    defaults.update(overrides)
    return SearchResult(**defaults)


@pytest.mark.asyncio
async def test_openalex_enrich_arxiv_single_id():
    """按 arXiv ID 批量反查: venue/published/ccf_level/doi/oa_status/openalex_id 补全。"""
    seen = {}

    def handler(request):
        seen["filter"] = request.url.params.get("filter")
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [ARXIV_ENRICH_WORK]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    arxiv_r = _arxiv_result()
    results = await searcher.enrich_arxiv([arxiv_r])
    assert seen["filter"] == "ids.arxiv:2401.12345"
    r = results[0]
    assert r.venue == "CVPR 2024"
    assert r.published is True
    assert r.ccf_level == "A"
    assert r.doi == "10.1000/real.doi"
    assert r.openalex_id == "W2741809807"
    assert r.oa_status == "open"  # OpenAlex oa_status="green" 归一化为规范三态 "open"
    assert r.pdf_url == "https://arxiv.org/pdf/2401.12345"  # 已有 arXiv 直链, 不被覆盖
    assert r.source == "arxiv"  # 仍是 arXiv 结果(结构不变)


@pytest.mark.asyncio
async def test_openalex_enrich_batch_multiple_ids():
    """多个 arXiv ID 用 | 连接一次请求(OpenAlex OR)。"""
    seen = {}

    def handler(request):
        seen["filter"] = request.url.params.get("filter")
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    await searcher.enrich_arxiv([
        _arxiv_result(),
        _arxiv_result(page_url="https://arxiv.org/abs/2401.54321"),
    ])
    assert seen["filter"] == "ids.arxiv:2401.12345|2401.54321"


@pytest.mark.asyncio
async def test_openalex_enrich_400_falls_back_to_title_search():
    """ids.arxiv filter 400 → 退化逐条按标题(+年份)搜索。"""
    calls: list[dict] = []

    def handler(request):
        f = request.url.params.get("filter")
        calls.append({
            "filter": f,
            "search": request.url.params.get("search"),
        })
        if f and f.startswith("ids.arxiv:"):
            return httpx.Response(400, json={"error": "bad filter"})
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [ARXIV_ENRICH_WORK]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results = await searcher.enrich_arxiv([_arxiv_result()])
    assert calls[0]["filter"] == "ids.arxiv:2401.12345"
    assert calls[1]["search"] == "Lightweight Attention for Super-Resolution"
    assert calls[1]["filter"] == "publication_year:2024"  # 标题搜索附带年份过滤
    assert results[0].venue == "CVPR 2024"
    assert results[0].published is True


@pytest.mark.asyncio
async def test_openalex_enrich_caps_and_chunks_targets():
    """补全目标钳制到 ENRICH_TARGETS_MAX 且按 ENRICH_BATCH_MAX 分批(防几十个 ID 单次反查)。"""
    from app.research.searchers import ENRICH_BATCH_MAX, ENRICH_TARGETS_MAX

    filters: list[str] = []

    def handler(request):
        f = request.url.params.get("filter")
        if f and f.startswith("ids.arxiv:"):
            filters.append(f)
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    results = [
        _arxiv_result(page_url=f"https://arxiv.org/abs/2401.{10000 + i:05d}")
        for i in range(150)
    ]
    await searcher.enrich_arxiv(results)
    # 150 条 → 钳制 ENRICH_TARGETS_MAX(120) → 120/25 向上取整 = 5 批
    assert len(filters) == 5
    total_ids = sum(len(f.split(":")[1].split("|")) for f in filters)
    assert total_ids == ENRICH_TARGETS_MAX
    for f in filters:
        assert len(f.split(":")[1].split("|")) <= ENRICH_BATCH_MAX


@pytest.mark.asyncio
async def test_openalex_enrich_failure_keeps_original():
    """反查 500 → 补全失败不阻断, 保留 arXiv 原始 venue。"""
    def handler(request):
        return httpx.Response(500, json={})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    arxiv_r = _arxiv_result()
    results = await searcher.enrich_arxiv([arxiv_r])
    r = results[0]
    assert r.venue == ""
    assert r.published is False
    assert r.ccf_level is None
    assert r.doi is None


@pytest.mark.asyncio
async def test_openalex_enrich_no_arxiv_id_no_request():
    """结果无法提取 arXiv ID / 非 arxiv 来源 → 不发请求。"""
    class BoomClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("不应发起 OpenAlex 请求")

    searcher = OpenAlexSearcher(client=BoomClient(), timeout=5.0)
    no_id = _arxiv_result(page_url="https://example.com/not-arxiv")
    other_source = SearchResult(
        source="semantic_scholar", title="S2", authors=[], page_url="https://x/y", year=2024
    )
    results = await searcher.enrich_arxiv([no_id, other_source])
    assert results == [no_id, other_source]


@pytest.mark.asyncio
async def test_openalex_enrich_preprint_no_venue_keeps_original():
    """反查命中但无 primary_location.source → 不覆盖 arXiv 原始 venue。"""
    def handler(request):
        work = dict(ARXIV_ENRICH_WORK)
        work["primary_location"] = None
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    arxiv_r = _arxiv_result(venue="ICLR 2024", published=True, ccf_level="A")
    results = await searcher.enrich_arxiv([arxiv_r])
    assert results[0].venue == "ICLR 2024"
    assert results[0].published is True
    assert results[0].ccf_level == "A"


@pytest.mark.asyncio
async def test_enrich_arxiv_skips_arxiv_venue():
    """OpenAlex venue 是 arXiv 本身('arXiv (Cornell University)') → 视为未正式发表。

    不更新 venue(保留 arXiv 原始空 venue)、published 保持 False、ccf_level 保持 None;
    但 openalex_id/doi 等其他字段仍正常补全。
    """
    def handler(request):
        work = dict(ARXIV_ENRICH_WORK)
        work["primary_location"] = {"source": {"display_name": "arXiv (Cornell University)"}}
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    arxiv_r = _arxiv_result()
    results = await searcher.enrich_arxiv([arxiv_r])
    r = results[0]
    assert r.venue == ""  # 未被 'arXiv (Cornell University)' 覆盖
    assert r.published is False  # 纯 arXiv 预印本不得被标为已发表
    assert r.ccf_level is None
    assert r.openalex_id == "W2741809807"  # 其他字段仍补全
    assert r.doi == "10.1000/real.doi"


@pytest.mark.asyncio
async def test_enrich_arxiv_updates_real_venue():
    """OpenAlex venue 是真实会议(CVPR 2024) → published=True, ccf_level 按 classify_ccf 更新。"""
    def handler(request):
        work = dict(ARXIV_ENRICH_WORK)
        work["primary_location"] = {"source": {"display_name": "CVPR 2024"}}
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work]})

    searcher = OpenAlexSearcher(client=client_with(handler), timeout=5.0)
    arxiv_r = _arxiv_result()
    results = await searcher.enrich_arxiv([arxiv_r])
    r = results[0]
    assert r.venue == "CVPR 2024"
    assert r.published is True
    assert r.ccf_level == "A"


