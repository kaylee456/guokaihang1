"""客户业务信息查询 Skill 编排：按执行规则检索 6 张业务统计表。

执行规则（对应技能定义）：
  1. 输入 CST_NM/CST_ID，在 6 张业务表匹配，返回 CST_ID、ORG_ID + 业务明细；
  2. 多表独立查询，可按需返回单表或多表合并结果；
  3. 名称多命中时列出全部候选，不自行过滤；
  4. 无匹配数据时明确返回「未查询到对应客户业务数据」，禁止编造；
  5. 输出标注数据来源表名。

本 Skill 是确定性取数编排，不经过 LLM（避免编造/漏项），直接给结构化结果。
上层若需自然语言总结，可再把 data 交给 LLM 合成。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.llm.client import LLMClient
from backend.skills.customer_business_data import BUSINESS_TABLES, CustomerBusinessData

NO_DATA_MSG = "未查询到对应客户业务数据"

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class CustomerBusinessQA:
    def __init__(
        self,
        cbd: CustomerBusinessData | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self._cbd = cbd or CustomerBusinessData()
        self._llm = llm  # 惰性创建：仅 ask() 走 LLM 时才需要

    def query(
        self,
        name_or_id: str,
        org_id: str | None = None,
        tables: list[str] | None = None,
    ) -> dict[str, Any]:
        """主入口：客户名称/编号 → 结构化业务明细（含来源表标注）。

        参数：
          name_or_id  客户名称 CST_NM 或客户编号 CST_ID
          org_id      可选，限定机构编号（客户所属分行），用于多命中时定位
          tables      可选，指定只查的业务表子集；缺省查全部 6 张表
        """
        q = (name_or_id or "").strip()
        if not q:
            return {"status": "empty_input", "message": "请输入客户名称或客户编号", "data": []}

        # 1) 解析客户
        cands = self._cbd.resolve_customer(q)
        if org_id:
            cands = [c for c in cands if c["org_id"] == org_id]

        if not cands:
            return {"status": "not_found", "message": NO_DATA_MSG, "data": []}

        # 规则4：名称多命中，列出全部候选，不自行过滤
        if len(cands) > 1:
            return {
                "status": "multiple",
                "message": f"匹配到 {len(cands)} 个客户，请通过 CST_ID 或 ORG_ID 进一步确认",
                "candidates": cands,
                "data": [],
            }

        # 2) 唯一命中 → 逐表取业务明细
        cust = cands[0]
        groups = self._cbd.fetch_business(cust["cst_id"], cust["org_id"], tables)

        # 规则5：标注来源表；同时汇总命中/空表，便于前端渲染
        non_empty = [g for g in groups if g["rows"]]
        total_rows = sum(len(g["rows"]) for g in groups)

        if total_rows == 0:
            return {
                "status": "no_business_data",
                "message": NO_DATA_MSG,
                "resolved": cust,
                "data": [],
            }

        return {
            "status": "ok",
            "resolved": {
                "cst_id": cust["cst_id"],
                "org_id": cust["org_id"],
                "cst_nm": cust["cst_nm"],
            },
            "matched_tables": [g["table"] for g in non_empty],
            "data": groups,  # 每组含 table/label/columns/rows，空表 rows=[] 亦保留
        }

    # ---------- 自然语言入口（LLM 抽参 → 确定性取数）----------
    async def ask(self, question: str) -> dict[str, Any]:
        """接收一句自然语言问题，LLM 抽出 客户/机构/业务表 后复用 query()。

        与 financial_qa.ask 同构：LLM 只负责抽参，取数全走固定参数化 SQL，
        不让模型编造数据。抽参失败或抽不到客户时按 query() 的边界返回。
        """
        params = await self._extract(question)
        name_or_id = params.get("name_or_id", "")
        org_id = params.get("org_id") or None
        tables = params.get("tables") or None
        # 只保留合法表名，防止 LLM 幻觉出不存在的表
        if tables:
            tables = [t for t in tables if t in BUSINESS_TABLES] or None
        result = self.query(name_or_id, org_id, tables)
        result["extracted"] = params  # 附带抽参原文，便于调试

        # LLM 合成中文回答：仅命中时基于取到的明细生成；其它状态用 message 兜底，
        # 保证 answer 字段恒存在，且绝不让模型编造（取数已确定性完成）。
        if result["status"] == "ok":
            result["answer"] = await self._synthesize(question, result)
        else:
            result["answer"] = result.get("message", "")
        return result

    async def _synthesize(self, question: str, result: dict[str, Any]) -> str:
        if self._llm is None:
            self._llm = LLMClient(get_settings())
        context = {"resolved": result.get("resolved"), "data": result.get("data", [])}
        prompt = (
            _load_prompt("cust_biz_answer.txt")
            + question
            + "\n\n可用数据(JSON)：\n"
            + json.dumps(context, ensure_ascii=False, default=str)
        )
        answer = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.5,
            enable_thinking=False,
        )
        return answer.strip()

    async def _extract(self, question: str) -> dict[str, Any]:
        if self._llm is None:
            self._llm = LLMClient(get_settings())
        prompt = _load_prompt("cust_biz_extract.txt") + question
        raw = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.0,
            enable_thinking=False,
        )
        return _parse_json(raw)


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _parse_json(raw: str) -> dict[str, Any]:
    """从模型输出里稳健地取出 JSON 对象（对齐 financial_qa 的解析）。"""
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
