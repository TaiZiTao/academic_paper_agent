"""
文档管理服务

负责文档摄入 + CRUD + 列表 + 搜索。
Phase 10：仅 ingest。
Phase 13C：扩展 CRUD 能力。

CLAUDE.md 约束：
- 不实现 Parser 细节
- 不直接操作 FAISS / BM25
"""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.sql import Select

from app.models.document import Document
from app.parser import parse_file
from app.rag.retriever import Retriever

try:
    from loguru import logger
except ImportError:
    logger = None  # type: ignore


class DocumentService:
    """文档管理服务"""

    def __init__(
        self,
        retriever: Retriever,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self.retriever = retriever
        self.session_factory = session_factory
        self._kb_id: int | None = None  # 当前摄入的 kb_id，由外部设置

    async def ingest_file(
        self,
        file_path: str | Path,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> tuple[int, list[str]]:
        """摄入单个文件：解析 → 清洗 → 切分 → 索引。返回 (count, chunk_ids)"""
        path = Path(file_path)
        self._log(f"开始摄入文档: {path.name}")

        chunks = await parse_file(str(path), chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            self._log(f"文档 {path.name} 无有效内容，跳过")
            return 0, []

        # 注入元数据 + 拼入 chunk 内容提升检索精度
        doc_name = getattr(self, "_original_filename", None) or path.name
        for c in chunks:
            c.metadata["kb_id"] = self._kb_id or 0
            c.metadata["doc_name"] = doc_name
            # 元数据前置：embedding 时文件名参与向量计算，同文件 chunk 相似度更高
            c.content = f"[文件:{doc_name}] [KB:{self._kb_id or 0}] {c.content}"

        await self.retriever.add_documents(chunks)
        chunk_ids = [c.chunk_id for c in chunks]
        self._log(f"文档 {path.name} 摄入完成, {len(chunks)} 个片段已索引")
        return len(chunks), chunk_ids

    async def ingest_files(
        self,
        file_paths: list[str],
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> tuple[int, list[str]]:
        """批量摄入"""
        total = 0
        all_ids: list[str] = []
        for fp in file_paths:
            cnt, ids = await self.ingest_file(fp, chunk_size=chunk_size, overlap=overlap)
            total += cnt
            all_ids.extend(ids)
        return total, all_ids

    # ============================================================
    # CRUD（Phase 13C）
    # ============================================================

    def _require_session(self):
        if self.session_factory is None:
            raise RuntimeError("DocumentService 未注入 session_factory，无法执行数据库操作")
        return self.session_factory

    async def create_record(
        self,
        kb_id: int,
        original_filename: str,
        stored_filename: str,
        extension: str,
        size: int,
        chunk_count: int,
        chunk_ids: list[str] | None = None,
        status: str = "completed",
    ) -> Document:
        """创建文档数据库记录"""
        import json
        sf = self._require_session()
        doc = Document(
            kb_id=kb_id,
            filename=stored_filename,
            original_filename=original_filename,
            extension=extension,
            size=size,
            chunk_count=chunk_count,
            chunk_ids=json.dumps(chunk_ids) if chunk_ids else None,
            status=status,
        )
        async with sf() as session:
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
        return doc

    async def list_documents(
        self,
        kb_id: int | None = None,
        search: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """文档列表（支持 KB 筛选 + 搜索 + 分页）"""
        sf = self._require_session()
        async with sf() as session:
            stmt: Select = select(Document)

            if kb_id is not None and kb_id > 0:
                stmt = stmt.where(Document.kb_id == kb_id)
            if search:
                stmt = stmt.where(Document.original_filename.contains(search))

            stmt = stmt.order_by(Document.created_at.desc())

            # 总数
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            # 分页
            offset = (page - 1) * page_size
            stmt = stmt.offset(offset).limit(page_size)
            result = await session.execute(stmt)
            items = result.scalars().all()

            return {"items": list(items), "total": total}

    async def get_document(self, doc_id: int) -> Document | None:
        """获取单个文档"""
        sf = self._require_session()
        async with sf() as session:
            return await session.get(Document, doc_id)

    async def delete_document(self, doc_id: int) -> bool:
        """删除文档数据库记录 + 精确移除索引（用 chunk_ids 定位）"""
        import json
        sf = self._require_session()
        async with sf() as session:
            doc = await session.get(Document, doc_id)
            if doc is None:
                return False
            filename = doc.filename
            chunk_ids_json = doc.chunk_ids
            await session.delete(doc)
            await session.commit()

        # 删上传文件
        from pathlib import Path
        from app.config.settings import settings
        file_path = Path(settings.data_dir) / "uploads" / filename
        if file_path.exists():
            file_path.unlink()

        # 精确移除索引
        if chunk_ids_json:
            try:
                ids = set(json.loads(chunk_ids_json))
                if ids:
                    self.retriever.remove_by_chunk_ids(ids)
                    self._log(f"已从索引移除 {len(ids)} 个片段")
            except (json.JSONDecodeError, TypeError):
                pass

        return True

    @property
    def indexed_count(self) -> int:
        """当前已索引的片段总数"""
        return self.retriever.chunk_count

    @staticmethod
    def _log(message: str) -> None:
        if logger:
            logger.info(message)
