"""Health polling and CLI entry point for deployment checks."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict

from blackbox.inference.gateway import InferenceGateway


def wait_until_healthy(timeout_s: float = 900, interval_s: float = 5) -> bool:
    """Wait within a bounded deadline so deploy automation cannot hang forever."""

    gateway = InferenceGateway()
    deadline = time.monotonic() + timeout_s
    while True:
        if gateway.health().ok:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval_s, remaining))


def main() -> int:
    """Print one machine-readable final status for shell health checks."""

    parser = argparse.ArgumentParser(description="Probe the inference gateway")
    parser.add_argument("--wait", type=float, default=0, metavar="N")
    args = parser.parse_args()
    gateway = InferenceGateway()
    if args.wait > 0:
        wait_until_healthy(timeout_s=args.wait)
    status = gateway.health()
    print(json.dumps(asdict(status), sort_keys=True))
    return 0 if status.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
