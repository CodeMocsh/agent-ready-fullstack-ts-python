# Context

The glossary: what a word means here, and which words to avoid because they mean something
else. Five terms come from the template and the rest is yours — add a term the moment two
people use two words for one thing, which is earlier than it feels.

## Tenancy

**Tenant**:
The isolation boundary. One tenant's rows are invisible to every other, enforced by a
row-level security policy rather than by application code. What a tenant *is* — a customer, an
organisation, a workspace, a single user — is your product's decision and the template is
deliberately ignorant of it.
_Avoid_: customer, account, org, workspace — **unless you rename `tenant_id` to match**. See
below; that rename is free today and a migration later.

**Sentinel tenant**:
The value `"default"`, carried by every row in a deployment that has not wired up
authentication. A named constant rather than a null or an empty string, so the eventual move
to real tenancy is an unambiguous `UPDATE ... WHERE tenant_id = 'default'`.
_Avoid_: anonymous, public, system, null tenant.

**Owner role**:
`<schema>_owner`. Owns the schema and every table in it and applies the DDL. `NOLOGIN` —
nothing serves traffic as it, and `make migrate` reaches it with `SET ROLE`.
_Avoid_: admin, root, superuser (a superuser is a different thing and **bypasses every
policy**).

**Application role**:
`<schema>_app`. What the running application connects as. DML only, no `CREATE`, no
`BYPASSRLS`, and no write access to the migration ledger.
_Avoid_: user, service account, client.

**Release step**:
Applying the schema, as an action taken *before* a new version serves traffic and by something
that is not the application. `make migrate` is it.
_Avoid_: auto-migrate, startup migration, boot migration — the application does none of these,
by design.

## Renaming `tenant_id`

Almost no product's users say "tenant"; they say org, workspace, team or account. If yours
does, rename the column now — while the only rows are the demo's. The name appears in
`app/store/ddl.py`, the policy in the same file, `app/identity.py`, and the `TENANT_GUC`
setting. After you have real data it is a migration, a backfill and a policy rewrite, so this
is a day-one decision or a never decision.

The concept is what the template is sure of. The word is yours.

## Decisions

`docs/adr/` holds the ones that would otherwise look like they could be simpler:

- **0001** — two substrates behind one contract.
- **0002** — tenant isolation is forced, and always on.
- **0003** — the application never applies DDL.
- **0004** — the schema and the binary must match.
