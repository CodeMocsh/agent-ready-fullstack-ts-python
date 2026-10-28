"""What the process says about identity at startup: whether the seam it serves with
authenticates, and whether serving everybody is deliberate.

Driven through a real `TestClient` so the lifespan runs, because a test that called the
reporting function directly would keep passing after somebody stopped calling it.
`docs/adr/template/0004`.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.deployment import ACKNOWLEDGED_ENV
from app.identity import principal_for
from app.identity.shared import SENTINEL_TENANT
from app.main import create_app
from tests.conftest import Logged
from tests.identity.doubles import refusing_async


def identity_lines(logged: Logged) -> list[dict[str, Any]]:
    """What one startup wrote about identity, with its level."""
    return [one for one in logged() if one["message"].startswith("identity:")]


def test_a_deployment_that_authenticates_nothing_is_warned_at_every_boot(
    logged: Logged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default build is a correct default *and* an indistinguishable one: from outside, a
    service that meant to have authentication and never got it looks exactly like this one.

    Boot is the moment somebody is watching, so it is where the difference gets said. This test
    is what stops the line being deleted by the first person who finds it noisy — and it asserts
    the message names the file to edit, because a warning nobody can act on is decoration.
    """
    monkeypatch.delenv(ACKNOWLEDGED_ENV, raising=False)

    with TestClient(create_app()):
        pass

    said = identity_lines(logged)
    assert len(said) == 1, said
    assert said[0]["severity"] == "WARNING"
    assert "app/identity/__init__.py" in said[0]["message"]
    assert said[0]["tenant"] == SENTINEL_TENANT


def test_a_deployment_that_says_it_is_deliberate_is_told_rather_than_warned(
    logged: Logged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serving everybody on purpose is a real thing to be, and a warning that cannot be
    acknowledged is one a deployment learns to mute — which costs the deployment that needed to
    hear it.

    The fact is still stated, at `INFO`. Silencing is never total, because "does this
    authenticate?" is the first question asked of a service that turns out to have leaked, and
    the log has to answer it either way.
    """
    monkeypatch.setenv(ACKNOWLEDGED_ENV, "1")

    with TestClient(create_app()):
        pass

    said = identity_lines(logged)
    assert len(said) == 1, said
    assert said[0]["severity"] == "INFO"
    assert ACKNOWLEDGED_ENV in said[0]["message"]


@pytest.mark.parametrize("denial", ["0", "false", "no", "off", " "])
def test_a_spelling_of_no_does_not_acknowledge_anything(
    logged: Logged, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    """`UNAUTHENTICATED_IS_INTENTIONAL=0` silencing the warning is the opposite of what somebody
    typing it meant, and the only way they would find out is by not being told."""
    monkeypatch.setenv(ACKNOWLEDGED_ENV, denial)

    with TestClient(create_app()):
        pass

    assert identity_lines(logged)[0]["severity"] == "WARNING"


def test_a_deployment_that_verifies_is_neither_warned_nor_nagged(
    logged: Logged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third direction, and the one that decides whether any of this is worth keeping. A
    project that replaced the stub must stop being warned without touching a flag, or the
    warning outlives the problem and becomes the noise it was supposed to avoid.

    The resolver here is `async`, because that is what a replacement is written as — and a
    synchronous stand-in is what let this pass while the opposite was true.
    """
    monkeypatch.delenv(ACKNOWLEDGED_ENV, raising=False)
    app = create_app()
    app.dependency_overrides[principal_for] = refusing_async

    with TestClient(app):
        pass

    said = identity_lines(logged)
    assert len(said) == 1, said
    assert said[0]["severity"] == "INFO"
    assert "verified" in said[0]["message"]
