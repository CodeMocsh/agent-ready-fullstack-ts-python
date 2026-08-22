"""The one-origin entrypoint: what it mounts, what it falls back to, and what it refuses.

Every test here stands on a real `TestClient`, which drives the ASGI lifespan the way a server
does. That matters more than usual: the failure this module exists to prevent is a mounted
application whose lifespan never ran, and it is invisible to anything that calls a handler
directly.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.serve import ASSETS, INDEX, create_server
from app.wiring import BUNDLE_ENV, BundleMissing, build_bundle

SHELL = "<!doctype html><title>the shell</title>"
SCRIPT = "export const built = 1;\n"
HASHED = f"{ASSETS}/index-abc123.js"
SECRET = "the private key nobody asked this process for"
BESIDE = "secret.txt"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """What `make build` writes, in miniature: a shell and one hashed asset.

    In a directory of its own, with a readable file *beside* it that the bundle does not
    contain. That file is what makes the traversal test mean something: pointed at a path
    that does not exist either way, the test passes against a `_within` with no containment
    check at all, because `is_file()` answers no for the same reason the guard would.
    """
    (tmp_path / BESIDE).write_text(SECRET)
    built = tmp_path / "dist"
    (built / ASSETS).mkdir(parents=True)
    (built / INDEX).write_text(SHELL)
    (built / HASHED).write_text(SCRIPT)
    return built


@pytest.fixture
def client(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The in-memory substrate, which is what no `DATABASE_URL` means."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_server(bundle)) as fresh:
        yield fresh


def test_the_mounted_api_gets_its_lifespan(client: TestClient) -> None:
    """The regression this module exists for.

    Starlette does not run a mounted application's lifespan, so without the delegation nothing
    reaches `app.state` and this answers 500. `/tasks` is the probe because it needs the store
    to answer at all — a route that only read its own arguments would pass either way.
    """
    answered = client.get("/api/tasks")
    assert answered.status_code == 200, answered.text
    assert isinstance(answered.json(), list)


def test_the_prefix_is_stripped_before_the_api_matches(client: TestClient) -> None:
    """`/api/tasks` reaches a route declared as `/tasks`, and the bare path is the frontend's."""
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/tasks").text == SHELL


def test_an_unknown_api_path_is_refused_rather_than_given_the_shell(client: TestClient) -> None:
    """The fallback must stop at the mount. A JSON caller handed HTML fails at the parse, one
    stack frame away from anything that would name the missing route."""
    answered = client.get("/api/no-such-route")
    assert answered.status_code == 404
    assert answered.headers["content-type"].startswith("application/json")


def test_an_unknown_path_serves_the_shell_so_the_client_router_can_take_it(
    client: TestClient,
) -> None:
    """A reload on a deep link is a request this service has no route for, and answering it
    with a 404 is how a single-page application breaks on refresh."""
    assert client.get("/some/client/route").text == SHELL


def test_a_built_asset_is_served_from_the_bundle(client: TestClient) -> None:
    answered = client.get(f"/{HASHED}")
    assert answered.text == SCRIPT
    assert "javascript" in answered.headers["content-type"]


def test_a_missing_asset_is_a_404_and_never_the_shell(client: TestClient) -> None:
    """An asset name carries the build's hash, so a request for one the bundle does not hold is
    a stale document. The shell would arrive at a script tag as a page of HTML."""
    answered = client.get(f"/{ASSETS}/index-deadbee.js")
    assert answered.status_code == 404
    assert answered.text != SHELL


def test_a_path_cannot_escape_the_bundle(client: TestClient) -> None:
    """`held` is whatever the client wrote. Anything outside the bundle is not a file this
    process will read, whatever the traversal spells.

    The target exists and is readable, so this fails against a `_within` with no containment
    check rather than passing for want of anything to find.
    """
    answered = client.get(f"/..%2f{BESIDE}")
    assert SECRET not in answered.text
    assert answered.text == SHELL


def test_a_directory_without_a_shell_is_refused_at_build_rather_than_at_request(
    tmp_path: Path,
) -> None:
    """A server that came up on an empty directory answers every path with a file it never
    found, which reads as a routing bug rather than as a missing build."""
    with pytest.raises(BundleMissing, match=INDEX):
        create_server(tmp_path)


def test_an_unnamed_bundle_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BUNDLE_ENV, raising=False)
    with pytest.raises(BundleMissing, match=BUNDLE_ENV):
        build_bundle()


def test_a_named_bundle_is_the_one_the_environment_gives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BUNDLE_ENV, "/srv/bundle")
    assert build_bundle() == Path("/srv/bundle")
