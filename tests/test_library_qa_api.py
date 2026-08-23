"""全库问答 API 路由测试。

- 路由注册冒烟测试(空 text 也应走业务校验, 而非 404)
- 空 input_text 返回 400
- /qa/history 带 session_id 返回 200(用独立 app + 依赖替换, 避免真实 DB/LLM)
"""

import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_library_qa_route_registered():
    resp = client.post("/api/v1/papers/qa/stream", json={})
    assert resp.status_code != 404


def test_library_qa_empty_text_rejected():
    resp = client.post("/api/v1/papers/qa/stream", json={"input_text": ""})
    assert resp.status_code == 400


class FakeLibraryService:
    async def get_library_history(self, session_id: str, limit: int = 50):
        return [
            {
                "role": "user",
                "content": "问题一",
                "citations": [],
                "timestamp": "2026-01-01T00:00:00",
            },
            {
                "role": "assistant",
                "content": "回答一",
                "citations": [{"paper_id": 1, "paper_title": "P1", "page": 3}],
                "timestamp": "2026-01-01T00:00:01",
            },
        ]


def test_library_qa_history_returns_200_with_session_id():
    module = importlib.import_module("app.paper.router")
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module.get_paper_service] = lambda: FakeLibraryService()
    client = TestClient(app)

    resp = client.get("/api/v1/papers/qa/history", params={"session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s1"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["content"] == "问题一"
    assert body["messages"][1]["citations"][0]["paper_id"] == 1
