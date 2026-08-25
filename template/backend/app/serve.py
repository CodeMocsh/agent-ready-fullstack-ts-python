"""Both halves on one origin, for a deployment with no reverse proxy in front of it.

`app.main` serves bare paths and the browser asks for `/api/...`, because one host name has to
carry two things that share path shapes: a `/tasks` page in the frontend router and a `/tasks`
route in this service are the same URL. The prefix is what tells them apart, and something has
to remove it again before FastAPI matches. In development that is the vite proxy; in a
deployment it is a load balancer, or — when there is neither — this module.

**Mounting alone is not enough, and the way it fails is quiet.** Starlette does not run a
mounted application's lifespan, so the service comes up with nothing on `app.state`: no
database, and therefore no substrate check either. Every route then raises where it reaches
for one, which reads as a broken handler rather than as a deployment that never started.
`create_server` delegates to the mounted app's lifespan context, so the one process still has
exactly one database, opened before the first request and closed after the last.

Run it with `uvicorn --factory app.serve:build_server`. A factory rather than a module-level
app because the bundle is read from the environment, and a module that raises on import is a
module no test can reach.

**This module is also the edge, and that is the half worth reading before deploying it.** A
reverse proxy is not only a router: it caps request bodies and sets the response headers that
decide what an injected string is allowed to become. `app.main` behind one inherits both and
declares neither, which is correct there and leaves nothing at all doing it here. So this
module carries what the proxy would have carried, and `app.main` still does not — a service
behind an edge must not send a second, weaker copy of headers that edge already set.

**Prefer a platform that fronts this process to running it as the public edge.** Cloud Run,
Fly, App Runner and an identity-aware proxy all terminate TLS, parse HTTP and absorb the
slow-client attacks a Python process should not be meeting, and they forward cleartext to this
one — so give it `--host 0.0.0.0 --port $PORT` and **no** `--ssl-keyfile`, or the platform's
health check meets a TLS handshake and the deploy never goes green. Serving 443 directly with
`--ssl-keyfile` is the shape to think twice about: it puts the TLS private key in the same
process that resolves client-supplied paths against a directory, and `_within` is then the
only thing standing between the two.

**Whatever terminates TLS, something must.** A session cookie worth setting is `Secure`, and a
browser reached over plaintext discards it — so sign-in fails by returning quietly to the
sign-in screen, with nothing anywhere saying why.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from app.main import create_app
from app.wiring import BundleMissing, build_bundle

INDEX: Final = "index.html"
ASSETS: Final = "assets"
PREFIX: Final = "/api"

MAX_BODY_BYTES: Final = 1_048_576
"""What a request body may weigh before this process refuses to read it.

One megabyte, because the largest thing this API accepts is a task title. Nothing else here
bounds a body — Starlette reads what the transport hands it — so without this the cheapest
request that can hurt this process is the one nobody wrote a route for. Raise it deliberately
for an endpoint that takes an upload, and prefer streaming that endpoint to raising this for
every other one.
"""

CARRY_BODIES: Final = frozenset({"POST", "PUT", "PATCH"})
"""The methods expected to declare a length even when nothing says they are sending one.

The cap reads `content-length`, so a request without one is unmeasured — and refusing that is
keyed on `transfer-encoding` rather than on the method, because a body may ride any of them. A
chunked `DELETE` is a legal request this once waved through for no better reason than that
`DELETE` usually has no body.

This set is the second half of the same rule: under HTTP/1.1 a request with neither header has
no body at all, but that framing is the transport's guarantee rather than ours, so the methods
that normally carry one are asked to say so regardless. An endpoint that streams an upload is a
reason to narrow this set, and to make that endpoint responsible for its own bound.
"""

SECURITY_HEADERS: Final = {
    "content-security-policy": "; ".join(
        (
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "frame-ancestors 'none'",
            "form-action 'self'",
        )
    ),
    "strict-transport-security": "max-age=31536000",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
}
"""What the edge would have set, and therefore what this module sets.

`script-src 'self'` is the one that matters: it is what keeps an injected string from becoming
executable, and a vite build has no inline script for it to break. Keep it that way — if
something one day needs an inline script, give it a nonce or a hash rather than widening this
to `'unsafe-inline'`, which would return the directive to decoration.

**`style-src` is deliberately weaker, and the reason is shadcn.** Radix positions its overlays
with inline `style` attributes, so a strict `style-src` breaks every popover and dialog added
after generation — quietly, as misplacement rather than as an error. Inline CSS cannot
execute, so the concession costs far less than the directive above would.

`strict-transport-security` names no subdomains: `includeSubDomains` from a template is a
promise about hosts this deployment has never seen, and it would take a plaintext sibling on
the same apex down from a header nobody remembers setting. Add it, and `preload`, once you own
every name under the domain and have checked they are all on TLS.

