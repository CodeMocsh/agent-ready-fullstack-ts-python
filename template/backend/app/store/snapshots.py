"""The reads of a GET request: one snapshot for each tenant, one connection, and each answer
once. `docs/adr/template/0033`.

A snapshot is one transaction, READ ONLY and REPEATABLE READ. It opens at the first read of its
tenant in the request. It ends before the response starts, after a write to its tenant, and
after a read in it fails in the database. The next read then opens a new one. Each read of a
snapshot runs on its one connection, one at a time. The same statement with the same arguments
reaches the database once in a snapshot.

A read runs alone, in a transaction of its own, outside a GET request, in work beside the
requests, in shared work other than a turn of a loader, and after the request's snapshots end.
"""

import asyncio
import re
from collections.abc import AsyncGenerator, Callable, Generator, Hashable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
    contextmanager,
)
from contextvars import ContextVar
from typing import Any, Final

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import waits

type Unit = Callable[[], AbstractAsyncContextManager[Any]]
"""A unit of work: a connection inside the tenant's transaction."""

READ_METHODS: Final = frozenset({"GET", "HEAD"})
"""The methods whose requests read through a snapshot."""

SENT_EACH_TIME: Final = re.compile(
    r"\b(clock_timestamp|statement_timestamp|timeofday|random|random_normal|gen_random_uuid|"
    r"uuidv[47]|uuid_generate_v\w+|pg_(try_)?advisory_\w+)\s*\(",
    re.IGNORECASE,
)
"""A statement that calls a function whose answer changes inside one transaction. A snapshot
sends it each time, and keeps nothing of its answer."""

_SNAPSHOTS: ContextVar["Snapshots | None"] = ContextVar("snapshots", default=None)

_HELD: ContextVar["_Snapshot | None"] = ContextVar("held_snapshot", default=None)

_FOR_A_TURN: ContextVar[bool] = ContextVar("for_a_turn", default=False)


class ReadInsideARead(RuntimeError):
    """A read of a snapshot, or a wait for a turn of a loader, started inside a read of a
    snapshot."""


def refuse_inside_a_read(what: str) -> None:
    """Raise `ReadInsideARead`, which names `what`, in a read of a snapshot."""
    if _HELD.get() is not None:
        raise ReadInsideARead(
            f"{what} started inside a read of a snapshot, and would wait for ever for the "
            f"connection that the read holds. End the read before it starts."
        )


@contextmanager
def for_a_turn() -> Generator[None]:
    """Mark the block as a turn of a loader. A turn reads on the snapshot of its request, as the
    request does. Other shared work reads alone."""
    marked = _FOR_A_TURN.set(True)
    try:
        yield
    finally:
        _FOR_A_TURN.reset(marked)


