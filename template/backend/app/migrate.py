"""`python -m app.migrate` — the release step.

Applying the schema is an operator action, not something the application does on the way up.
This is the tool that does it, and it is the only thing that reads `DATABASE_OWNER_URL`: the
application refuses to start if it can see that variable at all, so a compromised web process
cannot reach the credential that could drop the tables.

Wire it into whatever your platform calls a release command — `release_command` on Fly, a
pre-deploy command on Railway or Render, a `pre-upgrade` Job on Kubernetes, a one-off task on
ECS, the `migrate` service in `deploy/compose.yaml` here. It is idempotent, it serialises on
an advisory lock so two releases cannot race, and it exits 0 when the database is already
current, which is what makes it safe to wire into a hook that may fire more than once.

Skip it and the new version refuses to start, naming the entries it needs. That refusal is the
enforcement: a release step nobody wired up fails the deploy instead of quietly serving
queries against columns that are not there.
"""

import asyncio
import os
import sys
from typing import Any, Final

from app.store.conn import SCHEMA_ENV
from app.store.migrate import apply

OWNER_URL_ENV: Final = "DATABASE_OWNER_URL"
"""Read here and nowhere else. `wiring.py` refuses to build an application that can see it."""

_USAGE = f"""usage: python -m app.migrate

Applies the schema. Reads {OWNER_URL_ENV} for a connection that may create, and
{SCHEMA_ENV} for the schema name (default: app).

The role it connects as should be a member of <schema>_owner; the apply issues
SET ROLE so that every object ends up owned by it and the default privileges in
deploy/roles.sql bind. Where that role does not exist -- a developer's own
Postgres -- it applies as whoever connected, which is the bootstrap path.
"""


async def _apply(dsn: str, schema: str | None) -> str:
    conn: Any = await _connect(dsn)
    try:
        return await apply(conn, schema)
    finally:
        await conn.close()


async def _connect(dsn: str) -> Any:
    import asyncpg

    return await asyncpg.connect(dsn=dsn)


def main(argv: list[str]) -> int:
    if argv:
        sys.stderr.write(_USAGE)
        return 2
    dsn = os.environ.get(OWNER_URL_ENV)
    if dsn is None or dsn.strip() == "":
        sys.stderr.write(f"migrate: {OWNER_URL_ENV} is unset.\n\n{_USAGE}")
        return 2
    version = asyncio.run(_apply(dsn, os.environ.get(SCHEMA_ENV)))
    sys.stdout.write(f"migrate: schema is at {version}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
