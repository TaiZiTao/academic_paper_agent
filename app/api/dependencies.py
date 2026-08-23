"""
API 依赖注入

负责创建和组装 Service 层依赖。
- Graph / Retriever / LLM：模块单例，所有请求共享
- MemoryManager：每次请求创建（短期记忆需隔离）
"""

from langgraph.graph import StateGraph

from app.config.settings import settings
from app.database import async_session
from app.graph.workflow import build_graph
from app.memory import LongTermMemory, MemoryManager, ShortTermMemory
from app.services.qa_service import QAService

# ============================================================
# 模块级单例（无状态或共享状态）
# ============================================================

_graph: StateGraph | None = None
_retriever: "Retriever | None" = None
_checkpointer = None
_paper_retriever = None
_paper_graph = None
_paper_service = None


def _get_checkpointer():
    """AsyncSqliteSaver 单例 — 跨进程持久化 LangGraph State"""
    global _checkpointer
    return _checkpointer


def _get_graph() -> StateGraph:
    """编译后的 Graph 单例 — 编译一次，永久复用"""
    global _graph
    if _graph is None:
        _graph = build_graph(checkpointer=_get_checkpointer())
    return _graph


def _get_retriever() -> "Retriever":
    """
    Retriever 单例 — FAISS/BM25 索引跨请求共享。

    延迟导入 faiss / numpy / Retriever，避免启动时触发 OpenBLAS。
    只在第一次 API 调用时初始化。
    """
    global _retriever
    if _retriever is None:
        from app.rag.embedding import OpenAIEmbedding  # noqa: F811
        from app.rag.retriever import Retriever as _R

        embedding = OpenAIEmbedding(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
        )
        _retriever = _R(
            embedding=embedding,
            vector_weight=settings.retrieval_vector_weight,
            keyword_weight=settings.retrieval_keyword_weight,
        )
        # 尝试从磁盘加载已有索引
        import os as _os
        idx_dir = _os.path.join(settings.data_dir, "index")
        _retriever.set_save_dir(idx_dir)
        if _retriever.load(idx_dir):
            from loguru import logger
            logger.info(f"从磁盘加载索引: {_retriever.chunk_count} 个片段")
    return _retriever


# ============================================================
# 请求级依赖（每次请求创建）
# ============================================================

def get_qa_service() -> QAService:
    """
    创建 QAService（每次请求调用）。

    每次创建 MemoryManager（隔离短记忆），共享 Graph + Retriever。
    """
    short_term = ShortTermMemory(max_messages=settings.short_memory_size)
    long_term = LongTermMemory(async_session)
    memory = MemoryManager(short_term, long_term)

    return QAService(memory=memory, graph=_get_graph())


def get_retriever() -> "Retriever":
    """获取全局 Retriever 单例"""
    return _get_retriever()


def get_qa_config() -> dict:
    """
    构建 Graph config（每次请求调用）。

    注入 LLM + Retriever 到 Graph Node。
    """
    return {
        "configurable": {
            "llm": _get_llm(),
            "retriever": _get_retriever(),
            "embedding": _get_retriever().vector_store.embedding,
            "retrieval_k": settings.retrieval_top_k,
        }
    }


def get_paper_service():
    """创建论文助手服务，共享 LLM、Embedding 与按论文隔离的索引。"""
    global _paper_retriever, _paper_graph, _paper_service
    if _paper_service is not None:
        return _paper_service
    if _paper_retriever is None:
        from pathlib import Path

        from app.paper.retriever import PaperRetriever

        _paper_retriever = PaperRetriever(
            embedding=_get_retriever().vector_store.embedding,
            root_dir=Path(settings.data_dir) / "papers" / "index",
            vector_weight=settings.retrieval_vector_weight,
            keyword_weight=settings.retrieval_keyword_weight,
        )
    if _paper_graph is None:
        from app.paper.graph import build_paper_graph

        _paper_graph = build_paper_graph()

    from pathlib import Path

    from app.paper.service import PaperService

    _paper_service = PaperService(
        session_factory=async_session,
        retriever=_paper_retriever,
        llm=_get_llm(),
        files_dir=Path(settings.data_dir) / "papers" / "files",
        graph=_paper_graph,
        chunk_size=settings.paper_chunk_size,
        chunk_overlap=settings.paper_chunk_overlap,
    )
    return _paper_service


# ============================================================
# LLM 单例（与 Retriever 同级）
# ============================================================

_llm = None


