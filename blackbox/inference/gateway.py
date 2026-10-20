"""Stable inference gateway over the OpenAI-compatible vLLM surface."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from typing import Protocol

from blackbox.inference.client import HttpResult, InferenceClient
from blackbox.inference.schemas import (
    HealthStatus,
    JsonObject,
    JsonValue,
    ReasonResult,
    ToolCall,
    Usage,
)

_TOOL_BLOCK = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FUNCTION_BLOCK = re.compile(r"<function=([^>\s]+)>(.*?)</function>", re.DOTALL)
_PARAMETER = re.compile(r"<parameter=([^>\s]+)>(.*?)</parameter>", re.DOTALL)


class GatewayClient(Protocol):
    """Allow focused tests and alternate transports without exposing the SDK."""

    def chat_completions(self, **kwargs: JsonValue) -> JsonObject: ...

    def get(self, path: str, *, timeout_s: float | None = None) -> HttpResult: ...


class InferenceGateway:
    """Normalize request policy and vLLM response variants for all callers."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        client: GatewayClient | None = None,
    ) -> None:
        self._client = client or InferenceClient(base_url=base_url)
        self._model = model or os.getenv("BLACKBOX_INFERENCE_MODEL", "bolt")

    def reason(
        self,
        messages: Sequence[Mapping[str, JsonValue]],
        tools: Sequence[Mapping[str, JsonValue]] | None = None,
        reasoning_effort: str = "medium",
        max_tokens: int | None = None,
        temperature: float = 0.0,
        tool_choice: JsonValue = "auto",
        response_format: JsonObject | None = None,
        extra: JsonObject | None = None,
    ) -> ReasonResult:
        """Make one model call and return a stable result without hiding truncation."""

        thinking, default_tokens = _reasoning_options(reasoning_effort)
        extra_body = dict(extra or {})
        existing_template = extra_body.get("chat_template_kwargs")
        template_kwargs = dict(existing_template) if isinstance(existing_template, dict) else {}
        template_kwargs.update(thinking)
        extra_body["chat_template_kwargs"] = template_kwargs

        request: dict[str, JsonValue] = {
            "model": self._model,
            "messages": [dict(message) for message in messages],
            "max_tokens": default_tokens if max_tokens is None else max_tokens,
            "temperature": temperature,
            "extra_body": extra_body,
        }
        if tools:
            request["tools"] = [dict(tool) for tool in tools]
            request["tool_choice"] = tool_choice
        if response_format is not None:
            request["response_format"] = response_format

        started = time.perf_counter()
        raw = self._client.chat_completions(**request)
        latency_ms = round((time.perf_counter() - started) * 1000)
        return _normalize_response(raw, latency_ms)

    def health(self) -> HealthStatus:
        """Probe vLLM directly and fall back for versions without `/health`."""

        started = time.perf_counter()
        result = self._client.get("/health", timeout_s=5.0)
        if result.status == 404:
            result = self._client.get("/v1/models", timeout_s=5.0)
        latency_ms = round((time.perf_counter() - started) * 1000)
        ok = result.status is not None and 200 <= result.status < 300
        error = None if ok else result.error or _status_error(result.status)
        return HealthStatus(ok=ok, status=result.status, latency_ms=latency_ms, error=error)

    def models(self) -> list[str]:
        """List served model identifiers, returning an empty list on malformed data."""

        result = self._client.get("/v1/models")
        if result.status != 200 or not isinstance(result.body, dict):
            return []
        data = result.body.get("data")
        if not isinstance(data, list):
            return []
        models: list[str] = []
        for entry in data:
            if isinstance(entry, dict):
                identifier = entry.get("id")
                if isinstance(identifier, str):
                    models.append(identifier)
        return models


def _reasoning_options(effort: str) -> tuple[JsonObject, int]:
    """Keep effort validation and token defaults inseparable."""

    match effort:
        case "none" | "low":
            return {"enable_thinking": False}, 1024
        case "medium":
            return {"enable_thinking": True, "medium_effort": True}, 4096
        case "high":
            return {"enable_thinking": True}, 8192
        case _:
            raise ValueError(f"unsupported reasoning_effort: {effort}")