class Kept:
    """A connection in a snapshot that answers a read it already answered from what it kept.

    A value that a read answers is the same object each time: change none of it."""

    def __init__(self, conn: Any) -> None:
        self._conn: Any = conn
        self._kept: Final[dict[Hashable, Any]] = {}

    async def fetch(self, query: str, *args: Any, **kwargs: Any) -> list[Any]:
        rows: list[Any] = await self._answered("fetch", query, args, kwargs)
        return list(rows)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._answered("fetchrow", query, args, kwargs)

    async def fetchval(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._answered("fetchval", query, args, kwargs)

    async def _answered(
        self, method: str, query: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        sent = getattr(self._conn, method)
        if SENT_EACH_TIME.search(query) is not None:
            return await sent(query, *args, **kwargs)
        asked = (method, query, _frozen(args), _frozen(kwargs))
        try:
            hash(asked)
        except TypeError as unhashable:
            raise TypeError(
                f"a read in a snapshot passed an argument that cannot be kept: {unhashable}. "
                f"Pass a str, a number, a UUID, or a list, tuple, set or dict of them."
            ) from unhashable
        if asked not in self._kept:
            self._kept[asked] = await sent(query, *args, **kwargs)
        return self._kept[asked]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _frozen(value: Any) -> Any:
    if isinstance(value, list | tuple):
        return (type(value).__name__, tuple(_frozen(item) for item in value))
    if isinstance(value, set | frozenset):
        return (type(value).__name__, frozenset(_frozen(item) for item in value))
    if isinstance(value, dict):
        return ("dict", frozenset((key, _frozen(item)) for key, item in value.items()))
    return (type(value).__name__, value)


class _Snapshot:
    """The snapshot of one tenant of one database in one GET request, opened at its first read."""

    def __init__(self, opened: Unit, ends: tuple[type[BaseException], ...]) -> None:
        self._opened: Final = opened
        self._ends: Final = ends
        self._one_at_a_time: Final = asyncio.Lock()
        self._open: tuple[AsyncExitStack, Kept] | None = None
        self._stale: bool = False
        self.ended: bool = False

    @asynccontextmanager
    async def lent(self) -> AsyncGenerator[Kept | None]:
        """The connection of the snapshot, or `None` once the snapshot has ended."""
        refuse_inside_a_read("a read of a snapshot")
        async with self._one_at_a_time:
            if self.ended:
                yield None
                return
            held = _HELD.set(self)
            try:
                kept = await self._kept()
                try:
                    yield kept
                except BaseException as failed:
                    if self._ended_by(failed):
                        await self._close(failed)
                    raise
                if self.ended or self._stale:
                    await self._close(None)
            finally:
                _HELD.reset(held)

    def _ended_by(self, failed: BaseException) -> bool:
        database = not isinstance(failed, Exception) or isinstance(failed, self._ends)
        return database or self.ended or self._stale

    async def _kept(self) -> Kept:
        if self._stale:
            await self._close(None)
        if self._open is None:
            stack = AsyncExitStack()
            conn = await stack.enter_async_context(self._opened())
            self._open = (stack, Kept(conn))
            self._stale = False
        return self._open[1]

    async def _close(self, failed: BaseException | None) -> None:
        if self._open is None:
            return
        stack, _ = self._open
        self._open = None
        if failed is None:
            await stack.aclose()
        else:
            _ = await stack.__aexit__(type(failed), failed, failed.__traceback__)

    async def forget(self) -> None:
        """End the transaction, so the next read opens a new one. Inside a read of this
        snapshot, the read ends it when it ends."""
        self._stale = True
        if self._inside_a_read():
            return
        async with self._one_at_a_time:
            if self._stale:
                await self._close(None)

    async def end(self) -> None:
        """End the transaction, and every read of the snapshot from now on. Waits for the read
        in flight. When this wait is cancelled, that read ends the transaction."""
        self.ended = True
        if self._inside_a_read():
            return
        async with self._one_at_a_time:
            await self._close(None)

    def _inside_a_read(self) -> bool:
        return _HELD.get() is self and self._one_at_a_time.locked()


class Snapshots:
    """The snapshots of one scope of `snapshotted`: one for each `whose`."""

    def __init__(self) -> None:
        self._each: Final[dict[Hashable, _Snapshot]] = {}
        self._ended: bool = False

    def of(
        self, whose: Hashable, opened: Unit, ends: tuple[type[BaseException], ...]
    ) -> _Snapshot | None:
        if self._ended:
            return None
        if whose not in self._each:
            self._each[whose] = _Snapshot(opened, ends)
        return self._each[whose]

    async def forget(self, whose: Hashable) -> None:
        snapshot = self._each.get(whose)
        if snapshot is not None:
            await snapshot.forget()

    async def end(self) -> None:
        """End each snapshot, and read alone from now on. Raises what ending one snapshot
        raised, after it ends the others, or an `ExceptionGroup` when more than one raised."""
        self._ended = True
        for snapshot in self._each.values():
            snapshot.ended = True
        ended = await asyncio.gather(
            *(snapshot.end() for snapshot in self._each.values()), return_exceptions=True
        )
        failed = [failure for failure in ended if isinstance(failure, BaseException)]
        if len(failed) == 1:
            raise failed[0]
        if failed:
            raise BaseExceptionGroup("more than one snapshot did not end", failed)


def _borrows() -> bool:
    if waits.started_beside():
        return False
    return not waits.in_shared_work() or _FOR_A_TURN.get()


@asynccontextmanager
async def reading(
    whose: Hashable, opened: Unit, alone: Unit, ends: tuple[type[BaseException], ...]
) -> AsyncGenerator[Any]:
    """A connection to read on. In a GET request, the snapshot of `whose`, which `opened()`
    opens at the first read: a transaction that is READ ONLY and REPEATABLE READ. Otherwise
    `alone()`. `whose` names everything that a snapshot must not cross: the database and the
    tenant.

    In the snapshot, a read waits until the read before it ends, and its answer is kept for the
    snapshot. A read that raises one of `ends`, or is cancelled, ends the snapshot, and raises in
    its caller. Raises `ReadInsideARead` in a read of a snapshot, or in a task that one started."""
    snapshots = _SNAPSHOTS.get()
    snapshot = None if snapshots is None or not _borrows() else snapshots.of(whose, opened, ends)
    if snapshot is not None:
        async with snapshot.lent() as kept:
            if kept is not None:
                yield kept
                return
    async with alone() as conn:
        yield conn


async def written(whose: Hashable) -> None:
    """End the snapshot of `whose` in this request, if it has one, after a write to it, so the
    next read opens a new one that sees the write."""
    snapshots = _SNAPSHOTS.get()
    if snapshots is not None:
        await snapshots.forget(whose)


@asynccontextmanager
async def snapshotted() -> AsyncGenerator[Snapshots]:
    """A scope of snapshots, which `reading` reads in. Each GET request is one. A task started
    inside the scope, such as an item of an `asyncio.TaskGroup`, shares it. Each snapshot ends
    when the scope does, or at `Snapshots.end`."""
    snapshots = Snapshots()
    opened = _SNAPSHOTS.set(snapshots)
    try:
        yield snapshots
    finally:
        _SNAPSHOTS.reset(opened)
        await snapshots.end()


class SnapshotsPerRequest:
    """The middleware `instrument` adds."""

    def __init__(self, app: ASGIApp) -> None:
        self._app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in READ_METHODS:
            await self._app(scope, receive, send)
            return
        async with snapshotted() as snapshots:

            async def ending(message: Message) -> None:
                if message["type"] == "http.response.start":
                    await snapshots.end()
                await send(message)

            await self._app(scope, receive, ending)


def instrument(app: FastAPI) -> None:
    """Give each GET and HEAD request that `app` serves snapshots of its own, which end before
    its response starts. A part of `GET /reads` is a request of its own, so it gets its own.
    Call it before `request_events.instrument`, so each snapshot ends before the request event
    is recorded."""
    app.add_middleware(SnapshotsPerRequest)
