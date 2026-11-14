"""The bounds on each wait on Postgres, and how Postgres reads a bound."""

from dataclasses import dataclass
from typing import Final

SMALLEST_BOUND: Final = 0.001


@dataclass(frozen=True)
class Timeouts:
    """How long each wait may last, in seconds, before it raises. Each is at least a
    millisecond, because Postgres reads a bound of zero as no bound at all."""

    statement: float = 5.0
    """Postgres cancels a statement that runs longer, and asyncpg raises `QueryCanceledError`."""

    idle_in_transaction: float = 10.0
    """Postgres ends a session that holds a transaction open and sends nothing. Its locks go
    with it."""

    acquire: float = 5.0
    """A request that finds every connection in use waits this long, then `AcquireTimedOut`."""

    def __post_init__(self) -> None:
        if min(self.statement, self.idle_in_transaction, self.acquire) < SMALLEST_BOUND:
            raise ValueError(f"{self} holds a bound under a millisecond, which Postgres ignores")

    @property
    def answer(self) -> float:
        """How long asyncpg waits for any answer before it raises `TimeoutError`. A second
        longer than `statement`, so Postgres reports a slow statement first."""
        return self.statement + 1.0


TIMEOUTS: Final = Timeouts()


def milliseconds(seconds: float) -> str:
    """A bound in seconds, as the number of milliseconds a Postgres setting reads."""
    return str(round(seconds * 1000))
