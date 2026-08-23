"""
健康检查接口测试

使用 FastAPI TestClient 验证 /health 接口。
"""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_returns_200():
    """验证健康检查返回 HTTP 200"""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_status():
    """验证健康检查返回 status=ok"""
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"


def test_health_returns_app_info():
    """验证健康检查返回应用名称和版本号"""
    response = client.get("/health")
    data = response.json()
    assert "app_name" in data
    assert "version" in data
    assert len(data["app_name"]) > 0
    assert len(data["version"]) > 0
