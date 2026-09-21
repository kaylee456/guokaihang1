"""FastAPI 入口 —— M0 框架地基。

暴露：
  GET  /health     进程 + Provider + 配置就绪度自检
  GET  /llm/ping    向 vLLM 发最小 chat，验证 LLM 连通
  POST /llm/echo    非流式 chat 直通（联调用）
  POST /llm/stream  SSE 流式（前端进度用）

后续里程碑在此挂载 /analyze/q1|q2|q3 等业务路由。
"""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.capabilities.registry import get_provider
from backend.config import get_settings
from backend.llm.client import LLMClient, LLMError

app = FastAPI(title="国开行智能客户分析 Agent", version="0.1.0-M0")


def _client() -> LLMClient:
    return LLMClient(get_settings())


# ---------- 健康检查 ----------
@app.get("/health")
async def health() -> dict:
    """不外呼 LLM，仅报告本地就绪度，供探活。"""
    s = get_settings()
    provider = get_provider()
    return {
        "status": "ok",
        "version": app.version,
        "provider": await provider.healthcheck(),
        "llm": {"base_url": s.llm_base_url, "model": s.llm_model},
    }


@app.get("/llm/ping")
async def llm_ping() -> dict:
    """真实打一次 LLM，验证连通与模型可用。"""
    try:
        return await _client().ping()
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}


# ---------- 联调端点 ----------
class ChatRequest(BaseModel):
    prompt: str = Field(..., description="用户输入")
    max_tokens: int | None = None
    temperature: float | None = None
    enable_thinking: bool = False


@app.post("/llm/echo")
async def llm_echo(req: ChatRequest) -> dict:
    try:
        content = await _client().chat(
            [{"role": "user", "content": req.prompt}],
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            enable_thinking=req.enable_thinking,
        )
        return {"ok": True, "content": content}
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/llm/stream")
async def llm_stream(req: ChatRequest) -> StreamingResponse:
    """SSE 流式转发 LLM 增量输出。"""
    client = _client()

    async def gen() -> AsyncIterator[str]:
        try:
            async for delta in client.stream(
                [{"role": "user", "content": req.prompt}],
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                enable_thinking=req.enable_thinking,
            ):
                yield f"data: {delta}\n\n"
        except LLMError as exc:
            yield f"event: error\ndata: {exc}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 财务信息 Skill（C2）----------
class FinancialAskRequest(BaseModel):
    question: str = Field(..., description="用户的财务问题（自然语言）")


@app.post("/skill/financial/ask")
async def financial_ask(req: FinancialAskRequest) -> dict:
    """财务问答主入口：自然语言问题 → 中文回答 + 结构化指标数据。"""
    from backend.skills.financial_qa import FinancialQA

    try:
        return await FinancialQA().ask(req.question)
    except Exception as exc:  # noqa: BLE001 — 兜底返回错误，避免 500 裸抛
        return {"answer": f"查询出错：{exc}", "data": [], "error": str(exc)}


@app.get("/skill/financial/indicators")
async def financial_indicators(
    customer: str,
    year: int | None = None,
    cycle: str | None = None,
    period: int | None = None,
) -> dict:
    """免 LLM 直取指标（联调/前端直用）。缺周期取最新可用期。"""
    from backend.skills.financial_data import FinancialData

    fd = FinancialData()
    cands = fd.resolve_customer(customer)
    if not cands:
        return {"ok": False, "msg": "未找到客户", "data": []}
    if len(cands) > 1:
        return {"ok": False, "msg": "多个候选，请确认", "candidates": cands, "data": []}
    cust = cands[0]
    cid = cust["id"]
    if not (year and cycle and period):
        latest = fd.latest_period(cid)
        if not latest:
            return {"ok": False, "msg": "该客户无可用周期", "resolved": cust, "data": []}
        year, cycle, period = latest["report_year"], latest["cycle_type"], latest["period_no"]
    rows = fd.fetch_indicators(cid, year, cycle, period)
    return {
        "ok": True,
        "resolved": cust,
        "period": {"year": year, "cycle": cycle, "period_no": period},
        "data": rows,
    }


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("backend.app:app", host=s.app_host, port=s.app_port, reload=False)


if __name__ == "__main__":
    main()
