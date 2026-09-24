"""客户管理情况取数层：固定参数化 SQL（不做 NL→SQL）。

覆盖「客户管理情况」模块本期切片（不含评分/自动分类）：
  1. 参与人名称查询（按名称模糊 / 按机构反查）；
  2. 客户查询（MULTI_TENANCY_ID + ORG_ID → CST_ID）；
  3. 客户供求关系主表查询（按 CST_ID，当前有效）；
  4. 客户供求关系详情查询（按 CST_ID）；
  5. 客户经营主责人查询 + 保存 + 按主责人统计负责客户数。

━━━ 数据源 ━━━
参与人→客户解析链（两步，MULTI_TENANCY_ID 为表内关联列、非独立入参）：
  ① TBCCPNM0 参与人名称信息：CST_FULLNM → MULTI_TENANCY_ID + ORG_ID
  ② TBCCCUS0 客户：MULTI_TENANCY_ID + ORG_ID → CST_ID
两表在库内真实存在（schema/summary.txt 是不完整摘录，未收录此二表，以开发计划为准）。
CST_ID 之后所有客户管理数据均据此关联。

表名与关键列名（CST_FULLNM / MULTI_TENANCY_ID / ORG_ID / CST_ID）收敛为下方常量，
单点维护，确认后仅改此处。

C_CUST_SUPPLY_RELA / _DETAIL / C_CUST_MAIN_MGR_INFO 为本模块新建表（见 data/customer-mgmt/00-ddl.sql）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.db.dm import DMClient, get_dm

# ━━━ 待确认源表 / 字段（单点维护，确认后仅改此处）━━━
# 参与人名称信息表
PARTICIPANT_TABLE = "TBCCPNM0"
PARTICIPANT_NAME_COL = "CST_FULLNM"       # 参与人/客户全称
# 客户表
CUSTOMER_TABLE = "TBCCCUS0"
# 租户 + 机构隔离列（两表共用命名，若真实列名不同在此改）
TENANCY_COL = "MULTI_TENANCY_ID"
ORG_COL = "ORG_ID"
CST_ID_COL = "CST_ID"

# ━━━ 本模块新建表 ━━━
SUPPLY_RELA_TABLE = "C_CUST_SUPPLY_RELA"
SUPPLY_RELA_DETAIL_TABLE = "C_CUST_SUPPLY_RELA_DETAIL"
MAIN_MGR_TABLE = "C_CUST_MAIN_MGR_INFO"

# 供求主表列（对齐 DDL，用于 SELECT/INSERT 拼接的单点定义）
_SUPPLY_RELA_COLS = [
    "RELA_ID", "CST_ID", "SUPPLY_RELA", "INDUSTRY_TYPE",
    "CREDIT_LEVEL", "START_TIME", "END_TIME", "DETAIL_ID",
]
# 供求详情表列（对齐 DDL）
_SUPPLY_RELA_DETAIL_COLS = [
    "DETAIL_ID", "CST_ID", "AREA_LEVEL", "INDUSTRY_LEVEL", "LISTED_CENTRL_FLAG",
    "CREDIT_RATING", "CREDIT_RECORD", "CUSTTAG_FLAG", "SUPPORT_FLAG",
    "MARKET_POSITION", "FINANCE_COSTS", "SELF_SUFFICIENT", "BNK_TYPE",
    "FINANCING_CHANNEL", "CREDIT_LEVEL", "INDUSTRY_TYPE", "CREATE_USER",
    "CREATE_TIME", "SUPPLY_APPROVE_STATUS", "SCORE", "SUPPLY_RELA", "ORGID",
    "MANUAL_SUPPLY_RELA", "REMARK", "ATTCHMENT_ID", "UPDATE_USER", "UPDATE_TIME",
]
# 主责人表列（以 CST_ID 为键，无代理主键）
_MAIN_MGR_COLS = [
    "CST_ID", "MAIN_MANAGER_NAME", "MAIN_CORGNAME", "MANAGER_ROLE",
    "MAIN_MANAGER_OA", "MAIN_MANAGER_ORGCODE", "MAIN_MANAGER_CHANGE_REASON",
    "MAIN_MANAGER_OTHER_REASON", "MGRMNT_BANK", "MGRMNT_BANK_NAME",
    "DATERMINE_TIME", "MANUAL_DATERMINE_TIME", "ATTCHMENT_ID",
    "CREATE_USER", "CREATE_TIME", "UPDATE_USER", "UPDATE_TIME",
]
# 保存主责人时允许写入的业务字段（排除主键与创建/更新审计列，后者由本层统一维护）
_MAIN_MGR_WRITABLE = [
    "MAIN_MANAGER_NAME", "MAIN_CORGNAME", "MANAGER_ROLE", "MAIN_MANAGER_OA",
    "MAIN_MANAGER_ORGCODE", "MAIN_MANAGER_CHANGE_REASON", "MAIN_MANAGER_OTHER_REASON",
    "MGRMNT_BANK", "MGRMNT_BANK_NAME", "DATERMINE_TIME", "MANUAL_DATERMINE_TIME",
    "ATTCHMENT_ID",
]


class CustomerMgmtData:
    def __init__(self, dm: DMClient | None = None) -> None:
        self._dm = dm or get_dm()

    # ==================== 一、参与人名称 → 客户解析 ====================
    def resolve_customer(
        self,
        keyword: str,
        org_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按参与人名称 / 客户编号解析客户，返回候选（对齐 customer_business.resolve_customer）。

        解析链（两步）：
          ① keyword 命中 TBCCPNM0.CST_FULLNM（模糊）→ MULTI_TENANCY_ID + ORG_ID
          ② JOIN TBCCCUS0（MULTI_TENANCY_ID + ORG_ID）→ CST_ID
        18 位数字形态的 keyword 优先当 CST_ID，直接在 TBCCCUS0 精确匹配。
        MULTI_TENANCY_ID 为表内关联列，不作为入参暴露；org_id 传入时进一步限定。
        多命中不自行过滤，全部返回由上层澄清。

        返回：[{cst_id, org_id, cst_fullnm, multi_tenancy_id}]
        """
        kw = (keyword or "").strip()
        if not kw:
            return []

        # 路径 A：形似 CST_ID（纯数字）→ TBCCCUS0 精确匹配（TBCCPNM0 可能无对应名称，LEFT JOIN）
        if kw.isdigit():
            sql = (
                f"SELECT c.{CST_ID_COL} AS cst_id, c.{ORG_COL} AS org_id, "
                f"       p.{PARTICIPANT_NAME_COL} AS cst_fullnm, c.{TENANCY_COL} AS multi_tenancy_id "
                f"FROM {CUSTOMER_TABLE} c "
                f"LEFT JOIN {PARTICIPANT_TABLE} p "
                f"  ON p.{TENANCY_COL} = c.{TENANCY_COL} AND p.{ORG_COL} = c.{ORG_COL} "
                f"WHERE c.{CST_ID_COL} = ? "
            )
            params: list[Any] = [kw]
            if org_id:
                sql += f"AND c.{ORG_COL} = ? "
                params.append(org_id)
            sql += f"ORDER BY c.{CST_ID_COL} FETCH FIRST ? ROWS ONLY"
            params.append(limit)
            return self._dedup(self._dm.query(sql, params))

        # 路径 B：参与人名称模糊 → MULTI_TENANCY_ID + ORG_ID → CST_ID
        sql = (
            f"SELECT DISTINCT c.{CST_ID_COL} AS cst_id, c.{ORG_COL} AS org_id, "
            f"       p.{PARTICIPANT_NAME_COL} AS cst_fullnm, c.{TENANCY_COL} AS multi_tenancy_id "
            f"FROM {PARTICIPANT_TABLE} p "
            f"JOIN {CUSTOMER_TABLE} c "
            f"  ON c.{TENANCY_COL} = p.{TENANCY_COL} AND c.{ORG_COL} = p.{ORG_COL} "
            f"WHERE p.{PARTICIPANT_NAME_COL} LIKE ? "
        )
        params: list[Any] = [f"%{kw}%"]
        if org_id:
            sql += f"AND c.{ORG_COL} = ? "
            params.append(org_id)
        sql += f"ORDER BY c.{CST_ID_COL} FETCH FIRST ? ROWS ONLY"
        params.append(limit)
        return self._dedup(self._dm.query(sql, params))

    @staticmethod
    def _dedup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 (cst_id, org_id) 去重，保留首个名称，避免一客户多参与人名重复成行。"""
        seen: dict[tuple[Any, Any], dict[str, Any]] = {}
        for r in rows:
            key = (r.get("cst_id"), r.get("org_id"))
            if key not in seen:
                seen[key] = r
        return list(seen.values())

    def customer_exists(self, cst_id: str) -> bool:
        """校验 CST_ID 是否存在于客户表，供 C_ 表按 CST_ID 查询/维护前的归属校验。"""
        row = self._dm.query_one(
            f"SELECT 1 AS ok FROM {CUSTOMER_TABLE} WHERE {CST_ID_COL} = ?",
            [cst_id],
        )
        return row is not None

    # ==================== 三、供求关系主表 ====================
    def supply_rela_current(self, cst_id: str) -> dict[str, Any] | None:
        """取该客户当前有效的供求关系（END_TIME 为空视为当前有效；多条取最新开始）。"""
        cols = ", ".join(_SUPPLY_RELA_COLS)
        return self._dm.query_one(
            f"SELECT {cols} FROM {SUPPLY_RELA_TABLE} "
            f"WHERE CST_ID = ? AND END_TIME IS NULL "
            f"ORDER BY START_TIME DESC",
            [cst_id],
        )

    # ==================== 四、供求关系详情 ====================
    def supply_rela_details_by_cst(self, cst_id: str) -> list[dict[str, Any]]:
        """按 CST_ID 取该客户全部供求详情记录，创建时间倒序。"""
        cols = ", ".join(_SUPPLY_RELA_DETAIL_COLS)
        return self._dm.query(
            f"SELECT {cols} FROM {SUPPLY_RELA_DETAIL_TABLE} "
            f"WHERE CST_ID = ? ORDER BY CREATE_TIME DESC",
            [cst_id],
        )

    # ==================== 五、经营主责人 ====================
    def main_managers(self, cst_id: str) -> list[dict[str, Any]]:
        """取该客户经营主责人记录（CST_ID 为主键，同一客户一条）。"""
        cols = ", ".join(_MAIN_MGR_COLS)
        return self._dm.query(
            f"SELECT {cols} FROM {MAIN_MGR_TABLE} "
            f"WHERE CST_ID = ? "
            f"ORDER BY COALESCE(UPDATE_TIME, CREATE_TIME) DESC",
            [cst_id],
        )

    def main_manager_current(self, cst_id: str) -> dict[str, Any] | None:
        """取该客户当前有效经营主责人（最新一条）。"""
        rows = self.main_managers(cst_id)
        return rows[0] if rows else None

    def count_customers_by_manager(self, manager_oa: str) -> int:
        """统计某主责人（工号 MAIN_MANAGER_OA）当前负责的客户数（按 CST_ID 去重）。

        只计当前有效记录：对每个 CST_ID 取最新一条，其主责人为该工号才计入，
        避免历史记录导致重复/过期计数。
        """
        row = self._dm.query_one(
            f"SELECT COUNT(*) AS cnt FROM ("
            f"  SELECT CST_ID, MAIN_MANAGER_OA, "
            f"    ROW_NUMBER() OVER ("
            f"      PARTITION BY CST_ID "
            f"      ORDER BY COALESCE(UPDATE_TIME, CREATE_TIME) DESC"
            f"    ) AS rn "
            f"  FROM {MAIN_MGR_TABLE}"
            f") t WHERE t.rn = 1 AND t.MAIN_MANAGER_OA = ?",
            [manager_oa],
        )
        return int(row["cnt"]) if row and row.get("cnt") is not None else 0

    def save_main_manager(
        self,
        cst_id: str,
        fields: dict[str, Any],
        operator: str,
        is_update: bool,
    ) -> int:
        """新增或更新经营主责人记录（以 CST_ID 为键，无代理主键）。

        - is_update=False：INSERT 一条新记录，CREATE_USER/CREATE_TIME 由本层填充；
        - is_update=True ：按 CST_ID UPDATE，UPDATE_USER/UPDATE_TIME 由本层填充。
        fields 只接受 _MAIN_MGR_WRITABLE 白名单内的键，其余忽略，防止越权写列。
        返回受影响行数。
        """
        now = datetime.now()
        clean = {k: v for k, v in fields.items() if k in _MAIN_MGR_WRITABLE}

        if not is_update:
            cols = ["CST_ID", *clean.keys(), "CREATE_USER", "CREATE_TIME"]
            vals: list[Any] = [cst_id, *clean.values(), operator, now]
            placeholders = ", ".join(["?"] * len(cols))
            sql = (
                f"INSERT INTO {MAIN_MGR_TABLE} ({', '.join(cols)}) "
                f"VALUES ({placeholders})"
            )
            return self._dm.execute(sql, vals)

        set_parts = [f"{c} = ?" for c in clean] + ["UPDATE_USER = ?", "UPDATE_TIME = ?"]
        params: list[Any] = [*clean.values(), operator, now, cst_id]
        sql = f"UPDATE {MAIN_MGR_TABLE} SET {', '.join(set_parts)} WHERE CST_ID = ?"
        return self._dm.execute(sql, params)
