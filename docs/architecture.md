# Architecture

How SATVA is put together, and why each choice was made rather than an obvious
alternative.

---

## The shape of the system

```
┌─────────────────────────────────────────────────────────────────────┐
│  CONSUMER HANDSET (Flutter, offline-first)                          │
│                                                                     │
│   camera ──► Layer A: MobileNetV3-Small (TFLite)  ──► 0–100 score   │
│                 │ advisory only, image never uploaded               │
│                 ▼                                                   │
│              suspicious?                                            │
│                 │ yes                                               │
│                 ▼                                                   │
│   camera ──► Layer B: colorimetric engine (Dart)  ──► value ± CI    │
│                 │ card detect → quality gates → correction → ΔE     │
│                 ▼                                                   │
│              SQLite offline queue ──────────┐                       │
└─────────────────────────────────────────────┼───────────────────────┘
                                              │ when connectivity returns
┌─────────────────────────────────────────────▼───────────────────────┐
│  API (FastAPI)                                                      │
│                                                                     │
│   evidence.py  ◄── every escalation passes through here             │
│        │                                                            │
│        ├─► identity schema     users · devices · merchants · audit  │
│        └─► analytics schema    scans · readings · clusters          │
│                    ▲                                                │
│                    │ satva_analytics role: NO grant on identity      │
│   Celery ──────────┘                                                │
│    ├── DBSCAN clustering        every 30 min                        │
│    ├── daily Merkle root        00:15 IST                           │
│    ├── evidence retention sweep 03:00                               │
│    └── shelf prediction refresh every 6 h                           │
└──────────┬────────────────────────────────┬─────────────────────────┘
           │                                │
   ┌───────▼────────┐              ┌────────▼─────────┐
   │ PUBLIC MAP     │              │ OFFICER VIEW     │
   │ wards only     │              │ vendors, audited │
   └────────────────┘              └──────────────────┘
```

---

## Decisions that shaped everything else

### The evidence rule lives in one file

`app/services/evidence.py` holds a single table of what each evidence grade may
do, and one function that can promote a scan.

The alternative — checking `if scan.has_reading` at each call site — would work
today and rot immediately. A new endpoint that forgets the check ships silently.
With one gate, a new endpoint that forgets to call it fails its test, and the
rule can be read and reviewed as a unit.

The database backs it up with two CHECK constraints: an accepted reading must
carry a value, and a refused one must carry a reason. Neither state is
constructible by any code path.

### The privacy boundary is a database permission, not a convention

Two schemas, no foreign key between them, and a `satva_analytics` role with no
grant on `identity`.

A code-level convention ("the clustering job doesn't read the users table")
holds until someone writes a convenient join. A permission error holds
regardless of who writes what:

```
satva_analytics → SELECT FROM identity.users  →  ERROR: permission denied
```

The bridge between a person and a scan is `identity.scan_ownership`, deliberately
placed on the identity side so an identity-blind connection cannot traverse it.

### Append-only is a trigger, not discipline

```sql
CREATE TRIGGER trg_custody_events_append_only
BEFORE UPDATE OR DELETE ON analytics.custody_events ...
```

Tamper-evidence that relies on the application never issuing an `UPDATE` is only
as strong as the application. The trigger holds against a direct `psql` session
and a compromised service account.

### The colorimetry engine exists twice

Python on the server, Dart on the handset — because the reading must work with
no connectivity *and* be independently recomputable when a finding is disputed.

Two implementations is a real risk: they drift, and then the phone and the
server disagree about a number attached to someone's complaint. So both are
tested against one generated file, `docs/colorimetry/golden_vectors.json`,
regenerated from the Python tables. If they ever diverge, a test fails rather
than a reading quietly changing.

### A hash chain, not a blockchain

The requirement is tamper-evidence, not decentralised consensus.

