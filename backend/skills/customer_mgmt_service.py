"""客户管理情况服务编排：确定性取数/维护 + 自然语言问答入口。

接口面收敛为四个（对齐 financial / customer_business）：
  query   自然语言问答（LLM 抽参 → 确定性取数 → 合成 answer）
  fetch   参与人名称/客户编号 → 解析 CST_ID → 客户管理概览（确定性，供前端直调）
  count   按主责人工号统计负责客户数（主语是工号、非客户，不可并入 query）
  save    经营主责人保存（写操作，不可并入 GET）

本期切片不含自动评分 / 自动分类 / 手工分类保存（依赖评分规则，后续里程碑）。
统一返回 backend.common.response 信封 {status, message, data, ...}；
ask() 在此基础上附加 answer / extracted 字段（对齐 financial_qa / customer_business_qa）。
MULTI_TENANCY_ID 为表内关联列，不作为入参暴露；C_ 表按 CST_ID 取数前用客户表校验存在性。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.common import response as resp
from backend.config import get_settings
from backend.llm.client import LLMClient
from backend.skills.customer_mgmt_data import CustomerMgmtData

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class CustomerMgmtService:
    def __init__(
        self,
        data: CustomerMgmtData | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self._d = data or CustomerMgmtData()
        self._llm = llm  # 惰性创建：仅 ask() 走 LLM 时才需要

    # ==================== 主入口：query ====================
    def query(
        self,
        keyword: str,
        org_id: str | None = None,
    ) -> dict[str, Any]:
        """参与人名称/客户编号 → 客户管理概览（对齐 customer_business/query）。

        解析链：keyword 命中 TBCCPNM0.CST_FULLNM → MULTI_TENANCY_ID + ORG_ID
        → TBCCCUS0.CST_ID。未命中 → not_found；多命中 → multiple（列全部候选，不自行过滤）；
        唯一命中 → 供求关系(当前有效) + 供求详情 + 经营主责人(当前有效)。
        """
        kw = (keyword or "").strip()
        if not kw:
            return resp.empty_input("请输入参与人名称或客户编号")

        cands = self._d.resolve_customer(kw, org_id)
        if not cands:
            return resp.not_found("未查询到对应客户")
        if len(cands) > 1:
            return resp.multiple(cands, f"匹配到 {len(cands)} 个客户，请通过客户编号或机构进一步确认")

        cust = cands[0]
        return resp.ok(self._overview(cust["cst_id"], cust), resolved=cust)

    # ==================== 自然语言入口：ask ====================
    async def ask(self, question: str) -> dict[str, Any]:
        """自然语言问题 → 客户管理概览 + 中文回答（对齐 customer_business.ask / financial.ask）。

        LLM 只负责两件事：抽参（keyword/org_id）与合成中文回答；取数仍走确定性
        query()，不让模型编造。MULTI_TENANCY_ID 为表内关联列，不从问题抽取、也不作入参。
        """
        params = await self._extract(question)
        keyword = params.get("keyword", "")
        org_id = params.get("org_id") or None
        result = self.query(keyword, org_id)
        result["extracted"] = params
        if result["status"] == "ok":
            result["answer"] = await self._synthesize(question, result)
        else:
            result["answer"] = result.get("message", "")
        return result

    async def _extract(self, question: str) -> dict[str, Any]:
        if self._llm is None:
            self._llm = LLMClient(get_settings())
        prompt = _load_prompt("cust_mgmt_extract.txt") + question
        raw = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.0,
            enable_thinking=False,
        )
        return _parse_json(raw)

    async def _synthesize(self, question: str, result: dict[str, Any]) -> str:
        if self._llm is None:
            self._llm = LLMClient(get_settings())
        context = {"resolved": result.get("resolved"), "data": result.get("data", [])}
        prompt = (
            _load_prompt("cust_mgmt_answer.txt")
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

    # ==================== 主责人：计数 ====================
    def count_customers_by_manager(self, manager_oa: str) -> dict[str, Any]:
        """统计某主责人（工号）当前负责的客户数。按工号查、非按客户，故独立成口。"""
        if not manager_oa:
            return resp.invalid_input("缺少主责人工号 manager_oa")
        n = self._d.count_customers_by_manager(manager_oa)
        return resp.ok({"manager_oa": manager_oa, "customer_count": n})

    # ==================== 主责人：保存（写）====================
    def save_main_manager(
        self,
        cst_id: str,
        operator: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """保存经营主责人（以 CST_ID 为键）：该客户无记录 → 新增；已有 → 更新。

        operator 为当前操作员工号，写入创建/修改审计列（无鉴权，但需可追溯）。
        """
        guard = self._guard_cst(cst_id)
        if guard is not None:
            return guard
        if not operator:
            return resp.invalid_input("缺少操作员 operator，无法记录操作日志")
        if not isinstance(fields, dict) or not fields:
            return resp.invalid_input("缺少主责人字段 fields")

        is_update = self._d.main_manager_current(cst_id) is not None
        affected = self._d.save_main_manager(cst_id, fields, operator, is_update)
        return resp.ok(
            {"cst_id": cst_id, "affected": affected},
            message="保存成功",
            action="update" if is_update else "insert",
        )

    # ==================== 内部 ====================
    def _overview(self, cst_id: str, customer: dict[str, Any] | None = None) -> dict[str, Any]:
        """客户管理概览：当前有效供求关系 + 全部供求详情 + 当前有效经营主责人。"""
        return {
            "customer": customer,
            "supply_rela": self._d.supply_rela_current(cst_id),
            "supply_rela_details": self._d.supply_rela_details_by_cst(cst_id),
            "main_manager": self._d.main_manager_current(cst_id),
        }

    def _guard_cst(self, cst_id: str) -> dict[str, Any] | None:
        """C_ 表按 CST_ID 查询/维护前的统一前置校验：非空 + 客户存在。

        通过返回 None（放行），否则返回统一错误信封。C_ 表不带租户列，
        故用客户表校验 CST_ID 是否存在，避免按不存在的 CST_ID 取数/写入。
        """
        if not cst_id:
            return resp.invalid_input("缺少客户编号 cst_id")
        if not self._d.customer_exists(cst_id):
            return resp.invalid_input("客户不存在")
        return None


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _parse_json(raw: str) -> dict[str, Any]:
    """从模型输出里稳健地取出 JSON 对象（对齐 financial_qa / customer_business_qa）。"""
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
