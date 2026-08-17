"""`make db-demo` -- two tenants against one table, so the isolation is visible.

Everything else about tenant isolation is proved by tests nobody watches. This prints it:
two tenants write to the same table, each reads only its own rows, and a connection that set
no tenant at all reads nothing.
"""

import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.identity import SENTINEL_TENANT
from app.store.conn import SCHEMA_ENV, resolve_schema
from app.store.ddl import TENANT_GUC
from app.wiring import DATABASE_URL_ENV

TENANTS = ("acme", "globex", SENTINEL_TENANT)


async def scoped(conn: Any, tenant: str, sql: str, *args: Any) -> Any:
    async with conn.transaction():
        await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, tenant)
        return await conn.fetchval(sql, *args)


async def demo(dsn: str, schema: str) -> None:
    import asyncpg

    conn: Any = await asyncpg.connect(dsn=dsn, server_settings={"search_path": schema})
    try:
        for tenant in TENANTS:
            await scoped(conn, tenant, "DELETE FROM tasks RETURNING 1")
        for tenant in TENANTS:
            await scoped(
                conn,
                tenant,
                "INSERT INTO tasks (id, tenant_id, title) VALUES (gen_random_uuid(), $1, $2) "
                "RETURNING id",
                tenant,
                f"a task belonging to {tenant}",
            )
        print(f"\nthree tenants have written to {schema}.tasks. each one reads:\n")
        for tenant in TENANTS:
            count = await scoped(conn, tenant, "SELECT count(*) FROM tasks")
            print(f"  {tenant:<10} {count} row(s)")
        loose = await conn.fetchval("SELECT count(*) FROM tasks")
        print(f"  {'(no tenant)':<10} {loose} row(s)   <- the policy, failing closed")
        print(
            "\nNo query above carried a WHERE clause. The policy in app/store/ddl.py did it,\n"
            "which is why a route that forgot to filter still cannot cross a tenant.\n"
        )
    finally:
        await conn.close()


def main() -> int:
    dsn = os.environ.get(DATABASE_URL_ENV)
    if not dsn:
        print(f"{DATABASE_URL_ENV} is unset. Run `make db` first.", file=sys.stderr)
        return 2
    asyncio.run(demo(dsn, resolve_schema(os.environ.get(SCHEMA_ENV))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
