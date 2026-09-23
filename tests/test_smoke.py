"""M0 冒烟测试 —— 不外呼 LLM，只验证框架装配是否正确。

跑法（服务器上，项目根目录）：
    python -m pytest tests/ -v
或不装 pytest 时直接：
    python -m tests.test_smoke
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from backend.app import app
from backend.capabilities.base import CapabilityResult, Provider
from backend.capabilities.mock_provider import MockProvider
from backend.capabilities.registry import get_provider


def test_provider_registry_returns_mock_by_default() -> None:
    """默认 PROVIDER_MODE=mock，registry 应给出 MockProvider。"""
    p = get_provider()
    assert isinstance(p, MockProvider)
    assert p.mode == "mock"


def test_mock_provider_missing_customer_is_graceful() -> None:
    """mockdata 未落地时，取数返回 missing 而非抛错，框架仍可跑通。"""
    p = MockProvider()
    res = asyncio.run(p.c1_customer_info("NOT_EXIST"))
    assert isinstance(res, CapabilityResult)
    assert res.capability == "C1"
    assert res.status == "missing"


def test_provider_implements_all_ten_capabilities() -> None:
    """C1..C10 抽象方法全部落地，无遗漏。"""
    methods = [m for m in dir(Provider) if m[0] in "c" and m[1:2].isdigit()]
    # 形如 c1_... c10_...，应正好 10 个
    assert len(methods) == 10, methods


def test_health_endpoint() -> None:
    """/health 不外呼 LLM，应稳定返回 ok。"""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["provider"]["provider"] == "mock"


# ---------- 客户业务信息查询 Skill（注入假 DM，不连库）----------
class _FakeDM:
    """按 SQL 关键字返回预置行，模拟 6 张业务表，供无库环境跑通编排逻辑。"""

    def __init__(self, rows_by_table: dict[str, list[dict]]) -> None:
        self._rows = rows_by_table

    def query(self, sql: str, params=None):  # noqa: ANN001
        from backend.skills.customer_business_data import BUSINESS_TABLES

        table = next((t for t in BUSINESS_TABLES if t in sql), None)
        if table is None:
            return []
        rows = self._rows.get(table, [])
        p = (params or [None])[0]
        # resolve_customer：CST_ID 精确 或 CST_NM LIKE（%..%）
        if "WHERE CST_ID = ?" in sql and "SELECT DISTINCT" in sql:
            return [r for r in rows if r["cst_id"] == p]
        if "WHERE CST_NM LIKE ?" in sql:
            frag = str(p).strip("%")
            return [r for r in rows if frag in r["cst_nm"]]
        # fetch_table：按 CST_ID 取明细
        if "WHERE CST_ID = ?" in sql:
            return [r for r in rows if r["cst_id"] == p]
        return []


def _fake_cbd(rows_by_table):  # noqa: ANN001
    from backend.skills.customer_business_data import CustomerBusinessData

    return CustomerBusinessData(dm=_FakeDM(rows_by_table))


def test_cb_resolve_and_query_unique_hit() -> None:
    """唯一命中：返回 CST_ID/ORG_ID + 各表明细，并标注来源表。"""
    from backend.skills.customer_business_qa import CustomerBusinessQA

    base = {"cst_id": "1001", "org_id": "M001", "cst_nm": "测试科技有限公司"}
    rows = {
        "RPT_CUST_CAPITAL_DETAIL": [base],
        "RPT_CUST_LOAN_DETAIL": [base],
        "RPT_CUST_DEPOSIT_DETAIL": [],
        "RPT_CUST_FUND_DETAIL": [],
        "RPT_CUST_GUARANTY_DETAIL": [],
        "RPT_CUST_MIDBUSINESS_DETAIL": [],
    }
    res = CustomerBusinessQA(cbd=_fake_cbd(rows)).query("测试科技")
    assert res["status"] == "ok"
    assert res["resolved"]["cst_id"] == "1001"
    assert res["resolved"]["org_id"] == "M001"
    assert set(res["matched_tables"]) == {"RPT_CUST_CAPITAL_DETAIL", "RPT_CUST_LOAN_DETAIL"}
    assert len(res["data"]) == 6  # 空表也保留分组


def test_cb_multiple_candidates_not_filtered() -> None:
    """名称多命中：列出全部候选，不自行过滤。"""
    from backend.skills.customer_business_qa import CustomerBusinessQA

    a = {"cst_id": "1001", "org_id": "M001", "cst_nm": "新能源A股份公司"}
    b = {"cst_id": "1002", "org_id": "M002", "cst_nm": "新能源B股份公司"}
    rows = {"RPT_CUST_CAPITAL_DETAIL": [a, b]}
    res = CustomerBusinessQA(cbd=_fake_cbd(rows)).query("新能源")
    assert res["status"] == "multiple"
    assert len(res["candidates"]) == 2


def test_cb_not_found_returns_no_data_message() -> None:
    """无匹配：明确返回未查询到，禁止编造。"""
    from backend.skills.customer_business_qa import CustomerBusinessQA, NO_DATA_MSG

    res = CustomerBusinessQA(cbd=_fake_cbd({})).query("不存在客户")
    assert res["status"] == "not_found"
    assert res["message"] == NO_DATA_MSG
    assert res["data"] == []


def test_cb_org_id_disambiguates() -> None:
    """传 org_id 可在多命中中定位唯一分行。"""
    from backend.skills.customer_business_qa import CustomerBusinessQA

    a = {"cst_id": "1001", "org_id": "M001", "cst_nm": "同名集团股份公司"}
    b = {"cst_id": "1002", "org_id": "M002", "cst_nm": "同名集团股份公司"}
    rows = {"RPT_CUST_CAPITAL_DETAIL": [a, b], "RPT_CUST_LOAN_DETAIL": [a, b]}
    res = CustomerBusinessQA(cbd=_fake_cbd(rows)).query("同名集团", org_id="M002")
    assert res["status"] == "ok"
    assert res["resolved"]["org_id"] == "M002"


if __name__ == "__main__":
    test_provider_registry_returns_mock_by_default()
    test_mock_provider_missing_customer_is_graceful()
    test_provider_implements_all_ten_capabilities()
    test_health_endpoint()
    test_cb_resolve_and_query_unique_hit()
    test_cb_multiple_candidates_not_filtered()
    test_cb_not_found_returns_no_data_message()
    test_cb_org_id_disambiguates()
    print("M0 smoke: all passed")
