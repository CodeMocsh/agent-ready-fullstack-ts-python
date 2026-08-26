# 0002. Code-first contract with committed artifacts

## Status

Accepted, 2026-08-16.

## Context

The generated project is one system in two halves, and the halves have to agree on the shape
of every request and response between them. Something has to hold that agreement, and there
are three shapes it could take.

**Spec-first**: a hand-authored OpenAPI document is the source of truth, and both halves are
written to match it. The document is easy to review and belongs to neither half. It is also
inert — a YAML file can claim a route returns a `Task` when the service returns a `detail`
string, and nothing anywhere runs to contradict it. Keeping a hand-written spec honest
requires exactly the discipline that is hardest to sustain, and this template is aimed at
codebases written largely by agents, where undisciplined-but-plausible is the default
failure.

**Code-first, derived at build time**: the spec and the TypeScript types are generated on
demand and never committed. This is the conventional answer, and it breaks the property this
template is built around. Generating the frontend's types would require running the
backend's exporter, which requires Python and a synced virtualenv. The frontend half would
stop being installable, testable and buildable on its own, and mock mode — a frontend that
works with no backend at all — would become a claim rather than a fact.

**Code-first, derived and committed**: the same generation, with both outputs in git.

The backend's declarations are the natural authoring point in any of these. FastAPI route
decorators and pydantic models are executable: pytest exercises the same declarations the
exporter reads, so the spec cannot describe behaviour the service does not have. A spike
confirmed the pipeline composes and that its output is byte-deterministic across runs, and
it found the one place where the honesty mechanism has teeth — a `404` declared without a
`model` made the spec claim an empty body while `HTTPException` returns `{"detail": ...}`,
and the typed mock handlers refused to compile against it before a single test ran.

## Decision

The backend's pydantic models and route declarations are the single authoring point. Both
derived artifacts are **committed**:

```
backend/app/{models,routes}.py  ->  openapi.json  ->  frontend/src/api/schema.ts
                                                       -> types.ts, mocks/handlers.ts
```

`make openapi` regenerates both — exporting the spec in-process via `app.openapi()` without
starting a server, then running `openapi-typescript` over it. `make openapi-check`
regenerates and diffs, failing with a message that names the fix. CI splits the assertion so
that neither job needs the other toolchain: the backend job proves the spec matches the
code, the frontend job proves the types match the spec, and the two together prove the types
match the code.

Neither artifact is ever hand-edited, including during a merge conflict.

## Consequences

**Positive.** Each half stays independently operable, which is the whole point. A
contributor with only Node installed regenerates types, runs the full frontend test suite,
and produces a production build; a contributor with only uv runs the backend and its tests.
Mock mode's guarantee — the frontend works with no backend behind it — holds at the
repository level and not just at runtime.

CI stays two single-toolchain jobs, each fast and each able to fail for one reason.

The contract is reviewable. An API change arrives in a pull request as a diff to
`openapi.json`, which is the one place a breaking change is impossible to miss — a field
going optional, a status code disappearing, a response shape changing. A build-time-only
artifact would make the same change invisible until something downstream broke.

The mock handlers become a checked implementation rather than a fixture. Typed against the
committed schema by openapi-msw, a handler returning an undeclared status code or the wrong
shape is a compile error, so the fake and the real service cannot drift silently.

**Negative.** Regeneration is a discipline, and disciplines are exactly what this template
distrusts elsewhere. Every model or route change needs `make openapi` in the same commit.
This is enforced rather than requested — `make openapi-check` runs in CI and the failure
message names the command — but the enforcement is a build failure after the fact, not a
guardrail before it.

Generated files conflict in merges, and the conflicts are unreadable. A three-way merge of
an OpenAPI document produces something neither the generator nor the backend would emit, and
it will type-check. The rule is absolute: take either side wholesale, then regenerate with
`make openapi`. That rule has to be stated in `AGENTS.md`,
because
the default instinct — for a person and for an agent — is to resolve it by hand.

The artifacts churn on dependency upgrades. `app.openapi()` is deterministic for pinned
versions but not across FastAPI or pydantic minor bumps, and `openapi-typescript` changes
its own output between releases, so `make upgrade` has to end with `make openapi` and an
upgrade pull request carries artifact noise nobody wrote.

Some of what lands in the artifacts looks like clutter and is not. FastAPI adds a `422` to
every route with a parameter or a body, along with `HTTPValidationError` and
`ValidationError` schemas. They describe real behaviour, they will appear in the first diff
anyone reads, and someone will eventually try to clean them out. The documentation says not
to, which is a weaker mechanism than a check — but a check that distinguishes legitimate
spec pruning from vandalism is not something this template can write.
