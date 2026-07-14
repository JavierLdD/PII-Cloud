-- Destructive reset for the dedicated PII pipeline PostgreSQL database.
--
-- This script intentionally removes every object in the public schema and then
-- recreates an empty public schema owned by the connected migration role. It
-- preserves the database itself and cluster roles/users, but it can remove
-- tables, views, functions, types and extensions installed in public.
--
-- Run only with psql and all four guards, for example:
--
--   psql "$MIGRATOR_DATABASE_URL" --single-transaction \
--     --set=external_transaction=1 \
--     --set=expected_database=pii_pipeline_db \
--     --set=expected_role=pii_migrator \
--     --set=runtime_role=pii_app \
--     --set=confirm_reset=RESET_PII_PIPELINE_DATABASE \
--     --file=Cloud/Database/reset_database.sql \
--     --file=Cloud/Database/schema.sql

\set ON_ERROR_STOP on

\if :{?expected_database}
\else
  \set expected_database ''
\endif

\if :{?expected_role}
\else
  \set expected_role ''
\endif

\if :{?runtime_role}
\else
  \set runtime_role ''
\endif

\if :{?confirm_reset}
\else
  \set confirm_reset ''
\endif

\if :{?external_transaction}
\else
  BEGIN;
\endif

CREATE TEMP TABLE reset_database_guard (
    expected_database TEXT NOT NULL,
    expected_role TEXT NOT NULL,
    runtime_role TEXT NOT NULL,
    confirmation TEXT NOT NULL
) ON COMMIT DROP;

INSERT INTO reset_database_guard (
    expected_database,
    expected_role,
    runtime_role,
    confirmation
)
VALUES (
    :'expected_database',
    :'expected_role',
    :'runtime_role',
    :'confirm_reset'
);

DO $reset_guard$
DECLARE
    guard reset_database_guard%ROWTYPE;
    other_session_count INTEGER;
BEGIN
    SELECT * INTO STRICT guard FROM reset_database_guard;

    IF guard.expected_database = '' THEN
        RAISE EXCEPTION 'Missing required psql variable expected_database';
    END IF;

    IF guard.expected_role = '' THEN
        RAISE EXCEPTION 'Missing required psql variable expected_role';
    END IF;

    IF guard.runtime_role = '' THEN
        RAISE EXCEPTION 'Missing required psql variable runtime_role';
    END IF;

    IF guard.confirmation <> 'RESET_PII_PIPELINE_DATABASE' THEN
        RAISE EXCEPTION
            'Reset confirmation is invalid; expected RESET_PII_PIPELINE_DATABASE';
    END IF;

    IF current_database() IN ('postgres', 'template0', 'template1', 'cloudsqladmin') THEN
        RAISE EXCEPTION
            'Refusing to reset PostgreSQL system database %', current_database();
    END IF;

    IF current_database() <> guard.expected_database THEN
        RAISE EXCEPTION
            'Connected database does not match expected_database';
    END IF;

    IF current_user <> guard.expected_role THEN
        RAISE EXCEPTION
            'Connected role does not match expected_role';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_roles
         WHERE rolname = guard.runtime_role
    ) THEN
        RAISE EXCEPTION 'Configured runtime_role does not exist';
    END IF;

    SELECT COUNT(*)
      INTO other_session_count
     FROM pg_stat_activity
     WHERE datname = current_database()
       AND backend_type = 'client backend'
       AND pid <> pg_backend_pid();

    IF other_session_count > 0 THEN
        RAISE EXCEPTION
            'Refusing reset while % other database session(s) are connected',
            other_session_count;
    END IF;
END
$reset_guard$;

DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public AUTHORIZATION CURRENT_USER;

-- Keep only the configured migration/runtime roles on this dedicated database.
-- Roles and memberships are preserved; only privileges are narrowed.
REVOKE ALL PRIVILEGES ON DATABASE :"expected_database" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"expected_database" TO :"expected_role";
GRANT CONNECT ON DATABASE :"expected_database" TO :"runtime_role";

REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime_role";

-- schema.sql runs as expected_role. These defaults grant least-privilege
-- runtime access to the tables and any future sequences it creates.
ALTER DEFAULT PRIVILEGES FOR ROLE :"expected_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_role";

ALTER DEFAULT PRIVILEGES FOR ROLE :"expected_role" IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"runtime_role";

SET LOCAL search_path TO public, pg_catalog;

\if :{?external_transaction}
  \echo 'PII database reset staged; schema.sql must succeed before this transaction commits.'
\else
  COMMIT;
  \echo 'PII pipeline database reset completed; public is empty and ready for schema.sql.'
\endif
