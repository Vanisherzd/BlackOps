"""Small OpenAI and urllib adapter with contract-safe retry behavior."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openai import APIConnectionError, OpenAI
from openai.types.chat import ChatCompletion

from blackbox.inference.schemas import JsonObject, JsonValue

LOGGER = logging.getLogger(__name__)
DEFAULT_BASE_URL = "http://127.0.0.1:8200"


@dataclass(frozen=True, slots=True)
class HttpResult:
    """Preserve HTTP status separately from transport failures for fallback logic."""

    status: int | None
    body: JsonValue
    error: str | None = None


class InferenceClient:
    """Keep SDK construction and transport policy behind one narrow seam."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("BLACKBOX_INFERENCE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.getenv("BLACKBOX_INFERENCE_API_KEY", "none")
        self.timeout_s = timeout_s or float(os.getenv("BLACKBOX_INFERENCE_TIMEOUT_S", "300"))
        sdk_base_url = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        # SDK retries include HTTP statuses, so they are disabled and connection retries live below.
        self._sdk = OpenAI(
            base_url=sdk_base_url,
            api_key=self.api_key,
            timeout=self.timeout_s,
            max_retries=0,
        )

    def chat_completions(self, **kwargs: JsonValue) -> JsonObject:
        """Return the SDK response as plain data for version-independent parsing."""

        messages = kwargs.get("messages")
        tools = kwargs.get("tools")
        message_count = len(messages) if isinstance(messages, list) else 0
        tool_count = len(tools) if isinstance(tools, list) else 0
        safe_kwargs = {
            key: value for key, value in kwargs.items() if key not in {"messages", "tools"}
        }
        LOGGER.debug(
            "chat completion request messages=%d tools=%d kwargs=%s",
            message_count,
            tool_count,
            safe_kwargs,
        )

        for attempt in range(3):
            try:
                body = {key: value for key, value in kwargs.items() if key != "extra_body"}
                extra_body = kwargs.get("extra_body")
                if isinstance(extra_body, dict):
                    body.update(extra_body)
                response = self._sdk.post(
                    "/chat/completions",
                    cast_to=ChatCompletion,
                    body=body,
                )
                return response.model_dump()
            except APIConnectionError:
                if attempt == 2:
                    raise
                LOGGER.warning("chat completion connection failed; retrying (%d/2)", attempt + 1)
        raise RuntimeError("unreachable connection retry state")

    def get(self, path: str, *, timeout_s: float | None = None) -> HttpResult:
        """Use a direct probe so SDK status retries cannot mask server health."""

        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        timeout = self.timeout_s if timeout_s is None else timeout_s
        for attempt in range(3):
            try:
                with urlopen(request, timeout=timeout) as response:
                    return HttpResult(
                        status=response.status,
                        body=_decode_body(response.read()),
                    )
            except HTTPError as error:
                return HttpResult(
                    status=error.code,
                    body=_decode_body(error.read()),
                    error=f"HTTP {error.code}",
                )
            except (URLError, TimeoutError) as error:
                if attempt == 2:
                    return HttpResult(status=None, body=None, error=str(error))
                LOGGER.warning("GET %s connection failed; retrying (%d/2)", path, attempt + 1)
        return HttpResult(status=None, body=None, error="unreachable connection retry state")


def _decode_body(payload: bytes) -> JsonValue:
    """Decode health text and JSON endpoints through the same transport path."""

    text = payload.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
