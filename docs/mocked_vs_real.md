# What is real, what is seeded, what is scaffolded

The specification is explicit that scope must stay credible and that
presentation data must never be passed off as collected evidence. This document
is the honest accounting.

**Rule of thumb:** if it computes something, it computes it for real. If it
displays something without computing it, this document says so.

---

## Tier 1 — Real and working

These do the actual work, are covered by tests, and were verified running.

| Capability | Evidence it works |
|---|---|
| **Colorimetric pipeline** (all 10 steps of §7) | 65 tests. CIEDE2000 matches all 34 published Sharma test pairs to 5×10⁻⁵. Carbide series reads within 2.6% of full scale under simulated market capture. |
| **All 8 refusal conditions** | Each tested individually and fires. A refusal is asserted to carry no number, no interval, and no threshold flag. |
| **Dart/Python parity** | 33 Dart tests against shared golden vectors. An offline reading and a server recomputation produce identical values. |
| **Hash chain + Merkle anchoring** | 42 tests. Backdating, deletion, insertion and fully re-hashed forgery all detected. Inclusion proofs verified at 11 tree sizes. Duplicate-node attack (CVE-2012-2459) impossible by construction. |
| **Append-only custody** | Database trigger. Verified: `UPDATE` and `DELETE` both rejected from a direct `psql` session. |
| **DBSCAN clustering + corroboration** | 32 tests. Haversine metric. One device cannot publish a hotspot; two colluding devices cannot either. |
| **Identity/analytics separation** | Two PostgreSQL schemas. Verified: the analytics role gets `permission denied for schema identity` and cannot even enumerate identity tables. |
| **Evidence rule** | Enforced in one module, tested, and verified over HTTP: sharing and complaint generation both return `409 evidence_rule_violation` for a photo-only scan. |
| **Perceptual-hash deduplication** | Tested to survive JPEG recompression (q45) and resizing. |
| **Offline queue + sync** | SQLite schema, idempotency key, exponential backoff, per-item batch results. |
| **FSSAI complaint package** | Generates a real 4-page, 93 KB PDF with SHA-256 digests. [Sample included.](samples/sample_fssai_complaint_package.pdf) |
| **RBAC** | Verified matrix: anonymous → 401, wrong role → 403, right role → 200. Checked server-side against the database, not the token claim. |
| **Audit logging** | Every officer view of vendor detail writes a row before the handler runs. |
| **Node ingestion** | Verified: 14 samples stored, 4 advisory-flagged, re-sends idempotent, bad token → 401. |
| **REST API** | 53 endpoints, OpenAPI at `/docs`. |
| **Migrations** | 27 tables, 138 indexes, applied and rolled back cleanly. |

---

## Tier 2 — Real inference, development weights

| Capability | What is real | What is not |
|---|---|---|
| **Vision screening** | The architecture (MobileNetV3-Small, dual head), the training loop, calibration, ONNX export, INT8/float16 quantisation, parity verification, and on-device inference are all real and were run. | The **weights** were trained on procedurally generated images. The model knows nothing about real produce. |

The pipeline produced a genuine 2.10 MB float16 TFLite model that runs real
inference. What it cannot do is tell you anything true about a real mango.

Three things are surfaced rather than hidden:

- Validation ROC-AUC of **1.000** — a red flag, meaning the synthetic classes
  are trivially separable.
- Calibration was **declined**: the calibration set is perfectly separable, which
  makes temperature scaling ill-posed. The code detects this and refuses rather
  than returning the `NaN` an unguarded optimiser produces.
- Full INT8 **failed** the release gate (80% triage-band agreement against the
  FP32 source). Float16 passed at 100% and was shipped instead. Both INT8 files
  are retained so the comparison is reproducible.

See [ml/MODEL_CARD.md](../ml/MODEL_CARD.md).

---

## Tier 3 — Seeded demonstration data

**Every seeded row carries `is_synthetic = true`.** The API returns it, both
dashboards render a visible *Demo data* badge, and the officer worklist marks
affected clusters.

