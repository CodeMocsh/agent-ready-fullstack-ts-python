# The spec describes what the service actually does, including how it refuses

`app/main.py` carries two settings that look like noise and are load-bearing, and every route
declares a model for every status code it can return. None of that is inferable from the code
that sets it, and the zero-comments rule keeps the reasoning out of `main.py`, so it is here.

Each was verified against a running system rather than reasoned about.

## `separate_input_output_schemas=False`

With FastAPI's default, a model whose input and output schemas differ is emitted **twice**, as
`Task-Input` and `Task-Output`. The aliases in `frontend/src/api/types.ts` name `Task`, so the
frontend stops compiling the moment a model gains a field with a default. The failure arrives
in the other half, at a name nobody wrote.

## `redirect_slashes = False`

FastAPI redirects `/tasks/` to `/tasks` with a 307, and the `Location` it sends carries the
**backend's own origin**. Through the dev proxy that becomes a cross-origin request to an
origin serving no CORS headers:

```
GET http://localhost:5173/api/tasks/  ->  307   location: http://localhost:8000/tasks
```

In a browser it fails far from its cause and leaks internal topology. Turning redirects off
makes it a plain 404, and leaves the spec byte-identical, because this is behaviour rather
than contract.

## Every error response declares a model

A `404` declared without one says the response has **no body**, while `HTTPException` returns
`{"detail": ...}` — so the spec lies, and the typed mock handlers refuse to mock it. The type
error is the mechanism working before a single test runs.

This is why `422` also appears on every route with a parameter or a body, with
`HTTPValidationError` and `ValidationError` in the schemas. FastAPI adds them and they describe
real behaviour. Never "clean" them out of the committed artifact.

## A refusal's `detail` is part of the contract

`src/api/client.ts` reads `detail` off any non-2xx body and throws that sentence, so it is what
the UI renders. The two implementations of the contract have to agree on it the way they agree
on a status code. When a response carries no readable `detail` the client falls back to
`"PATCH /tasks/x failed with 404"`, which is a worse thing to put on a screen than the sentence
the service already wrote.

## Considered options

**Letting the two schemas split and renaming the aliases.** Rejected: the names would then
track FastAPI's emission rules rather than the domain, and every consumer changes when a model
gains a default.

**Leaving redirects on and adding CORS.** Rejected: it configures a cross-origin story for a
deployment that has one origin, to fix a redirect nobody wants. The browser should never see
the backend's origin at all.

**Declaring error status codes without models.** Rejected: it is the spec asserting something
untrue, and the cost lands on the other half as an un-mockable response.

## Consequences

`openapi.json` contains `422` responses and validation schemas that look like clutter and are
not. Two of the three settings change the committed contract artifacts when they change, so
`make openapi-check` fails until they are regenerated — which is the intended way to find out.

`redirect_slashes` is the exception, and it is the one that needed a test of its own. It
changes no byte of the spec, so the contract flow cannot see it go and every gate stays green
without it. `tests/routes/test_tasks.py::test_a_trailing_slash_is_a_404_and_never_a_redirect`
is what refuses that, by failing on the 307 rather than on anything the artifacts would show.
