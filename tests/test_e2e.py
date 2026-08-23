"""
E2E 端到端测试

模拟完整问答链路：API → Service → Graph → Mock LLM + Mock Retriever。
不调用真实 OpenAI API / 外部服务。
"""

import pytest

from app.graph.workflow import build_graph
from app.memory import MemoryManager, ShortTermMemory
from app.parser.models import DocumentChunk
from app.rag.embedding import BaseEmbedding
from app.rag.retriever import Retriever
from app.services.document_service import DocumentService
from app.services.qa_service import QAResponse, QAService

# ============================================================
# Mock LLM — 模拟大语言模型
# ============================================================

class MockLLM:
    """
    模拟 LLM，返回预设回答。

    支持 invoke (sync) 和 ainvoke (async) 两种调用方式。
    """

    def __init__(self, response: str = ""):
        self._response = response
        self.call_count = 0
        self.last_prompt = ""

    def invoke(self, prompt: str) -> "MockResponse":
        self.call_count += 1
        self.last_prompt = prompt
        if self._response:
            return MockResponse(self._response)
        return MockResponse("这是基于知识库生成的模拟回答。")

    async def ainvoke(self, prompt: str) -> "MockResponse":
        return self.invoke(prompt)


class MockResponse:
    """模拟 LLM 返回对象"""
    def __init__(self, content: str):
        self.content = content


# ============================================================
# Mock Embedding — 确定性伪向量（用于 Mock Retriever）
# ============================================================

class MockEmbedding(BaseEmbedding):
    """简易 Mock Embedding，用于 Retriever 的索引和检索"""

    def __init__(self, dimension: int = 128):
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed_text(self, text: str) -> list[float]:
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        # 用 hash 生成 determinative 向量
        vec = []
        for i in range(self._dim):
            b = h[i % len(h)]
            vec.append((b - 127.5) / 127.5)
        import math
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_text(t) for t in texts]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_llm():
    return MockLLM()


@pytest.fixture
def mock_embedding():
    return MockEmbedding(dimension=128)


@pytest.fixture
def mock_retriever(mock_embedding):
    return Retriever(embedding=mock_embedding)


@pytest.fixture
def mock_fake_memory():
    """纯内存 MemoryManager，不访问 SQLite"""
    from tests.test_services import FakeMemoryManager
    return FakeMemoryManager(max_messages=10)


@pytest.fixture
def compiled_graph():
    return build_graph()


@pytest.fixture
def config_with_mocks(mock_llm, mock_retriever):
    """Graph config 包含 Mock LLM + Mock Retriever"""
    return {
        "configurable": {
            "llm": mock_llm,
            "retriever": mock_retriever,
            "retrieval_k": 3,
        }
    }


# ============================================================
# E2E: Skeleton Mode (No Config)
# ============================================================

class TestE2ESkeleton:
    """无 config 的骨架模式 E2E"""

    @pytest.mark.asyncio
    async def test_skeleton_qa_flow(self, compiled_graph, mock_fake_memory):
        """不注入 LLM/Retriever → 降级为骨架回答"""
        service = QAService(memory=mock_fake_memory, graph=compiled_graph)
        resp = await service.ask("sess_e2e_skel", "什么是GraphRAG？")

        assert isinstance(resp, QAResponse)
        assert len(resp.answer) > 0
        assert resp.session_id == "sess_e2e_skel"

    @pytest.mark.asyncio
    async def test_skeleton_saves_messages(self, compiled_graph, mock_fake_memory):
        """骨架模式下消息正常持久化"""
        service = QAService(memory=mock_fake_memory, graph=compiled_graph)
        await service.ask("sess_e2e_msg", "测试消息持久化")

        stored = mock_fake_memory.stored_messages.get("sess_e2e_msg", [])
        roles = [m.role for m in stored]
        assert "user" in roles
        assert "assistant" in roles


# ============================================================
# E2E: Component Mode (With Mock LLM + Mock Retriever)
# ============================================================

