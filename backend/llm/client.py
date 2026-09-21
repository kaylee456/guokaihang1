"""LLM 客户端（OpenAI 兼容接口：DeepSeek 云端 / vLLM 均可）。

要点：
- 抽取用低温、综合建议略高，由调用方传 `temperature` 覆盖默认。
- analyze 类调用超时建议 120s，含有限次重试；前端进度用 SSE 流式。
- 思考开关（`enable_thinking`）仅对支持它的后端（vLLM/Qwen）生效，通过
  `chat_template_kwargs` 透传，由配置 `llm_thinking_toggle` 决定是否发送；
  DeepSeek 等标准接口不识别该参数，默认不发送。deepseek-chat 非推理模型、无思考链。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterable

import httpx

from backend.config import Settings, get_settings

# 仅对「连接失败 / 5xx」这类可恢复错误重试；4xx（如参数错误）直接抛出。
_RETRYABLE_STATUS = {500, 502, 503, 504}

Message = dict[str, str]


class LLMError(RuntimeError):
    """LLM 调用失败（重试耗尽或不可重试错误）。"""


class LLMClient:
    """OpenAI 兼容的 chat / stream 客户端，带思考开关与重试。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        base = self._s.llm_base_url.rstrip("/")
        self._url = f"{base}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {self._s.llm_api_key}",
            "Content-Type": "application/json",
        }

    # ---------- 请求体构造 ----------
    def _build_payload(
        self,
        messages: Iterable[Message],
        *,
        stream: bool,
        max_tokens: int | None,
        temperature: float | None,
        enable_thinking: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._s.llm_model,
            "messages": list(messages),
            "stream": stream,
            "max_tokens": max_tokens if max_tokens is not None else self._s.llm_max_tokens,
            "temperature": temperature if temperature is not None else self._s.llm_temperature,
        }
        # 思考开关仅对支持它的后端（vLLM/Qwen）生效：通过 chat_template_kwargs 透传。
        # DeepSeek 等标准 OpenAI 接口不识别该参数（会 400），由 llm_thinking_toggle 控制是否发送。
        if self._s.llm_thinking_toggle and not enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    # ---------- 非流式 ----------
    async def chat(
        self,
        messages: Iterable[Message],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        enable_thinking: bool = True,
    ) -> str:
        """一次性返回 assistant 文本内容。失败重试至耗尽后抛 LLMError。"""
        payload = self._build_payload(
            messages,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
        )
        data = await self._post_with_retry(payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"响应结构异常: {data!r}") from exc

    async def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        attempts = self._s.llm_retries + 1
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._s.llm_timeout) as cli:
            for i in range(attempts):
                try:
                    resp = await cli.post(self._url, headers=self._headers, json=payload)
                    if resp.status_code in _RETRYABLE_STATUS:
                        last_exc = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_exc = exc  # 网络层错误可重试
                except httpx.HTTPStatusError as exc:
                    # 4xx 等不可重试，立即抛出
                    raise LLMError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
        raise LLMError(f"重试 {attempts} 次仍失败") from last_exc

    # ---------- 流式（SSE）----------
    async def stream(
        self,
        messages: Iterable[Message],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        enable_thinking: bool = True,
    ) -> AsyncIterator[str]:
        """逐 token 产出 assistant 增量文本（供 FastAPI SSE 转发）。

        流式不做整段重试（已开始输出无法回退），仅在建连阶段失败时抛错。
        """
        payload = self._build_payload(
            messages,
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
        )
        async with httpx.AsyncClient(timeout=self._s.llm_timeout) as cli:
            async with cli.stream("POST", self._url, headers=self._headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise LLMError(f"HTTP {resp.status_code}: {body[:200]}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                        delta = obj["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if delta:
                        yield delta

    async def ping(self) -> dict[str, Any]:
        """/llm/ping 用：发一个最小 chat，验证连通与模型可用。"""
        content = await self.chat(
            [{"role": "user", "content": "ping"}],
            max_tokens=16,
            temperature=0.0,
            enable_thinking=False,
        )
        return {"ok": True, "model": self._s.llm_model, "reply": content.strip()}
