"""客户业务信息取数层：固定参数化 SQL（不做 NL→SQL）。

数据源：GKH 库 6 张客户业务统计表，均以 (WORK_DT, ORG_ID, CST_ID, CST_NM) 为公共键：
  RPT_CUST_CAPITAL_DETAIL      客户资金信息统计表
  RPT_CUST_LOAN_DETAIL         客户贷款信息统计表
  RPT_CUST_DEPOSIT_DETAIL      客户存款信息统计表
  RPT_CUST_FUND_DETAIL         客户基金信息统计表
  RPT_CUST_GUARANTY_DETAIL     客户担保信息统计表
  RPT_CUST_MIDBUSINESS_DETAIL  客户中间业务信息统计表

匹配路径（开发计划「(1)客户业务数据信息」两种方式）：
  路径①  参与人名称信息表 TBCCPNM0（CST_FULLNM → ORG_ID）——本期库内该表为空且无 CST_ID，
         无法据此拿到业务明细，故仅保留占位，实际检索走路径②。
  路径②  客户业务统计表（CST_NM → CST_ID / ORG_ID + 业务明细）——本层落地此路径。

字段口径：
  CST_NM  客户名称    CST_ID  客户编号    ORG_ID  机构编号（客户所属分行）
金额字段命名 *_W（原币，单位：万元）/ *_WRMB（折人民币，单位：万元），库内已是万元量级，
本层原样返回，不做二次换算。
"""
from __future__ import annotations

from typing import Any

from backend.db.dm import DMClient, get_dm

# 每张业务统计表的元数据：中文表义 + 除公共键外的业务明细字段（按展示顺序）。
# 公共键 WORK_DT / ORG_ID / CST_ID / CST_NM 统一由查询拼接，不在此重复。
BUSINESS_TABLES: dict[str, dict[str, Any]] = {
    "RPT_CUST_CAPITAL_DETAIL": {
        "label": "客户资金信息统计表",
        "detail_columns": [],  # 该表仅公共键，无额外业务明细列
    },
    "RPT_CUST_LOAN_DETAIL": {
        "label": "客户贷款信息统计表",
        "detail_columns": [
            "PROJECT_NO", "PROJECT_NAME", "CONTRACT_NO", "CONTRACT_BAL_W",
            "CONTRACT_BAL_WRMB", "CONTRACT_TERM", "SIGN_DATE", "CCY_DESC",
            "CONTRACT_STATUS_DESC", "LOAN_BAL_W", "LOAN_BAL_WRMB",
        ],
    },
    "RPT_CUST_DEPOSIT_DETAIL": {
        "label": "客户存款信息统计表",
        "detail_columns": [
            "DEPOSIT_TYPE_DESC", "CST_ACCNO", "CST_ACCNO_NM", "CCY_DESC",
            "DEPOSIT_AMT_W", "DEPOSIT_AMT_WRMB",
        ],
    },
    "RPT_CUST_FUND_DETAIL": {
        "label": "客户基金信息统计表",
        "detail_columns": [
            "PROJECT_NO", "PROJECT_NAME", "CRLN_CTR_ID", "ASPD_ID", "CCY_DESC",
            "LNACC_BAL_W", "LNACC_BAL_WRMB", "STKH_TYPE",
        ],
    },
    "RPT_CUST_GUARANTY_DETAIL": {
        "label": "客户担保信息统计表",
        "detail_columns": [
            "PROJECT_NO", "PROJECT_NAME", "CONTRACT_NO", "SIGN_DATE",
            "CON_CCY_DESC", "CONTRACT_STATUS_DESC", "CONTRACT_AMT_W",
            "CONTRACT_AMT_WRMB", "SUBCONTRACT_NO", "GUARANTY_TYPE_DESC",
        ],
    },
    "RPT_CUST_MIDBUSINESS_DETAIL": {
        "label": "客户中间业务信息统计表",
        "detail_columns": [
            "BIZ_TYPE_DESC", "CCY_DESC", "MID_BIZ_AMT_W", "MID_BIZ_AMT_WRMB",
        ],
    },
}

