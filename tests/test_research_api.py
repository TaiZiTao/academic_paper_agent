"""research 路由 API 测试(依赖覆盖, 不访问真实网络/DB)。"""

import httpx
import pytest
from fastapi import FastAPI

from app.research.router import router as research_router


class FakeResearchService:
    def __init__(self):
        self.search_called = False
        self.offset = None
        self.year_min = None
        self.year_max = None

    async def search(self, query, top_k, offset, year_min=None, year_max=None, on_event=None, refresh=False):
        self.search_called = True
        self.offset = offset
        self.year_min = year_min
        self.year_max = year_max
        self.refresh = refresh
        on_event({"event": "plan", "queries": [query], "sources": ["arxiv", "openalex"], "direct": False})
        on_event({"event": "results", "items": [], "total": 1, "offset": offset, "total_is_estimate": True})

    async def create_imports(self, items):
        return [{"id": 1, "title": items[0].title, "status": "pending", "progress": 0}]

    async def list_imports(self):
        return []

    async def get_import(self, import_id):
        return None

    async def retry(self, import_id):
        return None

    async def browser_status(self):
        return {"status": "none", "message": ""}


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(research_router, prefix="/api/v1")
    from app.research.router import get_research_service

    service = FakeResearchService()

    async def override():
        return service

    app.dependency_overrides[get_research_service] = override
    return app, service


@pytest.mark.asyncio
async def test_search_sse_stream():
    app, service = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/v1/research/search", json={"query": "超分", "top_k": 5}) as resp:
            assert resp.status_code == 200
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
    assert "event: plan" in body
    assert service.search_called
    assert service.offset == 0  # 未传 offset 时默认 0
    assert "total" in body  # results 事件携带 total
    assert '"offset": 0' in body  # results 事件携带 offset
    assert '"total_is_estimate": true' in body  # total 为估计上界


@pytest.mark.asyncio
async def test_search_offset_passthrough():
    """offset 字段透传到 service.search, results 事件回带 offset。"""
    app, service = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/v1/research/search", json={"query": "超分", "top_k": 5, "offset": 20}) as resp:
            assert resp.status_code == 200
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
    assert service.offset == 20
    assert '"offset": 20' in body


@pytest.mark.asyncio
async def test_search_offset_clamped():
    """offset 超出 [0, 10000] → 422(钳制合理上限, 防越界翻页)。"""
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/research/search", json={"query": "超分", "top_k": 5, "offset": 10001})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_imports():
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research/imports",
            json={"items": [{"source": "arxiv", "title": "Paper A", "pdf_url": "https://x/p.pdf"}]},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["items"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_create_imports_year_passthrough():
    """ImportItem.year 经 /imports 请求体透传(前端 downloadSelected 带 r.year)。"""
    received = {}

    class Service(FakeResearchService):
        async def create_imports(self, items):
            received["year"] = items[0].year
            return [{"id": 1, "title": items[0].title, "status": "pending", "progress": 0}]

    app = FastAPI()
    app.include_router(research_router, prefix="/api/v1")
    from app.research.router import get_research_service

    service = Service()

    async def override():
        return service

    app.dependency_overrides[get_research_service] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research/imports",
            json={"items": [{"source": "openalex", "title": "Paper A", "year": 2023, "pdf_url": "https://x/p.pdf"}]},
        )
        assert resp.status_code == 202
    assert received["year"] == 2023


@pytest.mark.asyncio
async def test_create_imports_year_omitted_defaults_none():
    """未传 year → service 收到 None(前端旧版本兼容)。"""
    received = {}

    class Service(FakeResearchService):
        async def create_imports(self, items):
            received["year"] = items[0].year
            return [{"id": 1, "title": items[0].title, "status": "pending", "progress": 0}]

    app = FastAPI()
    app.include_router(research_router, prefix="/api/v1")
    from app.research.router import get_research_service

    service = Service()

    async def override():
        return service

    app.dependency_overrides[get_research_service] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research/imports",
            json={"items": [{"source": "arxiv", "title": "Paper B", "pdf_url": "https://x/p.pdf"}]},
        )
        assert resp.status_code == 202
    assert received["year"] is None


