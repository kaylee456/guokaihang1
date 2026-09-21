"""财务问答编排：LLM 抽参 → 固定 SQL 取数 → LLM 合成回答。

LLM 只负责两件事（不做 NL→SQL）：
  1. 从自然语言问题抽取查询参数（客户/年份/周期/期次/类别/指标）
  2. 把结构化指标数据合成中文回答
中间的取数全走 financial_data 的固定参数化 SQL。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.llm.client import LLMClient
from backend.skills.financial_data import FinancialData

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


class FinancialQA:
    def __init__(self, fd: FinancialData | None = None, llm: LLMClient | None = None) -> None:
        self._fd = fd or FinancialData()
        self._llm = llm or LLMClient(get_settings())

    # ---------- 抽参 ----------
    async def _extract(self, question: str) -> dict[str, Any]:
        prompt = _load_prompt("fin_extract.txt") + question
        raw = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.0,
            enable_thinking=False,
        )
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """从模型输出里稳健地取出 JSON 对象。"""
        s = raw.strip()
        if s.startswith("```"):
            s = s.strip("`")
            s = s[s.find("{"):]
        start, end = s.find("{"), s.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {}

    # ---------- 合成 ----------
    async def _synthesize(self, question: str, context: dict[str, Any]) -> str:
        prompt = (
            _load_prompt("fin_answer.txt")
            + question
            + "\n\n可用数据(JSON)：\n"
            + json.dumps(context, ensure_ascii=False, default=str)
        )
        return await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.5,
            enable_thinking=False,
        )

    # ---------- 主入口 ----------
    async def ask(self, question: str) -> dict[str, Any]:
        params = await self._extract(question)

        # 1) 解析客户
        cands = self._fd.resolve_customer(params.get("customer", ""))
        if not cands:
            return {
                "answer": "没有找到匹配的客户，请提供更准确的客户名称、业务编号或客户ID。",
                "resolved": None,
                "data": [],
            }
        if len(cands) > 1:
            return {
                "answer": f"匹配到 {len(cands)} 个客户，请确认具体是哪一个。",
                "resolved": None,
                "candidates": cands,
                "data": [],
            }
        cust = cands[0]
        cid = cust["id"]

        # 2) 确定周期（缺则取最新可用期）
        year = params.get("year")
        cycle = params.get("cycle")
        period = params.get("period")
        if not (year and cycle and period):
            latest = self._fd.latest_period(cid)
            if not latest:
                return {
                    "answer": f"客户「{cust['customer_name']}」暂无可用的财务指标数据。",
                    "resolved": cust,
                    "data": [],
                }
            year, cycle, period = latest["report_year"], latest["cycle_type"], latest["period_no"]

        # 3) 取指标
        # 指定了具体指标（可能跨类别）时不按类别过滤，取全量让 LLM 挑，避免漏项；
        # 仅当只按类别提问、未点名指标时才用类别过滤。
        cats = params.get("categories") or None
        inds = params.get("indicators") or None
        category_filter = None if inds else cats
        rows = self._fd.fetch_indicators(cid, year, cycle, period, category_filter)

        # 4) 合成
        context = {
            "customer": {"id": cust["id"], "name": cust["customer_name"], "code": cust["customer_code"]},
            "period": {"year": year, "cycle": cycle, "period_no": period},
            "indicators": [
                {"name": r["indicator_name"], "category": r["category_name"],
                 "value": r["indicator_value"], "unit": r["display_unit"]}
                for r in rows
            ],
        }
        answer = await self._synthesize(question, context)

        return {
            "answer": answer.strip(),
            "resolved": cust,
            "period": {"year": year, "cycle": cycle, "period_no": period},
            "extracted": params,
            "data": rows,
        }