**`cross-origin-opener-policy` is the one to remember when you add sign-in.** It severs
`window.opener`, which is what a popup-based OAuth flow uses to hand its result back — the
popup completes, closes, and the page that opened it is never told, which reads as a sign-in
that did nothing. A redirect flow is unaffected and is the one to prefer. If you need the
popup, this is the line to drop, and dropping it costs the cross-window isolation it buys.
"""

Next = Callable[[Request], Awaitable[Response]]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
"""The ASGI signature, spelled here rather than imported.

These names are Starlette's too, but Starlette is a dependency of FastAPI rather than one this
project declares, and importing from it would put an undeclared package in the import graph of
the half whose bounds are the most deliberate thing in `pyproject.toml`. The protocol is a
specification, not a library's detail, so writing it costs five lines and owes nothing.
"""


def build_server() -> ASGIApp:
    """The server this deployment runs, with the bundle the environment names."""
    return create_server(build_bundle())


def create_server(bundle: Path) -> ASGIApp:
    """One origin: the API under `/api`, and the built frontend under everything else."""
    index = bundle / INDEX
    if not index.is_file():
        raise BundleMissing(
            f"{bundle} holds no {INDEX}, so there is no application to serve. Run `make build` "
            f"and point at the directory it wrote."
        )
    api = create_app()

    @asynccontextmanager
    async def lifespan(_server: FastAPI) -> AsyncGenerator[None, None]:
        async with api.router.lifespan_context(api):
            yield

    server = FastAPI(lifespan=lifespan, openapi_url=None)
    server.middleware("http")(_refuse_oversized)
    server.mount(PREFIX, api)

    @server.get("/{held:path}", include_in_schema=False)
    async def bundled(held: str) -> FileResponse:
        """The file the path names, or the shell so the client router can take the path.

        An asset is exempt from that fallback. Its name carries the build's hash, so a request
        for one the bundle does not hold is a stale document asking for a file that no longer
        exists — and answering it with the shell hands a script tag a page of HTML, which fails
        as a syntax error somewhere unrelated to the deploy that caused it.
        """
        found = _within(bundle, held)
        if found is not None:
            return FileResponse(found)
        if held.startswith(f"{ASSETS}/"):
            raise HTTPException(status_code=404, detail=f"no such asset: {held}")
        return FileResponse(index)

    return _confined(server)


def _confined(app: ASGIApp) -> ASGIApp:
    """`SECURITY_HEADERS` on every response that leaves this process, including the ones it
    never meant to send.

    **Wrapping the finished application rather than registering a middleware**, because
    Starlette puts its own error handler *outside* every user middleware. An unhandled
    exception raises back out through a middleware before it can set anything, and the bare 500
    that handler then writes goes out with no policy on it at all — which is exactly backwards,
    since a response produced by a failure is the one most likely to be carrying something an
    attacker put there. The catch-all below serves every HTML page in the application, so this
    is not a corner: it is the main path, on the day it breaks.

    Written against the ASGI message rather than a `Response`, because at this layer there is
    no response object yet — only the `http.response.start` that every one of them becomes, no
    matter which layer wrote it.

    A header already present is left alone, so a route with a reason to send its own is not
    overruled by a default it never asked for.
    """

    async def confined(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = list(message.get("headers", []))
                named = {name.lower() for name, _ in present}
                message["headers"] = present + [
                    (header.encode(), value.encode())
                    for header, value in SECURITY_HEADERS.items()
                    if header.encode() not in named
                ]
            await send(message)

        await app(scope, receive, sending)

    return confined


async def _refuse_oversized(request: Request, call_next: Next) -> Response:
    """Refuse before reading, which is the only point at which refusing is cheap.

    The declared length is checked rather than the body counted, because a body counted is a
    body already received — and the request worth refusing is exactly the one it would have
    cost something to read.
    """
    declared = request.headers.get("content-length")
    if declared is None:
        if request.headers.get("transfer-encoding") or request.method in CARRY_BODIES:
            return JSONResponse(
                {"detail": f"{request.method} must declare a content-length"}, status_code=411
            )
        return await call_next(request)
    if not declared.isdigit():
        return JSONResponse({"detail": "malformed content-length"}, status_code=400)
    if int(declared) > MAX_BODY_BYTES:
        return JSONResponse({"detail": f"body exceeds {MAX_BODY_BYTES} bytes"}, status_code=413)
    return await call_next(request)


def _within(bundle: Path, held: str) -> Path | None:
    """The file the request names inside the bundle, or `None` because there is not one.

    Resolved and checked against the bundle rather than trusted: `held` is whatever the client
    put in the path, and a `..` that escaped here would serve any file this process can read.
    """
    if held == "":
        return None
    candidate = (bundle / held).resolve()
    if not candidate.is_relative_to(bundle.resolve()):
        return None
    return candidate if candidate.is_file() else None
