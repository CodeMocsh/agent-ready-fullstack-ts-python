"""The worker is a process of its own, and the web process never runs it.
`docs/adr/template/0027`."""

from collections.abc import Callable, Iterable
from typing import Final, override

import pytest

from app.loops import DatabaseGone
from app.store.budget import Draws
from app.store.jobs import Claim
from app.store.pg import PostgresDatabase
from app.store.pg_jobs import JobClaims
from app.worker import work

ONE_PROCESS: Final = Draws(instances=1, in_process={}, outside={})


class GoneClaims(JobClaims):
    """Claims whose database is gone: each loop of the worker raises `DatabaseGone` at once."""

    @override
    async def listen(self, woken: Callable[[], None]) -> None:
        raise DatabaseGone("the database is gone")

    @override
    async def claim(self, kinds: Iterable[str]) -> Claim | None:
        raise DatabaseGone("the database is gone")


async def test_a_worker_whose_database_is_gone_stops_with_database_gone() -> None:
    database = PostgresDatabase(
        dsn="postgresql://nowhere.invalid/app", draws=ONE_PROCESS, kinds=lambda _: {}
    )

    with pytest.raises(DatabaseGone, match="the database is gone"):
        await work(GoneClaims(database), at_once=2)