# 公共键（每张表都有），统一置于业务明细列之前。
_COMMON_KEYS = ["CST_ID", "ORG_ID", "CST_NM", "WORK_DT"]


class CustomerBusinessData:
    def __init__(self, dm: DMClient | None = None) -> None:
        self._dm = dm or get_dm()

    # ---------- 客户解析 ----------
    def resolve_customer(self, q: str, limit: int = 50) -> list[dict[str, Any]]:
        """按 客户编号CST_ID / 客户名称CST_NM 在 6 张业务表中解析客户身份。

        - 形如 18 位数字编号 → 优先当 CST_ID 精确匹配
        - 否则按 CST_NM 模糊匹配（LIKE）
        跨 6 表 UNION 去重，返回 [{cst_id, org_id, cst_nm, source_tables}]。
        多命中不自行过滤，全部返回，由上层澄清。
        """
        q = (q or "").strip()
        if not q:
            return []

        # 各表投影出统一三元组 + 来源表名，再在 Python 侧聚合去重
        exact = q.isdigit()
        hits: dict[tuple[str, str], dict[str, Any]] = {}
        for table in BUSINESS_TABLES:
            if exact:
                sql = (
                    f"SELECT DISTINCT CST_ID, ORG_ID, CST_NM FROM {table} "
                    "WHERE CST_ID = ?"
                )
                params: list[Any] = [q]
            else:
                sql = (
                    f"SELECT DISTINCT CST_ID, ORG_ID, CST_NM FROM {table} "
                    "WHERE CST_NM LIKE ?"
                )
                params = [f"%{q}%"]
            for r in self._dm.query(sql, params):
                key = (r["cst_id"], r["org_id"])
                if key not in hits:
                    hits[key] = {
                        "cst_id": r["cst_id"],
                        "org_id": r["org_id"],
                        "cst_nm": r["cst_nm"],
                        "source_tables": [],
                    }
                hits[key]["source_tables"].append(table)
        out = sorted(hits.values(), key=lambda x: x["cst_id"])
        return out[:limit]

    # ---------- 单表业务明细 ----------
    def fetch_table(
        self, table: str, cst_id: str, org_id: str | None = None
    ) -> list[dict[str, Any]]:
        """取单张业务表中该客户的全部明细行。org_id 传入时进一步限定分行。"""
        if table not in BUSINESS_TABLES:
            raise ValueError(f"未知业务表：{table}")
        detail_cols = BUSINESS_TABLES[table]["detail_columns"]
        select_cols = _COMMON_KEYS + detail_cols
        sql = f"SELECT {', '.join(select_cols)} FROM {table} WHERE CST_ID = ?"
        params: list[Any] = [cst_id]
        if org_id:
            sql += " AND ORG_ID = ?"
            params.append(org_id)
        return self._dm.query(sql, params)

    # ---------- 多表业务明细 ----------
    def fetch_business(
        self,
        cst_id: str,
        org_id: str | None = None,
        tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """逐表独立查询该客户业务明细，返回带来源表标注的分组结果。

        tables 传入时只查指定表（子集），否则查全部 6 张表。
        返回结构：
          [{"table": 表名, "label": 中文表义, "columns": 展示列, "rows": [...]}...]
        无明细的表仍返回（rows=[]），便于上层区分「表内无数据」与「未匹配客户」。
        """
        targets = tables or list(BUSINESS_TABLES.keys())
        result: list[dict[str, Any]] = []
        for table in targets:
            if table not in BUSINESS_TABLES:
                continue
            rows = self.fetch_table(table, cst_id, org_id)
            result.append({
                "table": table,
                "label": BUSINESS_TABLES[table]["label"],
                "columns": _COMMON_KEYS + BUSINESS_TABLES[table]["detail_columns"],
                "rows": rows,
            })
        return result
