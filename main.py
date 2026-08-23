"""
论文智答 — 应用入口

职责：
- 创建 FastAPI 实例
- 管理应用生命周期（启动 / 关闭）
- 注册全局路由（健康检查）

架构约束：
- main.py 不包含业务逻辑
- API 路由在 app/api/ 中定义，通过 Router 注册
- 配置通过 app/config/settings.py 统一管理
- 日志通过 app/utils/logger.py 统一初始化
"""

# Windows 上 faiss-cpu 的 OpenBLAS 内存分配修复
# 必须在 import faiss 之前设置（dependencies.py 会间接触发 faiss 导入）
import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router as api_router
from app.config.settings import settings
from app.paper.router import router as paper_router
from app.research.router import router as research_router
from app.utils.logger import setup_logger


# ============================================================
# 应用生命周期管理
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan 事件处理器。

    启动时：初始化日志，打印启动信息。
    关闭时：执行清理操作（后续 Phase 扩展：关闭 DB 连接、释放模型等）。
    """
    # --- 启动事件 ---
    setup_logger(level=settings.log_level, log_dir=settings.log_dir)
    from loguru import logger

    logger.info(f"{'=' * 50}")

    from app.database import init_db
    await init_db()
    logger.info(f"  {settings.app_name} v{settings.app_version}")
    logger.info(f"  Environment: {'DEBUG' if settings.debug else 'PRODUCTION'}")
    logger.info(f"  Listening on: http://{settings.host}:{settings.port}")
    logger.info(f"{'=' * 50}")

    # 触发 Retriever 初始化
    from app.api.dependencies import get_retriever
    get_retriever()

    # 初始化 AsyncSqliteSaver checkpointer
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from app.api import dependencies as _deps
    conn = await aiosqlite.connect("data/graphrag.db")
    _deps._checkpointer = AsyncSqliteSaver(conn)
    # 确保 Graph 在 checkpointer 之后编译
    _deps._get_graph()
    logger.info("Checkpointer (AsyncSqliteSaver) 已就绪")

    yield  # 应用运行期间

    # --- 关闭事件 ---
    logger.info("Application shutting down...")
    try:
        from app.api import dependencies as _deps
        if _deps._research_service is not None:
            await _deps._research_service.aclose()
            logger.info("Research service 已关闭")
    except Exception:
        logger.exception("关闭 Research service 时出错")


# ============================================================
# FastAPI 实例
# ============================================================

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# --- CORS 中间件（允许前端跨域访问） ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 注册业务路由 ---
app.include_router(api_router, prefix="/api/v1")
app.include_router(paper_router, prefix="/api/v1")
app.include_router(research_router, prefix="/api/v1")


# ============================================================
# 全局路由 — 健康检查
# ============================================================

@app.get("/health", tags=["System"])
async def health_check():
    """
    健康检查接口。

    用于验证应用是否正常启动。
    返回应用名称、版本和运行状态。
    """
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "version": settings.app_version,
    }


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
