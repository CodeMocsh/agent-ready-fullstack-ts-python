# Deployment

Not configured, deliberately — the shape is yours. Nothing in this repository runs a deploy, so
none of what follows is checked by anything.

## The pieces

`make build` produces `frontend/dist/`, a static bundle. Serve it from any static host with an
SPA fallback. If it is served from a subpath, pass `--base=/that/path/` to `vite build`.

The backend runs under any ASGI server: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

Something has to strip the `/api` prefix, because the backend serves bare paths. In development
the Vite proxy does it. In a deployment, one of three:

- a reverse proxy in front of both, forwarding `/api/*` **with the prefix stripped**, which
  mirrors the dev setup;
- separate origins, with `VITE_API_BASE_URL` set at build time and `CORSMiddleware` added to
  the backend;
- one process carrying both, below.

## One process carrying both halves

```bash
cd backend
FRONTEND_BUNDLE=../frontend/dist uvicorn --factory app.serve:build_server \
  --host 0.0.0.0 --port 8000
```

`cd backend` because this half installs nothing, so `app` is importable from that directory and
nowhere else; `FRONTEND_BUNDLE` is relative to it too, and has no default, because a process
that came up on the wrong directory would answer every path with a file it never found.

**Run it behind something, not as the public edge.** Cloud Run, Fly, App Runner and an
identity-aware proxy terminate TLS and absorb the slow-client attacks a Python process should
not be meeting — so bind the `$PORT` they give you and pass **no** `--ssl-keyfile`, or the
platform's health check meets a TLS handshake and the deploy never goes green.

Whatever terminates TLS, something must. A session cookie worth setting is `Secure`, and a
browser reached over plaintext discards it, so sign-in fails by returning quietly to the
sign-in screen with nothing saying why.

With nothing in front, this process is the edge: read `SECURITY_HEADERS` and `MAX_BODY_BYTES`
in `app/serve.py` first, and [adr/0006](adr/0006-the-one-origin-entrypoint-is-the-edge.md) for
why `app.main` sets neither.

## The release step

The application verifies the schema at startup and refuses to serve if it is behind. Applying
is `make migrate`, run by something that is not the web process —
[adr/0003](adr/0003-the-application-never-applies-ddl.md). Wire it into whatever your platform
calls a release command: Fly's `[deploy] release_command`, a release or pre-deploy command on
Heroku, Railway and Render, a `Job` with `helm.sh/hook: pre-upgrade` on Kubernetes, a one-off
task on ECS, or the `migrate` service in `deploy/compose.yaml`.

It is idempotent, serialises on an advisory lock, and exits 0 when already current. The
application refuses to start if it can see `DATABASE_OWNER_URL`, so a single container that
migrates and then serves must drop the credential in between:
`env -u DATABASE_OWNER_URL uvicorn ...`.

**The schema version must match the build exactly**, in both directions
([adr/0003](adr/0003-the-application-never-applies-ddl.md)). The cost, worth knowing before
your first rolling deploy: between the release step and the last old instance being replaced,
any instance of the *previous* version that restarts will not come back up. Already-running
instances are fine. If that window matters, make the migration and the rollout one step — scale
down, migrate, scale up — and plan a rollback as a schema rollback.

## Three ways to ship something broken

**Never ship mock mode.** A production build must leave `VITE_ENABLE_MSW` unset, which is why
there is no `.env.production` to set it in.

**Set `DATABASE_URL`, or you are deploying the in-memory substrate.** It resets on every restart
and nothing complains: the app comes up, serves, and loses the data. It logs which substrate it
came up on — grep your deploy logs for `serving on the … substrate`, or assert
`app.state.database.name` from a smoke test.

**Replace `tenant_for()` in `app/identity.py`, or you are serving everybody.** It ships as a
stub: every request resolves to the tenant `default`, so anyone who reaches the process reads
and writes everything it holds. This one does complain — grep for `identity:` and read the
level. `WARNING` means nobody has replaced it. Serving everybody is a real thing to do for a
while, behind an authenticating proxy or on an internal tool; set
`UNAUTHENTICATED_IS_INTENTIONAL=1` and the same line is reported at `INFO`. That changes a log
level and nothing else. [adr/0008](adr/0008-a-route-cannot-escape-the-identity-seam.md) says
what a replacement owes.
