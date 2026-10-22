-- Schema for the 'app' schema, emitted by `make schema`. Do not hand-edit.
-- Apply as app_owner, or after `SET ROLE app_owner`: objects
-- created by another role fall outside its default privileges and the application
-- is then refused at query time rather than here.
-- Safe to re-apply: every statement is idempotent and every key is recorded once.

-- 0001_schema
DO $app$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'app') THEN
    CREATE SCHEMA "app";
  END IF;
END
$app$;

SET search_path TO "app";
SELECT set_config('app.actor_issuer', 'release', false), set_config('app.actor_subject', 'migrate', false), set_config('app.actor_name', 'The release step', false), set_config('app.on_behalf_of_issuer', '', false), set_config('app.on_behalf_of_subject', '', false), set_config('app.on_behalf_of_name', '', false), set_config('app.request_id', '', false), set_config('app.trace_id', '', false);

CREATE TABLE IF NOT EXISTS applied_once (
  key         text PRIMARY KEY,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO applied_once(key) VALUES('0001_schema') ON CONFLICT DO NOTHING;

-- 0010_tasks
CREATE TABLE IF NOT EXISTS tasks (
  id          uuid PRIMARY KEY,
  tenant_id   text NOT NULL,
  -- Ordering is `seq`, not `created_at`: now() is transaction start time, so two rows
  -- inserted in one transaction tie on it and the list order becomes arbitrary.
  seq         bigserial NOT NULL,
  title       text NOT NULL,
  done        boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  -- An unset GUC reads as the empty string after a pool issues RESET ALL, so a row
  -- carrying one would be visible to every connection that had not set a tenant. The
  -- policy alone does not stop that; this does.
  CONSTRAINT tasks_tenant_id_not_empty CHECK (tenant_id <> ''),
  -- The target a child table's foreign key must name. REFERENCES tasks(id) alone lets a
  -- child row point at another tenant's parent.
  UNIQUE (id, tenant_id)
);
INSERT INTO applied_once(key) VALUES('0010_tasks') ON CONFLICT DO NOTHING;

-- 0011_tasks_seq_idx
CREATE UNIQUE INDEX IF NOT EXISTS tasks_seq_idx ON tasks (tenant_id, seq);
INSERT INTO applied_once(key) VALUES('0011_tasks_seq_idx') ON CONFLICT DO NOTHING;

-- 0030_audit_events
CREATE TABLE IF NOT EXISTS audit_events (
  seq                   bigserial PRIMARY KEY,
  tenant_id             text NOT NULL,
  recorded_at           timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_issuer          text NOT NULL,
  actor_subject         text NOT NULL,
  actor_name            text NOT NULL,
  on_behalf_of_issuer   text,
  on_behalf_of_subject  text,
  on_behalf_of_name     text,
  action                text,
  outcome               text
    CONSTRAINT audit_events_outcome_known CHECK (outcome IN ('done', 'absent')),
  method                text,
  route                 text,
  status                smallint,
  resource_type         text,
  resource_ids          text[] NOT NULL,
  fields                text[] NOT NULL,
  request_id            text,
  trace_id              text,
  CONSTRAINT audit_events_tenant_id_not_empty CHECK (tenant_id <> ''),
  CONSTRAINT audit_events_action_known
    CHECK (action IN ('read', 'create', 'update', 'delete')),
  CONSTRAINT audit_events_on_behalf_of_whole CHECK (
    (on_behalf_of_issuer IS NULL) = (on_behalf_of_subject IS NULL)
    AND (on_behalf_of_subject IS NULL) = (on_behalf_of_name IS NULL)
  ),
  CONSTRAINT audit_events_kind_whole CHECK (
    (method IS NULL) = (status IS NULL)
    AND (method IS NULL) = (route IS NULL)
    AND (action IS NULL) = (outcome IS NULL)
    AND (action IS NULL) = (resource_type IS NULL)
    AND (method IS NULL) = (action IS NOT NULL)
  )
);
INSERT INTO applied_once(key) VALUES('0030_audit_events') ON CONFLICT DO NOTHING;

-- 0031_audit_events_seq_idx
CREATE UNIQUE INDEX IF NOT EXISTS audit_events_seq_idx ON audit_events (tenant_id, seq);
INSERT INTO applied_once(key) VALUES('0031_audit_events_seq_idx') ON CONFLICT DO NOTHING;

-- 0032_audit_events_append_only
DO $app$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_app') THEN
    REVOKE UPDATE, DELETE ON audit_events FROM "app_app";
  END IF;
END
$app$;
INSERT INTO applied_once(key) VALUES('0032_audit_events_append_only') ON CONFLICT DO NOTHING;

-- 0033_audit_row_change
CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $audit$
DECLARE
  issuer text := nullif(current_setting('app.actor_issuer', true), '');
  subject text := nullif(current_setting('app.actor_subject', true), '');
  key text[];
  none constant text := '(SELECT NULL::jsonb AS r WHERE false)';
BEGIN
  IF issuer IS NULL OR subject IS NULL THEN
    RAISE EXCEPTION 'a write to % names no actor', TG_TABLE_NAME
      USING HINT = 'Run the work inside app.audit_events.acting_as(actor).';
  END IF;
  SELECT array_agg(a.attname::text ORDER BY k.ord) INTO key
    FROM pg_index i
    CROSS JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
   WHERE i.indrelid = TG_RELID AND i.indisprimary;
  IF key IS NULL THEN
    RAISE EXCEPTION '% has no primary key, so an audit event cannot name its rows', TG_TABLE_NAME;
  END IF;
  EXECUTE format($sql$
    WITH pairs AS (
      SELECT coalesce(a.r, b.r) ->> 'tenant_id' AS tenant,
             array_to_string(coalesce(a.id, b.id), ':') AS id,
             b.r AS before, a.r AS after
        FROM (SELECT r, ARRAY(SELECT r ->> k FROM unnest($1::text[]) AS k) AS id FROM %s) AS b
        FULL JOIN (SELECT r, ARRAY(SELECT r ->> k FROM unnest($1::text[]) AS k) AS id FROM %s) AS a
          ON a.id = b.id
    ), changed AS (
      SELECT DISTINCT p.tenant, f.name
        FROM pairs AS p CROSS JOIN jsonb_object_keys(coalesce(p.before, p.after)) AS f(name)
       WHERE (p.before -> f.name) IS DISTINCT FROM (p.after -> f.name)
    )
    INSERT INTO audit_events (tenant_id, actor_issuer, actor_subject, actor_name,
      on_behalf_of_issuer, on_behalf_of_subject, on_behalf_of_name, action, outcome,
      resource_type, resource_ids, fields, request_id, trace_id)
    SELECT p.tenant, $2, $3, coalesce(current_setting('app.actor_name', true), ''),
           nullif(current_setting('app.on_behalf_of_issuer', true), ''),
           nullif(current_setting('app.on_behalf_of_subject', true), ''),
           nullif(current_setting('app.on_behalf_of_name', true), ''),
           $4, 'done', $5, array_agg(p.id ORDER BY p.id),
           coalesce((SELECT array_agg(c.name ORDER BY c.name) FROM changed AS c
                      WHERE c.tenant = p.tenant), '{}'),
           nullif(current_setting('app.request_id', true), ''),
           nullif(current_setting('app.trace_id', true), '')
      FROM pairs AS p
     GROUP BY p.tenant
  $sql$,
    CASE WHEN TG_OP = 'INSERT' THEN none ELSE '(SELECT to_jsonb(o) AS r FROM old_rows AS o)' END,
    CASE WHEN TG_OP = 'DELETE' THEN none ELSE '(SELECT to_jsonb(n) AS r FROM new_rows AS n)' END)
  USING key, issuer, subject,
    CASE TG_OP WHEN 'INSERT' THEN 'create' WHEN 'UPDATE' THEN 'update' ELSE 'delete' END,
    TG_TABLE_NAME;
  RETURN NULL;
END
$audit$;
INSERT INTO applied_once(key) VALUES('0033_audit_row_change') ON CONFLICT DO NOTHING;

-- 0100_tasks_policy
DO $app$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                 WHERE schemaname = current_schema()
                   AND tablename = 'tasks'
                   AND policyname = 'tasks_tenant_isolation') THEN
    CREATE POLICY tasks_tenant_isolation ON tasks FOR ALL
      USING (tenant_id = current_setting('app.tenant_id', true))
      WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
  END IF;
