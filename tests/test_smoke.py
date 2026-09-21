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


if __name__ == "__main__":
    test_provider_registry_returns_mock_by_default()
    test_mock_provider_missing_customer_is_graceful()
    test_provider_implements_all_ten_capabilities()
    test_health_endpoint()
    print("M0 smoke: all passed")
