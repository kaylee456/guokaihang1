"""财务取数层：固定参数化 SQL（不做 NL→SQL）。

数据源：GKH 库 5 张表
  cfrm_mock_customer            客户主表（id 主键 / customer_code 业务编号 / customer_name 名称）
  cfrm_mock_indicator_definition 22 个指标定义（含 category_name / display_unit）
  cfrm_mock_indicator_calc_batch 计算批次（含 report_year / cycle_type / period_no / current_marker）
  cfrm_mock_indicator_value      指标值（关联 batch_id + indicator_id）
  cfrm_mock_indicator_rule_set   规则集（本层暂不用）

关联键：value.customer_id / batch.customer_id → customer.id
"""
from __future__ import annotations

from typing import Any

from backend.db.dm import DMClient, get_dm

# 指标四大类，供按类别筛选
CATEGORIES = ["规模指标", "盈利指标", "偿债指标", "现金流指标"]


class FinancialData:
    def __init__(self, dm: DMClient | None = None) -> None:
        self._dm = dm or get_dm()

    # ---------- 客户解析 ----------
    def resolve_customer(self, q: str, limit: int = 10) -> list[dict[str, Any]]:
        """按 内部ID / 业务编号 / 客户名 解析客户，统一返回候选（含 id）。

        - 纯数字且长度较长 → 优先当 id 精确匹配
        - 形如 MOCKxxxx → 当 customer_code 精确匹配
        - 否则按 customer_name 模糊匹配
        多命中时返回候选列表，由上层决定澄清，不瞎猜。
        """
        q = (q or "").strip()
        if not q:
            return []
        base = (
            "SELECT id, customer_code, customer_name, customer_type, "
            "industry_type, credit_rating, listed "
            "FROM cfrm_mock_customer WHERE deleted = 0 "
        )
        # 1) 内部 ID 精确
        if q.isdigit():
            rows = self._dm.query(base + "AND id = ?", [int(q)])
            if rows:
                return rows
        # 2) 业务编号精确（大小写不敏感）
        rows = self._dm.query(base + "AND UPPER(customer_code) = UPPER(?)", [q])
        if rows:
            return rows
        # 3) 客户名模糊
        return self._dm.query(
            base + "AND customer_name LIKE ? ORDER BY id FETCH FIRST ? ROWS ONLY",
            [f"%{q}%", limit],
        )

    def customer_basic(self, customer_id: int) -> dict[str, Any] | None:
        """客户基础信息。"""
        return self._dm.query_one(
            "SELECT id, customer_code, customer_name, customer_type, location_type, "
            "industry_type, enterprise_scale, region_name, lifecycle_stage, "
            "credit_rating, listed FROM cfrm_mock_customer WHERE id = ? AND deleted = 0",
            [customer_id],
        )

    # ---------- 可用周期 ----------
    def available_periods(self, customer_id: int) -> list[dict[str, Any]]:
        """列出该客户可用的 年份+周期类型+期次（当前批次）。最新在前。"""
        return self._dm.query(
            "SELECT report_year, cycle_type, period_no "
            "FROM cfrm_mock_indicator_calc_batch "
            "WHERE customer_id = ? AND current_marker = 1 "
            "ORDER BY report_year DESC, period_no DESC",
            [customer_id],
        )

    def latest_period(self, customer_id: int) -> dict[str, Any] | None:
        """缺周期时的默认：取最新可用期。"""
        rows = self.available_periods(customer_id)
        return rows[0] if rows else None

    # ---------- 指标取数 ----------
    def fetch_indicators(
        self,
        customer_id: int,
        year: int,
        cycle: str,
        period: int,
        categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """三表 JOIN 取指标值。categories 传入时按 category_name 过滤。

        指标原始值 indicator_value 的单位是「元」(金额类)，需除以 display_divisor
        (金额类=10000) 才是展示单位(万元)；比率/倍数类 divisor=1。
        本方法统一按 display_divisor 换算并按 decimal_places 取整后返回，
        indicator_value 即为「按 display_unit 表示的展示值」，另存 raw_value 备查。
        """
        sql = (
            "SELECT d.indicator_code, d.indicator_name, d.category_name, "
            "d.value_kind, d.display_unit, d.display_divisor, d.decimal_places, "
            "v.indicator_value, v.value_status, d.sort "
            "FROM cfrm_mock_indicator_value v "
            "JOIN cfrm_mock_indicator_calc_batch b "
            "  ON b.customer_id = v.customer_id AND b.id = v.batch_id "
            "JOIN cfrm_mock_indicator_definition d ON d.id = v.indicator_id "
            "WHERE v.customer_id = ? AND b.report_year = ? "
            "  AND b.cycle_type = ? AND b.period_no = ? AND b.current_marker = 1 "
        )
        params: list[Any] = [customer_id, year, cycle, period]
        if categories:
            placeholders = ",".join(["?"] * len(categories))
            sql += f"AND d.category_name IN ({placeholders}) "
            params.extend(categories)
        sql += "ORDER BY d.sort"
        rows = self._dm.query(sql, params)
        return [self._scale_row(r) for r in rows]

    @staticmethod
    def _scale_row(r: dict[str, Any]) -> dict[str, Any]:
        """按 display_divisor 换算展示值，按 decimal_places 取整；保留 raw_value。"""
        raw = r.get("indicator_value")
        divisor = r.get("display_divisor") or 1
        dp = r.get("decimal_places")
        r["raw_value"] = raw
        if raw is not None:
            try:
                val = float(raw) / float(divisor)
                r["indicator_value"] = round(val, int(dp) if dp is not None else 2)
            except (TypeError, ValueError):
                pass  # 非数值保持原样
        return r
