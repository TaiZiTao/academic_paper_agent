"""按 paper_id 物理隔离的 FAISS + BM25 混合检索。"""

import shutil
from pathlib import Path

from app.paper.schemas import PaperChunkData, PaperSearchResult
from app.parser.models import DocumentChunk
from app.rag.embedding import BaseEmbedding
from app.rag.retriever import Retriever


class PaperRetriever:
    def __init__(
        self,
        embedding: BaseEmbedding,
        root_dir: str | Path,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> None:
        self.embedding = embedding
        self.root_dir = Path(root_dir).resolve()
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self._stores: dict[int, Retriever] = {}

    def _paper_dir(self, paper_id: int) -> Path:
        if paper_id <= 0:
            raise ValueError("paper_id 必须大于 0")
        path = (self.root_dir / str(paper_id)).resolve()
        if path.parent != self.root_dir:
            raise ValueError("非法的论文索引路径")
        return path

    def _new_store(self) -> Retriever:
        return Retriever(
            embedding=self.embedding,
            vector_weight=self.vector_weight,
            keyword_weight=self.keyword_weight,
        )

    @staticmethod
    def _to_document_chunk(chunk: PaperChunkData) -> DocumentChunk:
        return DocumentChunk(
            chunk_id=chunk.chunk_id,
            document_id=f"paper-{chunk.paper_id}",
            content=chunk.content,
            chunk_index=chunk.ordinal,
            metadata={
                **chunk.metadata,
                "paper_id": chunk.paper_id,
                "section": chunk.section,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
            },
        )

    @staticmethod
    def _from_document_chunk(chunk: DocumentChunk) -> PaperChunkData:
        metadata = chunk.metadata or {}
        return PaperChunkData(
            paper_id=int(metadata["paper_id"]),
            chunk_id=chunk.chunk_id,
            section=str(metadata.get("section", "")),
            page_start=int(metadata.get("page_start", 1)),
            page_end=int(metadata.get("page_end", metadata.get("page_start", 1))),
            ordinal=chunk.chunk_index,
            char_start=int(metadata.get("char_start", 0)),
            char_end=int(metadata.get("char_end", len(chunk.content))),
            content=chunk.content,
            metadata={
                key: value
                for key, value in metadata.items()
                if key not in {"paper_id", "section", "page_start", "page_end", "char_start", "char_end"}
            },
        )

    async def build(self, paper_id: int, chunks: list[PaperChunkData]) -> None:
        if not chunks:
            raise ValueError("论文没有可索引的文本分块")
        if any(chunk.paper_id != paper_id for chunk in chunks):
            raise ValueError("索引中包含其他论文的分块")

        paper_dir = self._paper_dir(paper_id)
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
        paper_dir.mkdir(parents=True, exist_ok=True)

        store = self._new_store()
        store.set_save_dir(str(paper_dir))
        await store.add_documents([self._to_document_chunk(chunk) for chunk in chunks])
        self._stores[paper_id] = store

    def _load(self, paper_id: int) -> Retriever | None:
        cached = self._stores.get(paper_id)
        if cached is not None:
            return cached
        paper_dir = self._paper_dir(paper_id)
        store = self._new_store()
        store.set_save_dir(str(paper_dir))
        if not store.load(str(paper_dir)):
            return None
        self._stores[paper_id] = store
        return store

    async def search(
        self,
        paper_id: int,
        query: str,
        k: int = 8,
        section: str | None = None,
    ) -> list[PaperSearchResult]:
        if not query.strip() or k <= 0:
            return []
        store = self._load(paper_id)
        if store is None:
            return []
        # 候选数上限: 最多取 k*4 个(过滤后够 k 个), 但不超索引总数;
        # 若用 max 会恒等于全库 chunk 数, 每次检索都变成全索引扫描
        candidate_k = min(k * 4, store.chunk_count)
        raw = await store.search(query, k=candidate_k)
        results: list[PaperSearchResult] = []
        for chunk, score in raw:
            paper_chunk = self._from_document_chunk(chunk)
            if paper_chunk.paper_id != paper_id:
                continue
            if section is not None and paper_chunk.section != section:
                continue
            results.append(PaperSearchResult(chunk=paper_chunk, score=score))
            if len(results) >= k:
                break
        return results

    def delete(self, paper_id: int) -> None:
        self._stores.pop(paper_id, None)
        paper_dir = self._paper_dir(paper_id)
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
