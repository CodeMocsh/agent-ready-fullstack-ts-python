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

**This module is also the edge**, because nothing is in front of it. It carries what a proxy
would have carried — `SECURITY_HEADERS` and `MAX_BODY_BYTES` below — and `app.main` carries
neither, deliberately: two policies disagreeing is worse than either alone. `docs/adr/0006`
holds that reasoning and the options it rejected, including why the refusals this module
issues are absent from `openapi.json`.

**Run it behind something that terminates TLS rather than as the public edge.** How, and the
one mistake that is easiest to make here, are in `docs/deployment.md`.
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
"""The methods asked to declare a length even when nothing else says they are sending one.

The last and weakest of the three tests in `_unmeasurable`, and the only one keyed on the verb.
An endpoint that streams an upload is a reason to narrow this set and to make that endpoint
bound itself, rather than to raise `MAX_BODY_BYTES` for every other route.
"""

FRAMED_BY_HEADERS: Final = "1."
"""The HTTP versions on which a missing `content-length` is a promise there is no body.

HTTP/1.x frames a body with `content-length` or `transfer-encoding` and offers nothing else, so
a request carrying neither is one that cannot have a body — which is the whole reason the verb
list above is safe. HTTP/2 and HTTP/3 frame in the protocol instead and make both headers
optional, so on those a bodied request may arrive announcing nothing at all. `uvicorn` speaks
only HTTP/1.1 today; this is what stops that from being load-bearing.
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

Three of these carry a caveat that will cost an afternoon if it is met without warning, and
each is argued in `docs/adr/0006`:

- `script-src 'self'` holds only while the build emits no inline script. Something that needs
  one gets a nonce or a hash, never `'unsafe-inline'`.
- `style-src` already allows `'unsafe-inline'`, because Radix positions every `shadcn` overlay
  with inline `style` attributes and a strict directive breaks them as misplacement rather
  than as an error.
- `cross-origin-opener-policy` severs `window.opener`, which is what a popup-based OAuth flow
  hands its result back through. Redirect flows are unaffected.

`strict-transport-security` names no subdomains, and adding `includeSubDomains` or `preload`
is a decision about a domain rather than about this code.
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

    `headers` is read with a default because ASGI declares the key optional and empty when
    absent. That is the specification's own answer, not a missing value being papered over —
    the distinction the *Fail loudly* rule turns on.
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
        if _unmeasurable(request):
            return JSONResponse(
                {"detail": f"{request.method} must declare a content-length"}, status_code=411
            )
        return await call_next(request)
    if not declared.isdecimal():
        return JSONResponse({"detail": "malformed content-length"}, status_code=400)
    if int(declared) > MAX_BODY_BYTES:
        return JSONResponse({"detail": f"body exceeds {MAX_BODY_BYTES} bytes"}, status_code=413)
    return await call_next(request)


def _unmeasurable(request: Request) -> bool:
    """Whether a request with no `content-length` could still be carrying a body.

    Three ways it can, and the method is the least of them. Keying only on the verb is what
    once let a chunked `DELETE` of any size through, so the framing is asked first.
    """
    if request.headers.get("transfer-encoding"):
        return True
    version = request.scope.get("http_version", "1.1")
    if not version.startswith(FRAMED_BY_HEADERS):
        return True
    return request.method in CARRY_BODIES


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
