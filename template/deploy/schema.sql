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

-- 0120_tasks_force
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
INSERT INTO applied_once(key) VALUES('0120_tasks_force') ON CONFLICT DO NOTHING;
