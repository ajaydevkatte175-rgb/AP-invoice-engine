-- Database initialization script
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'invoice_ro') THEN
        CREATE ROLE invoice_ro WITH LOGIN PASSWORD 'readonlypass';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE invoicedb TO invoice_ro;
GRANT USAGE ON SCHEMA public TO invoice_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO invoice_ro;

