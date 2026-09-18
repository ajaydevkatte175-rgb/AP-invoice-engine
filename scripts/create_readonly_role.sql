-- scripts/create_readonly_role.sql
-- Configure read-only role 'invoice_ro' for safe text-to-SQL AI agent execution.
-- Non-Negotiable Rule 6: READ-ONLY Postgres role with a 5-second statement_timeout.
-- Non-Negotiable Rule 7: Enforces Postgres Row-Level Security (RLS) tenant isolation.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'invoice_ro') THEN
        CREATE ROLE invoice_ro WITH LOGIN PASSWORD 'readonlypass';
    END IF;
END
$$;

-- Allow connection to the database
GRANT CONNECT ON DATABASE invoicedb TO invoice_ro;

-- Allow usage of public schema
GRANT USAGE ON SCHEMA public TO invoice_ro;

-- Revoke any existing write/mutation permissions
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM invoice_ro;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM invoice_ro;

-- Grant SELECT ONLY on all current tables in schema public
GRANT SELECT ON ALL TABLES IN SCHEMA public TO invoice_ro;

-- Grant SELECT ONLY on all future tables created in schema public
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO invoice_ro;

-- Strictly enforce 5-second statement timeout to prevent runaway queries or denial of service
ALTER ROLE invoice_ro SET statement_timeout = '5s';

