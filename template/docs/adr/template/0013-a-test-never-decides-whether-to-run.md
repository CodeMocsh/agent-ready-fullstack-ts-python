# 0013. A test never decides whether to run, and a tier is named for what it needs

Date: 2026-10-05

## Status

Accepted.

## Context

A skipped test exits 0. `pytest -q` prints the same green line when every test ran and when some
skipped. A suite whose Postgres tests skip on a laptop with no daemon then looks like a suite
where those tests passed. That is the normal state of a project where the daemon is optional. It
is worst on the day the schema changes, which is the day those tests matter most.

Some checks cannot run everywhere. One needs a Postgres daemon, one needs Docker, one needs a
browser binary. The gate runs on every commit and must not need any of them. So these checks need
a place to live that is not the gate and is not a skip.

Who starts a check is the least stable fact about it. In a project that ships
`.github/workflows/ci.yml`, `make db-test` needs no person: the workflow runs it for every pull
request. A name such as `manual/` is then false. In testing vocabulary a manual test is one a
person performs by hand, and no test here is one.

## Decision

- **No test skips itself.** `test_no_test_switches_itself_off` in `tests/test_gate.py` scans every
  test file in both halves. It fails on each spelling in `SKIP_MARKERS`: the pytest skip and xfail
  markers and calls, `importorskip`, and the vitest and Playwright skip, `only`, `fixme` and
  `todo` forms. `.only` is in the list because it stops every other test in the file.
- **A check that cannot run everywhere is a tier.** A tier is a folder held out of the default run
  and selected whole by a target of its own. `tests/tiers.py` declares every tier, as a `Tier`
  from `tests/tier.py`.
- **A tier is named for what it needs, not for who starts it.** `integration/` and `e2e/` are the
  names the ecosystem already uses. Google's test sizes classify a test by the resources it may
  use, for the same reason: that property does not change.
- **A run says which tiers it did not select.** `pytest_terminal_summary` in `tests/conftest.py`
  prints one line for each Python tier the run left out, with what the tier needs and the
  command that runs it.
- **A Python tier stays out of the default run.** `norecursedirs` in `backend/pyproject.toml`
  names each one. `test_every_python_tier_is_out_of_the_default_run` fails when that setting and
  `tests/tiers.py` disagree. A Playwright tier needs no such setting, because its runner is a
  different program with its own config.
- **A tier is selected by its folder, not by a list of files.**
  `test_the_python_tier_is_selected_whole_and_not_by_a_list` holds the Makefile recipe to that.
  `test_every_tier_is_selected_by_a_file_that_still_names_it` holds each recipe and config to its
  folder.
- **A tier always holds a test.** An empty tier makes `pytest` exit 5 and Playwright print "No
  tests found". Both read as a broken target, not as a tier that is gone.
  `test_every_tier_still_holds_tests` fails when no file in a tier declares a test.
- **A fixture inside a tier raises when its resource is missing.** `postgres_dsn` in
  `tests/integration/conftest.py` raises when `TEST_DATABASE_URL` is unset. Only a run that
  selected the tier reaches it, so the error names the missing thing and the command that
  supplies it.
- **The store contract runs once on each side of the line.** `TaskStoreContract` in
  `tests/store_contract.py` holds the tests. `tests/store/test_store_contract.py` runs it on memory
  in the gate. `tests/integration/test_store_contract.py` runs it on Postgres in the tier.

## Considered options

- **A skip that reports itself, with `pytest -rs`.** It fails on the only run that matters: the
  one nobody watches. `-rs` prints into a log whose result is green. The person who most needs the
  line is the person who did not read the output, because nothing was red.
- **A marker, and `addopts = -m "not integration"`.** Pytest documents it, and it keeps one suite
  in one place. But the marker travels with the test, not with the folder. A new file needs
  somebody to remember the decorator, and a forgotten decorator fails silently. A folder is
  selected whole, so a test added to it runs with no other change.
- **One contract suite, parametrised over both substrates.** That shape has only one place to put
  "there is no server today": a skip on half the parameters.
- **A tier named for its trigger: `manual/`, `ondemand/`, `nightly/`.** The trigger changes the day
  somebody writes a workflow, and the name is then false.

## Consequences

- Every tier costs a line in `tests/tiers.py`. A Python tier also costs an entry in
  `norecursedirs`. The checks in `tests/test_gate.py` fail until both are present.
- A green `make test` does not mean every test ran. It means every test in the gate ran, and the
  run names each tier it left out.
- A fixture in a tier cannot be forgiving. A tier run without its resource fails, and does not
  pass with nothing tested.
- No test in this project can switch itself off for one environment. A test that needs something
  a laptop may not have must move into a tier.
