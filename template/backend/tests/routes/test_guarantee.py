"""The guarantee: a route cannot be committed without resolving a tenant.

Asserted against the routes **read off the app**, never against a list written here. A list is a
second place to remember, and the route that escapes a rule is exactly the one nobody remembered
to add to it — which is the whole failure this file exists to catch.

The seam is substituted for one that refuses, because the implementation that ships resolves
everybody and so cannot tell a guarded route from an unguarded one: under the sentinel both
answer `200`. What is being checked is the wiring, not the sentinel — that every route goes
through whatever a deployment puts in that seam, so replacing it is a change in one place and
not an audit of every handler.
"""

import re
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app.identity import tenant_for
from app.main import create_app
from tests import widgets
from tests.identity.doubles import REFUSAL, refusing
from tests.routes.walk import UNKNOWN, endpoints_of, routes_of

ANY_BODY: dict[str, str] = {}
"""Sent to every route, including the ones that would reject it. A body that fails validation
would be a `422`, and a `422` here would mean the tenant was resolved first — so an empty
object is deliberately the wrong shape for every route that takes one."""


PUBLIC_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/health"),
    ("GET", "/ready"),
    ("POST", "/client-events"),
)
"""Every route allowed to answer without resolving a tenant, spelled exactly.

Exact pairs and never a prefix. A prefix would hand the exemption to every future route that
happened to be spelled that way, which is the kind of licence that grows quietly — the next one
has to be added here and argued for in review.

The list earns its keep in both directions: `test_no_route_outside_the_public_list_...` fails
when a route escapes into it, and `test_the_public_list_is_exactly_...` fails when a route is
deleted and leaves its licence behind.
"""


class Refusing(NamedTuple):
    """An app whose seam refuses everything, and a client onto it.

    Both, because a client does not usefully hand back what it drives: `TestClient.app` is typed
    as the ASGI callable it wraps, and the walk below needs the `FastAPI` object to read routes
    off. Keeping the pair is cheaper than casting one back into the other.
    """

    app: FastAPI
    client: TestClient


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch) -> Iterator[Refusing]:
    """An identity seam that refuses everything, driven through a real client.

    `dependency_overrides` rather than monkeypatching the module, because the router holds the
    function object taken at import time and a patched module attribute would never reach it.
    The override is resolved per request, which is the path a real deployment's resolver takes.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app()
    app.dependency_overrides[tenant_for] = refusing
    with TestClient(app) as client:
        yield Refusing(app, client)


def answering_without_a_tenant(refused: Refusing) -> list[tuple[str, str]]:
    """Every route that returned something other than a refusal when nothing could be
    resolved."""
    return [
        (method, path)
        for method, path in routes_of(refused.app)
        if refused.client.request(method, path, json=ANY_BODY).status_code != 401
    ]


def test_the_walk_finds_the_routes_this_app_actually_declares() -> None:
    """The three tests below all pass against a `routes_of` that returns nothing, and so does a
    suite where the walk quietly stopped descending into routers. This is what makes their
    silence mean something."""
    app = create_app()
    found = {path for _, path in routes_of(app)}
    declared = {re.sub(r"\{[^}]+\}", UNKNOWN, path) for path in app.openapi()["paths"]}

    assert declared <= found
    assert "/health" in found


def test_no_route_outside_the_public_list_answers_without_a_tenant(refused: Refusing) -> None:
    """The guarantee. A new route inherits it by being included under `app/routes/tenant/`; one
    that does not reach the seam at all shows up here as a route that answered."""
    escaped = [one for one in answering_without_a_tenant(refused) if one not in PUBLIC_ROUTES]

    assert escaped == [], (
        f"{escaped} answered a request whose tenant could not be resolved. Declare the route on "
        f"a router included by app/routes/tenant/, which carries the dependency -- or, if it "
        f"reads nothing that belongs to anybody, put it in app/routes/public.py and name it in "
        f"PUBLIC_ROUTES."
    )


def test_the_public_list_is_exactly_the_routes_that_answer(refused: Refusing) -> None:
    """The other direction, which the test above cannot see. A public route that is renamed or
    deleted leaves a licence behind it, and the next route to be spelled that way inherits an
    exemption nobody granted it."""
    assert sorted(answering_without_a_tenant(refused)) == sorted(PUBLIC_ROUTES)


def test_a_refused_request_says_so_in_the_contract_s_own_shape(refused: Refusing) -> None:
    """A refusal a client can act on: the declared status, the header that names the scheme, and
    an `ErrorBody` rather than whatever a bare exception renders as. Without the handler in
    `app/main.py` this is a `500`, which a client retries."""
    refused.app.include_router(widgets.router, dependencies=[Depends(tenant_for)])

    answer = refused.client.get("/widgets")

    assert answer.status_code == 401
    assert answer.headers["www-authenticate"] == "Bearer"
    assert answer.json() == {"detail": REFUSAL}


def test_the_public_route_still_answers_when_nothing_can_be_resolved(
    refused: Refusing,
) -> None:
    """A liveness probe carries no credential, so an exemption that stopped working would take
    the deployment out of rotation rather than fail a test. Asserted on the body as well as the
    status, since the shell fallback in `app/serve.py` answers `200` too."""
    answer = refused.client.get("/health")

    assert answer.status_code == 200
    assert answer.json() == {"status": "ok"}


def test_no_route_shape_escapes_being_driven() -> None:
    """Every route the walk finds must be one the guarantee can actually drive.

    `routes_of` builds `(method, path)` pairs, so a route with no HTTP methods — a websocket —
    is one it silently drops, and every assertion here would pass over it while it served. A
    project that adds one has to guard it deliberately: the dependency on `router` does apply
    to a websocket, but nothing in this file proves it, and a guarantee that quietly stops
    covering a route is worse than one that says it does not.
    """
    undriveable = [
        path
        for path, route in endpoints_of(create_app())
        if getattr(route, "methods", None) is None
    ]

    assert undriveable == [], (
        f"{undriveable} has no HTTP methods -- a websocket, or something like one -- so the "
        f"tests here cannot drive it and silently do not cover it. Assert its guard where it "
        f"is declared, and name it here."
    )


def test_every_mount_is_one_this_walk_can_see_into() -> None:
    """A mounted application whose routes cannot be enumerated is a surface no test above
    reaches.

    Starlette reports `Mount.routes` as `[]` for anything that is not a router -- a bare ASGI
    callable, a `StaticFiles`, another framework -- and the walk cannot tell that apart from a
    router holding nothing. Verified by mounting one: `routes_of` returns nothing for it and
    every assertion above stays green while the surface answers.
    """
    mounted = [route for route in create_app().routes if isinstance(route, Mount)]
    opaque = [route.path for route in mounted if not route.routes]

    assert opaque == [], (
        f"{opaque} is mounted on something this walk cannot enumerate, so the routes behind it "
        f"are checked by nothing here. Mount a router, or assert that surface where it is built."
    )
