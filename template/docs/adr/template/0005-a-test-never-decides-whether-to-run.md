# A test never decides whether to run, and a tier is named for what it needs

No test in this project skips itself. A check that cannot run everywhere is a **tier** — a
folder held out of the default run and selected by a target of its own — and `tests/tiers.py`
is where all of them are declared. `test_gate.py` fails on `pytest.skip`, `skipif`, `xfail`,
`importorskip`, and on `.skip`, `.skipIf`, `.runIf`, `.only`, `.fixme` and `{ skip: true }`
in the other half.

**The forcing reason is that a skipped test exits 0.** `pytest -q` prints `12 passed` whether
the twelve ran or twelve others quietly did not, and a suite whose Postgres members skip on a
laptop with no daemon is indistinguishable, in the one number anybody reads, from a suite
where every one of them passed. That is not a hypothetical failure mode: it is the normal
state of a project where the daemon is optional, and it is worst exactly when it matters
most — the day the schema changed.

A tier has the same silence, so it is paid for rather than assumed. `tests/conftest.py`
prints one line per tier the run did not select:

```
not in this run: tests/integration, which needs a Postgres daemon -- `make db-test` runs it
```

**Each tier is named for what it needs, not for who starts it.** Who types the command is the
least stable fact about a tier: `make db-test` is fully automated, a workflow can run it
the day someone writes one, and the template's own gate already runs it unattended wherever
Docker answers. A folder called `manual/` would be false by the time anyone read it — and in
testing vocabulary a manual test is one a person performs by hand, which none of these are.
`integration/` and `e2e/` are the names the surrounding ecosystem already uses, and Google's
test sizes make the same choice for the same reason: classify by the resources a test is
allowed, because that is the property that does not move.

## Considered options

**A skip that reports itself: `pytest -rs`, so the skips are printed.** This is what the
project did before, and it is the option to understand rather than dismiss. It fails on the
only run that matters — the one nobody is watching. `-rs` prints into a log that is green,
and the person who most needs to see it is the person who did not read the output because
nothing was red.

**A marker and `addopts = -m "not integration"`.** Equivalent in effect and documented by
pytest, and it keeps one suite in one file. Rejected because the mark travels with the test
rather than with the folder, so a new file needs somebody to remember the decorator; a folder
is selected whole, and a test added to it runs without anyone updating anything. The failure
mode of the marker is silent — the failure mode of a missing folder is that the target says
"no tests ran", which `test_gate.py` now refuses in its own right.

**One parametrised contract suite over both substrates.** The suite that proves
`MemoryDatabase` and `PostgresDatabase` keep one contract used to be a single file
parametrised over the two. That shape has nowhere to put "there is no server today" except a
skip on half the parameters. The tests now live once in `tests/store_contract.py`, and each
substrate subclasses it from its own side of the line — memory in the gate, Postgres in the
tier. Same coverage, and neither half can go quiet.

**Naming the tier for its trigger — `manual/`, `ondemand/`, `nightly/`.** Rejected above.

## Consequences

`norecursedirs` in `backend/pyproject.toml` carries the folder name of every tier pytest
would otherwise collect, and `test_gate.py` fails when that setting and `tiers.py` disagree.
A tier whose runner is a different program — Playwright — needs no such setting, only its own
config pointing at the folder.

A tier can empty out. `pytest` then exits 5 with "no tests ran" and Playwright says "No tests
found", both of which read as a broken target rather than as a tier that stopped existing, so
`test_gate.py` asserts each one still holds a file that declares a test.

The rule costs a fixture that used to be forgiving: `postgres_dsn()` raises when
`TEST_DATABASE_URL` is unset instead of skipping. That is only reachable from inside the
tier, where the run has already asked for a database, so the error names the missing thing
and `make db-test` supplies it.
