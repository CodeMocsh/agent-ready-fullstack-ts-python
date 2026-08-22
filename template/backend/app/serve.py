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
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.main import create_app
from app.wiring import BundleMissing, build_bundle

INDEX: Final = "index.html"
ASSETS: Final = "assets"
PREFIX: Final = "/api"


def build_server() -> FastAPI:
    """The server this deployment runs, with the bundle the environment names."""
    return create_server(build_bundle())


def create_server(bundle: Path) -> FastAPI:
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

    return server


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