def _get_llm():
    """LLM 单例 — 使用 OpenAI 兼容客户端（DeepSeek / OpenAI 等）"""
    global _llm
    if _llm is None:
        from langchain_openai import ChatOpenAI

        _llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=0.7,
            timeout=120,
        )
    return _llm


# ============================================================
# Research Service 单例
# ============================================================

_research_service = None


def get_research_service():
    """创建文献检索 Agent 服务单例(共享 LLM / PaperService / 浏览器)。"""
    global _research_service
    if _research_service is not None:
        return _research_service
    import asyncio
    from pathlib import Path

    import httpx

    from app.research.agent import SearchAgent
    from app.research.browser import BrowserService
    from app.research.downloader import Downloader
    from app.research.searchers import ArxivSearcher, OpenAlexSearcher, SemanticScholarSearcher, _make_client
    from app.research.service import ImportService

    client = _make_client(settings.research_proxy, settings.research_search_timeout)
    searchers = [
        ArxivSearcher(client=client, timeout=settings.research_search_timeout, proxy=settings.research_proxy),
        OpenAlexSearcher(client=client, timeout=settings.research_search_timeout, proxy=settings.research_proxy),
        SemanticScholarSearcher(client=client, timeout=settings.research_search_timeout, proxy=settings.research_proxy),
    ]
    downloader_client_kwargs: dict = {"timeout": 60.0, "follow_redirects": True}
    if settings.research_proxy:
        downloader_client_kwargs["proxy"] = settings.research_proxy
    downloader = Downloader(
        client=httpx.AsyncClient(**downloader_client_kwargs),
        unpaywall_email=settings.unpaywall_email,
        browser=None,
        delay=settings.research_download_delay,
        enable_vpn_download=False,  # 付费墙论文改为手动下载引导; browser 仍注入供未来启用 L3
        free_pdf_lookup=True,  # L1.5 免费论文源兜底(arXiv/ACL/PMLR/NeurIPS/OpenReview/AAAI/CVF, 默认开启)
    )
    browser = BrowserService(
        profile_dir=Path(settings.data_dir) / "browser_profile",
        vpn_portal_url=settings.vpn_portal_url,
    )
    downloader.browser = browser

    class ResearchServiceFacade:
        """把 agent/downloader/browser/imports 组装成路由可用的一层。"""

        def __init__(self):
            self.agent = SearchAgent(
                llm=_get_llm(),
                searchers=searchers,
                proxy=settings.research_proxy,
                timeout=settings.research_search_timeout,
            )
            self.imports = ImportService(
                session_factory=async_session,
                downloader=downloader,
                paper_service=get_paper_service(),
                files_dir=Path(settings.data_dir) / "papers" / "files",
                delay=settings.research_download_delay,
            )
            self.browser = browser
            self._searcher_client = client  # searchers 共享的 client, aclose 时统一关闭

        async def search(self, query, top_k, offset, year_min=None, year_max=None, on_event=None, refresh=False):
            return await self.agent.run(
                query, top_k=top_k, offset=offset, year_min=year_min, year_max=year_max, on_event=on_event, refresh=refresh
            )

        async def create_imports(self, items):
            tasks = await self.imports.create_imports(items)
            # 后台触发队列, 不阻塞 202 响应; run_pending 内部有 asyncio.Lock 防重入
            asyncio.create_task(self.imports.run_pending())
            return tasks

        async def list_imports(self):
            return await self.imports.list_imports()

        async def get_import(self, import_id):
            return await self.imports.get_import(import_id)

        async def retry(self, import_id):
            task = await self.imports.retry(import_id)
            if task:
                asyncio.create_task(self.imports.run_pending())
            return task

        async def browser_status(self):
            return await self.browser.status()

        async def browser_login(self):
            return await self.browser.login()

        async def browser_verify(self):
            return await self.browser.verify()

        async def browser_close(self):
            await self.browser.close()
            return {"status": "closed"}

        async def aclose(self) -> None:
            """关闭 searchers / downloader / browser 依赖资源(lifespan 关闭时调用)。"""
            agent_close = getattr(self.agent, "aclose", None)
            if agent_close is not None:
                await agent_close()
            # searchers 注入共享 client(_owns_client=False, searcher.aclose 不关闭它), 在此统一关闭
            searcher_client = getattr(self, "_searcher_client", None)
            if searcher_client is not None and getattr(searcher_client, "aclose", None) is not None:
                await searcher_client.aclose()
            dl_client = getattr(self.imports.downloader, "client", None)
            if dl_client is not None and getattr(dl_client, "aclose", None) is not None:
                await dl_client.aclose()
            await self.browser.close()

    _research_service = ResearchServiceFacade()
    return _research_service
