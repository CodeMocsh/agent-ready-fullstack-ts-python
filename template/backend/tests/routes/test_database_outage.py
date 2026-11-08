"""A route the database does not serve answers `503` with a time to ask again, and names the
refusal. `docs/adr/template/0022`."""

import math
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.testclient import TestClient

from app import log
from app.deps import database_of
from app.refusal import NoConnectionFree
from app.store.budget import Draws
from app.store.pg import TIMEOUTS, PostgresDatabase
from tests.conftest import Logged
from tests.routed import routed
from tests.store.test_pg import a_port_nothing_answers

router = APIRouter()


@router.get("/a-job")
async def a_job(request: Request) -> None:
    _ = await database_of(request).jobs("a-tenant").read(str(uuid4()))


def test_a_route_whose_database_nothing_answers_is_refused_with_a_time_to_ask_again(
    logged: Logged,
) -> None:
    database = PostgresDatabase(
        dsn=f"postgresql://app@127.0.0.1:{a_port_nothing_answers()}/app",
        draws=Draws(instances=1, in_process={}, outside={}),
        kinds=lambda _: {},
    )
    app = routed(router, database=database)
    log.configure(None)

    with TestClient(app) as client:
        answered = client.get("/a-job")

    assert (answered.status_code, answered.headers["retry-after"], answered.json()) == (
        503,
        str(math.ceil(TIMEOUTS.acquire)),
        {"detail": NoConnectionFree.description},
    )
    assert [
        (line["refusal"], line["cause"])
        for line in logged()
        if line["message"] == "database unavailable"
    ] == [("ConnectionRefused", "ConnectionRefusedError")]
