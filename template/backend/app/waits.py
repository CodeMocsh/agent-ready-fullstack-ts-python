"""The waits on systems outside the process, and the work that runs beside the requests.

A request waits on one system for less than `REQUEST_BUDGET_SECONDS`. `waiting` bounds each wait,
and refuses one in a request that may last as long as the budget. Work that waits longer runs
beside the requests: `beside` starts it outside every request, and the lifespan cancels what is
still running at shutdown. `docs/adr/template/0021`.
"""

import asyncio
import math
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from typing import Any, Final

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from app import log

REQUEST_BUDGET_SECONDS: Final = 10.0
"""The request budget: how long a request may take before the slow-request alert fires. One wait
in a request lasts less."""

_IN_A_REQUEST: ContextVar[bool] = ContextVar("in_a_request", default=False)

_BESIDE: Final[set[asyncio.Task[None]]] = set()


class WaitOutlastsTheRequest(RuntimeError):
    """A request started a wait whose bound is not under `REQUEST_BUDGET_SECONDS`."""


class WaitTimedOut(TimeoutError):
    """A wait that `waiting` bounds did not end within its bound."""


def instrument(app: FastAPI) -> None:
    """Mark every HTTP request `app` serves as a request, for `waiting`."""
    app.add_middleware(MarksRequests)


class MarksRequests:
    """The middleware `instrument` adds."""

    def __init__(self, app: ASGIApp) -> None:
        self._app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        marked = _IN_A_REQUEST.set(True)
        try:
            await self._app(scope, receive, send)
        finally:
            _IN_A_REQUEST.reset(marked)


@asynccontextmanager
async def waiting(seconds: float, on: str) -> AsyncGenerator[None, None]:
    """Bound the block to `seconds`. Past them the block is cancelled, and `WaitTimedOut` names
    `on`: the system and the call, such as `"sqlserver.login"`.

    In a request, raises `WaitOutlastsTheRequest` before the block runs when `seconds` is not under
    `REQUEST_BUDGET_SECONDS`. Raises `ValueError` on a bound that is not a positive number of
    seconds. A block that waits in a thread stops waiting at the bound, and the thread runs on:
    give the call in the thread the same bound.
    """
    if not 0 < seconds < math.inf:
        raise ValueError(f"{on} is bounded by {seconds} s, which is not a bound")
    if _IN_A_REQUEST.get() and seconds >= REQUEST_BUDGET_SECONDS:
        raise WaitOutlastsTheRequest(
            f"{on} may wait {seconds} s in a request, and a request waits on one system for less "
            f"than {REQUEST_BUDGET_SECONDS} s. Start the work with app.waits.beside, answer 202, "
            f"and tell the browser the result when the work ends."
        )
    bound = asyncio.timeout(seconds)
    try:
        async with bound:
            yield
    except TimeoutError as error:
        if not bound.expired():
            raise
        raise WaitTimedOut(f"{on} did not answer within {seconds} s") from error


def beside(work: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    """Run `work` on the running loop, outside every request, as the task named `name`, and
    return the task. A request does not await it.

    A `waiting` in it may last past `REQUEST_BUDGET_SECONDS`. It acts as the actor the caller acts
    as. `log.beside_failed` names a failure, and `log.beside_cancelled` a cancellation, before the
    task is done for anything that awaits it. `cancel_beside` cancels it at shutdown. Raises
    `RuntimeError` off the event loop.
    """
    outside = copy_context()
    _ = outside.run(_IN_A_REQUEST.set, False)
    task = asyncio.get_running_loop().create_task(work, name=name, context=outside)
    _BESIDE.add(task)
    task.add_done_callback(_ended)
    return task


async def cancel_beside() -> None:
    """Cancel each task that `beside` started on this loop and that still runs, and wait until
    every one has ended."""
    loop = asyncio.get_running_loop()
    running = [task for task in _BESIDE if task.get_loop() is loop]
    for task in running:
        _ = task.cancel()
    if running:
        _ = await asyncio.wait(running)


def _ended(task: asyncio.Task[None]) -> None:
    _BESIDE.discard(task)
    if task.cancelled():
        log.beside_cancelled(task.get_name())
        return
    error = task.exception()
    if error is not None:
        log.beside_failed(task.get_name(), error)
