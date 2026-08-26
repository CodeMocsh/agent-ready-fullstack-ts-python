# Context

The glossary: what a word means here, and which words to avoid because they mean something
else. The tenancy terms come from the template and the rest is yours — add a term the moment
two people use two words for one thing, which is earlier than it feels.

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
does, rename the column now — while the only rows are the demo's. `grep -r tenant_id` finds
every place it has to change: the schema and its policy, the identity stub, and the setting
the policy reads. After you have real data it is a migration, a backfill and a policy rewrite,
so this is a day-one decision or a never decision.

The concept is what the template is sure of. The word is yours.

## Decisions

`docs/adr/` holds the design decisions that would otherwise look like they could be simpler —
the store's two substrates, forced tenant isolation, why the application never applies DDL.
Each file's name is the decision it records, so the directory listing is the index. Its
[README](docs/adr/README.md) says what belongs there and how to write one.