Each custody record stores `SHA-256(canonical_payload ‖ previous_hash)`, so
altering history breaks every link after it. A daily Merkle root published as an
external anchor closes the remaining hole: an operator who rewrites a row *and*
re-hashes the whole chain produces an internally consistent chain whose root no
longer matches what was published.

A hash chain in PostgreSQL is simpler, cheaper, queryable, auditable — and
sufficient. That is the entire argument.

The one detail worth care is **canonicalisation**. If serialisation is ambiguous,
verification fails on rows nobody touched, and a chain that cries wolf is worse
than none. So: sorted keys, fixed separators, UTC ISO-8601 with explicit offset,
Decimals as plain strings, and NaN rejected outright rather than stringified.

### DBSCAN, on haversine, with corroboration applied afterwards

Contamination hotspots are irregular, unknown in number, and embedded in noise.
k-means needs the cluster count in advance and forces every point into a
cluster; DBSCAN needs neither and labels isolated readings as noise.

Distance is **haversine over radians**, not Euclidean over degrees. At Palakkad's
latitude a degree of longitude is 1.7% shorter than a degree of latitude, and
treating them alike would stretch every cluster east-west — silently changing
which readings corroborate each other.

Time is handled by **windowing**, not as a third metric dimension, because there
is no defensible exchange rate between metres and hours.

The independent-device rule is applied **after** clustering, so the officer view
can show a sub-threshold pattern with its reason while the public map cannot.

### Layer A never uploads the photograph

Specification §5.1 and the pitch's own framing: SATVA should not become a company
that stores a photograph of everything a consumer buys.

Only the score and a small saliency summary travel. An image is uploaded only for
Layer B, and only when the user is building evidence.

---

## The colorimetric pipeline

Ten steps, in specification order, with the residual gate applied **early** so a
badly corrected frame can never produce a number at all.

```
1  detect the reference card          → refuse: card not found / incomplete
2  validate exposure                  → refuse: out of range / clipped
3  validate illumination              → refuse: tinted / non-uniform
4  validate the strip region          → refuse: not found / not uniform / clipped
5  sample 12 known patches
6  fit a colour correction            → refuse: residual too high
7  correct the strip, convert to Lab
8  project onto the calibration path  → refuse: off-path (not this reagent)
9  read concentration
10 propagate uncertainty → interval
```

Three details that matter:

**The correction is fitted in linear RGB.** That is the space where camera
response to light is approximately affine. Fitting in gamma-encoded sRGB folds
the transfer function into the fit, producing a correction valid only at the
exposure it was fitted at.

**Model selection uses leave-one-out, not in-sample residual.** The 3×3 affine
model has enough freedom to fit 12 patches well while generalising worse to the
strip colour — precisely the failure an in-sample residual hides.

**The action threshold is compared against the interval's *lower* bound.**
Escalating on the point estimate would flag readings whose uncertainty still
spans "not detected". A complaint against an honest vendor is the exact harm
rule 1 exists to prevent.

### Clipping is measured excluding the strip well

An unreacted turmeric strip is a legitimately near-saturated yellow. Counting it
as card clipping would refuse exactly the "not detected" readings that clear a
vendor — so the well is excluded from the card metric and checked separately,
where clipping genuinely destroys the measurement.

This was found by testing, not by inspection: the 0.0 stop was being refused
under warm light.

---

## The model

```
MobileNetV3-Small backbone (576-d)
├── adulteration head  → logit → calibrated 0–100
└── ripeness head      → sigmoid → 0–1
```

One backbone, one forward pass, two answers — consumer safety and retailer
economics from the same photograph (specification §9.4). One ~2 MB model on the
handset instead of two.

The anomaly head does **not** detect a chemical and cannot: a camera measures
reflected light. It is trained on the morphological consequences the
specification lists — uniform skin colour with a green calyx, speckled surface
burns, unnatural sheen, texture lagging colour.

