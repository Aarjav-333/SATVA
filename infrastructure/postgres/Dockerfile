# PostgreSQL 16 with BOTH PostGIS and pgvector.
#
# SATVA needs PostGIS for spatial clustering and ward queries, and pgvector for
# near-duplicate image detection. No official image ships both, so this builds
# one: start from the PostGIS image and add pgvector from the PGDG repository
# that image already trusts.
FROM postgis/postgis:16-3.4

# Debian bullseye is past end-of-life, so its Release files have expired and a
# plain `apt-get update` exits non-zero. pgvector itself comes from PGDG, which
# is still maintained, but PGDG cannot be refreshed alone: postgresql-16-pgvector
# pulls postgresql-16 and libllvm16 forward, and those resolve against the Debian
# suites -- dropping them fails with "held broken packages".
#
# So the expiry check is switched off per-source, on the three deb.debian.org
# lines only, instead of globally with `-o Acquire::Check-Valid-Until=false`.
# PGDG -- the repository the extension actually ships from -- keeps both its
# signature and its freshness guarantee, so a stale or replayed PGDG index is
# still rejected. Signature verification stays on everywhere.
#
# Bullseye receives no security updates: see docs/known_limitations.md. There is
# no bookworm PostGIS tag for PostgreSQL 16 (16-3.4 and 16-3.5 are both
# bullseye), so that fix arrives with the PostgreSQL 17 upgrade.
#
# pgvector is pinned so a rebuild cannot silently change the extension under an
# existing volume. Bump with:
#   docker run --rm satva/postgis-pgvector:16 apt-cache policy postgresql-16-pgvector
ARG PGVECTOR_VERSION=0.8.6-1.pgdg11+1
RUN sed -i 's|^deb http://deb.debian.org|deb [check-valid-until=no] http://deb.debian.org|' \
        /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        "postgresql-16-pgvector=${PGVECTOR_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
