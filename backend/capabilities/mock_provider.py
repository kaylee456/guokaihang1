"""MockProvider —— 读 mockdata/*.yaml 返回照真实字段名的假数据。

M0 阶段 mockdata 尚未落地（属 M1 数据地基），这里先给可运行的占位实现：
- 若 mockdata 目录 / 客户文件存在，则按能力键取对应片段；
- 否则返回 status="missing" 的空壳，保证框架可跑通、接口契约成立。

M1 造好假数据后，只需保证 yaml 键与各 cX 方法约定一致，无需改动业务层。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from backend.capabilities.base import CapabilityResult, Provider
from backend.config import Settings, get_settings


class MockProvider(Provider):
    mode = "mock"

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._dir = Path(self._s.mockdata_dir)
        self._cache: dict[str, dict[str, Any]] = {}

    def _load_customer(self, cst_id: str) -> dict[str, Any] | None:
        """读取 mockdata/<cst_id>.yaml，缓存解析结果；不存在返回 None。"""
        if cst_id in self._cache:
            return self._cache[cst_id]
        fp = self._dir / f"{cst_id}.yaml"
        if not fp.exists():
            return None
        with fp.open("r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        self._cache[cst_id] = doc
        return doc

    def _pick(self, capability: str, key: str, cst_id: str) -> CapabilityResult:
        """按能力键从客户文档取片段，统一包装为 CapabilityResult。"""
        doc = self._load_customer(cst_id)
        if doc is None:
            return CapabilityResult(
                capability=capability,
                cst_id=cst_id,
                status="missing",
                gaps=[f"mockdata/{cst_id}.yaml 不存在（M1 待造）"],
            )
        section = doc.get(key)
        if section is None:
            return CapabilityResult(
                capability=capability,
                cst_id=cst_id,
                status="partial",
                gaps=[f"缺少键 '{key}'"],
            )
        return CapabilityResult(
            capability=capability,
            cst_id=cst_id,
            status="ok",
            data=section if isinstance(section, dict) else {"value": section},
        )

    async def c1_customer_info(self, cst_id: str) -> CapabilityResult:
        return self._pick("C1", "customer_info", cst_id)

    async def c2_financial(self, cst_id: str) -> CapabilityResult:
        return self._pick("C2", "financial", cst_id)

    async def c3_our_business(self, cst_id: str) -> CapabilityResult:
        return self._pick("C3", "our_business", cst_id)

    async def c4_risk_warning(self, cst_id: str) -> CapabilityResult:
        return self._pick("C4", "risk_warning", cst_id)

    async def c5_rating_event(self, cst_id: str) -> CapabilityResult:
        return self._pick("C5", "rating_event", cst_id)

    async def c6_customer_mgmt(self, cst_id: str) -> CapabilityResult:
        return self._pick("C6", "customer_mgmt", cst_id)

    async def c7_credit_report(self, cst_id: str) -> CapabilityResult:
        return self._pick("C7", "credit_report", cst_id)

    async def c8_namelist(self, cst_id: str) -> CapabilityResult:
        return self._pick("C8", "namelist", cst_id)

    async def c9_audit_report(self, cst_id: str) -> CapabilityResult:
        return self._pick("C9", "audit_report", cst_id)

    async def c10_relation(self, cst_id: str) -> CapabilityResult:
        return self._pick("C10", "relation", cst_id)

    async def healthcheck(self) -> dict[str, Any]:
        return {
            "provider": self.mode,
            "ready": self._dir.exists(),
            "mockdata_dir": str(self._dir),
            "note": "M0 骨架；mockdata 由 M1 落地",
        }