**Export is verified, not assumed.** Three quantisation schemes are built and
measured against the FP32 source on real inputs. The release gate is
**triage-band agreement**, not raw score drift, because band membership is the
only thing Layer A's output decides. INT8 scored 80% and was rejected; float16
scored 100% and ships.

The graph converter does not preserve output names, so the index of each output
is **measured at export time** and published in the model card. The app reads
that map rather than assuming an order — assuming would eventually display a
ripeness index as an anomaly score.

---

## Request flow: a confirmed scan

```
1  POST /scans                       Layer A result. Photograph NOT uploaded.
                                     → grade: screening_only
2  POST /colorimetry/scans/{id}/readings
                                     → accepted?  grade: confirmatory
                                     → refused?   grade: rejected  (and stored)
3  POST /scans/{id}/images           strip photo, retention deadline set
4  POST /scans/{id}/share            evidence.require_evidence_grade() ← gate
5  Celery: DBSCAN                    corroboration applied
6  POST /complaints                  gate again → PDF + SHA-256
```

Steps 1–3 all work offline and sync later. The sync order matters: create, then
attach the reading, then upload images, then apply sharing — sharing before the
reading exists would be refused by the evidence rule, correctly but pointlessly.

---

## Technology choices

| Layer | Choice | Why this one |
|---|---|---|
| Mobile | Flutter 3 + Riverpod | One codebase; Riverpod's async providers fit "load model once, use everywhere" |
| On-device Lab maths | Pure Dart | No FFI build. A pure-Dart dependency works on every Android device |
| On-device inference | `tflite_flutter` | The runtime the specification names |
| Local storage | sqflite | Relational queue with real transactions |
| API | FastAPI + Pydantic v2 | Validation and OpenAPI from the same types |
| ORM | SQLAlchemy 2.0 | Typed `Mapped[]`; PostGIS and pgvector both have first-class support |
| Database | PostgreSQL 16 + PostGIS + pgvector | Spatial clustering and vector similarity in one engine, one transaction |
| Jobs | Celery + Redis | Beat gives cron-like scheduling without a separate scheduler |
| Object storage | S3-compatible (MinIO) | Same code path for MinIO locally and R2/S3 in deployment |
| Dashboards | React 18 + Vite + Tailwind + Recharts + MapLibre | As specified; MapLibre avoids a proprietary tile dependency |
| Password hashing | `bcrypt` directly | `passlib` is unmaintained since 2020 and breaks on bcrypt 5.x |

---

## Data model

27 tables across two schemas. UUID primary keys throughout — scan and lot ids
travel through QR codes, URLs and complaint documents, and sequential integers
would leak volume and allow enumeration of other people's evidence.

**identity** (6) — `users`, `devices`, `scan_ownership`, `otp_challenges`,
`merchants`, `audit_log`

**analytics** (21) — `scans`, `scan_images`, `chemical_readings`, `farms`,
`lots`, `custody_events`, `temperature_readings`, `hotspot_clusters`,
`cluster_members`, `cluster_runs`, `complaints`, `merkle_roots`,
`retail_outlets`, `inventory_items`, `shelf_predictions`, `spoilage_events`,
`trust_scores`, `marketplace_listings`, `buyer_ratings`, `device_records`,
`node_telemetry`

Every table that can hold seeded data carries `is_synthetic`, indexed, so demo
rows can be filtered, badged, and reset without touching real data.

---

## Where to change things

| To change… | Edit only… |
|---|---|
| Add a reagent | `services/colorimetry/calibration.py`, then regenerate golden vectors |
| Adjust refusal thresholds | `services/colorimetry/quality.py` — one dataclass |
| Real ward boundaries | `services/watch/wards.py` |
| Clustering parameters | `.env` (eps, min samples, window, min devices) |
| Retrain the model | `ml/` — point `--manifest` at real data; nothing else changes |
| Shelf-life coefficients | `services/shelf/predictor.py` — one profile table |
| Trust Score weights | `services/direct/trust.py` — one dict |
