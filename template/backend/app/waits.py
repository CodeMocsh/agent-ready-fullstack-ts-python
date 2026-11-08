"""The waits on systems outside the process, and the tasks that run past one caller.

A wait in a request has a bound under `REQUEST_BUDGET_SECONDS`. `waiting` bounds each wait, and
refuses one in a request that may last as long as the budget. Work that waits longer is a job,
which a worker process runs: `app/jobs.py`. Work that the caller does not wait for, and that ends
within the budget, runs beside the requests: `beside` starts it outside every request. Work that
several callers await at once is shared work: `SharedWork` runs it one run at a time for each
key, and each wait in a run has a bound under the budget. The lifespan cancels what is still
running at shutdown. `docs/adr/template/0021` and `0027`.
"""

import asyncio
import math
from collections.abc import AsyncGenerator, Callable, Coroutine, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, copy_context
from typing import Any, Final

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from app import log

REQUEST_BUDGET_SECONDS: Final = 10.0
"""The request budget: the time a request may take, and the threshold of the deployment's
slow-request alert. Each wait in a request has a bound under it."""

CANCELLED_WITHIN_SECONDS: Final = 5.0
"""How long `cancel_beside` waits for the tasks it cancelled to end."""

_IN_A_REQUEST: ContextVar[bool] = ContextVar("in_a_request", default=False)

_IN_A_PART: ContextVar[bool] = ContextVar("in_a_part", default=False)

_STARTED: Final[set[asyncio.Task[Any]]] = set()


class WaitOutlastsTheRequest(RuntimeError):
    """A wait in a request, or in shared work, has a bound that is not under
    `REQUEST_BUDGET_SECONDS`."""


class PartWaitsOutside(RuntimeError):
    """A part of `GET /reads` waits on a system outside the process. A part reads only the
    database. `docs/adr/template/0029`."""


class BesideOutlastsTheBudget(ValueError):
    """Work beside the requests has a bound that is not under `REQUEST_BUDGET_SECONDS`. Work
    that may take longer is a job."""


class WaitTimedOut(TimeoutError):
    """A wait that `waiting` bounds did not end within its bound."""


class BesideOutlivedItsCancel(RuntimeError):
    """Work beside the requests, or shared work, did not end within `CANCELLED_WITHIN_SECONDS` of
    its cancel."""


def instrument(app: FastAPI) -> None:
    """Mark every HTTP request `app` serves as a request, for `waiting`. A WebSocket and the
    lifespan are not marked."""
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


@contextmanager
def in_a_part() -> Generator[None]:
    """Mark the block as a part of `GET /reads`, so that `waiting` in it raises
    `PartWaitsOutside`."""
    marked = _IN_A_PART.set(True)
    try:
        yield
    finally:
        _IN_A_PART.reset(marked)


@asynccontextmanager
async def waiting(seconds: float, on: str) -> AsyncGenerator[None, None]:
    """Bound the block to `seconds`. Past them the block is cancelled, and `WaitTimedOut` names
    `on`: the system and the call, such as `"sqlserver.login"`.

    In a request, raises `WaitOutlastsTheRequest` before the block runs when `seconds` is not under
    `REQUEST_BUDGET_SECONDS`. In a part of `GET /reads`, raises `PartWaitsOutside` before the block
    runs. Raises `ValueError` on a bound that is not a positive number of
    seconds. A block that waits in a thread stops waiting at the bound, and the thread runs on:
    give the call in the thread the same bound.
    """
    if not 0 < seconds < math.inf:
        raise ValueError(f"{on} is bounded by {seconds} s, which is not a bound")
    if _IN_A_PART.get():
        raise PartWaitsOutside(
            f"{on} waits outside the database in a part of GET /reads, and a part waits on "
            f"nothing but the database. Take PART off the route, so that it answers alone."
        )
    if _IN_A_REQUEST.get() and seconds >= REQUEST_BUDGET_SECONDS:
        raise WaitOutlastsTheRequest(
            f"{on} may wait {seconds} s in a request or in shared work, and each wait there has a "
            f"bound under {REQUEST_BUDGET_SECONDS} s. Make the work a job in app/jobs.py, ask for "
            f"it, and answer 202. The browser reads the job until it ends."
        )
    bound = asyncio.timeout(seconds)
    try:
        async with bound:
            yield
    except TimeoutError as error:
        if not bound.expired():
            raise
        raise WaitTimedOut(f"{on} did not answer within {seconds} s") from error


