"""
API 层测试

使用 FastAPI TestClient + dependency_overrides 注入 Mock Service。
不调用真实 OpenAI / FAISS / SQLite。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_qa_service
from app.services.qa_service import QAResponse
from app.utils.exceptions import AppException
from main import app


# ============================================================
# Fake QAService
# ============================================================

class FakeQAService:
    """
    模拟 QAService，不依赖 Memory / Graph / Database。
    可配置返回值或模拟失败。
    """

    def __init__(self, response=None, should_fail=False):
        self.response = response or QAResponse(
            answer="Mock answer for testing",
            citations=["ref1.md"],
            session_id="",
            intent="knowledge_qa",
        )
        self.should_fail = should_fail
        self.ask_calls: list[dict] = []

    async def ask(self, session_id: str, query: str, config=None, kb_id=0) -> QAResponse:
        self.ask_calls.append({"session_id": session_id, "query": query})
        if self.should_fail:
            raise AppException("模拟的业务异常")
        resp = self.response
        resp.session_id = session_id
        return resp


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def fake_service():
    """创建一个默认的 FakeQAService"""
    return FakeQAService()


@pytest.fixture
def client(fake_service):
    """TestClient，依赖已替换为 fake service"""
    app.dependency_overrides[get_qa_service] = lambda: fake_service
    yield TestClient(app)
    # 清理 override 避免影响其他测试
    app.dependency_overrides.clear()


# ============================================================
# Schema Validation Tests
# ============================================================

class TestAskRequestValidation:
    """请求参数校验测试"""

    def test_valid_request(self, client):
        """正常的请求参数通过校验"""
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "什么是GraphRAG？",
        })
        assert resp.status_code == 200

    def test_missing_session_id(self, client):
        """缺少 session_id 返回 422"""
        resp = client.post("/api/v1/chat", json={
            "query": "hello",
        })
        assert resp.status_code == 422

    def test_empty_session_id(self, client):
        """空 session_id 返回 422"""
        resp = client.post("/api/v1/chat", json={
            "session_id": "",
            "query": "hello",
        })
        assert resp.status_code == 422

    def test_missing_query(self, client):
        """缺少 query 返回 422"""
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
        })
        assert resp.status_code == 422

    def test_empty_query(self, client):
        """空 query 返回 422"""
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "",
        })
        assert resp.status_code == 422

    def test_query_exceeds_max_length(self, client):
        """query 超过 5000 字符返回 422"""
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "x" * 5001,
        })
        assert resp.status_code == 422


# ============================================================
# Success Response Tests
# ============================================================

class TestChatSuccess:
    """正常问答响应测试"""

    def test_returns_200(self, client):
        resp = client.post("/api/v1/chat", json={
            "session_id": "sess_ok",
            "query": "测试问题",
        })
        assert resp.status_code == 200

    def test_response_contains_answer(self, client):
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "test",
        })
        data = resp.json()
        assert "answer" in data
        assert len(data["answer"]) > 0

    def test_response_contains_session_id(self, client):
        resp = client.post("/api/v1/chat", json={
            "session_id": "my_session",
            "query": "test",
        })
        data = resp.json()
        assert data["session_id"] == "my_session"

    def test_response_contains_citations(self, client):
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "test",
        })
        data = resp.json()
        assert "citations" in data

    def test_response_contains_intent(self, client):
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "test",
        })
        data = resp.json()
        assert "intent" in data


# ============================================================
# Service Call Tests
# ============================================================

class TestServiceIntegration:
    """Service 调用验证"""

    def test_service_receives_session_id(self, fake_service):
        """验证 Service 收到了正确的 session_id"""
        app.dependency_overrides[get_qa_service] = lambda: fake_service
        client = TestClient(app)
        client.post("/api/v1/chat", json={
            "session_id": "target_session",
            "query": "hello",
        })
        app.dependency_overrides.clear()

        assert len(fake_service.ask_calls) == 1
        assert fake_service.ask_calls[0]["session_id"] == "target_session"

    def test_service_receives_query(self, fake_service):
        """验证 Service 收到了正确的 query"""
        app.dependency_overrides[get_qa_service] = lambda: fake_service
        client = TestClient(app)
        client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "什么是企业知识库？",
        })
        app.dependency_overrides.clear()

        assert len(fake_service.ask_calls) == 1
        assert fake_service.ask_calls[0]["query"] == "什么是企业知识库？"


# ============================================================
# Error Handling Tests
# ============================================================

class TestErrorHandling:
    """异常处理测试"""

    def test_app_exception_returns_500(self):
        """Service 抛出 AppException → HTTP 500"""
        fake = FakeQAService(should_fail=True)
        app.dependency_overrides[get_qa_service] = lambda: fake
        client = TestClient(app)

        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "crash",
        })
        app.dependency_overrides.clear()

        assert resp.status_code == 500

    def test_app_exception_response_contains_detail(self):
        """错误响应包含 detail 字段"""
        fake = FakeQAService(should_fail=True)
        app.dependency_overrides[get_qa_service] = lambda: fake
        client = TestClient(app)

        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "crash",
        })
        app.dependency_overrides.clear()

        data = resp.json()
        assert "detail" in data


# ============================================================
# Health Check Tests
# ============================================================

class TestHealthCheck:
    """健康检查测试（验证 main.py 的 /health 路由不被影响）"""

    def test_health_still_works(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ============================================================
# Router Prefix Tests
# ============================================================

class TestRouterPrefix:
    """路由前缀测试"""

    def test_chat_at_v1_prefix(self, client):
        """验证 /api/v1/chat 可达"""
        resp = client.post("/api/v1/chat", json={
            "session_id": "s1",
            "query": "test",
        })
        assert resp.status_code == 200

    def test_chat_without_prefix_not_found(self, client):
        """不带 /api/v1 前缀的路径返回 404"""
        resp = client.post("/chat", json={
            "session_id": "s1",
            "query": "test",
        })
        assert resp.status_code == 404