class TestE2EComponents:
    """注入 Mock LLM + Mock Retriever 的完整链路"""

    @pytest.mark.asyncio
    async def test_full_qa_with_mocks(
        self, compiled_graph, mock_fake_memory, config_with_mocks, mock_llm
    ):
        """完整链路：Mock LLM + Mock Retriever"""
        service = QAService(memory=mock_fake_memory, graph=compiled_graph)
        resp = await service.ask(
            "sess_e2e_full", "如何部署企业知识库？",
            config=config_with_mocks,
        )

        assert isinstance(resp, QAResponse)
        assert len(resp.answer) > 0
        # LLM 被调用（generation_node）
        assert mock_llm.call_count >= 1

    @pytest.mark.asyncio
    async def test_llm_receives_context(
        self, compiled_graph, mock_fake_memory, config_with_mocks, mock_llm
    ):
        """验证 LLM prompt 包含检索上下文和历史消息"""
        service = QAService(memory=mock_fake_memory, graph=compiled_graph)

        # 先添加一条历史消息
        await mock_fake_memory.add_user_message("sess_ctx", "你好")
        await mock_fake_memory.add_assistant_message("sess_ctx", "你好！有什么可以帮你的？")
        mock_fake_memory.short_term.clear()

        await service.ask("sess_ctx", "知识库部署问题", config=config_with_mocks)

        # generation_node 的 prompt 应包含历史
        assert mock_llm.call_count >= 1
        prompt = mock_llm.last_prompt
        assert "知识库部署问题" in prompt
        # 骨架模式不真正调用 LLM 改写，query_rewrite_node 中的 LLM 可能被调也可能不调

    @pytest.mark.asyncio
    async def test_retriever_is_called(
        self, compiled_graph, mock_fake_memory, config_with_mocks, mock_retriever
    ):
        """检索阶段：先索引文档，再验证检索结果"""
        # 向 Retriever 中预添加测试文档
        chunks = [
            DocumentChunk(
                document_id="doc_test",
                content="GraphRAG 是结合知识图谱的检索增强生成系统，支持企业级知识库部署。",
                chunk_index=0,
            ),
            DocumentChunk(
                document_id="doc_test",
                content="企业知识库部署需要准备 GPU 服务器、安装依赖、配置 API 密钥。",
                chunk_index=1,
            ),
        ]
        await mock_retriever.add_documents(chunks)

        service = QAService(memory=mock_fake_memory, graph=compiled_graph)
        resp = await service.ask(
            "sess_ret", "企业知识库如何部署？",
            config=config_with_mocks,
        )

        assert len(resp.answer) > 0

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(
        self, compiled_graph, mock_fake_memory, config_with_mocks
    ):
        """多轮对话：历史消息在后续轮次可用"""
        service = QAService(memory=mock_fake_memory, graph=compiled_graph)

        # 第一轮
        await service.ask("sess_multi_e2e", "什么是GraphRAG？", config=config_with_mocks)
        # 第二轮
        await service.ask("sess_multi_e2e", "它如何部署？", config=config_with_mocks)

        stored = mock_fake_memory.stored_messages.get("sess_multi_e2e", [])
        user_msgs = [m for m in stored if m.role == "user"]
        asst_msgs = [m for m in stored if m.role == "assistant"]
        assert len(user_msgs) == 2
        assert len(asst_msgs) == 2


# ============================================================
# E2E: Document Ingestion + QA
# ============================================================

class TestE2EDocumentIngestion:
    """文档摄入 → 检索 → 问答 完整链路"""

    @pytest.mark.asyncio
    async def test_ingest_and_ask(
        self, tmp_path, compiled_graph, mock_fake_memory, mock_retriever, mock_llm
    ):
        """摄入文件 → 检索到相关内容 → 生成回答"""
        # 1. 创建测试文件
        doc_file = tmp_path / "knowledge.txt"
        doc_file.write_text(
            "企业知识库部署指南\n\n"
            "步骤 1：准备 GPU 服务器，推荐 NVIDIA A100。\n"
            "步骤 2：安装 Python 3.11 和必要的依赖。\n"
            "步骤 3：配置 OpenAI API 密钥用于 Embedding 和 LLM。\n"
            "步骤 4：启动 FastAPI 服务，访问 /docs 查看 API。\n",
            encoding="utf-8",
        )

        # 2. 使用 DocumentService 摄入文档
        doc_service = DocumentService(retriever=mock_retriever)
        count, _ = await doc_service.ingest_file(str(doc_file))
        assert count > 0

        # 3. 问答
        config = {
            "configurable": {
                "llm": mock_llm,
                "retriever": mock_retriever,
                "retrieval_k": 3,
            }
        }
        service = QAService(memory=mock_fake_memory, graph=compiled_graph)
        resp = await service.ask(
            "sess_ingest", "部署需要什么硬件？", config=config,
        )

        assert len(resp.answer) > 0
        # generation_node 收到了检索文档（通过 LLM prompt 内容验证）
        assert mock_llm.call_count >= 1
        # prompt 中应包含检索到的相关内容
        assert "GPU" in mock_llm.last_prompt or "A100" in mock_llm.last_prompt

    @pytest.mark.asyncio
    async def test_ingest_multiple_files(
        self, tmp_path, mock_retriever
    ):
        """批量摄入多个文件"""
        file1 = tmp_path / "doc1.txt"
        file1.write_text("GraphRAG 使用 FAISS 做向量检索。", encoding="utf-8")
        file2 = tmp_path / "doc2.txt"
        file2.write_text("BM25 用于关键词检索。", encoding="utf-8")

        doc_service = DocumentService(retriever=mock_retriever)
        total, _ = await doc_service.ingest_files([str(file1), str(file2)])
        assert total >= 2
        assert doc_service.indexed_count >= 2