| Seeded | Volume | Why |
|---|---|---|
| Confirmed readings | 69 | A heatmap needs data to be legible |
| Screening-only scans | 26 | Shows the evidence-grade distinction |
| Refused measurements | 5 | Shows refusals as first-class records |
| Farms / FPOs | 5 | Palakkad and Thrissur, per the specification |
| Lots + custody chains | 11 (incl. 4 split children) | Real chains with real hashes |
| Merchants | 8 | Officer-only, in the identity schema |
| Retail inventory + predictions | 14 items | Shelf dashboard |
| Spoilage history | 45 days | Waste trend chart |
| Node telemetry | 144 samples | Hardware path |

The clustering that runs over this data is **not** seeded — DBSCAN runs for real
and produced: 6 clusters, 2 publishable, 4 withheld.

One seeded scenario matters more than the rest. Chittur has **9 readings at 100%
exceedance from a single device** — the most alarming-looking pattern in the
data — and it is correctly **suppressed** with
`awaiting_corroboration: 1 of 3 independent devices`. That is the
anti-defamation rule working, and it is visible in the officer view.

---

## Tier 4 — Scaffolded

Architecture complete, data model present, endpoints working; the missing part
is named.

| Capability | Present | Missing |
|---|---|---|
| **SATVA Direct** | Listings, Trust Score with four components and seasonal decay, buyer ratings, Bayesian shrinkage for small samples | Transactions, payments, settlement, escrow |
| **BLE temperature tags** | Ingest endpoint, storage, degree-hour integration into shelf life | Bluetooth stack. Readings come from the simulator or the MQTT bridge, flagged `is_simulated` |
| **Firebase phone OTP** | Provider seam, and a dev provider that is honest about being one | No Firebase project. The dev provider refuses to run when `SATVA_ENV=production` |
| **Device attestation** | Fields and trust weighting | Play Integrity integration |
| **Ward boundaries** | 13 hand-built wards for Palakkad/Thrissur with representative points | Official LSGD polygons. Every match is flagged `is_approximate` |

---

## Tier 5 — Documented, not built

| | Why not, and what exists instead |
|---|---|
| **Automatic FSSAI submission** | No public programmatic route into Food Safety Connect. SATVA prepares a package with filing instructions; status is `SUBMITTED_BY_USER`, never `SUBMITTED`. Inventing this would leave a citizen believing a complaint was filed when it was not — worse than doing nothing. |
| **NABL laboratory validation** | Requires laboratory access. `is_lab_validated` is `false` on every series and reaches the UI and the complaint PDF. [Validation plan.](validation_plan.md) |
| **Node gas-sensor science** | An MQ-series sensor is a broad reducing-gas detector, not an acetylene assay, and its baseline drifts with temperature and humidity. Telemetry is recorded as **advisory** and cannot promote a scan. |
| **On-device latency measurement** | No Android hardware was available. The <200 ms target is unverified, and XNNPACK currently fails to prepare the graph (node 139), which would make it worse. |
| **Multilingual UI** | Roadmap months 7–12. The system font is used precisely so Indic scripts will render when strings are added. |

---

## How to check any of this yourself

```bash
# The evidence rule really blocks escalation
curl -X POST localhost:8000/api/v1/complaints -H "Authorization: Bearer $TOKEN" \
     -d '{"scan_id":"<a screening-only scan>"}'
# → 409 evidence_rule_violation

# The analytics role really cannot read identity data
docker exec -e PGPASSWORD=satva_analytics_password satva-postgres \
  psql -U satva_analytics -d satva -c "SELECT count(*) FROM identity.users;"
# → ERROR: permission denied for schema identity

# Custody events really are append-only
docker exec satva-postgres psql -U satva -d satva \
  -c "UPDATE analytics.custody_events SET actor_kind='x';"
# → ERROR: append-only: UPDATE is not permitted

# Seeded rows really are flagged
docker exec satva-postgres psql -U satva -d satva \
  -c "SELECT is_synthetic, count(*) FROM analytics.scans GROUP BY 1;"
```
