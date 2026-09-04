# PostgreSQL 16 with BOTH PostGIS and pgvector.
#
# SATVA needs PostGIS for spatial clustering and ward queries, and pgvector for
# near-duplicate image detection. No official image ships both, so this builds
# one: start from the PostGIS image and add pgvector from the PGDG repository
# that image already trusts.
FROM postgis/postgis:16-3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
