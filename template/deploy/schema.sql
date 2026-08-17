-- Schema for the 'app' schema, emitted by `make schema`. Do not hand-edit.
-- Apply in order, before the app starts. Then set DB_MIGRATE=check.
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
  -- Ordering is `seq`, not `created_at`: now() is transaction start time, so two rows
  -- inserted in one transaction tie on it and the list order becomes arbitrary.
  seq         bigserial NOT NULL,
  title       text NOT NULL,
  done        boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO applied_once(key) VALUES('0010_tasks') ON CONFLICT DO NOTHING;

-- 0011_tasks_seq_idx
CREATE UNIQUE INDEX IF NOT EXISTS tasks_seq_idx ON tasks (seq);
INSERT INTO applied_once(key) VALUES('0011_tasks_seq_idx') ON CONFLICT DO NOTHING;
