"""Deterministic stdlib OpenAI-compatible server for local gateway tests."""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from blackbox.inference.schemas import JsonObject, JsonValue

LAST_REQUEST_BODY: JsonObject = {}


class MockHandler(BaseHTTPRequestHandler):
    """Serve only the endpoints needed to exercise the gateway contract."""

    server_version = "BlackboxMock/1.0"

    def do_GET(self) -> None:
        """Return deterministic health and model discovery responses."""

        path = self.path.split("?", maxsplit=1)[0]
        if path == "/health":
            self._send(200, b"ok", "text/plain")
            return
        if path == "/v1/models":
            self._send_json(200, {"data": [{"id": "bolt"}]})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        """Generate a scripted completion from the request body alone."""

        if self.path.split("?", maxsplit=1)[0] != "/v1/chat/completions":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return
        if not isinstance(value, dict):
            self._send_json(400, {"error": "request must be an object"})
            return

        LAST_REQUEST_BODY.clear()
        LAST_REQUEST_BODY.update(value)
        self._send_json(200, _completion(value))

    def log_message(self, format: str, *args: JsonValue) -> None:
        """Keep test output deterministic and quiet."""

    def _send_json(self, status: int, body: JsonObject) -> None:
        """Encode JSON responses consistently for SDK parsing."""

        self._send(status, json.dumps(body).encode(), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        """Send complete responses so clients can safely reuse connections."""

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _completion(body: JsonObject) -> JsonObject:
    """Build one deterministic OpenAI completion from the documented branches."""

    messages_value = body.get("messages")
    messages = messages_value if isinstance(messages_value, list) else []
    tools_value = body.get("tools")
    tools = tools_value if isinstance(tools_value, list) else []
    last = messages[-1] if messages and isinstance(messages[-1], dict) else {}
    last_user = _last_user_message(messages)
    tool_calls: list[JsonValue] = []

    if last.get("role") == "tool":
        tool_name = _tool_result_name(last, messages)
        tool_content = last.get("content")
        content_text = tool_content if isinstance(tool_content, str) else ""
        if _system_mentions_finish(messages):
            tool_calls = [_tool_call("finish", _required_arguments(_find_tool(tools, "finish"), last_user))]
            content = ""
        else:
            content = f"FINAL: {tool_name} says {content_text[:60]}"
    elif "MOCK_SALVAGE" in last_user:
        selected = _first_tool(tools)
        name = _tool_name(selected) or "mock_tool"
        arguments = _required_arguments(selected, last_user)
        parameters = "".join(
            f"<parameter={key}>{value}</parameter>" for key, value in arguments.items()
        )
        content = f"<tool_call><function={name}>{parameters}</function></tool_call>"
    else:
        selected = _selected_tool(tools, last_user)
        if selected is not None:
            name = _tool_name(selected) or "mock_tool"
            tool_calls = [_tool_call(name, _required_arguments(selected, last_user))]
            content = ""
        elif "JSON" in last_user:
            content = json.dumps({"status": "ok", "echo": last_user})
        elif "BLACKBOX_BOLT_OK" in last_user:
            content = "BLACKBOX_BOLT_OK"
        else:
            content = f"mock: {last_user}"

    max_tokens = body.get("max_tokens")
    finish_reason = "tool_calls" if tool_calls else "stop"
    if isinstance(max_tokens, int) and max_tokens <= 16:
        content = content[: max(0, max_tokens)]
        finish_reason = "length"

    message: JsonObject = {"role": "assistant", "content": content}
    template_value = body.get("chat_template_kwargs")
    if not isinstance(template_value, dict):
        extra_body = body.get("extra_body")
        template_value = extra_body.get("chat_template_kwargs") if isinstance(extra_body, dict) else {}
    if isinstance(template_value, dict) and template_value.get("enable_thinking") is True:
        message["reasoning_content"] = "mock reasoning"
    if tool_calls:
        message["tool_calls"] = tool_calls

    prompt_text = "".join(_message_content(entry) for entry in messages)
    completion_text = content + ("mock reasoning" if "reasoning_content" in message else "")
    prompt_tokens = len(prompt_text) // 4
    completion_tokens = len(completion_text) // 4
    model = body.get("model")
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model if isinstance(model, str) else "bolt",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _last_user_message(messages: list[JsonValue]) -> str:
    """Find the current user instruction even after assistant/tool turns."""

    for entry in reversed(messages):
        if isinstance(entry, dict) and entry.get("role") == "user":
            content = entry.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _message_content(entry: JsonValue) -> str:
    if not isinstance(entry, dict):
        return ""
    content = entry.get("content")
    return content if isinstance(content, str) else ""


def _selected_tool(tools: list[JsonValue], message: str) -> JsonObject | None:
    """Select an explicitly named tool, or the first tool for a check request."""

    for tool in tools:
        if isinstance(tool, dict):
            name = _tool_name(tool)
            if name and name.lower() in message.lower():
                return tool
    return _first_tool(tools) if "check" in message.lower() else None


def _first_tool(tools: list[JsonValue]) -> JsonObject | None:
    """Return the first structurally usable tool."""

    return next((tool for tool in tools if isinstance(tool, dict)), None)


def _find_tool(tools: list[JsonValue], name: str) -> JsonObject | None:
    """Locate a tool schema by function name."""

    return next(
        (tool for tool in tools if isinstance(tool, dict) and _tool_name(tool) == name),
        None,
    )


def _tool_name(tool: JsonObject | None) -> str | None:
    """Read the OpenAI function name without assuming a complete schema."""

    if tool is None:
        return None
    function = tool.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return name if isinstance(name, str) else None


def _required_arguments(tool: JsonObject | None, message: str) -> JsonObject:
    """Fill required fields predictably so tool-loop tests can execute them."""

    if tool is None:
        return {}
    function = tool.get("function")
    parameters = function.get("parameters") if isinstance(function, dict) else None
    required = parameters.get("required") if isinstance(parameters, dict) else None
    if not isinstance(required, list):
        return {}
    subject_match = re.search(r"\b(?:service|container)\s+([^\s,.!?]+)", message, re.IGNORECASE)
    subject = subject_match.group(1).strip("'\"") if subject_match else None
    return {
        key: subject or key
        for key in required
        if isinstance(key, str)
    }


def _tool_call(name: str, arguments: JsonObject) -> JsonObject:
    """Emit the canonical OpenAI tool-call shape."""

    return {
        "id": "call_mock_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _tool_result_name(last: JsonObject, messages: list[JsonValue]) -> str:
    """Recover a tool name when clients provide only `tool_call_id`."""

    name = last.get("name")
    if isinstance(name, str):
        return name
    call_id = last.get("tool_call_id")
    for entry in reversed(messages[:-1]):
        if not isinstance(entry, dict):
            continue
        calls = entry.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if isinstance(call, dict) and call.get("id") == call_id:
                function = call.get("function")
                if isinstance(function, dict):
                    function_name = function.get("name")
                    if isinstance(function_name, str):
                        return function_name
    return "tool"


def _system_mentions_finish(messages: list[JsonValue]) -> bool:
    """Model the incident loop's explicit finish-tool instruction."""

    for entry in messages:
        if not isinstance(entry, dict) or entry.get("role") != "system":
            continue
        content = entry.get("content")
        if isinstance(content, str) and "finish" in content.lower():
            return True
    return False


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Start an ephemeral daemon server that tests can shut down explicitly."""

    server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def main() -> int:
    """Run the mock until interrupted for manual smoke testing."""

    parser = argparse.ArgumentParser(description="Run the Blackbox inference mock")
    parser.add_argument("--port", type=int, default=8299)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), MockHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
