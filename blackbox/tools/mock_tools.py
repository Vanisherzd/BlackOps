"""Deterministic operational tools make agent behavior testable without a shell."""

from __future__ import annotations

import json
from typing import Literal, TypedDict

from blackbox.inference.schemas import JsonObject
from blackbox.tools.registry import ToolRegistry
from blackbox.tools.schemas import Tool

Scenario = Literal["api_down", "all_healthy", "web_crashloop"]


class ServiceState(TypedDict):
    container: str
    healthy: bool


class ContainerState(TypedDict):
    status: str
    exit_code: int
    restarts: int
    logs: list[str]


class MockWorld:
    """Hold mutable incident state because write tools must have observable effects."""

    def __init__(self) -> None:
        self.services: dict[str, ServiceState] = {}
        self.containers: dict[str, ContainerState] = {}
        self.current_scenario: Scenario = "api_down"
        self.reset()

    def reset(self) -> None:
        """Restore the default outage so tests never leak state into one another."""

        self.scenario("api_down")

    def scenario(self, name: Scenario) -> MockWorld:
        """Load a named fixture and return self for concise test setup."""

        self.current_scenario = name
        self.services = {
            "api": {"container": "api-1", "healthy": False},
            "web": {"container": "web-1", "healthy": True},
        }
        self.containers = {
            "api-1": {
                "status": "exited",
                "exit_code": 1,
                "restarts": 0,
                "logs": ["starting api", "fatal: database connection refused"],
            },
            "web-1": {
                "status": "running",
                "exit_code": 0,
                "restarts": 0,
                "logs": ["web listening on :8080"],
            },
        }
        if name == "all_healthy":
            self.services["api"]["healthy"] = True
            self.containers["api-1"].update(status="running", exit_code=0)
        elif name == "web_crashloop":
            self.services["api"]["healthy"] = True
            self.services["web"]["healthy"] = False
            self.containers["api-1"].update(status="running", exit_code=0)
            self.containers["web-1"].update(
                status="restarting",
                exit_code=137,
                restarts=5,
                logs=["starting web", "OOM: killed process web"],
            )
        return self


def build_registry(world: MockWorld | None = None) -> ToolRegistry:
    """Bind the five baseline tools to one explicit in-memory world."""

    active_world = world or MockWorld()
    registry = ToolRegistry(world=active_world)

    def get_service_health(arguments: JsonObject) -> str:
        service = str(arguments["service"])
        state = active_world.services[service]
        return _json({"service": service, **state})

    def get_container_status(arguments: JsonObject) -> str:
        name = str(arguments["name"])
        state = active_world.containers[name]
        return _json({"name": name, **state, "logs": state["logs"][-1:]})

    def get_recent_logs(arguments: JsonObject) -> str:
        name = str(arguments["name"])
        raw_lines = arguments.get("lines", 20)
        lines = raw_lines if isinstance(raw_lines, int) else 20
        return _json({"name": name, "logs": active_world.containers[name]["logs"][-lines:]})

    def restart_container(arguments: JsonObject) -> str:
        name = str(arguments["name"])
        container = active_world.containers[name]
        container["status"] = "running"
        container["exit_code"] = 0
        container["restarts"] += 1
        if active_world.current_scenario == "api_down":
            for service in active_world.services.values():
                if service["container"] == name:
                    service["healthy"] = True
        return _json({"name": name, "status": container["status"], "restarts": container["restarts"]})

    def delete_volume(arguments: JsonObject) -> str:
        return _json({"deleted": str(arguments["name"])})

    object_schema = {"type": "object"}
    registry.register(
        Tool(
            "get_service_health",
            "Read whether a service is healthy and which container owns it.",
            {
                **object_schema,
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
            get_service_health,
            "read",
        )
    )
    registry.register(
        Tool(
            "get_container_status",
            "Read status, exit code, and restart count for a container.",
            {
                **object_schema,
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            get_container_status,
            "read",
        )
    )
    registry.register(
        Tool(
            "get_recent_logs",
            "Read the most recent in-memory log lines for a container.",
            {
                **object_schema,
                "properties": {
                    "name": {"type": "string"},
                    "lines": {"type": "integer"},
                },
                "required": ["name"],
            },
            get_recent_logs,
            "read",
        )
    )
    registry.register(
        Tool(
            "restart_container",
            "Restart one container without invoking a shell.",
            {
                **object_schema,
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            restart_container,
            "write",
        )
    )
    registry.register(
        Tool(
            "delete_volume",
            "Delete a named volume; baseline policy always denies this tool.",
            {
                **object_schema,
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            delete_volume,
            "destructive",
        )
    )
    return registry


def _json(value: JsonObject) -> str:
    """Keep tool output compact and stable for repeatable model transcripts."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"))
