"""What `app.store.pg` decides before it reaches a server."""

import pytest

from app.store.pg import Timeouts


@pytest.mark.parametrize("field", ["statement", "idle_in_transaction", "acquire"])
def test_a_bound_postgres_would_read_as_none_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match="under a millisecond"):
        Timeouts(**{field: 0})
