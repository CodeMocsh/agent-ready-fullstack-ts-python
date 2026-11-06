"""Admission: how many requests of one actor this process serves at once, how fast it takes them,
and how many streams it holds open for one. An actor is the issuer and the subject a credential
names. A request over a bound is refused with `TooManyRequests`, a `429` with `Retry-After`, and
records no request event.

The template owns this module and updates it. The numbers are the project's, in
`app/admission_limits.py`, and a deployment may replace each one: `wiring.build_admission`.
`docs/adr/template/0023`.

**A policy enforces or evaluates.** Enforced, a request over a bound is refused, and the line
`admission refused` names the bound. Evaluated, it is admitted, and the line
`admission would refuse` names the bound.

**The count is per process.** A deployment of N processes admits up to N times each limit for
one actor. `DATABASE_INSTANCES` is that N, and `docs/adr/template/0022` bounds it.

**A request holds its place until its response starts**, after its request event is recorded. A
stream then holds a place of its own until its response ends, so a stream never holds a place
among the requests.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import audit_events, log
from app.identity.shared import Actor
from app.refusal import Bound, TooManyRequests

Release = Callable[[], None]
Key = tuple[str, str]

_ADMISSION: Final = "admission"
_REQUEST: Final = "admitted_request"
_STREAM: Final = "admitted_stream"


@dataclass(frozen=True)
class Limits:
    """What this process admits for one actor: `in_flight` requests at once, `per_second` on
    average after a `burst` of requests, and `streams` streams open at once."""

    in_flight: int
    per_second: float
    burst: int
    streams: int

    def __post_init__(self) -> None:
        if min(self.in_flight, self.burst, self.streams) < 1 or not (
            math.isfinite(self.per_second) and self.per_second > 0
        ):
            raise ValueError(
                f"{self} admits nothing: each count is at least one, and the rate is above zero"
            )


@dataclass(frozen=True)
class Policy:
    """`limits`, and whether a request over one is refused (`enforced`) or only reported."""

    limits: Limits
    enforced: bool


@dataclass
class _Account:
    tokens: float
    at: float
    in_flight: int = 0
    streams: int = 0


class Admission:
    """The places of every actor in this process, under one `Policy`. Not safe across threads:
    call it on the event loop."""

    def __init__(self, policy: Policy, clock: Callable[[], float] = time.monotonic) -> None:
        self.limits: Final = policy.limits
        self._enforced: Final = policy.enforced
        self._clock: Final = clock
        self._accounts: Final[dict[Key, _Account]] = {}
        self._swept_at: float = clock()

    def admit(self, actor: Actor) -> Release:
        """Take a place for one request of `actor`, and return what gives it back. Raises
        `TooManyRequests` when `actor` has `in_flight` requests here, or has spent its burst
        faster than `per_second`. Its `Retry-After` is the seconds until the next request fits
        the rate, or the spacing of the rate when one already fits, and at least 1. Under a
        policy that evaluates, admits the request outside the count and reports the bound
        instead, so the reports match what an enforced policy would refuse."""
        account = self._account(actor)
        if account.in_flight >= self.limits.in_flight:
            return self._over("in_flight", account)
        if account.tokens < 1:
            return self._over("rate", account)
        account.tokens -= 1
        account.in_flight += 1

        def release() -> None:
            account.in_flight -= 1

        return _once(release)

    def stream(self, actor: Actor) -> Release:
        """Take a place for one stream of `actor`, and return what gives it back. Raises
        `TooManyRequests` when `actor` has `streams` streams open here, with the `Retry-After`
        that `admit` gives. Under a policy that evaluates, admits the stream outside the count and
        reports the bound instead."""
        account = self._account(actor)
        if account.streams >= self.limits.streams:
            return self._over("streams", account)
        account.streams += 1

        def release() -> None:
            account.streams -= 1

        return _once(release)

    def actors(self) -> int:
        """How many actors this process holds an account for. An account is dropped once its
        actor holds no place and its burst is whole again."""
        return len(self._accounts)

    def _account(self, actor: Actor) -> _Account:
        now = self._clock()
        self._sweep(now)
        key = (actor.issuer, actor.subject)
        account = self._accounts.setdefault(key, _Account(tokens=self.limits.burst, at=now))
        account.tokens = min(
            self.limits.burst, account.tokens + (now - account.at) * self.limits.per_second
        )
        account.at = now
        return account

    def _sweep(self, now: float) -> None:
        refill = self.limits.burst / self.limits.per_second
        if now - self._swept_at < refill:
            return
        self._swept_at = now
        for key, account in list(self._accounts.items()):
            if account.in_flight == 0 and account.streams == 0 and now - account.at >= refill:
                del self._accounts[key]

    def _over(self, bound: Bound, account: _Account) -> Release:
        if not self._enforced:
            log.admission_would_refuse(bound)
            return _nothing_held
        log.admission_refused(bound)
        raise TooManyRequests(retry_after=self._wait(account), bound=bound)

    def _wait(self, account: _Account) -> int:
        short = 1 - account.tokens if account.tokens < 1 else 1
        return max(1, math.ceil(short / self.limits.per_second))


def _nothing_held() -> None:
    """The release of a request or a stream admitted outside the count."""


def _once(release: Release) -> Release:
    released = False

    def once() -> None:
        nonlocal released
        if not released:
            released = True
            release()

    return once


def instrument(app: FastAPI, policy: Policy) -> None:
    """Admit the requests `app` serves under `policy`, and keep it on
    `app.state.admission_policy` for the lifespan. Each build of the middleware stack holds one
    `Admission`. Call it after `request_events.instrument`, so a place comes back only once the
    request event is recorded."""
    app.state.admission_policy = policy
    app.add_middleware(AdmitsRequests, policy=policy)


def admit(request: Request, actor: Actor) -> None:
    """Take a place for `request` of `actor`, which `AdmitsRequests` gives back when the response
    starts. Call it once a credential is verified, before any store work. A request holds one
    place: a call on a request that holds one takes nothing. Raises `TooManyRequests` over a
    bound, and the request records no request event."""
    if getattr(request.state, _REQUEST, None) is not None:
        return
    try:
        release = _admission_of(request).admit(actor)
    except TooManyRequests:
        audit_events.unrecorded(request)
        raise
    setattr(request.state, _REQUEST, release)


def stream(request: Request, actor: Actor) -> None:
    """Take a place for the stream `request` opens, which `AdmitsRequests` gives back when the
    response ends. Call it in a route whose response stays open. Raises `TooManyRequests` over a
    bound, and the request records no request event. A request admitted outside the count, under
    a policy that evaluates, opens its stream outside the count too."""
    if getattr(request.state, _REQUEST, None) is _nothing_held:
        setattr(request.state, _STREAM, _nothing_held)
        return
    try:
        release = _admission_of(request).stream(actor)
    except TooManyRequests:
        audit_events.unrecorded(request)
        raise
    setattr(request.state, _STREAM, release)


def _admission_of(request: Request) -> Admission:
    admission: Admission | None = getattr(request.state, _ADMISSION, None)
    if admission is None:
        raise RuntimeError("no admission on this request: assemble did not instrument the app")
    return admission


class AdmitsRequests:
    """The middleware `instrument` adds."""

    def __init__(self, app: ASGIApp, policy: Policy) -> None:
        self._app: ASGIApp = app
        self._admission: Admission = Admission(policy)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state[_ADMISSION] = self._admission

        async def answering(message: Message) -> None:
            if message["type"] == "http.response.start":
                _give_back(state, _REQUEST)
            await send(message)

        try:
            await self._app(scope, receive, answering)
        finally:
            _give_back(state, _REQUEST)
            _give_back(state, _STREAM)


def _give_back(state: dict[str, object], place: str) -> None:
    release = state.pop(place, None)
    if callable(release):
        release()
