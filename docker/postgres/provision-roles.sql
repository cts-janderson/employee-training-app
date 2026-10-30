-- Two login roles do the work (docs/plans/36-current-task.md, step 1):
--   training_owner owns the schema and every object; only the migrate service uses it.
--   training_app is used by web, worker, cleanup and backup. It reads and writes rows and owns nothing.
-- Run as the superuser with psql variables db_name, owner_password and app_password; see provision-roles.sh.
-- Safe to run again: on an existing volume, and after a restore into a new cluster.
\set ON_ERROR_STOP on

SELECT 'CREATE ROLE training_owner'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'training_owner') \gexec
SELECT 'CREATE ROLE training_app'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'training_app') \gexec

ALTER ROLE training_owner WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'owner_password';
ALTER ROLE training_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'app_password';
SELECT 'REVOKE training_owner FROM training_app'
WHERE pg_has_role('training_app', 'training_owner', 'MEMBER') \gexec

REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db_name" TO training_owner;
GRANT CONNECT ON DATABASE :"db_name" TO training_app;

ALTER SCHEMA public OWNER TO training_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO training_app;

-- A restore run as the superuser can leave tables owned by it. Sequences behind identity and serial columns
-- move with their table.
DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND pg_get_userbyid(c.relowner) <> 'training_owner'
    LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO training_owner', item.relname);
    END LOOP;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM training_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM training_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO training_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO training_app;

-- Tables and sequences that later migrations create get the same grants.
ALTER DEFAULT PRIVILEGES FOR ROLE training_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO training_app;
ALTER DEFAULT PRIVILEGES FOR ROLE training_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO training_app;
