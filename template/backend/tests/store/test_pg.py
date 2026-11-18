"""What `app.store.pg` decides before it reaches a server."""

import math
import socket
from typing import final

import asyncpg
import pytest

from app.store.budget import Draws
from app.store.pg import ConnectionLost, ConnectionRefused, PostgresDatabase
from app.store.timeouts import TIMEOUTS, Timeouts


@pytest.mark.parametrize("field", ["statement", "idle_in_transaction", "acquire"])
def test_a_bound_postgres_would_read_as_none_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match="under a millisecond"):
        Timeouts(**{field: 0})


def a_port_nothing_answers() -> int:
    with socket.socket() as unbound:
        unbound.bind(("127.0.0.1", 0))
        return unbound.getsockname()[1]


async def test_an_address_nothing_answers_is_refused_with_a_time_to_try_again() -> None:
    database = PostgresDatabase(
        dsn=f"postgresql://app@127.0.0.1:{a_port_nothing_answers()}/app",
        draws=Draws(instances=1, in_process={}, outside={}),
        kinds=lambda _: {},
    )

    with pytest.raises(ConnectionRefused, match="ConnectionRefusedError") as refused:
        await database.check()

    assert refused.value.retry_after == math.ceil(TIMEOUTS.acquire)
    assert refused.value.cause == "ConnectionRefusedError"


@final
class _Session:
    """A pooled session whose driver answers `InternalClientError`, as on a session that Postgres
    ended before the driver read its end."""

    def __init__(self) -> None:
        self._con = self

    def is_closed(self) -> bool:
        return True

    def terminate(self) -> None:
        pass

    async def execute(self, *_: object) -> None:
        raise asyncpg.InternalClientError(
            "cannot switch to state 12; another operation (2) is in progress"
        )


@final
class _Lends:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def acquire(self, timeout: float) -> _Session:
        _ = timeout
        return self._session

    async def release(self, session: _Session) -> None:
        _ = session


def lending(monkeypatch: pytest.MonkeyPatch, session: _Session) -> PostgresDatabase:
    database = PostgresDatabase(
        dsn="postgresql://app@127.0.0.1:1/app",
        draws=Draws(instances=1, in_process={}, outside={}),
        kinds=lambda _: {},
    )

    async def pool() -> _Lends:
        return _Lends(session)

    monkeypatch.setattr(database, "pool", pool)
    return database


async def test_the_first_statement_on_a_session_that_ended_before_it_was_lent_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = lending(monkeypatch, _Session())

    with pytest.raises(ConnectionLost, match="InternalClientError") as lost:
        async with database.connection():
            pass

    assert lost.value.cause == "InternalClientError"
