"""论文助手 HTTP 与 SSE 接口测试。"""

import importlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakePaperService:
    def __init__(self, tmp_path):
        self.paper = SimpleNamespace(
            id=1,
            original_filename="paper.pdf",
            stored_filename="stored.pdf",
            title="Test Paper",
            authors_json='["Alice"]',
            abstract="Abstract",
            keywords_json="[]",
            language="en",
            page_count=2,
            status="ready",
            error_code="",
            error_message="",
            created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1),
        )
        self.pdf = tmp_path / "stored.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 test")
        self.processed = []
        self.deleted = []

    async def create_paper(self, filename, _content):
        self.paper.original_filename = filename
        self.paper.status = "uploaded"
        return self.paper

    async def process_paper(self, paper_id):
        self.processed.append(paper_id)

    async def list_papers(self, **_kwargs):
        return {"items": [self.paper], "total": 1}

    async def get_paper(self, paper_id):
        return self.paper if paper_id == 1 else None

    async def get_detail(self, paper_id):
        if paper_id != 1:
            return None
        return {
            "paper": self.paper,
            "sections": [],
            "artifacts": [],
            "messages": [],
        }

    async def progress_events(self, _paper_id):
        yield {"event": "progress", "stage": "reporting", "status": "reporting"}
        yield {"event": "done", "stage": "ready", "status": "ready"}

    async def run_task(self, *_args, **_kwargs):
        yield {"event": "token", "content": "answer"}
        yield {"event": "done", "artifact_id": 9, "content": "answer", "citations": []}

    def file_path(self, _paper):
        return self.pdf

    async def delete_paper(self, paper_id):
        self.deleted.append(paper_id)
        return paper_id == 1


@pytest.fixture
def paper_client(tmp_path):
    try:
        module = importlib.import_module("app.paper.router")
    except ModuleNotFoundError:
        pytest.fail("app.paper.router 尚未实现")
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    service = FakePaperService(tmp_path)
    app.dependency_overrides[module.get_paper_service] = lambda: service
    return TestClient(app), service


def test_upload_rejects_non_pdf(paper_client):
    client, _ = paper_client
    response = client.post(
        "/api/v1/papers",
        files={"file": ("paper.txt", b"not pdf", "text/plain")},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_upload_starts_background_processing(paper_client):
    client, service = paper_client
    response = client.post(
        "/api/v1/papers",
        files={"file": ("paper.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 202
    assert response.json()["id"] == 1
    assert service.processed == [1]


def test_list_and_detail_restore_saved_state(paper_client):
    client, _ = paper_client
    listing = client.get("/api/v1/papers")
    detail = client.get("/api/v1/papers/1")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["paper"]["title"] == "Test Paper"


def test_pdf_endpoint_is_inline_and_task_endpoint_streams_sse(paper_client):
    client, _ = paper_client
    pdf = client.get("/api/v1/papers/1/pdf")
    stream = client.post(
        "/api/v1/papers/1/tasks/stream",
        json={"task_type": "qa", "input_text": "方法是什么？", "session_id": "s1"},
    )
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert "inline" in pdf.headers["content-disposition"]
    assert stream.status_code == 200
    assert "event: token" in stream.text
    assert "event: done" in stream.text


def test_delete_returns_404_for_missing_paper(paper_client):
    client, service = paper_client
    response = client.delete("/api/v1/papers/99")
    assert response.status_code == 404
    assert service.deleted == [99]


def test_paper_dependency_reuses_service_for_background_progress(monkeypatch, tmp_path):
    dependencies = importlib.import_module("app.api.dependencies")
    monkeypatch.setattr(dependencies, "_paper_service", None, raising=False)
    monkeypatch.setattr(dependencies, "_paper_retriever", object())
    monkeypatch.setattr(dependencies, "_paper_graph", object())
    monkeypatch.setattr(dependencies, "_get_llm", lambda: object())
    monkeypatch.setattr(dependencies.settings, "data_dir", str(tmp_path))

    first = dependencies.get_paper_service()
    second = dependencies.get_paper_service()

    assert first is second
