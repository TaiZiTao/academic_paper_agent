"""全库聚合检索器: 复用单篇索引, 综述型问题按篇采样保证每篇都有发言权。"""

import asyncio
import logging

from app.paper.retriever import PaperRetriever
from app.paper.schemas import PaperChunkData

logger = logging.getLogger(__name__)


def build_evidence_plan(paper_count: int, max_chars: int = 20000, avg_chunk_chars: int = 800) -> int:
    """按候选论文数计算每篇取几个片段: 论文少取3个, 论文多自动降配, 每篇保底1个。

    注: 篇数 >25 时"每篇保底1个"会让总量超过 20000 字预算(设计固有取舍,
    保证全覆盖优先)。
    """
    if paper_count <= 0:
        return 0
    return max(1, min(3, max_chars // max(1, paper_count * avg_chunk_chars)))


class AggregateRetriever:
    """对候选论文列表逐篇采样 top-k, 合并证据。"""

    def __init__(self, retriever: PaperRetriever):
        self.retriever = retriever

    async def sample_papers(
        self,
        paper_ids: list[int],
        query: str,
        per_paper: int | None = None,
    ) -> list[PaperChunkData]:
        if not paper_ids:
            return []
        per = build_evidence_plan(len(paper_ids)) if per_paper is None else per_paper
        if per < 1:
            per = 1
        sem = asyncio.Semaphore(8)

        async def _sample_one(pid: int) -> list[PaperChunkData]:
            async with sem:
                try:
                    hits = await self.retriever.search(pid, query, k=per)
                    return [h.chunk for h in hits]
                except Exception as exc:
                    logger.warning("采样论文 %s 失败: %s", pid, exc)
                    return []

        grouped = await asyncio.gather(*(_sample_one(pid) for pid in paper_ids))
        return [chunk for chunks in grouped for chunk in chunks]
