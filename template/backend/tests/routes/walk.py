"""Every route an app serves, read off the app rather than listed.

Imports nothing about identity or the example resource, so every suite that walks the routes
keeps working in a project that replaced either.
"""

import re
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI

UNKNOWN = "does-not-exist"
"""What a `{path_param}` becomes when a route is driven generically."""


def generated_by_fastapi(app: FastAPI) -> frozenset[str]:
    """The paths FastAPI adds itself -- the schema and the docs -- read off the app, as exact
    paths and never a prefix, so they follow when a project renames or turns them off."""
    named = (
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url,
    )
    return frozenset(one for one in named if one)


def routes_of(app: FastAPI) -> list[tuple[str, str]]:
    """Every route this app declares, as `(method, path)` with the parameters filled in.

    Reaches routes inside included routers, which Starlette 1.6 leaves in place as an
    `_IncludedRouter` rather than flattening into `app.routes`. Skips a route with no HTTP
    methods, such as a websocket, and does not see into a mount whose application is not a
    router; a caller that must cover every surface checks for both.
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

    A container is anything that has `routes`; everything else is an endpoint, whatever its
    shape, so a websocket is yielded rather than dropped.
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
