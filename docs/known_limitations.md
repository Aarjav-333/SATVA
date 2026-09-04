# Known limitations

Everything that is incomplete, unverified, or approximate. Written so a reviewer
does not have to find it themselves.

Ordered by how much it matters.

---

## 1. The vision model has not been trained on real produce

**Status:** development model.

Trained on procedurally generated images. It has learned the morphological
signatures as *rendered by the generator*, which is not the same as learning
them from fruit.

Three consequences, all surfaced rather than hidden:

- Validation ROC-AUC is **1.000**. That is a red flag, not a result: it means the
  generated classes are trivially separable. A real field dataset will produce
  lower and far more informative numbers.
- **Calibration was declined.** Temperature scaling is ill-posed on a perfectly
  separable set — the likelihood is minimised as T→0 and an unguarded optimiser
  returns `NaN`. `calibrate.py` detects the separability and refuses. The
  reported ECE of 0.38 means the scores are **not** well-calibrated probabilities.
- **Full INT8 quantisation failed the release gate** at 80% triage-band
  agreement against the FP32 source (correlation 0.845). Float16 passed at 100%
  and ships instead, at 2.10 MB — still under the specification's ~4 MB target.
  Both INT8 files are retained so the comparison is reproducible.

**What would fix it:** the field and controlled-lab datasets in specification
§6.3. The pipeline takes them unchanged — point `--manifest` at a new CSV.

---

## 2. The calibration series have no laboratory validation

**Status:** provisional engineering calibrations.

The five reagent series encode the correct *shape* of each reaction path and
produce reproducible, self-consistent numbers. They have not been checked
against a NABL-accredited laboratory.

`is_lab_validated` is `false` on every series and travels all the way to the UI
and the complaint PDF, where it prints as a limitation the reader must weigh.

The quality thresholds (exposure limits, maximum illuminant chroma, residual
ceilings) are conservative engineering defaults, not validated operating points.
They live in one dataclass so a calibration campaign can replace them without
touching logic.

**What would fix it:** [the validation plan](validation_plan.md).

---

## 3. On-device performance is unmeasured

No Android hardware was available during the build.

- The **<200 ms** inference target is **unverified**.
- The **~₹8,000 device** target is unverified.
- **XNNPACK fails to prepare** the exported graph (`node 139`). TFLite falls back
  to reference kernels, so results are correct but slower than they should be.
  Verification deliberately uses reference kernels for this reason.
- The card detector runs in pure Dart rather than OpenCV, to avoid an FFI build.
  It downsamples to 640 px before contour finding, but its throughput on a
  low-end handset is unmeasured.

**What would fix it:** a ₹8,000-class Android device and a morning.

---

## 4. SATVA cannot file a complaint

There is no public programmatic route into FSSAI Food Safety Connect.

SATVA prepares a complete evidence package and gives the citizen filing
instructions. Status is `SUBMITTED_BY_USER`, never `SUBMITTED`, because SATVA
cannot verify a filing.

This is a deliberate refusal rather than a gap. A citizen who believes their
complaint was filed, when it was not, is left worse off than if SATVA had said
nothing.

---

## 5. Ward boundaries are approximate

13 hand-built wards for Palakkad and Thrissur, each a representative point with
a radius. A coordinate is assigned to the nearest ward centre within range.

Every match is flagged `is_approximate = true`. A point outside every ward
returns `None` rather than being guessed into the nearest one — it still records
its coordinates for the officer view, but does not appear on the ward map.

**What would fix it:** load official LSGD ward polygons into a PostGIS table.
Only `app/services/watch/wards.py` changes.

---

## 6. The Node gas sensor is not a specific assay

An MQ-series sensor is a heated tin-dioxide element whose resistance falls in
the presence of a broad class of reducing gases. It cannot distinguish acetylene
from ethylene, ethanol, LPG, or ordinary market volatiles, and its baseline
drifts with temperature and humidity.

Node telemetry is therefore **advisory**: a raised reading is a reason to perform
a confirmatory strip test on that crate. It is never evidence, and
`app/services/evidence.py` will not promote a scan on it.

The ingestion path is real (firmware → MQTT → bridge → API → database, verified
end to end). No physical device was built; readings come from
`scripts/simulate_node.py` and are stored with `is_simulated` and
`is_synthetic` set.

---

## 7. Saliency is regional, not per-pixel

The overlay shows the anatomical regions the model was trained to attend to,
weighted by the score — not a gradient-based attention map.