END
$app$;
INSERT INTO applied_once(key) VALUES('0100_tasks_policy') ON CONFLICT DO NOTHING;

-- 0101_audit_events_policy
DO $app$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                 WHERE schemaname = current_schema()
                   AND tablename = 'audit_events'
                   AND policyname = 'audit_events_tenant_isolation') THEN
    CREATE POLICY audit_events_tenant_isolation ON audit_events FOR ALL
      USING (tenant_id = current_setting('app.tenant_id', true))
      WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
  END IF;
END
$app$;
INSERT INTO applied_once(key) VALUES('0101_audit_events_policy') ON CONFLICT DO NOTHING;

-- 0120_tasks_force
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
INSERT INTO applied_once(key) VALUES('0120_tasks_force') ON CONFLICT DO NOTHING;

-- 0121_audit_events_force
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
INSERT INTO applied_once(key) VALUES('0121_audit_events_force') ON CONFLICT DO NOTHING;

-- 0140_tasks_audit
DO $app$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger
                 WHERE tgrelid = 'tasks'::regclass
                   AND tgname = 'tasks_audit_insert') THEN
    CREATE TRIGGER tasks_audit_insert AFTER INSERT ON tasks
      REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION audit_row_change();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger
                 WHERE tgrelid = 'tasks'::regclass
                   AND tgname = 'tasks_audit_update') THEN
    CREATE TRIGGER tasks_audit_update AFTER UPDATE ON tasks
      REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION audit_row_change();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger
                 WHERE tgrelid = 'tasks'::regclass
                   AND tgname = 'tasks_audit_delete') THEN
    CREATE TRIGGER tasks_audit_delete AFTER DELETE ON tasks
      REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION audit_row_change();
  END IF;
