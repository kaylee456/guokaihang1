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


# ---------- 客户管理情况 Skill（注入假 DM，不连库）----------
class _CmFakeDM:
    """按 SQL 关键字路由的假 DM，覆盖客户管理情况的 query/query_one/execute。

    构造入参：
      customers   已存在的客户 [{cst_id, org_id, multi_tenancy_id}]（供存在性校验）
      participants 参与人 [{cst_fullnm, org_id, multi_tenancy_id}]
      managers    主责人明细行（含 cst_id / main_manager_oa 等）
    execute 记录最后一次写操作，供断言 INSERT/UPDATE 分派。
    """

    def __init__(self, customers=None, participants=None, managers=None):  # noqa: ANN001
        self.customers = customers or []
        self.participants = participants or []
        self.managers = managers or []
        self.last_execute: tuple[str, list] | None = None

    def query(self, sql: str, params=None):  # noqa: ANN001
        p = list(params or [])
        if "C_CUST_MAIN_MGR_INFO" in sql:  # 主责人列表
            return [r for r in self.managers if r["cst_id"] == p[0]]
        if "JOIN" in sql:  # resolve_customer 的两种解析链（TBCCPNM0 × TBCCCUS0）
            return self._resolve(sql, p)
        return []

    def _resolve(self, sql: str, p: list):  # noqa: ANN001
        """模拟 TBCCPNM0×TBCCCUS0 的 JOIN：名称→MULTI_TENANCY_ID+ORG_ID→CST_ID / 或按 CST_ID 精确。"""
        if "LIKE" in sql:  # 路径 B：参与人名称模糊 → 客户
            name_frag = str(p[0]).strip("%")
            org_filter = p[1] if len(p) > 2 else None
            orgs = {r["org_id"] for r in self.participants if name_frag in r["cst_fullnm"]}
            name_by_org = {r["org_id"]: r["cst_fullnm"] for r in self.participants}
            out = []
            for c in self.customers:
                if c["org_id"] not in orgs:
                    continue
                if org_filter and c["org_id"] != org_filter:
                    continue
                out.append({
                    "cst_id": c["cst_id"], "org_id": c["org_id"],
                    "cst_fullnm": name_by_org.get(c["org_id"]),
                    "multi_tenancy_id": c.get("multi_tenancy_id"),
                })
            return out
        # 路径 A：CST_ID 精确
        cst_id = p[0]
        return [
            {"cst_id": c["cst_id"], "org_id": c["org_id"],
             "cst_fullnm": None, "multi_tenancy_id": c.get("multi_tenancy_id")}
            for c in self.customers
            if c["cst_id"] == cst_id
        ]

    def query_one(self, sql: str, params=None):  # noqa: ANN001
        p = list(params or [])
        if "TBCCCUS0" in sql:  # 客户存在性校验
            hit = any(r["cst_id"] == p[0] for r in self.customers)
            return {"ok": 1} if hit else None
        if "C_CUST_MAIN_MGR_INFO" in sql and "COUNT" in sql:  # 主责人计数
            latest: dict[str, dict] = {}
            for r in self.managers:
                latest[r["cst_id"]] = r  # 简化：测试数据每客户一条即为当前
            cnt = sum(1 for r in latest.values() if r.get("main_manager_oa") == p[0])
            return {"cnt": cnt}
        return None

    def execute(self, sql: str, params=None):  # noqa: ANN001
        self.last_execute = (sql, list(params or []))
        p = list(params or [])
        # 模拟 INSERT：记住该客户已有主责人记录，供后续存在性判断走 UPDATE 分支
        if "INSERT" in sql:
            self.managers.append({"cst_id": p[0]})
        return 1


def _cm_service(**kw):  # noqa: ANN003
    from backend.skills.customer_mgmt_data import CustomerMgmtData
    from backend.skills.customer_mgmt_service import CustomerMgmtService

    return CustomerMgmtService(data=CustomerMgmtData(dm=_CmFakeDM(**kw)))


def test_cm_query_empty_keyword() -> None:
    """空关键字 → empty_input。"""
    res = _cm_service().query(keyword="   ")
    assert res["status"] == "empty_input"


def test_cm_guard_rejects_missing_cst() -> None:
    """CST_ID 不存在时，写入被 _guard_cst 拦截。"""
    res = _cm_service().save_main_manager(
        "C1", operator="E001", fields={"MAIN_MANAGER_NAME": "张三"}
    )
    assert res["status"] == "invalid_input"


