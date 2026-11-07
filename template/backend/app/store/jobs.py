"""Durable work: what both substrates and the worker share.

A job is one unit of durable work. A request asks for it and answers `202`. On Postgres a worker
process claims it, as `app/worker.py` says. The memory substrate holds its rows in one process,
so it runs its own jobs. `app/jobs.py` maps each kind of job to its handler.
`docs/adr/template/0027`.

A job runs at least once, so a handler is idempotent. A handler that raises is tried again, up to
`MAX_ATTEMPTS` attempts, and the job is then dead.
"""

import json
import re
from collections.abc import Awaitable, Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from pydantic import JsonValue

from app import log
from app.audit_events import current_actor
from app.identity.shared import Actor
from app.models import JsonObject
from app.store import Database, DatabaseBusy, TaskStore

MAX_ATTEMPTS: Final = 5
"""The most attempts a job gets. A job whose last attempt fails is dead."""

ATTEMPTS_SPENT: Final = "AttemptsSpent"
"""The failure of a job whose last attempt ended with no outcome and no failure it named: its
lease ended, or its worker stopped."""

Handler = Callable[[TaskStore, JsonObject], Awaitable[JsonObject]]
"""What runs a job: the store of the job's tenant and the job's input, to the job's outcome."""

Kinds = Mapping[str, Handler]
"""Each kind of job, and its handler."""


class UnknownKind(LookupError):
    """A job was asked for with a kind that no handler runs."""


class AskedByNobody(RuntimeError):
    """A job was asked for outside a request and outside `acting_as`, so no actor asked."""


class UnstorableJson(ValueError):
    """An input or an outcome holds a value that a `jsonb` column cannot hold: a number that is
    not finite, the character U+0000, or a surrogate code point, U+D800 to U+DFFF."""


class LeaseLost(RuntimeError):
    """A transaction of a job found that the job's claim is no longer this attempt's: the lease
    ended, or another worker claimed the job. The transaction rolls back."""


@dataclass(frozen=True)
class Leases:
    """How long a worker holds a claim, in seconds, and when a failed job is tried again.

    The worker renews a lease every `renewal`. A lease that nobody renews for `lease` ends, and
    another worker can claim the job. A job that failed attempt `n` is ready again after
    `retry * 2 ** (n - 1)`.
    """

    lease: float = 30.0
    renewal: float = 10.0
    retry: float = 5.0

    def __post_init__(self) -> None:
        if min(self.lease, self.renewal, self.retry) <= 0:
            raise ValueError(f"{self} holds a time that is not above zero")
        if self.renewal >= self.lease:
            raise ValueError(f"{self} renews a lease no sooner than it ends")

    def retry_in(self, attempts: int) -> float:
        """How long a job that failed its attempt number `attempts` waits before it is ready."""
        return self.retry * 2 ** (attempts - 1)


LEASES: Final = Leases()


@dataclass(frozen=True)
class Claim:
    """One attempt of one job: the job, its tenant and kind, the token of this attempt's lease,
    and the number of this attempt."""

    id: str
    tenant_id: str
    kind: str
    token: UUID
    attempts: int


@dataclass(frozen=True)
class Asked:
    """What the job's tenant asked: the input, and the actor that asked."""

    input: JsonObject
    actor: Actor


@dataclass(frozen=True)
class Failed:
    """An attempt whose handler raised, or returned an outcome that cannot be stored. `failure`
    names the class of the error."""

    failure: str


@dataclass(frozen=True)
class Deferred:
    """An attempt that got no database connection. It does not count, and the job is ready again
    after `after` seconds, as the refusal asked."""

    after: float


_FENCE: ContextVar[Claim | None] = ContextVar("fence", default=None)


@contextmanager
def fenced(claim: Claim) -> Generator[Claim]:
    """Inside the block, each transaction on Postgres first holds `claim`, and raises `LeaseLost`
    when the claim is no longer this attempt's."""
    token = _FENCE.set(claim)
    try:
        yield claim
    finally:
        _FENCE.reset(token)


def fence() -> Claim | None:
    """The claim each transaction holds here, and `None` outside a job."""
    return _FENCE.get()


def handler_of(kinds: Kinds, kind: str) -> Handler:
    """The handler of `kind`. Raises `UnknownKind` when no handler runs it."""
    handler = kinds.get(kind)
    if handler is None:
        raise UnknownKind(f"no handler runs the kind {kind!r}. Name it in app/jobs.py.")
    return handler


def store_of_the_job(database: Database, tenant_id: str) -> TaskStore:
    """The store a job's handler gets: scoped to `tenant_id`, the tenant of the job. The one
    place a job obtains a store, as `deps.get_store` is for a request."""
    return database.store(tenant_id)


def asker() -> Actor:
    """The actor that asks for a job here. Raises `AskedByNobody` where nothing names one."""
    actor = current_actor()
    if actor is None:
        raise AskedByNobody(
            "a job was asked for with no actor. Ask in a request, or inside "
            "app.audit_events.acting_as(actor)."
        )
    return actor


def encoded(value: JsonObject) -> str:
    """`value` as JSON that a `jsonb` column can hold. Raises `UnstorableJson` on a number that is
    not finite, on the character U+0000, and on a surrogate code point."""
    try:
        text = json.dumps(value, allow_nan=False)
    except ValueError as error:
        raise UnstorableJson("a value holds a number that is not finite") from error
    if _holds_unstorable_text(value):
        raise UnstorableJson("a value holds the character U+0000 or a surrogate code point")
    return text


_UNSTORABLE_TEXT: Final = re.compile("[\x00\ud800-\udfff]")


def _holds_unstorable_text(value: JsonValue) -> bool:
    if isinstance(value, str):
        return _UNSTORABLE_TEXT.search(value) is not None
    if isinstance(value, dict):
        return any(
            _holds_unstorable_text(key) or _holds_unstorable_text(held)
            for key, held in value.items()
        )
    if isinstance(value, list):
        return any(_holds_unstorable_text(held) for held in value)
    return False


async def attempted(
    handler: Handler, store: TaskStore, asked: Asked, *, job: str, kind: str, attempt: int
) -> JsonObject | Failed | Deferred:
    """The outcome of one attempt of `handler`: the outcome, `Failed` when the handler raises or
    returns an outcome that cannot be stored, or `Deferred` when it got no database connection.
    `log.job_failed` names each failure. `LeaseLost` and a cancellation raise."""
    try:
        outcome = await handler(store, asked.input)
        _ = encoded(outcome)
    except LeaseLost:
        raise
    except DatabaseBusy as busy:
        log.database_busy(type(busy).__name__)
        return Deferred(after=busy.retry_after)
    except Exception as error:
        log.job_failed(kind=kind, job=job, attempt=attempt, error=error)
        return Failed(failure=type(error).__name__)
    return outcome
