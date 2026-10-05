# The environment is read in one place, and run in another

Three modules answer three questions, and each is the only one that answers its own.

- `app/environment.py` — *is this configuration legitimate at all?* It names every variable
  the process reads and holds `refuse_development_settings`. It builds nothing.
- `app/wiring.py` — *what did this deployment configure?* It reads the environment and builds
  from it: the substrate, the bundle, the telemetry settings.
- `app/lifespan.py` — *what is this process running?* It holds what `wiring` built for the life
  of the process, and reads no variable. `tests/lifespan/test_lifespan.py` holds that.

The rule that sorts a new function is mechanical: **a `build_` function that reads no variable
belongs in `lifespan.py`.**

**An unset `APP_ENV` is production.** Production refuses to start without `DATABASE_URL`,
because the in-memory substrate keeps every row in the process and loses them when it exits.
`make dev`, the contract suite and the tests set `APP_ENV=development`. Any value other than
`development` or `production` is refused, because `APP_ENV=prod` meant something.

## Why

`wiring.py` held both the reading and the running, and it is the third file every project grows
past. The sibling project this template was extracted alongside split it this way once the file
held both its identity configuration and the loops its process runs; the split is what made the
list of variables reviewable in one place again.

The in-memory substrate is a correct default for a fresh clone and a wrong one for a
deployment, and nothing told the two apart. A deployment that forgot `DATABASE_URL` came up,
served, and lost its data on the next restart. Defaulting the unset case to production means a
forgotten variable is checked rather than waved through.

## Considered options

- **Unset is development.** Every deployment that forgot the variable would be waved through,
  which is the case the refusal exists for.
- **Refuse an unauthenticated seam in production too.** Rejected in
  [0008](0008-a-route-cannot-escape-the-identity-seam.md): an authenticating proxy in front of a
  single-tenant tool is a legitimate deployment. The boot log still says it.
- **Keep one `wiring.py`.** The variables' names, the validation and the lifespan interleave,
  and every project's edits to any of them conflict on `copier update`.

## Consequences

- A deployment that relied on the in-memory substrate without setting `APP_ENV` stops starting
  after this update. It names the variable and both ways out.
- A new development-only convenience adds a finding to `refuse_development_settings`. Every
  finding is reported at once, so a deploy is fixed in one round.
- `docs/deployment.md` names `APP_ENV`.
