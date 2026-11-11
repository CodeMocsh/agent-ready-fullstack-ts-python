"""The jobs contract, written once, run by every substrate that claims to keep it.

`JobsContract` is not collected, as `TaskStoreContract` is not. A substrate runs it by
subclassing it and supplying two fixtures: `database`, built with `contract_kinds`, and `finish`,
which runs every job that is asked for to its end. `tests/store/test_jobs_contract.py` runs it on
memory, and `tests/integration/test_jobs_contract.py` on Postgres, where a worker ends the job.
Postgres alone also runs the kill, the reclaim and the fence, in
`tests/integration/test_jobs_on_postgres.py`. `docs/adr/template/0027`.

Every test asks as `ASKER`, and asserts only the jobs it asked for.
"""

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import Context
from typing import Final

import pytest

from app import log
from app.audit_events import acting_as, current_actor
from app.failing import Internal
from app.identity.shared import SENTINEL_TENANT, Actor
from app.models import CreateTaskBody, Failure, JsonObject
from app.store import Database, TaskStore
from app.store.jobs import (
    MAX_ATTEMPTS,
    AskedAgainWithAnotherInput,
    AskedByNobody,
    Kinds,
    UnknownKind,
    UnstorableJson,
)
from tests.conftest import Logged
from tests.pages import every_task

OTHER_TENANT: Final = "a-second-tenant"

PERSON: Final = Actor(issuer="a-test", subject="a-person", name="A person")
ASKER: Final = Actor(issuer="a-test", subject="the-asker", name="The asker", on_behalf_of=PERSON)

INTERNAL: Final = Failure(cause=Internal.cause)

ATTEMPTS: Final[dict[str, int]] = {}
"""How many attempts each job of the flaky kind has had, by the key in its input."""


async def answers_who_asked(_tenant_id: str, input: JsonObject) -> JsonObject:
    actor = current_actor()
    if actor is None or actor.on_behalf_of is None:
        raise RuntimeError("the job runs as no actor, or as one for nobody")
    return {"input": input, "actor": actor.subject, "for": actor.on_behalf_of.subject}


async def creates_a_task(store: TaskStore, input: JsonObject) -> JsonObject:
    title = input["title"]
    if not isinstance(title, str):
        raise TypeError("the title is not a string")
    return {"id": (await store.create(CreateTaskBody(title=title))).id}


async def always_fails(_tenant_id: str, _input: JsonObject) -> JsonObject:
    raise ValueError("this kind never ends")


async def answers_what_cannot_be_stored(_tenant_id: str, _input: JsonObject) -> JsonObject:
    return {"ratio": math.inf}


async def fails_once(_tenant_id: str, input: JsonObject) -> JsonObject:
    key = str(input["key"])
    ATTEMPTS[key] = ATTEMPTS.get(key, 0) + 1
    if ATTEMPTS[key] == 1:
        raise ConnectionError("the first attempt fails")
    return {"attempts": ATTEMPTS[key]}


def contract_kinds(database: Database) -> Kinds:
    return {
        "answers who asked": answers_who_asked,
        "creates a task": lambda tenant_id, input: creates_a_task(database.store(tenant_id), input),
        "always fails": always_fails,
        "fails once": fails_once,
        "answers what cannot be stored": answers_what_cannot_be_stored,
    }


Finish = Callable[[], Awaitable[None]]
"""Runs every job that is asked for to its end: done or dead."""


async def without_an_actor[T](work: Awaitable[T]) -> T:
    """The result of `work`, awaited in a context where no actor acts."""

    async def awaited() -> T:
        return await work

    return await asyncio.get_running_loop().create_task(awaited(), context=Context())


