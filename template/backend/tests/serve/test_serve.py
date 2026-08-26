"""The one-origin entrypoint: what it mounts, what it falls back to, and what it refuses.

Every test here stands on a real `TestClient`, which drives the ASGI lifespan the way a server
does. That matters more than usual: the failure this module exists to prevent is a mounted
application whose lifespan never ran, and it is invisible to anything that calls a handler
directly.
"""

import inspect
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import serve
from app.main import create_app
from app.serve import (
    ASSETS,
    INDEX,
    MAX_BODY_BYTES,
    SECURITY_HEADERS,
    ASGIApp,
    Message,
    Scope,
    create_server,
)
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


def test_the_shell_carries_the_headers_a_proxy_would_have_set(client: TestClient) -> None:
    """Nothing is in front of this process, so it is the only thing that can send these."""
    headers = client.get("/").headers
    for header, value in SECURITY_HEADERS.items():
        assert headers[header] == value


def test_the_api_carries_them_too(client: TestClient) -> None:
    """The wrapper sits outside the mount rather than inside it, so one application of the
    policy covers both halves and the mounted one cannot be left out by being added later."""
    headers = client.get("/api/tasks").headers
    for header, value in SECURITY_HEADERS.items():
        assert headers[header] == value


def test_the_policy_confines_scripts_and_framing_to_this_origin(client: TestClient) -> None:
    """The four directives that decide whether an injected string becomes an origin's worth of
    trouble. `style-src` is deliberately not among them, and `script-src` holds only while the
    build emits no inline script."""
    policy = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'self'" in policy
    assert "frame-ancestors 'none'" in policy


def test_the_transport_policy_does_not_claim_subdomains(client: TestClient) -> None:
    """Neither `includeSubDomains` nor `preload` ships from a template. Both are decisions
    about a domain rather than about this code, and `docs/adr/0006` says why."""
    transport = client.get("/").headers["strict-transport-security"]
    assert transport.startswith("max-age=")
    assert "includeSubDomains" not in transport
    assert "preload" not in transport


def test_a_body_over_the_cap_is_refused_before_the_route_sees_it(client: TestClient) -> None:
    """Nothing else in this process bounds a request body, and the process has to survive the
    refusal: a cap that took the server down with it would be the denial it exists to stop."""
    answered = client.post("/api/tasks", json={"title": "x" * (MAX_BODY_BYTES + 1)})
    assert answered.status_code == 413
    assert client.get("/api/tasks").status_code == 200


def test_a_body_within_the_cap_reaches_the_route(client: TestClient) -> None:
    """The cap has to let the application through, which is the half a refusal cannot show."""
    answered = client.post("/api/tasks", json={"title": "a task"})
    assert answered.status_code == 201, answered.text
    assert answered.json()["title"] == "a task"


def test_a_bodied_request_that_will_not_say_its_length_is_refused(client: TestClient) -> None:
    """A cap read from `content-length` is only a cap if the header is required: chunked with no
    length is otherwise the way around it."""
    answered = client.post("/api/tasks", content=iter([b'{"title": "smuggled"}']))
    assert answered.status_code == 411


async def drive(
    app: ASGIApp,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    version: str = "1.1",
) -> Message:
    """Push one request through the ASGI application and hand back its response start.

    For the cases httpx cannot construct. It re-encodes a header value on the way out — a
    latin-1 `content-length` arrives as two utf-8 bytes and stops being the input under test —
    and it speaks only HTTP/1.1, so a transport that frames a body without headers is out of
    reach entirely. Both are real over the wire, so both are driven at the layer that has them.
    """
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"A" * 4096, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": version,
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "https",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 443),
    }
    await app(scope, receive, send)
    return sent[0]


def test_a_length_that_is_not_a_number_is_refused_rather_than_crashed_on(
    client: TestClient,
) -> None:
    """A length this cannot parse is a refusal, not an exception on the way to one."""
    for value in ("1.5", " 12", "-1", "", "12abc"):
        unparsable = client.build_request("POST", "/api/tasks", json={"title": "x"})
        unparsable.headers["content-length"] = value
        assert client.send(unparsable).status_code == 400, f"{value!r} was not refused"


async def test_a_length_that_is_a_digit_but_not_a_number_is_refused(bundle: Path) -> None:
    """`'²'.isdigit()` is `True` while `int('²')` raises, so screening with the wrong predicate
    turns this 400 into an unhandled 500.

    U+00B2 is one latin-1 byte, which a header value may carry as obs-text, so the input is
    reachable over the wire. It is not reachable through httpx, which re-encodes it to two utf-8
    bytes that decode back as something no predicate calls a digit — a test that goes through a
    client passes here against either spelling, for the wrong reason.
    """
    started = await drive(
        create_server(bundle), "POST", "/api/tasks", [(b"content-length", b"\xb2")]
    )
    assert started["status"] == 400


async def test_a_body_on_a_transport_that_frames_it_without_headers_is_refused(
    bundle: Path,
) -> None:
    """HTTP/2 makes both framing headers optional, so on it a bodied `DELETE` can announce
    nothing at all — and a rule keyed on the verb waves it through exactly as the chunked one
    was waved through."""
    started = await drive(create_server(bundle), "DELETE", "/api/tasks/1", [], version="2")

    assert started["status"] == 411
    named = {name.lower() for name, _ in started["headers"]}
    assert b"content-security-policy" in named


