# Deployment

From a working local stack to something other people can use.

---

## Before anything else

The application refuses to start in production with a development secret:

```python
if settings.env == "production" and settings.is_dev_secret:
    raise RuntimeError("Refusing to start in production with the development SECRET_KEY.")
```

Generate real values:

```bash
python -c "import secrets; print('SATVA_SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('SATVA_DEVICE_HASH_PEPPER=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('SATVA_NODE_INGEST_TOKEN=' + secrets.token_urlsafe(32))"
```

**`SATVA_DEVICE_HASH_PEPPER` cannot be rotated casually.** Device pseudonyms are
derived from it, so changing it makes every existing device look new and breaks
independent-device corroboration until history ages out of the window. Treat it
like a database encryption key.

---

## Minimum viable deployment

Fits comfortably on one small VPS (2 vCPU, 4 GB) for a pilot.

```bash
git clone <repo> satva && cd satva
cp .env.example .env
# edit .env: SATVA_ENV=production, real secrets, real database URL

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.create_admin   # not the demo seeder
```

**Do not run `seed_demo` on a production database.** It creates accounts with a
published password.

---

## Production overrides

Create `docker-compose.prod.yml`:

```yaml
services:
  api:
    command: >
      uvicorn app.main:app --host 0.0.0.0 --port 8000
      --workers 4 --proxy-headers --forwarded-allow-ips='*'
    volumes: []          # no bind mount; the image is the artefact
    restart: always
    environment:
      SATVA_ENV: production
      SATVA_DEBUG: "false"

  postgres:
    ports: []            # not reachable from outside the compose network
  redis:
    ports: []
  minio:
    ports: []

  dashboard:
    command: npm run build && npx serve -s dist -l 5173
```

Removing the published ports on Postgres, Redis and MinIO matters. A database
listening on `0.0.0.0:5432` with a password from `.env.example` is the single
most likely way this gets compromised.

---

## Database

The stack builds its own Postgres image because no official one ships both
PostGIS and pgvector:

```dockerfile
FROM postgis/postgis:16-3.4
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-16-pgvector
```

### Using a managed service instead

Most managed Postgres offerings support both extensions. Point
`SATVA_DATABASE_URL` at it and run the migration; the extension `CREATE`
statements are idempotent.

Then create the identity-blind role by hand — the migration creates it, but a
managed instance may not permit `CREATE ROLE` from the migration user:

```sql
CREATE ROLE satva_analytics LOGIN PASSWORD '<strong-password>';
GRANT USAGE ON SCHEMA analytics TO satva_analytics;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics TO satva_analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO satva_analytics;

REVOKE ALL ON SCHEMA identity FROM satva_analytics;
REVOKE ALL ON ALL TABLES IN SCHEMA identity FROM satva_analytics;
REVOKE ALL ON SCHEMA identity FROM PUBLIC;
```

**Verify it, don't assume it:**

```bash
PGPASSWORD=... psql -U satva_analytics -d satva -c "SELECT count(*) FROM identity.users;"
# must be: ERROR: permission denied for schema identity
```

Then set `SATVA_ANALYTICS_DATABASE_URL` to that role's URL.

### Backups

```bash
docker compose exec postgres pg_dump -U satva -Fc satva > satva-$(date +%F).dump
```

Two notes specific to SATVA:

- **The custody chain is self-verifying.** After any restore, run
  `GET /lots/{id}/verify` across the lots. A restore that silently lost rows
  will show as a sequence gap.
- **Published Merkle roots are the external check.** Recompute a day's root
  after a restore and compare it with what was published. Divergence means the
  restore is not faithful.

---

## Object storage

MinIO locally; Cloudflare R2 or S3 in deployment. Only the endpoint and
credentials change.

```bash
SATVA_S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
SATVA_S3_PUBLIC_ENDPOINT_URL=https://evidence.your-domain.example
SATVA_S3_BUCKET=satva-evidence
```

**The bucket must be private.** Evidence images are reached through short-lived
presigned URLs (15 minutes). A public bucket would make every complaint
photograph world-readable.

The retention sweep runs at 03:00 and deletes objects past their deadline —
90 days by default, extended while a complaint is live (specification §15.3).

---

## Reverse proxy

```nginx
server {
    listen 443 ssl http2;
    server_name satva.example;

    # Evidence photographs are several megabytes.
    client_max_body_size 12M;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        root /var/www/satva-dashboard;
        try_files $uri $uri/ /index.html;   # SPA routing
    }

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
}
```

`--proxy-headers` on uvicorn matters: without it every request appears to come
from the proxy, and the rate limiter's IP fallback becomes useless.

---

## CORS

Development allows `localhost:5173`. Production allows nothing by default —
set it deliberately in `app/main.py`:

```python
allow_origins=["https://satva.example"]
```

Never `["*"]` with `allow_credentials=True`. It is forbidden by the spec and
would expose every authenticated dashboard call to any origin.

---

## Mobile

```bash
cd mobile
flutter build apk --release \
  --dart-define=SATVA_API_BASE=https://satva.example/api/v1
```

Signing: create `android/key.properties` (gitignored) and a keystore, then
configure `signingConfigs` in `android/app/build.gradle`. A debug-signed APK
cannot be updated in place later.

To ship a retrained model, replace `assets/models/satva_screen.tflite` **and**
`model_card.json` together. The card carries the measured output index map; a
model without a matching card will read the wrong tensor.

---

## Workers

```
worker  → celery -A app.workers.celery_app worker --concurrency=2
beat    → celery -A app.workers.celery_app beat
```

**Exactly one beat process.** Two will double-publish Merkle roots, and the
second publish will look like a divergence.

| Job | Schedule |
|---|---|
| Clustering | every 30 min |
| Merkle root | 00:15 IST, over the previous closed day |
| Retention sweep | 03:00 |
| Shelf refresh | every 6 h |

The worker should use `SATVA_ANALYTICS_DATABASE_URL`, so the identity-blind
constraint holds in practice and not only in the migration.

---

## What to watch

Structured JSON logs outside development. The lines worth alerting on:

| Event | Why |
|---|---|
| `merkle_root_divergence_detected` | **Highest priority.** Covered custody history changed after publication. |
| `duplicate_scan_detected` with a different device | Possible coordinated reporting. |
| `rate_limiter_using_in_process_fallback` | Redis is unreachable; limits are weaker than configured. |
| `officer.vendor_detail_viewed` | Not an alert — the audit trail. Retain it. |
| `object_storage_unavailable` | Evidence uploads are failing. |

Health check: `GET /api/v1/health` reports database reachability.

---

## Before going live

- [ ] `SATVA_ENV=production`, real `SECRET_KEY` and `DEVICE_HASH_PEPPER`
- [ ] Postgres, Redis and MinIO not publicly reachable
- [ ] `satva_analytics` role created **and its denial verified**
- [ ] Object store private; presigned URLs working
- [ ] TLS with HSTS; CORS set to the real origin
- [ ] Backups scheduled **and a restore tested**
- [ ] Exactly one beat process
- [ ] Demo seeder **not** run; demo accounts absent
- [ ] `docs/known_limitations.md` read by whoever is accountable for the deployment
