# Validation plan

What would have to be true before SATVA's numbers could be relied on. This is
specification §19, made concrete.

Nothing in this document has been done. It is written so the gap between "the
pipeline works" and "the readings are trustworthy" is explicit and costed.

---

## Why this document exists

SATVA currently produces reproducible numbers from a verified pipeline. That is
not the same as producing *correct* numbers.

Two things separate the two:

1. The vision model was trained on generated images.
2. The calibration series were constructed from the known chemistry of each
   reagent, not measured against a laboratory.

Both are flagged everywhere they surface. Neither is fixable with more code.

---

## Phase 1 — Controlled ripening (weeks 1–3)

**The experiment that answers "how do you know your labels are correct?"**

Take one lot of a single cultivar and split it three ways:

| Arm | Treatment | n |
|---|---|---|
| A | Natural ripening, ambient | 60 |
| B | Ethylene, at the permitted concentration | 60 |
| C | Calcium carbide, as practised in the trade | 60 |

Arm C requires institutional ethics approval and a fume hood; the fruit is
destroyed afterwards and never enters a food chain.

**Why this matters more than a larger field set.** Every field label is
*inferred* — from a strip reading, itself unvalidated. Here the treatment is
known by construction. This is the only data that can anchor anything else.

**Capture protocol.** Every fruit photographed at 0, 24, 48 and 72 hours, on
three handsets spanning the price range, under four lighting conditions
(daylight, shade, tungsten, fluorescent). Roughly 8,600 images.

**Outputs**
- The evaluation set the model is measured on — never trained on.
- The calibration set for temperature scaling. Unlike the current synthetic set,
  this one will contain genuinely overlapping cases, so calibration will be
  well-posed and will actually fit.

---

## Phase 2 — Laboratory validation of the strips (weeks 2–6)

**The step that lets `is_lab_validated` become `true`.**

For each reagent series, prepare a dilution series spanning the calibrated range
with five replicates per level. Read each with SATVA. Send a split sample to a
NABL-accredited laboratory.

**Analyse with the right tools.** Not correlation — correlation can be near 1.0
while every reading is off by a constant.

- **Bland–Altman** for bias and limits of agreement.
- **Deming regression** (both axes carry error, unlike ordinary least squares).
- **Repeatability**: replicate spread at each level, which replaces the
  currently-guessed `reagent_repeatability_de`.
- **Limit of detection and limit of quantification**, which the calibration
  series currently asserts rather than measures.

**Acceptance criteria, set before looking at the data**

| | Criterion |
|---|---|
| Bias | Within ±20% across the working range |
| Limits of agreement | Narrower than ±30% |
| Repeatability CV | Under 15% at the action threshold |
| False-positive rate at threshold | Under 5% on blanks |

**If a series fails**, it stays `is_lab_validated: false` and — if it fails
badly — is withdrawn. A reagent that cannot be validated should not be offered.

---

## Phase 3 — Combined pipeline performance (weeks 6–10)

Sensitivity and specificity of the **two-layer pipeline**, not of either layer
alone, because that is what a user actually experiences.

Against the Phase 1 controlled set, held out entirely:

| Metric | What it means here |
|---|---|
| Layer A sensitivity at the operating point | How often a carbide-ripened fruit gets sent for a strip test |
| Layer A specificity | How often a clean fruit wastes ₹5 |
| Combined sensitivity | Carbide-ripened fruit correctly flagged after both layers |
| Combined specificity | **The number that matters for defamation risk** |
| Positive predictive value | At realistic prevalence, not at 50% |

**The asymmetry must be stated.** A false negative sends someone home with bad
fruit. A false positive, if it survives corroboration, can damage an honest
trader. They are not equally costly, and the operating point should be chosen
accordingly — which means combined specificity is the binding constraint.

Report PPV at plausible prevalence. A test with 95% specificity looks excellent
and still produces mostly false positives when prevalence is 2%.

---

## Phase 4 — Capture-quality thresholds (weeks 8–10)

The refusal thresholds are currently conservative engineering defaults. Validate
them:

- Sweep exposure, illuminant tint and shadow gradient across a grid.
- For each, measure the reading error against the known concentration.
- Set each threshold where error exceeds the acceptable band — rather than where
  it currently sits, which is a guess.
- Measure the **refusal rate** in real market conditions. A pipeline that refuses
  40% of genuine attempts is correct but unusable, and that trade-off should be
  measured rather than assumed.

---

## Phase 5 — Field deployment (months 3–6)

**Five cultivars, Palakkad and Thrissur.** Field capture with strip confirmation
at the moment of capture, per specification §6.3.

**Inspection hit rate** — the outcome the specification proposes SATVA be judged
on. Run SATVA-directed inspections alongside the existing random-sampling
baseline, and compare confirmed-violation rates. This needs the Kerala Food
Safety Department as a partner, and it is the only way to know whether the
worklist ordering is worth anything.

**Adversarial testing.** Commission an attempt to manufacture a false hotspot:
multiple handsets, coordinated submissions, resubmitted and lightly edited
photographs. The corroboration and deduplication rules are tested in unit tests
against a threat model we invented; they should be tested against someone trying
to break them.

**Retail spoilage.** One quarter, with a control outlet, measuring whether Shelf
actually reduces waste. The degree-hour coefficients get fitted to measured
outcomes at this point, and the model stops being a published-coefficient
estimate.

---

## Phase 6 — Usability and equity (months 4–6)

- **Multilingual validation**: Malayalam, Tamil, Hindi. Not just translation —
  whether the refusal messages remain actionable, since they are instructions
  rather than labels.
- **Low-end device testing**: the ₹8,000 target, measuring actual inference
  latency and whether the ten-second interaction holds.
- **Unseen-cultivar refusal testing**: confirm the model declines rather than
  guessing, on cultivars deliberately excluded from training.

---

## What changes in the code

Almost nothing, which is the point of the current structure.

| Result | Change |
|---|---|
| Real training data | `--manifest` path. Nothing else. |
| Lab-validated calibration | Stop values and `is_lab_validated: true` in `calibration.py`; regenerate golden vectors. |
| Measured repeatability | One field per series. |
| Validated thresholds | One dataclass in `quality.py`. |
| Fitted shelf coefficients | One profile table in `predictor.py`. |
| Real ward boundaries | `wards.py` only. |

The pipeline was built so that validation results are *data*, not a rewrite.

---

## Until then

Every surface says so:

- The app shows "development model" wherever a score appears.
- The API returns `is_lab_validated: false` on every reading.
- The complaint PDF prints the limitation on the page a food safety officer
  reads first.
- The model card leads with a warning rather than a metric.

That is the correct behaviour for a system that is not yet validated, and it
should stay in place until each phase above is actually done.