def beside[T](work: Coroutine[Any, Any, T], name: str, *, within: float) -> asyncio.Task[T]:
    """Run `work` on the running loop, outside every request, as the task named `name`, and
    return the task. Past `within` seconds the work is cancelled, and the task raises
    `WaitTimedOut`. The task ends with the result of `work`. A request does not await it: a
    request that awaits it waits as long as the work.

    It acts as the actor the caller acts as. `log.beside_failed` names a failure, and
    `log.beside_cancelled` a cancellation, before the task is done for anything that awaits it.
    `cancel_beside` cancels it at shutdown. Raises `ValueError` on a bound that is not a positive
    number of seconds, and `BesideOutlastsTheBudget` when `within` is not under
    `REQUEST_BUDGET_SECONDS`: work that may take longer is a job, in `app/jobs.py`. Raises
    `RuntimeError` off the event loop.
    """
    if not 0 < within < math.inf:
        work.close()
        raise ValueError(f"{name} is bounded by {within} s, which is not a bound")
    if within >= REQUEST_BUDGET_SECONDS:
        work.close()
        raise BesideOutlastsTheBudget(
            f"{name} runs beside the requests within {within} s, and work beside the requests "
            f"ends within {REQUEST_BUDGET_SECONDS} s. Make work that may take longer a job in "
            f"app/jobs.py, which the worker process runs."
        )
    return _started(
        _bounded(work, name, within),
        name,
        in_a_request=False,
        failed=log.beside_failed,
        cancelled=log.beside_cancelled,
    )


async def _bounded[T](work: Coroutine[Any, Any, T], name: str, within: float) -> T:
    async with waiting(within, on=name):
        return await work


class SharedWork[K, T]:
    """Work that several callers await at once, as runs named `name`: one run at a time for each
    key.

    Each run is in a request, whoever starts it: a `waiting` in it has a bound under
    `REQUEST_BUDGET_SECONDS`. Each caller that joins a run gets the result of the work that the
    first caller started, in the context of the first caller: its actor, its request id and its
    trace. The
    key names everything the result depends on, such as the tenant, the actor and the arguments.
    """

    def __init__(self, name: str) -> None:
        self._name: Final = name
        self._runs: Final[dict[K, asyncio.Task[T]]] = {}

    async def joined(self, key: K, start: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """The result of the run for `key`. Joins the run in flight, or starts `start()` when
        none is. A call after the run ends starts another run, and nothing keeps the result.

        A cancelled caller stops awaiting, and the run goes on for the others. A failure raises in
        each caller that awaits the run. `log.shared_failed` names each failure, and
        `log.shared_cancelled` a cancellation. `cancel_beside` cancels a run at shutdown. Raises
        `RuntimeError` when the task of a run joins its own key, which would wait for ever.
        """
        run = self._runs.get(key)
        if run is None:
            run = _started(
                start(),
                self._name,
                in_a_request=True,
                failed=log.shared_failed,
                cancelled=log.shared_cancelled,
            )
            self._runs[key] = run
            run.add_done_callback(lambda _: self._runs.pop(key))
        elif run is asyncio.current_task():
            raise RuntimeError(f"a run of {self._name} joined its own key, and would wait for ever")
        return await asyncio.shield(run)


async def cancel_beside() -> None:
    """Cancel each task that `beside` or `SharedWork` started on this loop and that still runs,
    and wait until every one has ended. Raises `BesideOutlivedItsCancel`, which names each task
    that did not end within `CANCELLED_WITHIN_SECONDS`."""
    loop = asyncio.get_running_loop()
    running = [task for task in _STARTED if task.get_loop() is loop]
    for task in running:
        _ = task.cancel()
    if not running:
        return
    _, still = await asyncio.wait(running, timeout=CANCELLED_WITHIN_SECONDS)
    if still:
        raise BesideOutlivedItsCancel(
            f"{', '.join(sorted(task.get_name() for task in still))} did not end within "
            f"{CANCELLED_WITHIN_SECONDS} s of the cancel. Let a cancel end the work."
        )


def _started[T](
    work: Coroutine[Any, Any, T],
    name: str,
    *,
    in_a_request: bool,
    failed: Callable[[str, BaseException], None],
    cancelled: Callable[[str], None],
) -> asyncio.Task[T]:
    context = copy_context()
    _ = context.run(_IN_A_REQUEST.set, in_a_request)
    _ = context.run(_IN_A_PART.set, False)
    task = asyncio.get_running_loop().create_task(work, name=name, context=context)
    _STARTED.add(task)
    task.add_done_callback(lambda _: _ended(task, failed, cancelled))
    return task


def _ended(
    task: asyncio.Task[Any],
    failed: Callable[[str, BaseException], None],
    cancelled: Callable[[str], None],
) -> None:
    _STARTED.discard(task)
    if task.cancelled():
        cancelled(task.get_name())
        return
    error = task.exception()
    if error is not None:
        failed(task.get_name(), error)