class JobsContract:
    """What `Jobs` promises. Subclass it, and give it `database` and `finish`."""

    @pytest.fixture(autouse=True)
    async def acting(self) -> AsyncIterator[Actor]:
        with acting_as(ASKER) as actor:
            yield actor

    async def test_a_job_that_is_asked_for_is_pending_and_reads_back(
        self, database: Database
    ) -> None:
        jobs = database.jobs(SENTINEL_TENANT)

        asked = await jobs.ask("answers who asked", {"n": 1})

        assert asked.state == "pending"
        assert asked.kind == "answers who asked"
        assert (asked.outcome, asked.failure) == (None, None)
        read = await jobs.read(asked.id)
        assert read is not None
        assert read.id == asked.id

    async def test_a_job_ends_with_the_outcome_of_its_handler_as_the_actor_that_asked(
        self, database: Database, finish: Finish
    ) -> None:
        jobs = database.jobs(SENTINEL_TENANT)
        asked = await jobs.ask("answers who asked", {"n": 2})

        await finish()

        read = await jobs.read(asked.id)
        assert read is not None
        assert read.state == "done"
        assert read.outcome == {"input": {"n": 2}, "actor": "the-asker", "for": "a-person"}
        assert read.failure is None

    async def test_a_job_writes_in_the_tenant_that_asked_for_it(
        self, database: Database, finish: Finish
    ) -> None:
        asked = await database.jobs(OTHER_TENANT).ask("creates a task", {"title": "From a job"})

        await finish()

        read = await database.jobs(OTHER_TENANT).read(asked.id)
        assert read is not None
        assert read.outcome is not None
        created = read.outcome["id"]
        assert [task.id for task in await every_task(database.store(OTHER_TENANT))] == [created]
        assert created not in {
            task.id for task in await every_task(database.store(SENTINEL_TENANT))
        }

    async def test_a_tenant_cannot_read_the_job_of_another(self, database: Database) -> None:
        asked = await database.jobs(SENTINEL_TENANT).ask("answers who asked", {"n": 3})

        assert await database.jobs(OTHER_TENANT).read(asked.id) is None

    async def test_a_job_that_is_not_there_reads_as_none(self, database: Database) -> None:
        assert await database.jobs(SENTINEL_TENANT).read("does-not-exist") is None

    async def test_a_handler_that_fails_once_is_tried_again(
        self, database: Database, finish: Finish
    ) -> None:
        key = f"{type(self).__name__}-again"
        jobs = database.jobs(SENTINEL_TENANT)
        asked = await jobs.ask("fails once", {"key": key})

        await finish()

        read = await jobs.read(asked.id)
        assert read is not None
        assert (read.state, read.outcome) == ("done", {"attempts": 2})

    async def test_a_handler_that_always_fails_is_dead_after_the_last_attempt(
        self, database: Database, finish: Finish, logged: Logged
    ) -> None:
        log.configure(None)
        jobs = database.jobs(SENTINEL_TENANT)
        asked = await jobs.ask("always fails", {})

        await finish()

        read = await jobs.read(asked.id)
        assert read is not None
        assert (read.state, read.outcome, read.failure) == ("dead", None, INTERNAL)
        lines = logged()
        failed = [line for line in lines if line["message"] == "job failed"]
        assert [line["attempt"] for line in failed] == list(range(1, MAX_ATTEMPTS + 1))
        [dead] = [line for line in lines if line["message"] == "job dead"]
        assert (dead["job"], dead["cause"], dead["error"]) == (asked.id, "internal", "ValueError")

    async def test_a_kind_that_no_handler_runs_is_refused(self, database: Database) -> None:
        with pytest.raises(UnknownKind, match="no such kind"):
            _ = await database.jobs(SENTINEL_TENANT).ask("no such kind", {})

    async def test_a_job_that_no_actor_asks_for_is_refused(self, database: Database) -> None:
        with pytest.raises(AskedByNobody):
            _ = await without_an_actor(
                database.jobs(SENTINEL_TENANT).ask("answers who asked", {"n": 4})
            )

    @pytest.mark.parametrize(
        "input", [{"ratio": math.nan}, {"name": "a\x00b"}, {"a\x00b": 1}, {"half": "\ud800"}]
    )
    async def test_an_input_that_cannot_be_stored_is_refused(
        self, database: Database, input: JsonObject
    ) -> None:
        with pytest.raises(UnstorableJson):
            _ = await database.jobs(SENTINEL_TENANT).ask("answers who asked", input)

    async def test_an_outcome_that_cannot_be_stored_fails_each_attempt(
        self, database: Database, finish: Finish
    ) -> None:
        jobs = database.jobs(SENTINEL_TENANT)
        asked = await jobs.ask("answers what cannot be stored", {})

        await finish()

        read = await jobs.read(asked.id)
        assert read is not None
        assert (read.state, read.failure) == ("dead", INTERNAL)

    async def test_asks_once_per_a_key_while_its_job_waits_for_its_first_claim_are_one_job(
        self, database: Database, finish: Finish
    ) -> None:
        jobs = database.jobs(SENTINEL_TENANT)

        first = await jobs.ask("answers who asked", {"n": 5}, once_per="plan 5")
        second = await jobs.ask("answers who asked", {"n": 5}, once_per="plan 5")
        await finish()

        assert second.id == first.id
        read = await jobs.read(first.id)
        assert read is not None
        assert read.state == "done"

    async def test_an_ask_once_per_a_key_after_its_job_was_claimed_is_a_new_job(
        self, database: Database, finish: Finish
    ) -> None:
        jobs = database.jobs(SENTINEL_TENANT)
        first = await jobs.ask("answers who asked", {"n": 6}, once_per="plan 6")
        await finish()

        later = await jobs.ask("answers who asked", {"n": 6}, once_per="plan 6")
        await finish()

        assert later.id != first.id
        read = await jobs.read(later.id)
        assert read is not None
        assert read.state == "done"

    async def test_asks_for_other_keys_kinds_or_tenants_are_other_jobs(
        self, database: Database
    ) -> None:
        jobs = database.jobs(SENTINEL_TENANT)
        plan: JsonObject = {"title": "plan 7"}

        asked = [
            await jobs.ask("answers who asked", plan, once_per="plan 7"),
            await jobs.ask("answers who asked", plan, once_per="plan 8"),
            await jobs.ask("creates a task", plan, once_per="plan 7"),
            await database.jobs(OTHER_TENANT).ask("answers who asked", plan, once_per="plan 7"),
            await jobs.ask("answers who asked", plan),
            await jobs.ask("answers who asked", plan),
        ]

        assert len({job.id for job in asked}) == len(asked)

    async def test_an_ask_once_per_a_key_with_another_input_is_refused(
        self, database: Database
    ) -> None:
        jobs = database.jobs(SENTINEL_TENANT)
        _ = await jobs.ask("answers who asked", {"n": 9}, once_per="plan 9")

        with pytest.raises(AskedAgainWithAnotherInput, match="answers who asked"):
            _ = await jobs.ask("answers who asked", {"n": 10}, once_per="plan 9")

    async def test_an_ask_once_per_no_key_is_refused(self, database: Database) -> None:
        with pytest.raises(ValueError, match="names no key"):
            _ = await database.jobs(SENTINEL_TENANT).ask("answers who asked", {}, once_per="")
