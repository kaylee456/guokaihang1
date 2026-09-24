"""统一返回结构。

本模块把现有 skill 里散落的 {status, message, data, ...} 形态收敛为单一事实来源，
供「客户管理情况」及后续模块复用。故意保持成 dict（而非强类型模型），
与现有 financial_qa / customer_business_qa 的返回习惯一致，前端零改动。

status 取值约定（贯穿本模块所有端点）：
  ok             成功，data 有效
  not_found      按条件未匹配到记录
  multiple       多命中，需上层澄清（候选放 candidates）
  empty_input    入参为空
  invalid_input  入参非法（如缺必填参数）
  error          执行异常（message 带原因，绝不裸抛 500）
"""
from __future__ import annotations

from typing import Any


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    """成功返回。data 可为 list / dict / 标量；extra 附加 resolved、matched 等字段。"""
    out: dict[str, Any] = {"status": "ok", "message": "", "data": data if data is not None else []}
    out.update(extra)
    return out


def fail(status: str, message: str, **extra: Any) -> dict[str, Any]:
    """非成功返回。status 用本模块约定的取值；data 恒置空列表，便于前端统一渲染。"""
    out: dict[str, Any] = {"status": status, "message": message, "data": []}
    out.update(extra)
    return out


def not_found(message: str = "未查询到对应数据", **extra: Any) -> dict[str, Any]:
    return fail("not_found", message, **extra)


def multiple(candidates: list[dict[str, Any]], message: str | None = None, **extra: Any) -> dict[str, Any]:
    """多命中：列出全部候选，不自行过滤，由上层澄清。"""
    msg = message or f"匹配到 {len(candidates)} 条，请进一步确认"
    return fail("multiple", msg, candidates=candidates, **extra)


def empty_input(message: str = "入参为空") -> dict[str, Any]:
    return fail("empty_input", message)


def invalid_input(message: str) -> dict[str, Any]:
    return fail("invalid_input", message)


def error(exc: Exception | str) -> dict[str, Any]:
    """异常兜底，供端点 try/except 使用，避免 500 裸抛。"""
    msg = str(exc)
    return {"status": "error", "message": f"查询出错：{msg}", "data": [], "error": msg}