def _normalize_response(raw: JsonObject, latency_ms: int) -> ReasonResult:
    """Parse only the first OpenAI choice because the gateway requests one answer."""

    choices = raw.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message_value = choice.get("message")
    message = message_value if isinstance(message_value, dict) else {}
    content_value = message.get("content")
    content = content_value if isinstance(content_value, str) else ""
    reasoning_value = message.get("reasoning_content") or message.get("reasoning")
    reasoning = reasoning_value if isinstance(reasoning_value, str) and reasoning_value else None
    tool_calls = _parse_tool_calls(message.get("tool_calls"))
    if not tool_calls and "<tool_call>" in content:
        content, tool_calls = _salvage_tool_calls(content)

    finish_value = choice.get("finish_reason")
    finish_reason = finish_value if isinstance(finish_value, str) else ""
    usage_value = raw.get("usage")
    usage_data = usage_value if isinstance(usage_value, dict) else {}
    usage = Usage(
        prompt_tokens=_integer(usage_data.get("prompt_tokens")),
        completion_tokens=_integer(usage_data.get("completion_tokens")),
        total_tokens=_integer(usage_data.get("total_tokens")),
    )
    model_value = raw.get("model")
    model = model_value if isinstance(model_value, str) else ""
    return ReasonResult(
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        truncated=finish_reason == "length",
        reasoning_only=bool(reasoning) and not content and not tool_calls,
        usage=usage,
        latency_ms=latency_ms,
        model=model,
        raw=raw,
    )


def _parse_tool_calls(value: JsonValue) -> list[ToolCall]:
    """Normalize native OpenAI tool calls and preserve malformed arguments."""

    if not isinstance(value, list):
        return []
    parsed: list[ToolCall] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        function_value = item.get("function")
        function = function_value if isinstance(function_value, dict) else {}
        name_value = function.get("name")
        if not isinstance(name_value, str):
            continue
        arguments, raw_arguments = _parse_arguments(function.get("arguments"))
        identifier = item.get("id")
        parsed.append(
            ToolCall(
                id=identifier if isinstance(identifier, str) else f"call_{index}",
                name=name_value,
                arguments=arguments,
                raw=raw_arguments,
            )
        )
    return parsed


def _parse_arguments(value: JsonValue) -> tuple[JsonObject, str | None]:
    """Keep invalid argument strings usable without retrying the model."""

    if isinstance(value, dict):
        return value, None
    if not isinstance(value, str):
        return {}, None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {"_raw": value}, value
    if isinstance(decoded, dict):
        return decoded, None
    return {"_raw": value}, value


def _salvage_tool_calls(content: str) -> tuple[str, list[ToolCall]]:
    """Recover documented model-emitted tool blocks when vLLM's parser misses."""

    salvaged: list[ToolCall] = []
    for index, match in enumerate(_TOOL_BLOCK.finditer(content)):
        raw_block = match.group(0)
        inner = match.group(1).strip()
        call = _salvage_json_call(inner, raw_block, index) or _salvage_xml_call(
            inner, raw_block, index
        )
        if call is not None:
            salvaged.append(call)
    return _TOOL_BLOCK.sub("", content).strip(), salvaged


def _salvage_json_call(inner: str, raw: str, index: int) -> ToolCall | None:
    """Accept the compact JSON tool-call form emitted by some templates."""

    try:
        value = json.loads(inner)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return None
    arguments, _ = _parse_arguments(value.get("arguments"))
    return ToolCall(id=f"salvaged_{index}", name=value["name"], arguments=arguments, raw=raw)


def _salvage_xml_call(inner: str, raw: str, index: int) -> ToolCall | None:
    """Parse multiline function parameters without assuming their value syntax."""

    function_match = _FUNCTION_BLOCK.search(inner)
    if function_match is None:
        return None
    arguments = {
        parameter.group(1): parameter.group(2).strip()
        for parameter in _PARAMETER.finditer(function_match.group(2))
    }
    return ToolCall(
        id=f"salvaged_{index}",
        name=function_match.group(1),
        arguments=arguments,
        raw=raw,
    )


def _integer(value: JsonValue) -> int:
    """Treat absent or malformed usage fields as the contract's zero value."""

    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _status_error(status: int | None) -> str:
    """Give health failures a useful message even when no body was available."""

    return "connection failed" if status is None else f"HTTP {status}"
