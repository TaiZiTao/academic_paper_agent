"""文献检索 Agent REST 与 SSE 路由。"""

import asyncio
import contextlib
import json
from collections import deque

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.research.schemas import ImportItem


router = APIRouter(prefix="/research", tags=["文献检索"])

# 单次检索总超时: 防止后台任务悬挂
SEARCH_TIMEOUT = 120.0


class SearchRequest(BaseModel):
    """检索请求体: query/top_k 由 Pydantic 校验(FastAPI 自动 422)。"""

    query: str = Field(min_length=1)
    top_k: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)  # I2: 钳制合理上限, 防越界翻页
    year_min: int | None = Field(default=None, ge=1991, le=2030)  # 提交年份下界(含)
    year_max: int | None = Field(default=None, ge=1991, le=2030)  # 提交年份上界(含)
    # 是否强制重新检索(搜索/搜索新结果按钮传 true; 翻页不传 → 走结果集缓存切片)
    refresh: bool = Field(default=False)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("检索词不能为空")
        return value


class ImportItemsRequest(BaseModel):
    """导入请求体: 至少一项, 每项按 ImportItem 校验(非法项 → 422 而非 500)。"""

    items: list[ImportItem] = Field(min_length=1)


def get_research_service():
    """占位依赖: 由 app.api.dependencies 提供真实实现(避免循环 import)。"""
    from app.api.dependencies import get_research_service as _real
    return _real()


def _sse(event: dict) -> str:
    event_type = event.get("event", "message")
    payload = {key: value for key, value in event.items() if key != "event"}
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/search")
async def search(request: SearchRequest, service=Depends(get_research_service)):
    query = request.query
    top_k = request.top_k
    offset = request.offset
    year_min = request.year_min
    year_max = request.year_max
    refresh = request.refresh

    events: deque = deque()

    async def _run():
        try:
            await asyncio.wait_for(
                service.search(
                    query,
                    top_k=top_k,
                    offset=offset,
                    year_min=year_min,
                    year_max=year_max,
                    on_event=events.append,
                    refresh=refresh,
                ),
                timeout=SEARCH_TIMEOUT,
            )
            events.append({"event": "done"})
        except asyncio.TimeoutError:
            events.append({"event": "error", "message": f"检索超过 {SEARCH_TIMEOUT:.0f}s 超时"})
            events.append({"event": "done"})
        except Exception as exc:
            events.append({"event": "error", "message": str(exc)})
            events.append({"event": "done"})

    task = asyncio.create_task(_run())

    async def stream():
        try:
            while True:
                if events:
                    yield _sse(events.popleft())
                    if not events and task.done():
                        break
                elif task.done():
                    if not events:
                        yield _sse({"event": "done"})
                    break
                else:
                    await asyncio.sleep(0.05)
        finally:
            # 客户端断开/生成器提前退出: 取消后台检索任务, 防泄漏
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/imports", status_code=202)
async def create_imports(request: ImportItemsRequest, service=Depends(get_research_service)):
    tasks = await service.create_imports(request.items)
    return {"items": tasks}


@router.get("/imports")
async def list_imports(service=Depends(get_research_service)):
    return {"items": await service.list_imports()}


@router.get("/imports/{import_id}")
async def get_import(import_id: int, service=Depends(get_research_service)):
    task = await service.get_import(import_id)
    if task is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return task


@router.post("/imports/{import_id}/retry")
async def retry_import(import_id: int, service=Depends(get_research_service)):
    task = await service.retry(import_id)
    if task is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return task


@router.get("/browser/status")
async def browser_status(service=Depends(get_research_service)):
    return await service.browser_status()


@router.post("/browser/login")
async def browser_login(service=Depends(get_research_service)):
    return await service.browser_login()


@router.post("/browser/verify")
async def browser_verify(service=Depends(get_research_service)):
    return await service.browser_verify()


@router.post("/browser/close")
async def browser_close(service=Depends(get_research_service)):
    return await service.browser_close()
