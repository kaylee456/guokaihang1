"""Provider 抽象 + DTO。

十大能力 C1..C10 的统一取数接口。业务层（workflows）只依赖本抽象，
不关心数据来自 mock 还是 db —— 由 registry 按 PROVIDER_MODE 分发。

M0 只定契约：DTO 用 pydantic 定形，Provider 定抽象方法。
具体取数逻辑（读 mockdata / 走固定 SQL）在 M1 及之后的里程碑实现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


# ============ DTO ============
# 贴真实表字段名建模，DTO/SQL/报告面向真实字段编程，接库零改动。
# M0 先给最小骨架字段，随取数实现逐步补全。

class CapabilityResult(BaseModel):
    """单个能力的取数结果统一包装。

    data 内部结构由各能力自定；status/gaps 供上层判断覆盖度与缺口。
    """
    capability: str = Field(..., description="能力编号，如 C1")
    cst_id: str | None = Field(None, description="客户编号 CST_ID")
    status: str = Field("ok", description="ok | partial | missing")
    data: dict[str, Any] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list, description="缺口标注，对齐 mapping.md")


# ============ Provider 抽象 ============
class Provider(ABC):
    """十大能力取数接口。

    每个方法对应一个原子能力，入参统一以 cst_id 为主键
    （必要时后续扩展 org_id / grp_id）。返回 CapabilityResult。
    """

    mode: str = "abstract"

    @abstractmethod
    async def c1_customer_info(self, cst_id: str) -> CapabilityResult:
        """C1 客户基本信息（工商 / 主营 / 股东股权 / 证件 / 上市）。"""

    @abstractmethod
    async def c2_financial(self, cst_id: str) -> CapabilityResult:
        """C2 财务信息（总资产 / 年销售额 …，完整财报待补）。"""

    @abstractmethod
    async def c3_our_business(self, cst_id: str) -> CapabilityResult:
        """C3 我行业务（贷款余额 / 合约快照 / 交易 / 信用等级）。"""

    @abstractmethod
    async def c4_risk_warning(self, cst_id: str) -> CapabilityResult:
        """C4 风险预警信号（一/二级指标）。"""

    @abstractmethod
    async def c5_rating_event(self, cst_id: str) -> CapabilityResult:
        """C5 评级重大风险事件 / 客户关注事件。"""

    @abstractmethod
    async def c6_customer_mgmt(self, cst_id: str) -> CapabilityResult:
        """C6 客户管理（等级/分类 / 经营主责人 / 机构客户关系）。"""

    @abstractmethod
    async def c7_credit_report(self, cst_id: str) -> CapabilityResult:
        """C7 征信（缺口，先 mock 占位）。"""

    @abstractmethod
    async def c8_namelist(self, cst_id: str) -> CapabilityResult:
        """C8 名单管理（科创 / 战略优质 / 研发贷款，缺口，先标志位模拟）。"""

    @abstractmethod
    async def c9_audit_report(self, cst_id: str) -> CapabilityResult:
        """C9 审计报告（附件管理，PDF 解析待接入）。"""

    @abstractmethod
    async def c10_relation(self, cst_id: str) -> CapabilityResult:
        """C10 关联关系（集团 / 股东 / 参与人 / 组织关联人）。"""

    async def healthcheck(self) -> dict[str, Any]:
        """Provider 自检，供 /health 汇报数据侧就绪度。"""
        return {"provider": self.mode, "ready": True}
