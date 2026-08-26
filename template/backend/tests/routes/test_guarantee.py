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
from typing import Any, NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app.identity import tenant_for
from app.main import create_app
from tests.doubles import REFUSAL, refusing

UNKNOWN = "does-not-exist"
"""What a `{path_param}` becomes when a route is driven generically. It never reaches a
handler: the refusal is raised resolving the router's dependency, before anything looks at a
path parameter or a body."""

ANY_BODY: dict[str, str] = {}
"""Sent to every route, including the ones that would reject it. A body that fails validation
would be a `422`, and a `422` here would mean the tenant was resolved first — so an empty
object is deliberately the wrong shape for every route that takes one."""


def generated_by_fastapi(app: FastAPI) -> frozenset[str]:
    """The paths FastAPI adds itself, asked of the app rather than spelled here.

    Filtered rather than listed as public, because nobody in a generated project writes them
    and naming them beside a route somebody did write invites the two to be read as the same
    kind of decision. A project that does not want its schema readable turns it off at
    `create_app`, and this follows without an edit.

    **Exact paths, and never a prefix.** This was `startswith(("/openapi", "/docs", "/redoc"))`,
    which quietly exempted `/docs-internal` and `/openapi-status` -- licence by spelling, and
    the very thing the `PUBLIC_ROUTES` docstring below refuses. Reading the four values off the
    app is both exact and correct when a project renames them.
    """
    named = (
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url,
    )
    return frozenset(one for one in named if one)


PUBLIC_ROUTES: tuple[tuple[str, str], ...] = (("GET", "/health"),)
"""Every route allowed to answer without resolving a tenant, spelled exactly.

Exact pairs and never a prefix. A prefix would hand the exemption to every future route that
happened to be spelled that way, which is the kind of licence that grows quietly — the next one
has to be added here and argued for in review.

The list earns its keep in both directions: `test_no_route_outside_the_public_list_...` fails
when a route escapes into it, and `test_the_public_list_is_exactly_...` fails when a route is
deleted and leaves its licence behind.
"""


def routes_of(app: FastAPI) -> list[tuple[str, str]]:
    """Every route this app declares, as `(method, path)` with the parameters filled in.

    **`include_router` does not flatten, and reading `app.routes` alone finds nothing.** Since
    Starlette 1.6 an included router is left in place as an `_IncludedRouter`, which carries
    neither `methods` nor `routes` — so a walk that asks only those two questions skips every
    route in the application and returns an empty list. Every assertion below then passes
    against nothing, which is why `test_the_walk_finds_the_routes_this_app_actually_declares`
    exists: it caught exactly this, on the first run, in a suite that was otherwise green.
    The routes are reached through `original_router`, and the prefix handed to `include_router`
    through `include_context` — the one the router was built with is already in the path.

    A route with no HTTP methods is skipped here rather than guessed at, and
    `test_no_route_shape_escapes_being_driven` refuses one instead — a websocket cannot be
    driven as `(method, path)`, and dropping it quietly is how a guarantee stops covering a
    route without saying so.

    It does **not** see into a mount whose application is not a router — Starlette answers `[]`
    there, indistinguishable from an empty router — so
    `test_every_mount_is_one_this_walk_can_see_into` is what stops a surface being added out of
    this function's reach.
    """
    built: list[tuple[str, str]] = []
    for path, route in endpoints_of(app):
        methods: set[str] | None = getattr(route, "methods", None)
        if methods is None:
            continue
        filled = re.sub(r"\{[^}]+\}", UNKNOWN, path)
        built.extend((method, filled) for method in sorted(methods - {"HEAD", "OPTIONS"}))
    return sorted(built)


def endpoints_of(app: FastAPI) -> Iterator[tuple[str, Any]]:
    """Every route that ends in a handler, as `(path, route)` with the prefixes composed.

    The walk itself, kept apart from `routes_of` because two questions are asked of it: what a
    caller can reach, and what the handler behind it does. The second needs the route object.

    **A container is one that has `routes`, not one that lacks `methods`.** Asking for `methods`
    first looked equivalent and was not: an `APIWebSocketRoute` has neither, so it was treated
    as a container, descended into for the `()` it does not have, and dropped -- the one route
    shape the guarantee never saw. Asking what a thing *contains* is the question that
    distinguishes them, and it leaves every endpoint shape in the walk whether or not this
    file knows how to drive it. `test_no_route_shape_escapes_being_driven` is what refuses the
    ones it cannot.
    """
    generated = generated_by_fastapi(app)
    pending: list[tuple[str, object]] = [("", one) for one in app.routes]
    while pending:
        prefix, route = pending.pop()
        carrier = getattr(route, "original_router", route)
        context = getattr(route, "include_context", None)
        under = prefix + str(getattr(context, "prefix", ""))
        nested = getattr(carrier, "routes", None)
        if nested is not None:
            pending.extend((under, one) for one in nested)
            continue
        path = under + str(getattr(carrier, "path", ""))
        if path not in generated:
            yield path, carrier


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
    found = routes_of(create_app())

    assert ("GET", "/tasks") in found
    assert ("DELETE", f"/tasks/{UNKNOWN}") in found
    assert ("GET", "/health") in found


def test_no_route_outside_the_public_list_answers_without_a_tenant(refused: Refusing) -> None:
    """The guarantee. A new route inherits it by being declared on `router`; one that does not
    reach the seam at all shows up here as a route that answered."""
    escaped = [one for one in answering_without_a_tenant(refused) if one not in PUBLIC_ROUTES]

    assert escaped == [], (
        f"{escaped} answered a request whose tenant could not be resolved. Declare the route on "
        f"`router` in app/routes.py, which carries the dependency -- or, if it reads nothing "
        f"that belongs to anybody, put it on `public_router` and name it in PUBLIC_ROUTES."
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
    answer = refused.client.get("/tasks")

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
