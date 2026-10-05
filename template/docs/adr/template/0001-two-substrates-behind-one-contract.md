# The store is two implementations behind one contract, and the in-memory one is permanent

`app/store/` declares two protocols — `Database` and `TaskStore` — and ships two
implementations of them: `MemoryDatabase` and `PostgresDatabase`.
`tests/store_contract.py` is one suite and both substrates run it.

**Each substrate runs it from a suite of its own** — `tests/store/test_store_contract.py` for
memory, `tests/integration/test_store_contract.py` for Postgres — rather than one suite
parametrised over both. The parametrised shape needs a skip on the Postgres half whenever
there is no server, and a skipped test exits 0 and looks like a test that passed. A tier that
was not selected is reported as not run, which is the same information without the lie.

The in-memory substrate is **not** a placeholder to delete once Postgres feels real. It is
what `make test` runs against, what `make pre-commit` needs in order to stay free of a daemon,
and what lets `make dev` work on a fresh clone in about a minute. Deleting it costs the fast
tier, and the fast tier is the one that runs on every commit.

Two implementations are also the only reason the protocol means anything. One implementation
plus a `Protocol` is a shape nothing checks: the interesting rules — that a missing task is
reported rather than raised, that an id which cannot exist is a 404 and not a 500, that
`list()` is in creation order — are exactly the ones a single implementation satisfies by
accident. The suite that runs twice is what turns them into a contract.

**The memory substrate seeds three tasks and Postgres starts empty, deliberately.** A suite
that passes against both cannot have assumed either. Seed rows are asserted in
`tests/routes/test_tasks.py` and nowhere else, which is the same rule
`frontend/tests/api/contract.test.ts` already follows one layer up.

**And the rule reaches `frontend/e2e/tasks.live.spec.ts`, which is where it was broken.**
That spec asserted `Read AGENTS.md` — a row that exists only because the in-memory
substrate put it there — so it passed on the default `make dev` and failed against a real
database, on a fixture nothing in the frontend half declares. It now creates the row it
asserts and deletes it again, which is the only shape that means the same thing on both
substrates and does not leave a run's litter behind on the one that remembers. The mock
spec still names the seed, and may: those rows are the frontend's own, in
`src/mocks/store.ts`.

## Considered options

**Postgres only, deleting the in-memory store.** Rejected: it puts a daemon on the default
path. `make pre-commit` runs on every commit, and a gate that needs a container is a gate
people commit around. It also costs the zero-infrastructure first run, which is the property
new projects are started for.

**In-memory only, with Postgres left as an exercise.** This is what the template did before.
Rejected because it does not survive contact with the first real feature: every project
replaces the store in week one, and that is the week the data layer gets invented with no
migration discipline, no committed schema, and no transaction boundary. Those are exactly the
things a template should carry, by the same argument that put `openapi.json` in it.

**A template question, `database: none | postgres`.** Considered seriously and rejected for
now. It doubles the `check-all` matrix, and it means half of all generated projects never see
the pattern. Shipping both costs one runtime dependency — asyncpg, imported lazily — and the
in-memory substrate stays the default at run time, so the zero-infrastructure property
survives anyway. Revisit if the Postgres half grows enough that carrying it unused is a real
cost.

**Raising `TaskNotFound` instead of returning `None`.** Rejected against the *Fail loudly*
test in `AGENTS.md`, which is three conditions and not a blanket ban: the design plans for a
missing task, the contract names it — `404` with a model, in `openapi.json` — and the route
reports it. Three out of three, so a return value is legitimate here. Raising would also make
`update()` and `remove()` disagree with the spec that generates the frontend's types.

## Consequences

`reset()` is gone from every store. A test that wants a clean substrate builds a clean app —
`create_app()` plus a `TestClient`, per test — so no production interface carries a test-only
method. That is why `app/deps.py` reads the database off the request rather than importing a
module-level singleton, and it is what lets one test process hold two apps on two substrates.

`tenant_id` and row-level security are **not** here. Adding them is a schema change and a
scoping parameter on `store()`, and the machinery that makes that additive rather than a
rewrite is in place: the repair band in `ddl.py` exists so a column can be added to a table
that already shipped, and `tests/integration/test_postgres.py` proves a database missing a
later column gets repaired. The remaining decision — whether a `tenant_id` column should
exist from the start with isolation switched off — is deliberately still open, because it is
the kind of choice that depends on the product rather than on the template.

Ordering is `tasks.seq`, a `bigserial`, and not `created_at`. `now()` is transaction start
time, so two rows inserted in one transaction tie on it and the list order becomes arbitrary.
