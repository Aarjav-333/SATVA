# SATVA

**Scan · Analyse · Trace · Verify · Alert**

A food-safety screening instrument for the person holding the fruit — and a way
to turn what they find into evidence a regulator can act on.

> **A photograph should never convict a vendor.**
> That single rule shapes every part of this system.

---

## What SATVA does

Screening happens in two layers, and they are kept strictly apart:

| | **Layer A — Vision** | **Layer B — Colorimetry** |
|---|---|---|
| Where | On the handset | On the handset (or server) |
| Cost | ₹0 | ~₹5 per strip |
| Network | None needed | None needed |
| Output | 0–100 anomaly score | A concentration with a confidence interval |
| Status | **Advisory. Never evidence.** | **The only result SATVA will act on.** |

Layer A's entire job is to decide whether a five-rupee chemical test is worth
performing. Publishing, clustering, and complaint generation all run on Layer B.

Attempting to escalate a Layer-A result returns `409 evidence_rule_violation` —
this is enforced in one place, [`app/services/evidence.py`](backend/app/services/evidence.py),
and covered by tests.

### The five modules

| Module | What it is | State in this build |
|---|---|---|
| **Scan** | Dual-signal adulteration screening | ✅ Working end to end |
| **Watch** | Anonymised hotspots, corroboration, FSSAI complaints | ✅ Working end to end |
| **Trace** | Tamper-evident provenance (hash chain + Merkle root) | ✅ Working |
| **Shelf** | Ripeness and remaining shelf life for retailers | ✅ Working (documented model) |
| **Direct** | Verified farm-to-consumer marketplace, Trust Score | 🟡 Scaffolded |
| **Node** | ESP32 field sensing | 🟡 Ingestion real, sensor unvalidated |

Full breakdown: **[docs/mocked_vs_real.md](docs/mocked_vs_real.md)**

---

## Quick start

```bash
git clone <repository-url> satva && cd satva
cp .env.example .env

docker compose up -d                       # Postgres+PostGIS+pgvector, Redis, MinIO, API, workers
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed_demo
```

Then:

| | |
|---|---|
| API docs | <http://localhost:8000/docs> |
| Dashboards | <http://localhost:5173> |
| MinIO console | <http://localhost:9001> |

**Demo sign-in** — `officer@satva.demo` / `satva-demo-2026`
(also `retailer@`, `farmer@`, `admin@`).

Consumer sign-in uses phone OTP. With no SMS provider configured, the dev
provider returns the code in the API response and says so — it never pretends
an SMS was sent.

### Without Docker

<details>
<summary>Running the pieces directly</summary>

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m scripts.seed_demo
uvicorn app.main:app --reload

# Dashboards
cd dashboard && npm install && npm run dev

