"""The identity stub: what it answers, and what a deployment running it is told.

`tests/routes/test_guarantee.py` asserts that every route *reaches* this. These assert what
happens once they get here, and what the process says out loud about it at startup.

**Every resolver is substituted in both shapes.** A replacement that verifies a credential reaches
a session store, so `async def` is what one is actually written as — and a suite that only ever
swapped in a synchronous resolver reported a fully authenticating deployment as authenticating
nothing, green, on every boot.
"""

from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.deployment import ACKNOWLEDGED_ENV
from app.identity import principal_for
from app.identity.shared import (
    SENTINEL_PRINCIPAL,
    SENTINEL_TENANT,
    Actor,
    Principal,
    resolved_without_a_credential,
)
from app.main import create_app
from app.routes import tenant
from tests.conftest import Logged
from tests.identity.doubles import (
    AN_ASYNC_PRINCIPAL,
    failing,
    refusing,
    refusing_async,
    resolving_async,
)
from tests.widgets import router as widgets_router


def test_the_stub_resolves_every_request_to_the_sentinel() -> None:
    """A deployment with no login is one where every row belongs to a single named tenant, and
    every action to a single named actor.

    Named rather than empty or null, so the eventual move to real tenancy is an unambiguous
    `UPDATE ... WHERE tenant_id = 'default'` rather than a hunt for empty strings.
    """
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    assert principal_for(request) == SENTINEL_PRINCIPAL


def test_the_seam_takes_a_request_so_a_replacement_has_something_to_read() -> None:
    """The parameter is underscored because this implementation has no use for it. Anything
    replacing it reads a header, a cookie or a session from it — and FastAPI injects on the
    annotation rather than the name, so the annotation is the part that has to survive."""
    assert principal_for.__annotations__["_request"] is Request
    assert principal_for.__annotations__["return"] is Principal


async def test_the_probe_reports_the_principal_a_bare_request_resolves_to() -> None:
    """What the startup message is decided from, asserted on its own.

    A property rather than a flag: *if a request carrying nothing resolves to a principal, this
    deployment authenticates nobody.* True of any implementation, so it cannot go stale the way
    an `AUTHENTICATES = False` constant beside the seam would.
    """
    assert await resolved_without_a_credential(principal_for) == SENTINEL_PRINCIPAL


async def test_the_probe_is_silent_when_a_synchronous_resolver_refuses() -> None:
    assert await resolved_without_a_credential(refusing) is None


async def test_the_probe_is_silent_when_an_async_resolver_refuses() -> None:
    """The shape a real resolver takes, and the one this got wrong.

    Calling an `async def` returns a coroutine rather than raising, so the refusal never
    surfaced and the coroutine was read as a principal — reporting a deployment that
    authenticates perfectly as one that authenticates nobody, forever.
    """
    assert await resolved_without_a_credential(refusing_async) is None


async def test_an_async_resolver_that_hands_out_a_principal_is_still_reported_as_one() -> None:
    """The other half of awaiting it: a coroutine must not be *silence* either. An `async` resolver
    that resolves everybody is a deployment that authenticates nothing, and the principal
    reported has to be the one it handed out rather than the object that carried it."""
    assert await resolved_without_a_credential(resolving_async) == AN_ASYNC_PRINCIPAL


async def test_a_resolver_that_breaks_in_its_own_way_is_silence_too() -> None:
    """Anything other than handing out a principal is a resolver that did not serve this
    request, which is all the probe asks. Quiet is the safe default: a false negative rather
    than a deployment warned about a resolver that works."""
    assert await resolved_without_a_credential(failing) is None


def identity_lines(logged: Logged) -> list[dict[str, Any]]:
    """What one startup wrote about identity, with its level.

    Driven through a real `TestClient` so the lifespan runs, because a test that called the
    reporting function directly would keep passing after somebody stopped calling it.
    """
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


def test_a_route_under_the_tenant_requirement_names_the_principal_the_seam_resolved(
    logged: Logged,
) -> None:
    """The widgets carry whatever the shipped tenant requirement carries, so a requirement that
    reaches the seam without naming its principal fails here, in a project with no tenant route
    yet. `docs/adr/template/0011`."""
    app = create_app()
    app.include_router(widgets_router, dependencies=tenant.router.dependencies)

    def named(request: Request) -> Principal:
        tenant_id = request.headers["x-test-tenant"]
        return Principal(
            tenant_id=tenant_id,
            actor=Actor(issuer="a-test", subject=f"actor-in-{tenant_id}", name="An actor"),
        )

    app.dependency_overrides[principal_for] = named
    with TestClient(app) as client:
        logged()
        client.get("/widgets", headers={"x-test-tenant": "tenant-a"})
        client.get("/widgets", headers={"x-test-tenant": "tenant-b"})

    lines = [line for line in logged() if line["message"] == "request completed"]

    assert [(line["tenant_id"], line["actor.issuer"], line["actor.subject"]) for line in lines] == [
        ("tenant-a", "a-test", "actor-in-tenant-a"),
        ("tenant-b", "a-test", "actor-in-tenant-b"),
    ]


def test_a_request_the_seam_refuses_is_recorded_only_on_its_request_line(logged: Logged) -> None:
    """It has no tenant to record an audit event in, and anybody can send one. Its `request
    completed` line, with a `401` and a null tenant, is the record. `docs/adr/template/0017`."""
    app = create_app()
    app.include_router(widgets_router, dependencies=tenant.router.dependencies)
    app.dependency_overrides[principal_for] = refusing
    with TestClient(app) as client:
        logged()
        assert client.get("/widgets").status_code == 401

    [line] = [line for line in logged() if line["message"] == "request completed"]
    assert (line["http.response.status_code"], line["tenant_id"]) == (401, None)
