# Devpost submission — MonsoonGuard AI

Draft text for the IEEE Global ClimateChain Hackathon 2026 submission.
Numbers here must match `reports/evaluation_results.json`; if the evaluation
is re-run on a different split, update this file in the same pass.

---

## Tagline

Telling you when a heavy-rain forecast is likely to be wrong — and proving we
said so before the rain.

---

## Track

**Climate Data & Environmental Monitoring.**

That track's stated problem is that environmental data is *fragmented and
difficult to verify*, and the first build it names is a **climate data
verification platform**. That is precisely what this is, applied to forecast
data:

| Track asks for | What this project does |
|---|---|
| Climate data verification platforms | Every forecast and its trust label is hashed and committed **before** the rain window opens; anyone can recompute the hash and check the timing |
| Tools for research and insights | A reproducible pipeline that quantifies *when* an extreme-rainfall forecast should not be trusted, with honest skill scores and confidence intervals |

The angle is reliability and accountability rather than data fragmentation:
the problem we attack is that a forecast is easy to issue and hard to hold to
account afterwards. A warning that fails repeatedly teaches people to ignore
the next one, and nothing in the current system records what was claimed, or
how confident it was, before the outcome became knowable.

---

## Inspiration

The 2022 Pakistan floods affected over 30 million people. Forecasts existed.
The harder question is not "will it rain heavily" but "should I act on this
particular forecast" — and a warning that fails repeatedly trains people to
ignore the next one.

We wanted to test something narrow and falsifiable: when the members of an
ensemble forecast disagree with each other, does that disagreement predict
that the forecast is about to be wrong?

---

## What it does

MonsoonGuard AI attaches a **trust label** to extreme-rainfall forecasts over
northern Pakistan, and commits that label to a tamper-evident record
**before** the rain window opens.

Two separate parts:

1. **The science.** Compare archived TIGGE ensemble forecasts against GPM
   IMERG satellite rainfall, then train two models — one that predicts false
   alarms, one that predicts misses — from ensemble spread and monsoon state.
2. **The audit trail.** Hash each forecast record, commit the hash before the
   window opens, attach the observed outcome afterwards, and let anyone
   re-verify it.

The science works entirely without the blockchain. The chain only supports
the audit trail.

---

## What we found

Honest results on held-out data:

| Error model | Brier skill vs climatology | ROC AUC |
|---|---|---|
| **False alarm** | **+0.29** | 0.735 |
| **Miss** | +0.012 | 0.702 |

**Ensemble spread genuinely predicts false alarms.** On the test split the
underlying forecast detects 74% of extreme events but 57% of its warnings
never verify. Our false-alarm model separates those: cells it labels LOW trust
fail 62% of the time, against 25% for MEDIUM — a 2.5× separation, capturing
94% of all false alarms.

**It does not predict misses well.** The model ranks missed events above
chance, but converting that into calibrated probabilities adds essentially
nothing over quoting the base rate. We report that as a negative result rather
than hiding it.

**Calibration mattered far more than expected.** The uncalibrated miss model
scored a Brier skill of **−4.86** — dramatically worse than climatology —
because class weighting buys ranking at the cost of calibrated probabilities.
Isotonic calibration, fitted only on the calibration split, moved it to
+0.012 with a calibration error of 0.0055.

---

## How we built it

- **Observations:** GPM IMERG Final daily, 0.1°, fetched by OPeNDAP
  server-side subsetting — 30 MB and ~30 minutes instead of 20.7 GB and ~23
  hours, verified bitwise-identical to downloading whole granules.
- **Forecasts:** TIGGE NCEP, 30 perturbed members, via the ECMWF Data Store.
- **Alignment:** IMERG averaged onto the 0.5° forecast grid with half weight
  for cells sitting exactly on a box edge, giving exactly 25 effective cells
  per box.
- **Labels:** per-cell 95th-percentile thresholds from training months only;
  two conditional targets that never mix.
- **Audit:** canonical JSON with floats rejected outright (probabilities in
  basis points) so hashes reproduce across platforms.

**147 tests**, including leakage checks that multiply every test-year
observation by 1000 and assert the thresholds come back bit-identical, and
tamper tests that confirm altering any field breaks verification.

