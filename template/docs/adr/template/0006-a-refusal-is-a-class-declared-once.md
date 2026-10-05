# 0006. A refusal is a class, declared once

Date: 2026-10-05

## Status

Accepted.

## Context

The obvious design writes a refusal two times. It writes `raise HTTPException(404, "Task not
found")` where the refusal occurs. It writes a hand-kept `{404: {...}}` dictionary on the
decorator. Nothing compares the two. In a backend of real size, raises are more numerous than
the dictionaries. Some refusals are never declared, and some declarations describe responses
that no route produces. Both stay for months.

The declaration is the half that reaches the frontend. `openapi.json` is generated from it,
`frontend/src/api/schema.ts` from that, and the mock handlers are typed from `schema.ts`. The
mocks cannot return a status that is raised and not declared. The contract suite then passes
against a service that answers differently.

A test that drives the app finds only the branches that a test reaches. A raise on a branch that
no test reaches is still wrong, and that branch is the one that fails in production.

## Decision

- Each refusal is a class in `app/errors.py`. It subclasses `ApiError` and carries its own
  `status` and `description`.
- The mechanism is in `app/refusal.py`: `ApiError` and `responses(...)`. The template owns it
  and updates it. The project owns `app/errors.py`, which re-exports both.
  [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md) records that
  split.
- A route raises the class and names it in `responses(...)` on its decorator. `responses(...)`
  builds the OpenAPI declaration from the same class, with `ErrorBody` as the model.
- Refusals that share a status are one declaration. Its description names each refusal.
- `tests/errors/test_errors.py` reads the source under `app/`, and does not drive the app. It
  fails when:
  - a route raises a refusal that it does not declare
    (`test_a_route_declares_every_refusal_it_raises`);
  - a refusal in `app/errors.py` is declared by no route
    (`test_no_refusal_is_declared_and_unreachable`);
  - a module in `app/` builds an `HTTPException` directly
    (`test_nothing_in_app_builds_an_http_exception`). `ApiError` subclasses `HTTPException` and
    does not build one, so no module is exempt;
  - `app/errors.py` defines a refusal that is not built on `app.refusal.ApiError`
    (`test_every_refusal_in_app_errors_is_built_on_app_refusal`).
- `test_the_walk_finds_every_route_the_app_serves_and_no_other` compares the routes read from
  the source with the handlers read off the app. The tests above cannot pass against an empty
  walk.
- `NoSuchAsset` is a refusal outside the contract. It is in `app/refusal.py`, and `app/serve.py`
  raises it from a route that is not in `openapi.json`. The tests hold only the refusals in
  `app/errors.py` to a declaration.

## Considered options

- **Hand-kept dictionaries, checked by a test.** The test compares two spellings of one fact.
  When one spelling is generated from the other, nothing is left to compare.
- **Driving every route to find its refusals.** It finds only the branches that a test reaches.
- **Declaring `401` the same way.** [0004](0004-a-route-cannot-escape-the-identity-seam.md)
  rejects it: the identity stub never raises it.

## Consequences

- A refusal's text is part of the contract. A change to a `description` changes `openapi.json`,
  and `make openapi-check` fails until `make openapi` regenerates it.
- `raise NoSuchTask` is the whole refusal at the raise site. A refusal that must say more in its
  body passes a detail. A refusal that needs a different status is a different class.
- Nothing in `app/` can build an `HTTPException` for a single case. Each refusal is a class,
  even when one route raises it.
- A refusal that no route can raise cannot stay in `app/errors.py`.