END
$app$;
INSERT INTO applied_once(key) VALUES('0140_tasks_audit') ON CONFLICT DO NOTHING;

-- 0200_audit_events_outcome
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS outcome text NOT NULL DEFAULT 'done' CONSTRAINT audit_events_outcome_known CHECK (outcome IN ('done', 'absent'));
INSERT INTO applied_once(key) VALUES('0200_audit_events_outcome') ON CONFLICT DO NOTHING;

-- 0201_audit_events_method
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS method text;
INSERT INTO applied_once(key) VALUES('0201_audit_events_method') ON CONFLICT DO NOTHING;

-- 0202_audit_events_route
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS route text;
INSERT INTO applied_once(key) VALUES('0202_audit_events_route') ON CONFLICT DO NOTHING;

-- 0203_audit_events_status
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS status smallint;
INSERT INTO applied_once(key) VALUES('0203_audit_events_status') ON CONFLICT DO NOTHING;

-- 0204_audit_events_action_optional
ALTER TABLE audit_events ALTER COLUMN action DROP NOT NULL;
INSERT INTO applied_once(key) VALUES('0204_audit_events_action_optional') ON CONFLICT DO NOTHING;

-- 0205_audit_events_outcome_optional
ALTER TABLE audit_events ALTER COLUMN outcome DROP NOT NULL;
INSERT INTO applied_once(key) VALUES('0205_audit_events_outcome_optional') ON CONFLICT DO NOTHING;

-- 0206_audit_events_outcome_no_default
ALTER TABLE audit_events ALTER COLUMN outcome DROP DEFAULT;
INSERT INTO applied_once(key) VALUES('0206_audit_events_outcome_no_default') ON CONFLICT DO NOTHING;

-- 0207_audit_events_resource_type_optional
ALTER TABLE audit_events ALTER COLUMN resource_type DROP NOT NULL;
INSERT INTO applied_once(key) VALUES('0207_audit_events_resource_type_optional') ON CONFLICT DO NOTHING;

-- 0208_audit_events_kind_whole
DO $app$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'audit_events'::regclass
                   AND conname = 'audit_events_kind_whole') THEN
    ALTER TABLE audit_events ADD CONSTRAINT audit_events_kind_whole CHECK (
      (method IS NULL) = (status IS NULL)
      AND (method IS NULL) = (route IS NULL)
      AND (action IS NULL) = (outcome IS NULL)
      AND (action IS NULL) = (resource_type IS NULL)
      AND (method IS NULL) = (action IS NOT NULL)
    );
  END IF;
END
$app$;
INSERT INTO applied_once(key) VALUES('0208_audit_events_kind_whole') ON CONFLICT DO NOTHING;

-- The writes the application role is never granted.

DO $app$
BEGIN
  IF to_regclass('"app".applied_once') IS NOT NULL
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_app') THEN
    EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON "app".applied_once FROM %I', 'app_app');
  END IF;
END
$app$;
DO $app$
DECLARE
  writable name;
BEGIN
  IF to_regclass('"app".audit_events') IS NOT NULL
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_app') THEN
    EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON "app".audit_events FROM %I', 'app_app');
    FOR writable IN
      SELECT attname FROM pg_attribute
      WHERE attrelid = '"app".audit_events'::regclass AND attnum > 0 AND NOT attisdropped
        AND attname NOT IN ('seq', 'recorded_at')
    LOOP
      EXECUTE format('GRANT INSERT (%I) ON "app".audit_events TO %I', writable, 'app_app');
    END LOOP;
  END IF;
END
$app$;
