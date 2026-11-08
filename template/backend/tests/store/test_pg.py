"""What `app.store.pg` decides before it reaches a server."""

import math
import socket

import pytest

from app.store.budget import Draws
from app.store.pg import TIMEOUTS, ConnectionRefused, PostgresDatabase, Timeouts


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
