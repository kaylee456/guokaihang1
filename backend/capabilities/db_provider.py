"""DBProvider —— 未来接库实现（本期只留契约 TODO）。

对齐开发计划：取数走固定参数化 SQL / 视图，不做 NL→SQL（vanna 留给模板外兜底）。
本期不实现，任何方法被调用时明确报错，避免静默返回空数据误导上层。
一键切换：把 .env 的 PROVIDER_MODE 改为 db 即启用（届时补全下列方法）。
"""
from __future__ import annotations

from typing import Any

from backend.capabilities.base import CapabilityResult, Provider


class DBProvider(Provider):
    mode = "db"

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        # TODO(M5+): 初始化连接池 / 载入 C1-C10 固定 SQL 模板。
        pass

    def _todo(self, capability: str) -> CapabilityResult:
        raise NotImplementedError(
            f"{capability}: DBProvider 本期未实现，请用 PROVIDER_MODE=mock。"
        )

    async def c1_customer_info(self, cst_id: str) -> CapabilityResult:
        return self._todo("C1")

    async def c2_financial(self, cst_id: str) -> CapabilityResult:
        return self._todo("C2")

    async def c3_our_business(self, cst_id: str) -> CapabilityResult:
        return self._todo("C3")

    async def c4_risk_warning(self, cst_id: str) -> CapabilityResult:
        return self._todo("C4")

    async def c5_rating_event(self, cst_id: str) -> CapabilityResult:
        return self._todo("C5")

    async def c6_customer_mgmt(self, cst_id: str) -> CapabilityResult:
        return self._todo("C6")

    async def c7_credit_report(self, cst_id: str) -> CapabilityResult:
        return self._todo("C7")

    async def c8_namelist(self, cst_id: str) -> CapabilityResult:
        return self._todo("C8")

    async def c9_audit_report(self, cst_id: str) -> CapabilityResult:
        return self._todo("C9")

    async def c10_relation(self, cst_id: str) -> CapabilityResult:
        return self._todo("C10")

    async def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.mode, "ready": False, "note": "本期未实现，留契约"}
