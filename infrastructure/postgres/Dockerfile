# PostgreSQL 16 with BOTH PostGIS and pgvector.
#
# SATVA needs PostGIS for spatial clustering and ward queries, and pgvector for
# near-duplicate image detection. No official image ships both, so this builds
# one: start from the PostGIS image and add pgvector from the PGDG repository
# that image already trusts.
FROM postgis/postgis:16-3.4

# Debian bullseye is past end-of-life, so its security suite's Release file has
# expired and plain `apt-get update` now exits non-zero on it. pgvector itself
# comes from the PGDG repository, which is still maintained; the expiry check is
# the only thing standing in the way, so it is what we switch off -- signature
# verification stays on. Bullseye no longer receives security updates: see
# docs/known_limitations.md. The fix is a bookworm base, which arrives with the
# PostgreSQL 17 upgrade.
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