# Mobile
cd mobile && flutter pub get
flutter run --dart-define=SATVA_API_BASE=http://10.0.2.2:8000/api/v1   # emulator
```

You still need PostgreSQL 16 with **both** PostGIS and pgvector. No official
image ships both, so [`infrastructure/postgres/Dockerfile`](infrastructure/postgres/Dockerfile)
builds one:

```bash
docker compose up -d postgres redis minio
```
</details>

---

## Repository layout

```
satva/
├── backend/          FastAPI · SQLAlchemy · Celery · the colorimetry engine
│   ├── app/
│   │   ├── api/v1/          53 REST endpoints
│   │   ├── core/            config, security, the domain vocabulary
│   │   ├── db/              two schemas: identity and analytics
│   │   ├── models/          27 tables
│   │   ├── services/
│   │   │   ├── colorimetry/ ← Layer B. The reference implementation.
│   │   │   ├── evidence.py  ← the evidence rule, in one place
│   │   │   ├── trace/       hash chain + Merkle
│   │   │   ├── watch/       DBSCAN, dedup, ward resolution
│   │   │   ├── shelf/       degree-hour shelf-life model
│   │   │   └── complaints/  FSSAI package generation
│   │   └── workers/         Celery tasks + the MQTT bridge
│   ├── alembic/             migrations
│   └── tests/               190 tests
├── mobile/           Flutter 3 · Riverpod · sqflite · TFLite
│   └── lib/features/colorimetry/engine/  ← Layer B, mirrored in Dart
├── dashboard/        React 18 · Vite · Tailwind · Recharts · MapLibre
├── ml/               PyTorch → ONNX → TFLite, with a model card
├── hardware/         ESP32 firmware + MQTT schema
├── infrastructure/   Postgres image, mosquitto config
├── docs/             architecture, demo, limitations, validation plan
└── scripts/          node simulator
```

---

## The parts worth reading first

### The colorimetric reader

[`backend/app/services/colorimetry/`](backend/app/services/colorimetry/) implements
specification §7 in order: detect the reference card → validate exposure and
lighting → fit a colour correction → isolate the strip → convert to CIE L\*a\*b\*
→ compute ΔE against calibrated stops → report a value with a confidence
interval → **refuse if the correction residual is too large**.

CIEDE2000 is verified against all 34 published Sharma/Wu/Dalal test pairs.
Under simulated market capture (warm light, perspective, noise, JPEG) the
carbide series reads within **2.6% of full scale**, and the confidence interval
covers the true value at every stop.

Every one of the eight refusal conditions is tested and fires:

```
no card              → reference_card_not_found
over-exposed         → exposure_out_of_range
under-exposed        → clipped_shadows
tinted light         → illuminant_too_tinted
shadow across card   → correction_residual_too_high
glare on strip       → clipped_highlights
strip missing        → out_of_calibration_range
wrong reagent        → unknown_reagent
```

A refusal **never** carries a number. That contract is asserted directly.

### Dart/Python parity

The engine exists twice — Python on the server, Dart on the handset — because
the reading must work offline *and* be independently recomputable during a
dispute. Both are tested against the same generated file,
[`docs/colorimetry/golden_vectors.json`](docs/colorimetry/golden_vectors.json).
All 33 Dart parity tests pass, so an offline reading and a server recomputation
cannot disagree.

### The privacy boundary, enforced by the database

Non-negotiable rules 11 and 12 are structural, not conventions:

```sql
identity.*    people, phones, sessions, merchants, audit trail
analytics.*   scans, readings, clusters   — no PII columns, no FK to identity
```

The clustering worker connects as `satva_analytics`, a role with **no grant** on
the identity schema. Verified:

```
satva_analytics → SELECT FROM identity.users     ERROR: permission denied for schema identity
satva_analytics → SELECT FROM analytics.scans    0 rows (allowed)
satva_analytics → list identity tables            0
```

### Append-only custody, enforced by the database

```
UPDATE analytics.custody_events → ERROR: append-only: UPDATE is not permitted
DELETE FROM analytics.custody_events → ERROR: append-only: DELETE is not permitted
```

A trigger blocks both, so tamper-evidence does not depend on the application
remembering to behave.

---

## Verification

```bash
cd backend  && python -m pytest -q          # 190 passed
cd mobile   && flutter test                 # 33 passed
cd dashboard && npm run build && npx eslint src --ext js,jsx
```

What the backend suite actually covers:

| Area | Notable assertions |
|---|---|
| Colorimetry (65) | CIEDE2000 vs published data; all 8 refusals; refusal carries no number; no series claims lab validation |
| Hash chain (42) | Backdating, deletion, insertion and re-hashed forgery all detected; Merkle inclusion proofs at 11 tree sizes; CVE-2012-2459 duplicate-node attack impossible |
| Watch (32) | One device cannot publish a hotspot; two colluding devices cannot either; simultaneous readings suppressed; pHash survives recompression |

---

## Documentation

| | |
|---|---|
| [Architecture](docs/architecture.md) | How the pieces fit and why |
| [Demo walkthrough](docs/demo_walkthrough.md) | The 3-minute run, with fallbacks |
| [Judging checklist](docs/judging_checklist.md) | Specification §18 checked item by item |
| [Mocked vs real](docs/mocked_vs_real.md) | What works, what is seeded, what is scaffolded |
| [Known limitations](docs/known_limitations.md) | Everything that is not finished, stated plainly |
| [Validation plan](docs/validation_plan.md) | What would make this trustworthy |
| [Deployment](docs/deployment.md) | Getting it onto a server |
| [API](docs/api.md) | Endpoint reference (live at `/docs`) |
| [Model card](ml/MODEL_CARD.md) | What the model is, and what it is not |

---

## Honest status

This is a hackathon build. Three things must be said plainly:

1. **The vision model is a development model.** It was trained on procedurally
   generated images, not real produce. Its validation ROC-AUC of 1.000 is a red
   flag — it means the synthetic classes are trivially separable — not an
   achievement. The app labels it, the API records it, and
   [the model card](ml/MODEL_CARD.md) explains it.

2. **The calibration series are provisional.** They produce reproducible,
   self-consistent numbers, but they have not been checked against a
   NABL-accredited laboratory. `is_lab_validated` is `false` on every series,
   and that flag reaches the UI and the complaint PDF.

3. **SATVA does not file complaints.** There is no public programmatic route
   into FSSAI Food Safety Connect. SATVA prepares an evidence package and the
   citizen files it. A complaint's status is `SUBMITTED_BY_USER`, never
   `SUBMITTED`, because SATVA cannot verify a filing.

---

## Licence and standing disclaimer

MIT. Built for Infinity Hacks 2026, Food Safety track, by Team Real Fighters.

> SATVA is a screening aid. It is not a statutory test, a certification, or a
> legal determination of food safety. A visual screening result is advisory only
> and must never be treated as proof of adulteration. Only a confirmatory
> colorimetric strip reading may be escalated, and escalation is routed to the
> statutory FSSAI channel rather than replacing it.
