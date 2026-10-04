"""HTTP 层: 并发受限的排料服务, 求解不阻塞健康检查。"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .schemas import OptimizeRequest, OptimizeResponse
from .service import run_optimize

MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_SOLVES", "2"))

app = FastAPI(title="Profile Cutting Optimizer", version="1.0.0")
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/optimize", response_model=OptimizeResponse)
async def optimize(req: OptimizeRequest) -> OptimizeResponse:
    # 超出并发容量时立即拒绝, 不排队占用连接
    if _semaphore.locked():
        raise HTTPException(status_code=503, detail="solver busy, try again later")
    async with _semaphore:
        try:
            # 在线程池中执行 CP-SAT, 事件循环保持响应
            return await asyncio.to_thread(run_optimize, req)
        except Exception as exc:  # 容量随 async with 释放
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled(request, exc):  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": str(exc)})
