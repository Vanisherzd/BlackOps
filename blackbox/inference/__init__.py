"""Stable inference boundary for Blackbox callers."""

from blackbox.inference.gateway import InferenceGateway
from blackbox.inference.schemas import HealthStatus, ReasonResult, ToolCall, Usage


def wait_until_healthy(timeout_s: float = 900, interval_s: float = 5) -> bool:
    """Delay the import so running the health module does not trigger a runpy warning."""

    from blackbox.inference.health import wait_until_healthy as wait

    return wait(timeout_s=timeout_s, interval_s=interval_s)

__all__ = [
    "HealthStatus",
    "InferenceGateway",
    "ReasonResult",
    "ToolCall",
    "Usage",
    "wait_until_healthy",
]