A true attention map needs gradient access or an occlusion sweep (~25 extra
forward passes), which would blow the ten-second interaction budget. The UI
labels it "regions this result responded to" rather than claiming more than it
delivers.

For the heuristic screener the regions *are* the indicators that actually fired,
which is closer to a genuine explanation.

---

## 8. Firebase phone OTP is a seam, not an integration

No Firebase project exists. The `FirebaseOtpProvider` class defines the
interface; the `DevOtpProvider` handles the demo.

The dev provider is honest about what it is: it stamps `provider="dev"`, returns
the code in the API response with a message explaining why, and **refuses to run
when `SATVA_ENV=production`**.

---

## 9. Security work left undone

Adequate for a hackathon deployment; not adequate for production.

| Gap | Consequence |
|---|---|
| Node auth is a shared secret | Fine for a bridge inside the deployment's own network. Per-device credentials and mTLS before exposing it to field hardware. |
| MQTT broker allows anonymous | Only reachable inside the compose network. Must not be exposed as-is. |
| No refresh-token rotation | A stolen refresh token stays valid for 14 days. |
| Rate limiting falls back in-process | Without Redis the limit is per-worker, so weaker than configured. It logs a warning when this happens. |
| No device attestation | Fields and trust weighting exist; Play Integrity is not wired up. |
| Image EXIF is not stripped | Uploaded evidence may carry embedded GPS and device metadata beyond what the scan records. |
| No custody handover record | See below. |

### Custody write authority is bounded, not proven

`POST /custody/{lot_id}` now requires the caller to be the lot's farm owner, a
retailer holding the lot in inventory, or an officer — instead of merely holding
a supply-chain role, which previously let any farmer, retailer or officer append
to any lot's chain. Because custody events are append-only at the database
level, those writes could never be withdrawn.

What is still missing is a model of who *currently* holds a lot. A retailer
establishes standing by booking the lot into their own inventory, and nothing
verifies that the booking reflects a real handover. So the check bounds who can
write and makes the write attributable; it does not prove custody. A
`custody_transfer` record, accepted by the receiving party and signed by the
sending one, is the actual fix.

### A device-computed reading is checkable, not verified

A scan reaches `confirmatory` only once the strip photograph it was derived from
is stored (`app/services/evidence.py`), so any reading that carries consequence
can be put back through the same pipeline via
`POST /colorimetry/scans/{id}/recompute`. That is what makes a disputed number
answerable.

It is not the same as verification. The server still stores the handset's
number rather than deriving its own, and nothing yet checks that a submitted
value matches what the stored image would produce. A client that uploads a
genuine strip photograph and reports a different concentration would not be
caught until someone recomputes. Recomputing on ingest — and grading a scan on
the server's number, not the client's — closes that gap and is the natural next
step.

---

## 10. Scale

Honest about where the current implementation stops.

- **Perceptual-hash lookup** does an exact indexed match first, then a linear
  scan over the recent window (bounded at 5,000 rows). At national volume this
  needs a BK-tree or a `pg_bktree` index. The interface does not change.
- **Clustering recomputes wholesale** rather than incrementally. Correct — a
  reading leaving the window can dissolve a cluster — but O(n) per run.
- **No read replicas, no partitioning.** `analytics.scans` is the table that
  will grow without bound; it wants time partitioning first.

---

## 11. Testing gaps

139 backend tests and 33 Dart tests, concentrated on the parts where being wrong
would cause harm: colorimetry, the hash chain, corroboration, and the evidence
rule.

Thinner coverage:

- **No database integration tests.** Service-layer logic is tested against pure
  functions; the API was verified manually end to end over HTTP rather than in
  CI.
- **No widget tests** for the Flutter UI.
- **No end-to-end browser tests** for the dashboards.
- **No load testing.**

---

## 12. Environment notes

- **Python 3.11 locally, 3.12 in Docker.** The specification asks for 3.12; the
  Docker image uses it. Local development was on 3.11 and nothing depends on a
  3.12-only feature.
- **No single official Postgres image has both PostGIS and pgvector.**
  `infrastructure/postgres/Dockerfile` builds one by adding pgvector to the
  PostGIS image.
- **`passlib` was removed.** Unmaintained since 2020 and incompatible with
  bcrypt 5.x. `app/core/security.py` uses `bcrypt` directly, with SHA-256
  pre-hashing so passwords over 72 bytes are not silently truncated.
