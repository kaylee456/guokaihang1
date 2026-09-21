"""全局配置：从 .env / 环境变量读取，单一事实来源。

服务器无外网、无法 pip 安装 pydantic-settings，故用标准库解析 .env +
pydantic BaseModel 做类型校验（pydantic 已随 fastapi 安装）。
get_settings() 接口与字段名保持不变，上层零改动。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


def _load_dotenv() -> None:
    """把项目根 .env 读进 os.environ（不覆盖已存在的环境变量）。"""
    # backend/config.py → 上一级是项目根
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val


def _env(key: str, default: str) -> str:
    return os.environ.get(key.upper(), default)


class Settings(BaseModel):
    # ===== LLM（OpenAI 兼容：DeepSeek 云端 / vLLM 均可）=====
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_api_key: str = "EMPTY"
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.3
    llm_timeout: float = 120.0
    llm_retries: int = 2
    # 是否发送 vLLM 专有的 chat_template_kwargs 思考开关。
    # DeepSeek 不识别该参数（会 400），故默认 False；切回 vLLM/Qwen 时在 .env 设 True。
    llm_thinking_toggle: bool = False

    # ===== Provider =====
    provider_mode: Literal["mock", "db"] = "mock"
    mockdata_dir: str = "mockdata"

    # ===== 达梦 DM8（财务库 GKH）=====
    dm_host: str = "localhost"
    dm_port: int = 5236
    dm_user: str = "SYSDBA"
    dm_password: str = "SYSDBA"
    dm_schema: str = "SYSDBA"

    # ===== 服务 =====
    app_host: str = "0.0.0.0"
    app_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """进程级单例，避免重复解析 .env。"""
    _load_dotenv()
    d = Settings()  # 默认值
    # 用环境变量覆盖默认值（键名大写匹配）
    data = {}
    for name, field in Settings.model_fields.items():
        env_val = os.environ.get(name.upper())
        if env_val is not None:
            data[name] = env_val
    return Settings(**data) if data else d
