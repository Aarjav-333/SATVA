-- Extensions SATVA requires. Run once, at container initialisation, before
-- Alembic connects: creating an extension needs superuser, which the
-- application role does not have in a hardened deployment.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;
