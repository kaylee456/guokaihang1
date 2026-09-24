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


@app.post("/skill/financial/query")
async def financial_query(req: FinancialAskRequest) -> dict:
    """财务问答主入口：自然语言问题 → 中文回答 + 结构化指标数据。"""
    from backend.common import response as resp
    from backend.skills.financial_qa import FinancialQA

    try:
        return await FinancialQA().ask(req.question)
    except Exception as exc:  # noqa: BLE001 — 兜底返回错误，避免 500 裸抛
        return resp.error(exc)


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


# ---------- 客户业务信息查询 Skill（6 张 RPT 业务统计表）----------
class CustomerBusinessAskRequest(BaseModel):
    query: str = Field(..., description="用户的自然语言问题，如「查西安正和科技的贷款和存款」")


@app.post("/skill/customer_business/query")
async def customer_business_query(req: CustomerBusinessAskRequest) -> dict:
    """客户业务信息查询主入口：自然语言问题 → 6 张业务表结构化明细（含来源表标注）。

    处理链：LLM 抽参（客户名/编号、机构、想查的业务表）→ 确定性固定 SQL 取数。
    取数不经 LLM，杜绝编造。status 取值：
      ok / not_found / multiple / no_business_data / empty_input / error
    """
    from backend.skills.customer_business_qa import CustomerBusinessQA

    try:
        return await CustomerBusinessQA().ask(req.query)
    except Exception as exc:  # noqa: BLE001 — 兜底避免 500 裸抛
        return {"status": "error", "message": f"查询出错：{exc}", "data": [], "error": str(exc)}


class CustomerBusinessFetchRequest(BaseModel):
    name_or_id: str = Field(..., description="客户名称 CST_NM 或客户编号 CST_ID")
    org_id: str | None = Field(None, description="可选，机构编号（客户所属分行），多命中时定位")
    tables: list[str] | None = Field(None, description="可选，指定只查的业务表子集；缺省查全部 6 张")


@app.post("/skill/customer_business/fetch")
async def customer_business_fetch(req: CustomerBusinessFetchRequest) -> dict:
    """免 LLM 直取（前端/联调用）：直接传结构化参数，不做自然语言抽参。"""
    from backend.skills.customer_business_qa import CustomerBusinessQA

    try:
        return CustomerBusinessQA().query(req.name_or_id, req.org_id, req.tables)
    except Exception as exc:  # noqa: BLE001 — 兜底避免 500 裸抛
        return {"status": "error", "message": f"查询出错：{exc}", "data": [], "error": str(exc)}


@app.get("/skill/customer_business/resolve")
async def customer_business_resolve(customer: str, org_id: str | None = None) -> dict:
    """仅做客户解析：按名/编号在 6 表匹配，返回候选（含各自命中的来源表）。"""
    from backend.skills.customer_business_data import CustomerBusinessData

    cbd = CustomerBusinessData()
    cands = cbd.resolve_customer(customer)
    if org_id:
        cands = [c for c in cands if c["org_id"] == org_id]
    return {"ok": bool(cands), "count": len(cands), "candidates": cands}


# ---------- 客户管理情况 Skill（参与人/客户/供求关系/经营主责人）----------
# 本期切片：不含自动评分/自动分类/手工分类保存（依赖评分规则，后续里程碑）。
# 统一返回 backend.common.response 信封；本期不做鉴权。
def _cm_service():
    from backend.skills.customer_mgmt_service import CustomerMgmtService

    return CustomerMgmtService()


class CustomerMgmtAskRequest(BaseModel):
    query: str = Field(..., description="用户的自然语言问题，如「查一下测试科技的经营主责人」")


@app.post("/skill/customer_mgmt/query")
async def cm_query(req: CustomerMgmtAskRequest) -> dict:
    """主入口（对齐 customer_business/query）：自然语言 → 客户管理概览 + 中文回答。

    处理链：LLM 抽参（参与人名称/编号、机构）→ 确定性固定 SQL 取数 → LLM 合成 answer。
    取数不经 LLM，杜绝编造。status 取值：
      ok / not_found / multiple / empty_input / invalid_input / error；ok 时附加 answer。
    """
    from backend.common import response as resp

    try:
        return await _cm_service().ask(req.query)
    except Exception as exc:  # noqa: BLE001 — 兜底避免 500 裸抛
        return resp.error(exc)


class CustomerMgmtFetchRequest(BaseModel):
    keyword: str = Field(..., description="参与人名称 CST_FULLNM 或客户编号 CST_ID")
    org_id: str | None = Field(None, description="可选，机构编号（客户所属分行），多命中时定位")


@app.post("/skill/customer_mgmt/fetch")
async def cm_fetch(req: CustomerMgmtFetchRequest) -> dict:
    """免 LLM 直取（前端/联调用）：结构化参数 → 客户管理概览（无 answer 字段）。"""
    from backend.common import response as resp

    try:
        return _cm_service().query(req.keyword, req.org_id)
    except Exception as exc:  # noqa: BLE001 — 兜底避免 500 裸抛
        return resp.error(exc)


@app.get("/skill/customer_mgmt/manager_customer_count")
async def cm_manager_customer_count(manager_oa: str) -> dict:
    """统计某经营主责人（工号）当前负责的客户数。按工号查，故独立成口。"""
    from backend.common import response as resp

    try:
        return _cm_service().count_customers_by_manager(manager_oa)
    except Exception as exc:  # noqa: BLE001
        return resp.error(exc)


class MainManagerSaveRequest(BaseModel):
    cst_id: str = Field(..., description="客户编号（该客户无主责人记录时新增，已有则更新）")
    operator: str = Field(..., description="当前操作员工号，写入创建/修改审计列")
    fields: dict = Field(..., description="主责人业务字段（仅白名单列生效）")


@app.post("/skill/customer_mgmt/main_manager/save")
async def cm_main_manager_save(req: MainManagerSaveRequest) -> dict:
    """保存客户经营主责人（以 CST_ID 为键）：无记录=新增，已有=更新。"""
    from backend.common import response as resp

    try:
        return _cm_service().save_main_manager(
            req.cst_id, req.operator, req.fields
        )
    except Exception as exc:  # noqa: BLE001
        return resp.error(exc)


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("backend.app:app", host=s.app_host, port=s.app_port, reload=False)


if __name__ == "__main__":
    main()
