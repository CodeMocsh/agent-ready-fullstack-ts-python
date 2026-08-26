"""The seam itself: what the stub answers, and what a deployment running it is told.

`tests/routes/test_guarantee.py` asserts that every route *reaches* this. These assert what
happens once they get here, and what the process says out loud about it at startup.

**Every seam is substituted in both shapes.** A replacement that verifies a credential reaches
a session store, so `async def` is what one is actually written as — and a suite that only ever
swapped in a synchronous seam reported a fully authenticating deployment as authenticating
nothing, green, on every boot.
"""

import logging

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.identity import (
    SENTINEL_TENANT,
    Unauthenticated,
    resolved_without_a_credential,
    tenant_for,
)
from app.main import create_app
from app.wiring import ACKNOWLEDGED_ENV
from tests.doubles import failing, refusing, refusing_async, resolving_async

SEAM = "app.identity.tenant_for"
"""What the probe reads. Patched there rather than where it is imported, because
`resolved_without_a_credential` looks it up in its own module at call time."""


def test_the_stub_resolves_every_request_to_the_sentinel() -> None:
    """A deployment with no login is one where every row belongs to a single named tenant.

    Named rather than empty or null, so the eventual move to real tenancy is an unambiguous
    `UPDATE ... WHERE tenant_id = 'default'` rather than a hunt for empty strings.
    """
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    assert tenant_for(request) == SENTINEL_TENANT


def test_a_refusal_is_declared_here_rather_than_by_whoever_replaces_the_stub() -> None:
    """`Unauthenticated` lives in `app/identity.py` and `app/main.py` already answers it with a
    `401`, so a replacement cannot ship the refusal and forget the answer — which would return
    `500` to every request it meant to refuse, and a `500` is what a client retries."""
    with pytest.raises(Unauthenticated, match="nobody"):
        raise Unauthenticated("nobody")


def test_the_seam_takes_a_request_so_a_replacement_has_something_to_read() -> None:
    """The parameter is underscored because this implementation has no use for it. Anything
    replacing it reads a header, a cookie or a session from it — and FastAPI injects on the
    annotation rather than the name, so the annotation is the part that has to survive."""
    assert tenant_for.__annotations__["_request"] is Request
    assert tenant_for.__annotations__["return"] is str


async def test_the_probe_reports_the_tenant_a_bare_request_resolves_to() -> None:
    """What the startup message is decided from, asserted on its own.

    A property rather than a flag: *if a request carrying nothing resolves to a tenant, this
    deployment authenticates nobody.* True of any implementation, so it cannot go stale the way
    an `AUTHENTICATES = False` constant beside the seam would.
    """
    assert await resolved_without_a_credential() == SENTINEL_TENANT


async def test_the_probe_is_silent_when_a_synchronous_seam_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SEAM, refusing)

    assert await resolved_without_a_credential() is None


async def test_the_probe_is_silent_when_an_async_seam_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape a real resolver takes, and the one this got wrong.

    Calling an `async def` returns a coroutine rather than raising, so the refusal never
    surfaced and the coroutine was read as a tenant — reporting a deployment that authenticates
    perfectly as one that authenticates nobody, forever.
    """
    monkeypatch.setattr(SEAM, refusing_async)

    assert await resolved_without_a_credential() is None


async def test_an_async_seam_that_hands_out_a_tenant_is_still_reported_as_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of awaiting it: a coroutine must not be *silence* either. An `async` seam
    that resolves everybody is a deployment that authenticates nothing, and the tenant reported
    has to be the one it handed out rather than the object that carried it."""
    monkeypatch.setattr(SEAM, resolving_async)

    assert await resolved_without_a_credential() == "an-async-tenant"


async def test_a_seam_that_breaks_in_its_own_way_is_silence_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anything other than handing out a tenant is a resolver that did not serve this request,
    which is all the probe asks. Quiet is the safe default: a false negative rather than a
    deployment warned about a seam that works."""
    monkeypatch.setattr(SEAM, failing)

    assert await resolved_without_a_credential() is None


def identity_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What one startup wrote about identity, with its level.

    Driven through a real `TestClient` so the lifespan runs, because a test that called the
    reporting function directly would keep passing after somebody stopped calling it.
    """
    return [
        f"{one.levelname} {one.getMessage()}"
        for one in caplog.records
        if one.getMessage().startswith("identity:")
    ]


def test_a_deployment_that_authenticates_nothing_is_warned_at_every_boot(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default build is a correct default *and* an indistinguishable one: from outside, a
    service that meant to have authentication and never got it looks exactly like this one.

    Boot is the moment somebody is watching, so it is where the difference gets said. This test
    is what stops the line being deleted by the first person who finds it noisy — and it asserts
    the message names the file to edit, because a warning nobody can act on is decoration.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(ACKNOWLEDGED_ENV, raising=False)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(create_app()):
        pass

    said = identity_lines(caplog)
    assert len(said) == 1, said
    assert said[0].startswith("WARNING")
    assert "app/identity.py" in said[0]
    assert SENTINEL_TENANT in said[0]


def test_a_deployment_that_says_it_is_deliberate_is_told_rather_than_warned(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serving everybody on purpose is a real thing to be, and a warning that cannot be
    acknowledged is one a deployment learns to mute — which costs the deployment that needed to
    hear it.

    The fact is still stated, at `INFO`. Silencing is never total, because "does this
    authenticate?" is the first question asked of a service that turns out to have leaked, and
    the log has to answer it either way.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(ACKNOWLEDGED_ENV, "1")

    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(create_app()):
        pass

    said = identity_lines(caplog)
    assert len(said) == 1, said
    assert said[0].startswith("INFO")
    assert ACKNOWLEDGED_ENV in said[0]


@pytest.mark.parametrize("denial", ["0", "false", "no", "off", " "])
def test_a_spelling_of_no_does_not_acknowledge_anything(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    """`UNAUTHENTICATED_IS_INTENTIONAL=0` silencing the warning is the opposite of what somebody
    typing it meant, and the only way they would find out is by not being told."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(ACKNOWLEDGED_ENV, denial)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(create_app()):
        pass

    assert identity_lines(caplog)[0].startswith("WARNING")


def test_a_deployment_that_verifies_is_neither_warned_nor_nagged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third direction, and the one that decides whether any of this is worth keeping. A
    project that replaced the seam must stop being warned without touching a flag, or the
    warning outlives the problem and becomes the noise it was supposed to avoid.

    The seam here is `async`, because that is what a replacement is written as — and a
    synchronous stand-in is what let this pass while the opposite was true.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(ACKNOWLEDGED_ENV, raising=False)
    monkeypatch.setattr(SEAM, refusing_async)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(create_app()):
        pass

    said = identity_lines(caplog)
    assert len(said) == 1, said
    assert said[0].startswith("INFO")
    assert "verified" in said[0]