@pytest.mark.asyncio
async def test_browser_status():
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/research/browser/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "none"


@pytest.mark.asyncio
async def test_imports_invalid_item_422():
    """items 缺 title → 422(而非 500)。"""
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research/imports",
            json={"items": [{"source": "arxiv", "pdf_url": "https://x/p.pdf"}]},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_empty_query_422():
    """query 为空 → 422(而非 400)。"""
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/research/search", json={"query": "", "top_k": 5})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_topk_clamped():
    """top_k 超出 [1, 50] → 422(而非静默钳制)。"""
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/research/search", json={"query": "超分", "top_k": 999})
        assert resp.status_code == 422

@pytest.mark.asyncio
async def test_search_year_range_passthrough():
    """year_min/year_max 字段透传到 service.search。"""
    app, service = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/research/search",
            json={"query": "超分", "top_k": 5, "year_min": 2020, "year_max": 2024},
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_text():
                pass
    assert service.year_min == 2020
    assert service.year_max == 2024


@pytest.mark.asyncio
async def test_search_year_defaults_none():
    """未传年份 → service.search 收到 None。"""
    app, service = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/v1/research/search", json={"query": "超分", "top_k": 5}) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_text():
                pass
    assert service.year_min is None
    assert service.year_max is None


@pytest.mark.asyncio
async def test_search_year_out_of_range_422():
    """year_min/year_max 超出 [1991, 2030] → 422。"""
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/research/search", json={"query": "超分", "top_k": 5, "year_min": 1990})
        assert resp.status_code == 422
        resp = await client.post("/api/v1/research/search", json={"query": "超分", "top_k": 5, "year_max": 2031})
        assert resp.status_code == 422


# ---------- OpenAlex 适配: plan sources 双源 + 新字段经 SSE 序列化 ----------

@pytest.mark.asyncio
async def test_search_plan_sources_include_openalex():
    """plan 事件携带双源 [arxiv, openalex](ALL_SOURCES 变化)。"""
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/v1/research/search", json={"query": "超分", "top_k": 5}) as resp:
            assert resp.status_code == 200
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
    assert '"sources": ["arxiv", "openalex"]' in body


@pytest.mark.asyncio
async def test_search_sse_openalex_fields_serialized():
    """results 事件透传 OpenAlex 新字段(oa_status/openalex_id), 前端无需改即可消费。"""
    class ServiceWithOpenAlex(FakeResearchService):
        async def search(self, query, top_k, offset, year_min=None, year_max=None, on_event=None, refresh=False):
            self.search_called = True
            self.refresh = refresh
            on_event({"event": "plan", "queries": [query], "sources": ["arxiv", "openalex"], "direct": False})
            on_event({
                "event": "results",
                "items": [{
                    "source": "openalex",
                    "title": "Closed Access Paper",
                    "authors": ["Carol Li"],
                    "year": 2023,
                    "venue": "IEEE Transactions on Image Processing",
                    "abstract": "",
                    "doi": "10.1000/ieee.tip",
                    "pdf_url": None,
                    "page_url": "https://doi.org/10.1000/ieee.tip",
                    "citations": 0,
                    "published": True,
                    "ccf_level": "B",
                    "oa_status": "closed",
                    "openalex_id": "W2999999999",
                }],
                "total": 1,
                "offset": offset,
                "total_is_estimate": True,
            })

    app = FastAPI()
    app.include_router(research_router, prefix="/api/v1")
    from app.research.router import get_research_service

    service = ServiceWithOpenAlex()

    async def override():
        return service

    app.dependency_overrides[get_research_service] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/v1/research/search", json={"query": "超分", "top_k": 5}) as resp:
            assert resp.status_code == 200
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
    assert '"source": "openalex"' in body
    assert '"oa_status": "closed"' in body
    assert '"openalex_id": "W2999999999"' in body
    assert '"ccf_level": "B"' in body


