"""The app `create_app` builds, started: on which substrate, and with a store of its own."""

from fastapi.testclient import TestClient

from app.main import create_app

ONLY_IN_THE_FIRST = "only in the first app"


def titles(client: TestClient) -> set[str]:
    return {task["title"] for task in client.get("/tasks").json()}


def test_it_comes_up_on_the_in_memory_substrate() -> None:
    """With no `DATABASE_URL`, the started app serves from the in-memory substrate: the one the
    development loop and the contract suite run on."""
    app = create_app()
    with TestClient(app):
        assert app.state.database.name == "memory"


def test_two_apps_do_not_share_a_store() -> None:
    """Each app `create_app` builds holds a substrate of its own, so one process may hold two
    apps and no store needs a `reset()`."""
    with TestClient(create_app()) as first, TestClient(create_app()) as second:
        assert first.post("/tasks", json={"title": ONLY_IN_THE_FIRST}).status_code == 201

        assert ONLY_IN_THE_FIRST in titles(first)
        assert ONLY_IN_THE_FIRST not in titles(second)