---

## Challenges

**The archive nearly sank the project.** ECMWF's TIGGE sits on a shared tape
queue. Requests for a single month of data took anywhere from 0.8 minutes to
15.9 hours; our queue position climbed from 173 to 196 while we sat idle. Over
eight days we received 19 of 40 requested files and **nothing at all** for the
originally planned calibration and test years.

We also lost completed jobs: the standard client couples submit, wait and
download into one blocking call, so a dropped connection forfeited hours of
tape time and stranded the job server-side, blocking everything behind it. We
rewrote it to separate submission from collection, persisting job ids so the
queue drains whether or not anything is listening.

**Mid-project, ECMWF's entire access route was decommissioned.** The portal
our plan targeted was shut down on 27 May 2026 and TIGGE moved to a new data
store with a completely different API. We discovered this only because the
licence page we were told to accept had no accept button — the service behind
it no longer existed. We rewrote the downloader against the new API.

---

## What we learned

That the unglamorous parts decide whether a climate-data project finishes.
Our scientific pipeline was working and tested within a day; the remaining
week was spent fighting data access, and it still cost us the evaluation we
designed.

Also that calibration deserves to be a first-class stage rather than a final
polish — our miss model would have looked like a catastrophic failure without
it, when in fact it ranked perfectly well.

---

## Limitations we want to be upfront about

- The test set is **two months inside a single season**, not an independent
  season. No cross-season generalisation is demonstrated.
- Those two months are **August–September 2022, the flood period** — the most
  extreme data in the archive, deliberately not representative.
- Confidence intervals are wide (false-alarm error rate 0.57, 95% CI
  [0.45, 0.83]) because there are only 9 calendar weeks of test data.
- **The atmospheric features were never obtained**, so the planned ablation
  testing whether CAPE, humidity and MJO state add skill could not be run.
- The trust bands are badly positioned: the HIGH band is empty for false
  alarms, because 0.20/0.50 cutoffs were frozen before we knew the base error
  rate would be 57%. We report them as frozen rather than re-tuning after
  seeing results.
- A testnet contract was written but not deployed; the mock registry
  implements and enforces the same rules.

The blockchain proves a record existed unchanged before its window opened. It
does not prove the forecast was good, and an owner-controlled registry cannot
prove no forecast was quietly omitted.

---

## What's next

1. Obtain 2024–2025 and run the originally designed split — the single change
   that turns a fragment into a real evaluation.
2. Download the atmospheric groups (control run only costs ~1/30th of the
   full ensemble) and run the ablation.
3. Derive trust band cutoffs from the observed error distribution instead of
   fixing them at round numbers.
4. Resolve the arid-cell threshold problem: in 16 of 195 cells the 95th
   percentile is under 1 mm, so "extreme" means little more than "any rain".

---

## How this differs from prior work

Two closely related papers, both checked before citing:

**arXiv:2508.09299** (Umar et al., 2025) puts weather *models* on chain:
federated learning plus reputation-weighted voting to decide whose model
update to trust. We put the *claim* on chain instead — no consensus, no
voting, just an append-only timestamp fixing what one forecaster asserted
before the outcome was knowable.

**arXiv:2605.00420, Foresight Arena** (2026) is methodologically very close
to our audit half: commit-reveal forecasting on Polygon, scored by Brier.
The difference is that their outcomes resolve trustlessly via prediction
markets, while ours resolve against a satellite estimate that carries real
error over mountains — our ground truth is weaker by construction, and we
say so. Their competitive framing also suggests the natural next step:
multiple providers committing trust labels for the same region and window,
scored against each other.

We deliberately do **not** compete with AI weather models like ECMWF's AIFS.
Those try to forecast better. We take a forecast as given and ask when to
distrust it — complementary, and not specific to NCEP.

---

## Built with

Python 3.11 · xarray · cfgrib/ecCodes · scikit-learn · earthaccess ·
ECMWF Data Store API · Streamlit · Solidity · Hardhat

## Try it

```bash
pytest -q                                   # 147 tests
streamlit run src/app/streamlit_app.py      # verification page
```

Open the verification page, pick any record, then use **"Try tampering with
it"** to change a value and watch verification fail.
