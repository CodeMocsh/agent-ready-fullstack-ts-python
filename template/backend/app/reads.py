"""Reads in a group: `GET /reads` answers each of its parts as the part's own route answers it
alone.

The template owns this module and updates it. The numbers are the project's, in
`app/read_limits.py`, and a deployment may replace each one: `wiring.build_reads`.
`docs/adr/template/0029`.

**A part is a GET route that declares it**, with `openapi_extra=PART`. `openapi.json` names it
with `x-part`, and the frontend sends only those paths in a group.

**Each part is a request of its own, in this process.** It passes every middleware the app has,
so it is admitted, recorded, logged and bounded as it would be alone. It carries the credential
and the request id of its group. It takes its own connection and gives it back, so a group holds
at most `part_concurrency` connections, and none between parts.

**A part that cannot answer in its group leaves it**, and the client sends it again, alone: a part
past `part_bound_seconds`, a path that is not a part, a part past `max_parts`, and a part whose
answer does not fit under the ceiling of the response. `part left its group` names each one.
"""

import asyncio
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, get_args
from urllib.parse import unquote

from fastapi import FastAPI, Request, Response
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.routing import Match
from starlette.types import Message, Scope

from app import log, response_limits, waits
from app.models import AnsweredPart, LeftBecause, LeftPart
from app.refusal import GroupTooLarge

_X_PART: Final = "x-part"

PART: Final = {_X_PART: True}
"""The `openapi_extra` of a GET route that may answer in a group. A route declares it alone, or
merged into its own `openapi_extra`."""

GROUP_REASON: Final = "a group of reads holds the answer of each of its parts"

PATHS_AT_MOST: Final = 256
"""The most paths one `GET /reads` names. A group past it is refused with `422`."""

_CARRIED: Final = ("asgi", "http_version", "scheme", "server", "client")

_NOT_CARRIED: Final = (b"content-length", b"x-request-id")

_ENVELOPE: Final = (b'{"parts":[', b"]}")


@dataclass(frozen=True)
class Limits:
    """What one `GET /reads` runs: each part within `part_bound_seconds`, at most `max_parts`
    parts, and `part_concurrency` parts at once."""

    part_bound_seconds: float
    max_parts: int
    part_concurrency: int

    def __post_init__(self) -> None:
        if min(self.max_parts, self.part_concurrency) < 1 or not (
            math.isfinite(self.part_bound_seconds) and self.part_bound_seconds > 0
        ):
            raise ValueError(
                f"{self} reads nothing: each count is at least one, and the bound is above zero"
            )
        if self.longest_seconds() >= waits.REQUEST_BUDGET_SECONDS:
            raise ValueError(
                f"{self} may take {self.longest_seconds()} s, and a request has a bound under "
                f"{waits.REQUEST_BUDGET_SECONDS} s"
            )

    def longest_seconds(self) -> float:
        """The longest a group of `max_parts` parts takes, `part_concurrency` at a time."""
        return math.ceil(self.max_parts / self.part_concurrency) * self.part_bound_seconds


def instrument(app: FastAPI, limits: Limits) -> None:
    """Keep `limits` on `app.state.read_limits`, for `answer` and for the lifespan."""
    app.state.read_limits = limits


@dataclass(frozen=True)
class _Outcome:
    answer: AnsweredPart | LeftPart
    route: str | None


async def answer(request: Request, paths: Sequence[str]) -> Response:
    """The answer of each part `paths` names, in their order, under the ceiling of the response.
    A part that raises fails the group. Writes one `part left its group` line for each route and
    reason of the parts that left. Raises `GroupTooLarge` when the ceiling cannot name each path as
    a part that left."""
    limits: Limits = request.app.state.read_limits
    ceiling = response_limits.ceiling_of(request)
    if sum(map(len, _ENVELOPE)) + len(paths) * _LEFT_AT_MOST > ceiling:
        raise GroupTooLarge
    response_limits.allow(request, max_bytes=ceiling, reason=GROUP_REASON)
    at_once = asyncio.Semaphore(limits.part_concurrency)
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(_part(request, path, limits.part_bound_seconds, at_once))
            for path in paths[: limits.max_parts]
        ]
    past_the_cap = [_Outcome(_left("past the cap"), None) for _ in paths[limits.max_parts :]]
    outcomes = _within([task.result() for task in tasks] + past_the_cap, ceiling)
    left = Counter(
        (outcome.route, outcome.answer.reason)
        for outcome in outcomes
        if isinstance(outcome.answer, LeftPart)
    )
    for (route, reason), parts in left.items():
        log.part_left(route=route, reason=reason, parts=parts)
    body = b",".join(outcome.answer.model_dump_json().encode() for outcome in outcomes)
    opening, closing = _ENVELOPE
    return Response(opening + body + closing, media_type="application/json")