def test_a_bodied_method_declaring_nothing_at_all_is_refused(client: TestClient) -> None:
    """The second half of the rule, and the half no ordinary client can reach.

    Under HTTP/1.1 a request with neither `content-length` nor `transfer-encoding` has no body,
    so `CARRY_BODIES` never fires there and every normal client sends one header or the other.
    It is the backstop for a transport that makes neither compulsory — HTTP/2 frames a body with
    neither — and it has to be built by hand because httpx will always supply a length.
    """
    unframed = client.build_request("POST", "/api/tasks", json={"title": "unframed"})
    del unframed.headers["content-length"]
    assert "transfer-encoding" not in unframed.headers

    assert client.send(unframed).status_code == 411


def test_a_chunked_body_is_refused_whatever_the_method(client: TestClient) -> None:
    """The cap reads `content-length`, so anything arriving without one is unbounded no matter
    which method carries it. Asking only the methods that usually have a body left `DELETE` and
    `GET` free to stream one straight past — a chunked `DELETE` of any size was answered."""
    for method in ("DELETE", "GET", "POST", "PATCH"):
        answered = client.request(method, "/api/tasks", content=iter([b"A" * 64]))
        assert answered.status_code == 411, f"{method} was not refused: {answered.status_code}"


def test_a_bodiless_request_needs_no_length(client: TestClient) -> None:
    """The requirement is on the methods that carry bodies and on no others, or every read in
    the application would need a header it has no reason to send."""
    assert client.get("/api/tasks").status_code == 200
    assert client.delete("/api/tasks/1").status_code in (204, 404)


def test_a_refusal_still_carries_the_headers(client: TestClient) -> None:
    """A response this module writes itself is still a response the browser applies a policy
    to, so the refusals must not be the ones that go out bare."""
    answered = client.post("/api/tasks", json={"title": "x" * (MAX_BODY_BYTES + 1)})
    assert answered.status_code == 413
    assert "content-security-policy" in answered.headers


def test_a_server_error_still_carries_the_headers(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A policy that lapses exactly when something breaks is not a policy.

    Starlette wraps every user middleware in its own error handler, so headers applied *as* a
    middleware are missed by the 500 that handler writes — the middleware raised on the way out
    and never reached them. The route that fails here is the catch-all, which is the one
    serving every HTML page, and HTML with no policy is the case the policy exists for.

    The shell is removed *after* the server is built rather than a function being replaced: a
    deploy that swaps the directory beneath a live process is how this actually happens, it
    needs no private name to arrange, and the build-time check cannot catch it.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = create_server(bundle)
    (bundle / INDEX).unlink()

    with TestClient(server, raise_server_exceptions=False) as fresh:
        answered = fresh.get("/some/client/route")

    assert answered.status_code == 500
    for header, value in SECURITY_HEADERS.items():
        assert answered.headers[header] == value


DECLARED_HERE = re.compile(r"@server\.(?:websocket|get|post|put|patch|delete|head|options)\(")
ADDED_HERE = re.compile(r"server\.add_(?:api_)?(?:route|websocket_route)\(")
MOUNTED_HERE = re.compile(r"server\.mount\(")
"""Every way a surface gets onto this origin, counted rather than named.

`MOUNTED_HERE` used to capture an identifier — `mount\\(\\s*([A-Za-z_]\\w*)` — which matched
`server.mount(PREFIX, api)` and **not** `server.mount("/admin", opaque)`, so a mount written
the ordinary way left the captured list unchanged and the assertion passed over it. Counting
the calls has no such hole: one `mount`, one route decorator, and nothing added imperatively.

`websocket` and `add_route` are here because a route need not arrive through an HTTP-verb
decorator, and each of those is a way onto the deployment's own origin that the guarantee in
`tests/routes/` never sees."""


def test_the_server_adds_exactly_the_bundle_surface() -> None:
    """What this module puts in front of the API, named rather than counted.

    `tests/routes/test_guarantee.py` drives every route and demands a refusal, and it cannot be
    pointed at this application: a mount reports its routes *without* its prefix, so `/tasks`
    read off the server does not match `/api/tasks` when driven, falls through to the shell
    fallback, and is answered `200` with HTML. A route added here is therefore invisible to the
    guarantee, and it would be a route on the deployment's own origin that nothing checks.

    Read from the source because `create_server` returns the confined callable rather than the
    application — the routes cannot be read back off what it hands out. Static, and the thing it
    is watching for is a decorator somebody typed, which is exactly what source shows.
    """
    source = inspect.getsource(serve)

    assert len(DECLARED_HERE.findall(source)) == 1, (
        "app/serve.py declares a route besides the bundle fallback. Routes belong on `router` "
        "in app/routes.py, where the tenant is resolved and the guarantee test can drive them."
    )
    assert ADDED_HERE.findall(source) == [], (
        "app/serve.py adds a route imperatively, which no decorator scan would have shown. "
        "Routes belong on `router` in app/routes.py."
    )
    assert len(MOUNTED_HERE.findall(source)) == 1, (
        "app/serve.py mounts something besides the API, so there is a surface on this origin "
        "that neither this file nor the guarantee test looks at."
    )
    assert "mount(PREFIX, api)" in source, (
        "the one mount in app/serve.py is no longer the API under its own prefix"
    )


def test_the_service_on_its_own_sets_none_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """`app.main` is what runs behind a real proxy, and an edge's headers are that proxy's to
    set. Two `content-security-policy` headers are enforced as their intersection, so a second
    copy from here would silently narrow whatever the deployment's edge allowed."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app()) as bare:
        headers = bare.get("/tasks").headers
    for header in SECURITY_HEADERS:
        assert header not in headers
