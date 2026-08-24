"""The store contract, against a real Postgres. The same class the memory suite runs.

Nothing here is memory-specific and nothing there is Postgres-specific: the tests live in
`tests/store_contract.py` and each substrate only supplies the `database` fixture, which for
this half is in `conftest.py`. Postgres starts empty and memory seeds three tasks, which is
what stops either suite from assuming its own seed rows.
"""

from tests.store_contract import TaskStoreContract


class TestThePostgresSubstrate(TaskStoreContract):
    substrate: str = "postgres"
