"""Fixtures for the tier in this directory. Everything here needs the otel-lgtm viewer.

`make observe-test` starts one on ephemeral ports and sets `OBSERVE_GRAFANA_URL` and
`OBSERVE_OTLP_URL`. Unset, the fixtures raise: a tier that skipped would read as one that
passed.
"""

import os

import pytest


def required(variable: str) -> str:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise RuntimeError(
            f"{variable} is unset, and this suite needs a running otel-lgtm. Run "
            f"`make observe-test`, which starts one and sets it."
        )
    return value


@pytest.fixture(scope="module")
def grafana() -> str:
    return required("OBSERVE_GRAFANA_URL")


@pytest.fixture(scope="module")
def otlp() -> str:
    return required("OBSERVE_OTLP_URL")
