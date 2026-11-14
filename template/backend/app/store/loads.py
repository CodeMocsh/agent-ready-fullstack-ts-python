"""The reads of a request's items: one set read for each kind, in one transaction.

A set read is one statement that answers one value for each of many keys, as `= ANY($1)` does.
Code for one item asks a loader for one key. The loader holds each key that the request asks in
one turn of the event loop. Then it runs every set read of that turn, one after another, in one
unit of work. So a page of items costs one statement for each kind of read, whatever the page
holds, and the request holds one connection for them. `docs/adr/template/0032`.

Items share a turn only when each asks before the turn ends. Start them in one
`asyncio.TaskGroup`. An item that awaits something else first asks in a later turn.
"""

import asyncio
from collections.abc import Awaitable, Callable, Generator, Hashable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, contextmanager
from contextvars import ContextVar
from typing import Any, Final

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from app.models import PAGE_MAX
from app.waits import SharedWork

type SetRead[K: Hashable, V] = Callable[[Any, Sequence[K]], Awaitable[Mapping[K, V | Exception]]]
"""One statement, on a connection inside the tenant's transaction, that answers one value for
each key it is handed. It is handed at most `PAGE_MAX` keys. A fact of many rows for each key is
an aggregate in SQL, such as a `GROUP BY` of the key, and never a page for each key.

A failure of one key's value, such as a row that this build cannot read, is the answer for that
key: an exception, which `Loader.load` raises in the callers of that key only. A set read raises
only when the statement fails, and that fails every key of its turn."""

type Unit = Callable[[], AbstractAsyncContextManager[Any]]
"""A unit of work: a connection inside the tenant's transaction."""

type _Answers = dict[SetRead[Any, Any], Mapping[Any, Any]]

_LOADERS: ContextVar[dict[Hashable, "Loader"] | None] = ContextVar("loaders", default=None)


class NotAnswered(LookupError):
    """A set read did not answer exactly the keys it was handed."""


class NotLoading(RuntimeError):
    """A loader was asked for outside `loading`."""


class Loader:
    """The set reads of one scope of `loading` for one key, such as a tenant of a database.
    `loader_of` makes each one."""

    def __init__(self, unit: Unit) -> None:
        self._unit: Final = unit
        self._turn: int = 0
        self._asked: Final[dict[int, dict[SetRead[Any, Any], dict[Any, None]]]] = {}
        self._turns: Final = SharedWork[int, _Answers]("a turn of set reads")
        self._one_at_a_time: Final = asyncio.Lock()

    async def load[K: Hashable, V](self, read: SetRead[K, V], key: K) -> V:
        """The value that `read` answers for `key`. Each key asked in the same turn goes in the
        same statement, and each key asked twice in it goes once.

        Raises the exception that `read` answers for `key`, in the callers of `key` only. A turn
        waits until the turn before it has ended, so one loader holds one connection at a time. A
        failure of the turn raises in each of its callers: `NotAnswered` when a set read answers
        other keys than it was handed, `DatabaseUnavailable` when the database does not serve the
        turn, and what a set read raises. The turn is shared work: when each of its callers is cancelled, the
        turn is cancelled and its transaction rolls back."""
        turn = self._turn
        self._asked.setdefault(turn, {}).setdefault(read, {})[key] = None
        answers = await self._turns.joined(turn, lambda: self._read(turn))
        answer: V | Exception = answers[read][key]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def _read(self, turn: int) -> _Answers:
        async with self._one_at_a_time:
            asked = self._asked.pop(turn)
            self._turn = turn + 1
            async with self._unit() as conn:
                return {
                    read: await _answered(conn, read, list(keys)) for read, keys in asked.items()
                }


async def _answered(conn: Any, read: SetRead[Any, Any], keys: list[Any]) -> Mapping[Any, Any]:
    answers: dict[Any, Any] = {}
    for start in range(0, len(keys), PAGE_MAX):
        handed = keys[start : start + PAGE_MAX]
        answered = await read(conn, handed)
        missing = set(handed) - answered.keys()
        unasked = answered.keys() - set(handed)
        if missing or unasked:
            raise NotAnswered(
                f"{read.__qualname__} answered no value for {sorted(map(repr, missing))} and a "
                f"value for {sorted(map(repr, unasked))}, which it was not handed. A set read "
                f"answers one value for each key it is handed."
            )
        answers.update(answered)
    return answers


@contextmanager
def loading() -> Generator[None]:
    """A scope of loaders. Inside it, `loader_of` answers one loader for each key. Each HTTP
    request is one scope, and each attempt of a job is one. A task started inside the scope,
    such as an item of an `asyncio.TaskGroup`, shares it."""
    scope = _LOADERS.set({})
    try:
        yield
    finally:
        _LOADERS.reset(scope)


def loader_of(key: Hashable, unit: Unit) -> Loader:
    """The loader of `key` in this scope of `loading`, made on first use, which runs each turn
    in `unit()`. A key names everything that a loader must not cross, such as the database and
    the tenant. Raises `NotLoading` outside `loading`."""
    loaders = _LOADERS.get()
    if loaders is None:
        raise NotLoading(
            f"a loader for {key!r} was asked for outside a request and outside an attempt of a "
            f"job. Run the work inside app.store.loads.loading."
        )
    if key not in loaders:
        loaders[key] = Loader(unit)
    return loaders[key]


class LoadsPerRequest:
    """The middleware `instrument` adds."""

    def __init__(self, app: ASGIApp) -> None:
        self._app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        with loading():
            await self._app(scope, receive, send)


def instrument(app: FastAPI) -> None:
    """Give each HTTP request that `app` serves a scope of loaders of its own. A part of
    `GET /reads` is a request of its own, so it gets one too."""
    app.add_middleware(LoadsPerRequest)