async def _part(
    request: Request, path: str, bound_seconds: float, at_once: asyncio.Semaphore
) -> _Outcome:
    scope = _scope_of(request, path)
    route = _part_route(request.app, scope)
    if route is None:
        return _Outcome(_left("not a part"), None)
    held = _Held()
    async with at_once:
        bound = asyncio.timeout(bound_seconds)
        try:
            async with bound:
                with waits.in_a_part():
                    await request.app(scope, _no_body, held.send)
        except TimeoutError:
            if not bound.expired():
                raise
            return _Outcome(_left("timed out"), route)
    return _Outcome(held.answered(), route)


def _scope_of(request: Request, path: str) -> Scope:
    parent = request.scope
    root = parent.get("root_path", "")
    route_path, _, query = path.partition("?")
    headers = [(name, value) for name, value in parent["headers"] if name not in _NOT_CARRIED]
    request_id = log.current_request_id()
    if request_id is not None:
        headers.append((b"x-request-id", request_id.encode()))
    return {
        **{key: parent[key] for key in _CARRIED if key in parent},
        "type": "http",
        "method": "GET",
        "root_path": root,
        "path": root + unquote(route_path),
        "raw_path": (root + route_path).encode(),
        "query_string": query.encode(),
        "headers": headers,
        "state": {},
    }


def _part_route(app: FastAPI, scope: Scope) -> str | None:
    for context in iter_route_contexts(app.routes):
        match, _ = context.matches(scope)
        if match is Match.FULL:
            route = context.original_route
            declared = (
                isinstance(route, APIRoute)
                and route.openapi_extra is not None
                and route.openapi_extra.get(_X_PART) is True
            )
            return context.path if declared else None
    return None


async def _no_body() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


class _Held:
    """One part's response, held whole."""

    def __init__(self) -> None:
        self._status: int | None = None
        self._headers: dict[str, str] = {}
        self._body: bytearray = bytearray()
        self._complete: bool = False

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self._status = message["status"]
            self._headers = {
                name.decode("latin-1").lower(): value.decode("latin-1")
                for name, value in message.get("headers", [])
                if name.lower() != b"content-length"
            }
        elif message["type"] == "http.response.body":
            self._body.extend(message.get("body", b""))
            self._complete = not message.get("more_body", False)

    def answered(self) -> AnsweredPart:
        if self._status is None or not self._complete:
            raise RuntimeError("a part of GET /reads ended without its whole response")
        return AnsweredPart(
            kind="answered",
            status=self._status,
            headers=self._headers,
            body=self._body.decode("utf-8"),
        )


def _left(reason: LeftBecause) -> LeftPart:
    return LeftPart(kind="left", reason=reason)


_LEFT_AT_MOST: Final = max(
    len(_left(reason).model_dump_json()) + 1 for reason in get_args(LeftBecause)
)


def _within(outcomes: Sequence[_Outcome], ceiling: int) -> list[_Outcome]:
    """`outcomes`, with each answer that does not fit under `ceiling` replaced by a part that left
    as too large. Each later outcome keeps room to leave."""
    room = ceiling - sum(map(len, _ENVELOPE))
    kept: list[_Outcome] = []
    for index, outcome in enumerate(outcomes):
        later = (len(outcomes) - index - 1) * _LEFT_AT_MOST
        size = len(outcome.answer.model_dump_json()) + 1
        if isinstance(outcome.answer, AnsweredPart) and size + later > room:
            outcome = _Outcome(_left("too large"), outcome.route)
            size = len(outcome.answer.model_dump_json()) + 1
        room -= size
        kept.append(outcome)
    return kept
