"""Stand-ins for what a deployment supplies, and the canary no output may carry.

Not a `test_*.py` file, so nothing here is collected. It imports nothing about identity, so a
project that replaced the identity stub keeps every suite that uses it.
"""

from dataclasses import replace
from typing import Any

from app.wiring import TelemetrySettings

CANARY = "canary-6f1e2d-alice@example.com"
"""A value a request carries that no log line, span or metric may. Asserted absent from each."""


def telemetry_settings(**changed: Any) -> TelemetrySettings:
    """What a deployment naming a Collector configures, for tests that export elsewhere."""
    return replace(
        TelemetrySettings(
            endpoint="unused", service="tasks-test", sampling_ratio=1.0, trust_inbound_context=False
        ),
        **changed,
    )
