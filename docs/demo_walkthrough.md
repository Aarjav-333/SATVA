# Demo walkthrough

Target: **three minutes**, with the live scan kept under thirty seconds as the
pitch deck recommends.

The core path is the one that must not fail: **photograph → score → strip test →
number → hotspot → complaint.** Everything else is optional and can be dropped
without breaking the narrative.

---

## Before you start

```bash
docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed_demo --reset
```

Then confirm, in order:

```bash
curl -s localhost:8000/api/v1/health              # {"status":"ok", ... "database":"up"}
curl -s localhost:8000/api/v1/hotspots | head -c 200
open http://localhost:5173                        # dashboards load
```

Physical props:

- **Print the reference card.** `docs/reference_card/satva_reference_card_v1.pdf`,
  at 100% scale, matte paper. Two per sheet — keep the spare clean.
- **A real piece of fruit.** The pitch deck specifically recommends handing a
  judge a fruit and scanning it on stage.
- **A reacted strip**, or a printed swatch on the card if you have no reagent.

Have the app already open on the Scan screen, phone unlocked, screen timeout
disabled.

---

## The run

### 1 · Frame the problem (20 s)

> "One in five food samples tested in India fails safety standards. In FY 2025–26
> that was 40,023 non-conforming samples out of 2,23,808 analysed — which works
> out to roughly one laboratory test per 6,300 citizens per year.
>
> The person holding the fruit has the best opportunity to check it, and no
> instrument to do it with."

### 2 · Scan the fruit (30 s — the moment that matters)

Hand a judge the fruit. Photograph it.

> "That ran entirely on the phone. No network, no data cost, under 200
> milliseconds."

Point at the score and the saliency overlay.

> "It's flagging the regions it responded to — uniform skin colour, stem end
> still green. Those are the visual consequences of forced ripening."

**Then say the line that matters most:**

> "But this is not evidence, and SATVA will not let it become evidence. A
> photograph cannot detect a chemical. All this score does is decide whether a
> five-rupee test is worth doing."

### 3 · Show the refusal — *do not skip this* (20 s)

Deliberately photograph the strip badly: cover half the card with your hand, or
stand in a shadow.

> "It refused. Not a low-confidence number — a refusal, with a reason and an
> instruction."

> "That's the whole design. A wrong reading attached to a complaint could ruin an
> honest trader. So when the photo isn't good enough to measure, SATVA says so."

This is the single most persuasive thing in the demo. A system that declines is
far more credible than one that always answers.

### 4 · Take the real reading (25 s)

Retake it properly.

> "5.27 milligrams per litre equivalent, and the interval — 4.51 to 6.02. The
> number and its uncertainty carry the same visual weight, because a point
> estimate on its own invites over-reading."

> "That correction came from twelve printed patches on the card, fitted in
> linear RGB, converted to CIE L\*a\*b\*, and matched by CIEDE2000 against a
> calibrated series. It's a measurement, not a guess."

If asked whether it is accurate: **"The maths is verified against published
reference data. The calibration series is provisional and flagged as such — it
hasn't been checked against a NABL lab yet, and we don't claim otherwise."**

### 5 · Contribute to Watch (20 s)

Tap *Contribute anonymously*.

Switch to the dashboard, `/watch`.

> "Ward level. Never a shop, never a name. Your identity was separated from the
> reading before it ever reached the clustering pipeline — that's enforced by
> two database schemas and a role that has no permission to read the other one."

### 6 · Show the suppressed cluster — *the differentiator* (25 s)

Sign in as `officer@satva.demo` and open the worklist. Point at **Chittur**.

> "Nine readings. A hundred percent above the action threshold. The most
> alarming-looking pattern in the whole dataset — and it is **not** on the
> public map."

> "All nine came from one device. That's what a competitor trying to damage a
> rival looks like, and DBSCAN plus an independent-device requirement catches it.
> The officer can see it. The public cannot."

Then open a published cluster.

> "This one had nine different devices over three weeks. That's a real signal,
> and only here — behind an authenticated officer login, with every view written
> to an audit log — does a vendor name appear."

### 7 · Generate the complaint (20 s)

Back on the phone, tap *Prepare an FSSAI complaint*. Open the PDF.

> "Reading, confidence interval, GPS, timestamp, photographs with SHA-256
> digests, and the limitation stated in plain words on the first page."

> "SATVA did **not** submit this. There's no public API into Food Safety Connect,
> and pretending there was would leave someone believing they'd filed a complaint
> when they hadn't. The document tells them exactly how to file it themselves."

### 8 · Shelf, if time allows (15 s)

Open `/retail`, expand a row.

> "Same photograph, second head on the same model. Ripeness plus degree-hours
> from a crate tag gives remaining shelf life — and every term behind the
> recommendation is shown, because a shop manager asked to discount stock
> deserves to know why."

### 9 · Close (10 s)

> "Five modules in the architecture. Scan and Watch are what we promised to
> build in 36 hours, and they work end to end.
>
> Nobody should have to guess whether their food is safe."

---

## Questions you will be asked

**"How accurate is your model?"**
> "The model in this build is a development model trained on generated images —
> it tells you nothing about real fruit, and the app says so on every screen.
> That's deliberate: we built the whole pipeline so real weights drop in
> unchanged. But it wouldn't matter much either way, because the model is only a
> triage filter. Nothing it says can be published or escalated."

**"So the CNN detects calcium carbide?"**
> "No, and it can't. A camera measures reflected light. It screens for the
> morphological consequences — uniform colour with a green calyx, speckled
> surface burns, sheen, texture lagging colour. Then chemistry does the actual
> detection."

**"What stops someone attacking a competitor?"**
> "Four things, and you have to beat all of them. Perceptual hashing and
> embedding similarity stop the same photo being resubmitted. Independent-device
> corroboration stops one actor submitting many different photos. Temporal
> spread stops a burst. And the public map only ever shows wards. Chittur in this
> dataset is exactly that attack, and it's suppressed."

**"Isn't this defamatory?"**
> "That's the risk the whole design is organised around. A photograph is never
> evidence. Publication needs a chemical reading plus corroboration from
> multiple independent devices. Public surfaces show wards, never vendors.
> Vendor detail requires an authenticated officer and is audited. And we route
> escalation through FSSAI rather than around it."

**"Why not blockchain for Trace?"**
> "The requirement is tamper-evidence, not decentralised consensus. A hash chain
> in PostgreSQL gives that: each record hashes its payload with the previous
> hash, so altering history breaks every link after it. A daily Merkle root is
> published as an external anchor, which means even we can't rewrite it
> silently. It's simpler, cheaper, queryable, and it's what the problem actually
> needs."

**"What's not finished?"**
> "Laboratory validation of the calibration series, real field training data,
> on-device latency measurement, and the marketplace transaction flow.
> `docs/known_limitations.md` lists all of it. We'd rather show you that list
> than have you find it."

---

## If something breaks

| Problem | Do this |
|---|---|
| Camera fails on stage | Use *Choose from gallery* with a pre-taken photo |
| Strip read keeps refusing | Say so — *"this is the refusal path, and it's the point"* — then use a pre-captured good image |
| Dashboard will not load | `docker compose restart api dashboard`; the API docs at `/docs` make a decent fallback |
| Map tiles fail (venue wifi) | The tables below the map carry the same data |
| Everything is down | `docs/samples/sample_fssai_complaint_package.pdf` and this document carry the narrative |

**Rehearse the fallback path at least once.** A demo that survives a failure is
more convincing than one that never meets one.
