"""The connection budget: the share of the server's connections that each process may hold, so
that the connections of every process fit inside Postgres's `max_connections`.

The template owns this module and updates it. What the project draws beside the template's pool
is the project's, in `app/connection_budget.py`. `docs/adr/template/0022`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.deployment import INSTANCES_ENV

MAX_CONNECTIONS: Final = "max_connections"
RESERVING: Final = ("superuser_reserved_connections", "reserved_connections")
"""The settings that keep connections for roles the application does not connect as.
`reserved_connections` exists from Postgres 16."""

_LIMITS: Final = "SELECT name, setting::int FROM pg_settings WHERE name = ANY($1::text[])"


class OverBudget(RuntimeError):
    """The declared draws do not fit inside the server's `max_connections`, so the process
    refuses to start."""


@dataclass(frozen=True)
class ServerLimits:
    """What the server allows: `max_connections`, and how many of them it keeps for other
    roles."""

    max_connections: int
    reserved: int


@dataclass(frozen=True)
class Draws:
    """Everything that holds connections to the server, as the deployment and the project
    declare it.

    `instances` is the most processes that hold connections at once, the processes of a revision
    that drains during a rollout included. `in_process` names each pool or connection that one
    process holds beside the template's pool. `outside` names each holder that is not one of the
    processes, such as the release step. Each value is the most connections that holder has
    open at once. `pool` fixes the size of the template's pool. `None` gives the pool what
    remains of the process's share.
    """

    instances: int
    in_process: Mapping[str, int]
    outside: Mapping[str, int]
    pool: int | None = None

    def __post_init__(self) -> None:
        if self.instances < 1:
            raise ValueError(f"{self.instances} instances hold no connection")
        negative = [
            name
            for draws in (self.in_process, self.outside)
            for name, held in draws.items()
            if held < 0
        ]
        if negative:
            raise ValueError(f"{', '.join(negative)} hold fewer than no connections")
        if self.pool is not None and self.pool < 1:
            raise ValueError(f"a pool of {self.pool} connections lends none")


@dataclass(frozen=True)
class ConnectionBudget:
    """The share of each process, and the size of the template's pool inside it."""

    limits: ServerLimits
    draws: Draws
    per_process: int
    pool: int


async def server_limits(conn: Any) -> ServerLimits:
    """The limits of the server `conn` is connected to. Raises `RuntimeError` when the server
    does not report `max_connections`."""
    settings = {
        row["name"]: row["setting"]
        for row in await conn.fetch(_LIMITS, [MAX_CONNECTIONS, *RESERVING])
    }
    if MAX_CONNECTIONS not in settings:
        raise RuntimeError(f"the server reports no {MAX_CONNECTIONS} in pg_settings")
    return ServerLimits(
        max_connections=settings[MAX_CONNECTIONS],
        reserved=sum(held for name, held in settings.items() if name in RESERVING),
    )


def budgeted(limits: ServerLimits, draws: Draws) -> ConnectionBudget:
    """The budget of each process under `limits`. Raises `OverBudget` when the template's pool
    gets no connection, or when what one process holds is more than its share."""
    outside = sum(draws.outside.values())
    per_process = (limits.max_connections - limits.reserved - outside) // draws.instances
    beside = sum(draws.in_process.values())
    pool = per_process - beside if draws.pool is None else draws.pool
    if pool < 1 or beside + pool > per_process:
        raise OverBudget(
            f"Postgres allows {limits.max_connections} connections and keeps {limits.reserved} "
            f"for other roles. {_named(draws.outside)} hold {outside} outside the processes. "
            f"That leaves {per_process} for each of {draws.instances} processes. Each process "
            f"holds {beside} beside the pool ({_named(draws.in_process)}), and the pool needs "
            f"{max(pool, 1)}. Lower {INSTANCES_ENV} or a draw in app/connection_budget.py, or "
            f"raise max_connections."
        )
    return ConnectionBudget(limits=limits, draws=draws, per_process=per_process, pool=pool)


def _named(held: Mapping[str, int]) -> str:
    if not held:
        return "nothing"
    return ", ".join(f"{name}: {count}" for name, count in held.items())
