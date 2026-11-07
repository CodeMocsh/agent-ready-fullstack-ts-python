"""What both substrates and the worker share: the delay before a retry, the JSON a job may hold,
and how an attempt ends. `docs/adr/template/0027`."""

import math

import pytest

from app.identity.shared import Actor
from app.models import JsonObject
from app.store import DatabaseBusy, TaskStore
from app.store.jobs import Asked, Deferred, Failed, Leases, UnstorableJson, attempted, encoded
from app.store.memory import MemoryDatabase
from tests.jobs_contract import CONTRACT_KINDS

ASKED = Asked(input={}, actor=Actor(issuer="a-test", subject="the-asker", name="The asker"))


def test_the_delay_before_a_retry_doubles_with_each_attempt() -> None:
    leases = Leases(lease=30.0, renewal=10.0, retry=5.0)

    assert [leases.retry_in(attempt) for attempt in (1, 2, 3, 4)] == [5.0, 10.0, 20.0, 40.0]


@pytest.mark.parametrize(
    ("lease", "renewal", "retry"), [(30.0, 30.0, 5.0), (30.0, 0.0, 5.0), (30.0, 10.0, -1.0)]
)
def test_leases_that_end_before_their_renewal_or_hold_no_time_are_refused(
    lease: float, renewal: float, retry: float
) -> None:
    with pytest.raises(ValueError, match="Leases"):
        _ = Leases(lease=lease, renewal=renewal, retry=retry)


@pytest.mark.parametrize(
    "value",
    [
        {"ratio": math.nan},
        {"ratio": math.inf},
        {"names": ["a\x00b"]},
        {"a\x00b": 1},
        {"half": "\ud800"},
        {"half": "\udfff"},
    ],
)
def test_json_that_jsonb_cannot_hold_is_refused(value: JsonObject) -> None:
    with pytest.raises(UnstorableJson):
        _ = encoded(value)


def test_a_backslash_and_a_u_are_not_the_character_u_0000() -> None:
    assert encoded({"text": "\\u0000"}) == '{"text": "\\\\u0000"}'


def test_a_character_past_the_surrogates_is_stored() -> None:
    assert encoded({"text": "\U0001f600"}) == '{"text": "\\ud83d\\ude00"}'


@pytest.fixture
def store() -> TaskStore:
    return MemoryDatabase(seed_tenant="a-tenant", kinds=CONTRACT_KINDS).store("a-tenant")


async def test_an_attempt_that_gets_no_connection_is_deferred_by_the_refusal(
    store: TaskStore,
) -> None:
    async def busy(_store: TaskStore, _input: JsonObject) -> JsonObject:
        raise DatabaseBusy("no connection", retry_after=7)

    assert await attempted(busy, store, ASKED, job="1", kind="busy", attempt=1) == Deferred(7)


async def test_an_attempt_that_answers_what_cannot_be_stored_fails(store: TaskStore) -> None:
    async def answers_nan(_store: TaskStore, _input: JsonObject) -> JsonObject:
        return {"ratio": math.nan}

    assert await attempted(answers_nan, store, ASKED, job="1", kind="nan", attempt=1) == Failed(
        failure="UnstorableJson"
    )
