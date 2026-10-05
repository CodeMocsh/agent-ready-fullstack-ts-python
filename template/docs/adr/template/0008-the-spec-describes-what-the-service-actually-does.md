# 0008. The spec describes what the service actually does, including how it refuses

Date: 2026-10-05

## Status

Accepted.

## Context

`create_app` in `app/main.py` carries two settings that look like noise and are necessary. The
code that sets them does not show why. The zero-comments rule keeps the reason out of
`app/main.py`, so this record holds it. Each force below is observed on a running system.

With the FastAPI default, a model whose input and output schemas differ is emitted two times, as
`Task-Input` and `Task-Output`. The type aliases in `frontend/src/api/` name `Task`. The frontend
stops compiling when a model gets a field with a default. The failure occurs in the other half,
at a name that nobody wrote.

FastAPI redirects `/tasks/` to `/tasks` with a `307`. The `Location` it sends carries the
backend's own origin. Through the dev proxy, the browser then makes a cross-origin request to an
origin that sends no CORS headers:

```
GET http://localhost:5173/api/tasks/  ->  307   location: http://localhost:8000/tasks
```

The request fails far from its cause, and it shows the internal topology to the browser.

An error status declared without a model says that the response has no body. `HTTPException`
returns `{"detail": ...}`. The spec is then false, and the typed mock handlers in
`frontend/src/mocks/` refuse to mock the response. That type error is the mechanism, and it
occurs before a test runs.

`request` in `frontend/src/api/client.ts` reads `detail` off any non-2xx body and throws that
sentence. The UI shows it. When the body has no readable `detail`, the client throws a sentence
such as `PATCH /tasks/x failed with 404`. That is a worse sentence for a screen than the one the
service wrote.

## Decision

- `create_app` sets `separate_input_output_schemas=False`. Each model is one schema, with its
  domain name.
- `create_app` sets `app.router.redirect_slashes = False`. A trailing slash is a plain `404`. The
  spec does not change, because this is behaviour and not contract.
  `test_a_trailing_slash_is_a_404_and_never_a_redirect` in `tests/routes/test_tasks.py` holds it.
- Every route declares a model for every status code that it can return. `responses(...)` in
  `app/refusal.py` gives each refusal status `ErrorBody` as its model.
  [0006](0006-a-refusal-is-a-class-declared-once.md) records how a refusal is declared.
- FastAPI adds `422`, with `HTTPValidationError` and `ValidationError`, to each route that takes
  a parameter or a body. They describe real behaviour, and they stay in `openapi.json`.
- A refusal's `detail` is part of the contract. The service and the mock handlers agree on it
  the same way that they agree on a status code.

## Considered options

- **Let the two schemas split and rename the aliases.** The names then follow the emission rules
  of FastAPI and not the domain. Every consumer changes when a model gets a default.
- **Keep redirects on and add CORS.** It configures a cross-origin path for a deployment that has
  one origin, to fix a redirect that nobody wants. The browser must never see the backend's
  origin.
- **Declare error status codes without models.** The spec then states something false. The cost
  is on the frontend, as a response that the mocks cannot return.

## Consequences

- `openapi.json` contains `422` responses and validation schemas that look like clutter. Do not
  remove them from the committed artifact.
- A change to `separate_input_output_schemas` or to an error model changes the committed contract
  artifacts. `make openapi-check` fails until `make openapi` regenerates them.
- A change to `redirect_slashes` changes no byte of the spec. The contract flow cannot see it,
  and every other gate stays green without it. Only
  `test_a_trailing_slash_is_a_404_and_never_a_redirect` refuses it, by failing on the `307`.
- That test is in the tests of the example resource. A project that deletes the example resource
  and its tests must keep an equivalent test on one of its own routes.