def test_cm_main_manager_save_insert_vs_update() -> None:
    """该客户无主责人记录 → INSERT；已有 → UPDATE，且审计列由本层填充。"""
    custs = [{"cst_id": "C1", "multi_tenancy_id": "T01", "org_id": "O1"}]
    svc = _cm_service(customers=custs)
    ins = svc.save_main_manager(
        "C1", operator="E001", fields={"MAIN_MANAGER_NAME": "张三"}
    )
    assert ins["status"] == "ok"
    assert ins["action"] == "insert"

    # 已有记录后再次保存 → 按 CST_ID 更新
    upd = svc.save_main_manager(
        "C1", operator="E001", fields={"MAIN_MANAGER_NAME": "李四"}
    )
    assert upd["status"] == "ok"
    assert upd["action"] == "update"


def test_cm_manager_customer_count() -> None:
    """按主责人工号统计当前负责客户数（每客户取最新一条）。"""
    mgrs = [
        {"cst_id": "C1", "main_manager_oa": "E001"},
        {"cst_id": "C2", "main_manager_oa": "E001"},
        {"cst_id": "C3", "main_manager_oa": "E002"},
    ]
    res = _cm_service(managers=mgrs).count_customers_by_manager("E001")
    assert res["status"] == "ok"
    assert res["data"]["customer_count"] == 2


def test_cm_resolve_participant_name_to_cst() -> None:
    """参与人名称模糊 → MULTI_TENANCY_ID + ORG_ID → CST_ID 解析链跑通。"""
    parts = [{"cst_fullnm": "测试科技有限公司", "multi_tenancy_id": "T01", "org_id": "O1"}]
    custs = [{"cst_id": "C1", "multi_tenancy_id": "T01", "org_id": "O1"}]
    cands = _cm_service(participants=parts, customers=custs)._d.resolve_customer(
        "测试科技"
    )
    assert len(cands) == 1
    assert cands[0]["cst_id"] == "C1"
    assert cands[0]["cst_fullnm"] == "测试科技有限公司"


def test_cm_query_unique_hit_returns_overview() -> None:
    """唯一命中：返回客户 + 供求关系 + 详情 + 主责人的概览结构。"""
    parts = [{"cst_fullnm": "唯一集团", "multi_tenancy_id": "T01", "org_id": "O1"}]
    custs = [{"cst_id": "C1", "multi_tenancy_id": "T01", "org_id": "O1"}]
    res = _cm_service(participants=parts, customers=custs).query("唯一集团")
    assert res["status"] == "ok"
    assert res["resolved"]["cst_id"] == "C1"
    assert set(res["data"]) >= {"customer", "supply_rela", "supply_rela_details", "main_manager"}


def test_cm_query_multiple_candidates() -> None:
    """名称多命中（跨机构同名参与人）→ multiple，全部候选返回。"""
    parts = [
        {"cst_fullnm": "同名集团", "multi_tenancy_id": "T01", "org_id": "O1"},
        {"cst_fullnm": "同名集团", "multi_tenancy_id": "T01", "org_id": "O2"},
    ]
    custs = [
        {"cst_id": "C1", "multi_tenancy_id": "T01", "org_id": "O1"},
        {"cst_id": "C2", "multi_tenancy_id": "T01", "org_id": "O2"},
    ]
    res = _cm_service(participants=parts, customers=custs).query("同名集团")
    assert res["status"] == "multiple"
    assert len(res["candidates"]) == 2


def test_cm_query_not_found() -> None:
    """无匹配 → not_found，不编造。"""
    res = _cm_service().query("不存在的客户")
    assert res["status"] == "not_found"


if __name__ == "__main__":
    test_provider_registry_returns_mock_by_default()
    test_mock_provider_missing_customer_is_graceful()
    test_provider_implements_all_ten_capabilities()
    test_health_endpoint()
    test_cb_resolve_and_query_unique_hit()
    test_cb_multiple_candidates_not_filtered()
    test_cb_not_found_returns_no_data_message()
    test_cb_org_id_disambiguates()
    test_cm_query_empty_keyword()
    test_cm_guard_rejects_missing_cst()
    test_cm_main_manager_save_insert_vs_update()
    test_cm_manager_customer_count()
    test_cm_resolve_participant_name_to_cst()
    test_cm_query_unique_hit_returns_overview()
    test_cm_query_multiple_candidates()
    test_cm_query_not_found()
    print("M0 smoke: all passed")
