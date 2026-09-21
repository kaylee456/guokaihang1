"""Provider 分发：按 PROVIDER_MODE 返回单例 Provider。

业务层通过 get_provider() 拿取数接口，永远不 import 具体实现类，
从而 mock↔db 一键切换、业务代码零改动。
"""
from __future__ import annotations

from functools import lru_cache

from backend.capabilities.base import Provider
from backend.capabilities.db_provider import DBProvider
from backend.capabilities.mock_provider import MockProvider
from backend.config import get_settings

_REGISTRY: dict[str, type[Provider]] = {
    "mock": MockProvider,
    "db": DBProvider,
}


@lru_cache
def get_provider() -> Provider:
    """按 .env 的 PROVIDER_MODE 实例化并缓存 Provider 单例。"""
    mode = get_settings().provider_mode
    try:
        cls = _REGISTRY[mode]
    except KeyError:
        raise ValueError(
            f"未知 PROVIDER_MODE={mode!r}，可选：{sorted(_REGISTRY)}"
        ) from None
    return cls()
