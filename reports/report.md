# MonsoonGuard AI: Detecting Unreliable Extreme Rainfall Forecasts in Pakistan

**Technical report — 24 October 2026**

---

## 1. Question

Weather models forecast heavy rain, and they are sometimes wrong. This project
asks a narrow, testable question:

> Can forecast disagreement (ensemble spread) and monsoon indicators identify
> when an extreme rainfall forecast is likely to be unreliable during
> Pakistan's monsoon season?

It is not a new weather model, does not attempt to beat operational forecasts,
and is not an operational warning system. A secondary component commits a
tamper-evident record of each forecast and its trust label before the rain
window opens, so the claim "we said this in advance" can be checked rather
than trusted.

**Headline finding, in two parts.**

*Within the same seasonal regime*, yes. Trained on 2021-2022 and tested on
August-September 2022, the false-alarm model reaches Brier skill **+0.29**
over climatology with ROC AUC **0.735**, and its trust bands separate error
rates by 2.5x. Ensemble spread genuinely carries information about which
warnings will fail.

*Across seasons, largely no.* Tested on a genuinely held-out year
(June 2024, three seasons after training), discrimination survives
(ROC AUC **0.740**) but the method stops being useful: every sample lands in
a single trust band, and the miss model falls to Brier skill **-0.009**,
marginally worse than climatology.

The gap between those two results is the most informative thing in this
report, and Section 4.4 is about why.

---

## 2. Data

| Source | Product | Use |
|---|---|---|
| NASA GPM IMERG Final | `GPM_3IMERGDF` v07, daily, 0.1° | observed rainfall |
| ECMWF TIGGE via ECDS | `tigge-forecasts`, NCEP, 30 perturbed members | forecasts |
| NOAA PSL OMI | daily PC1/PC2/amplitude | MJO state |

**Region.** Northern Pakistan monsoon belt, 30–37 N / 69–75 E, evaluated on
the native 0.5° TIGGE grid: 15 latitudes × 13 longitudes = **195 cells**.

**Season.** June–September (JJAS). Forecasts are 00 UTC runs with lead times
24, 48 and 72 h, giving day 1, day 2 and day 3 windows.

**Observations onto the forecast grid.** IMERG 0.1° cells are averaged into
each 0.5° box with **half weight for cells whose centres lie exactly on a box
edge**, since those are shared between adjacent boxes. This yields exactly
25.0 effective cells per box. Naive inclusive bounds would inflate every box
to 36 cells and double-count edges. The forecast grid is never regridded.

**Accumulation handling.** TIGGE precipitation accumulates from forecast
start, so day 1 = step 24, day 2 = step 48 − step 24, day 3 = step 72 − step
48. Differencing produces small negative values from GRIB packing: 1.01% of
values, all bounded at −0.0625 mm (the 1/16 mm packing step). These are
clipped to zero and counted per file, never silently dropped.

**Independent validation.** The pipeline reproduces both acceptance figures
specified in advance: ensemble area mean range **7.62–18.78 mm** (target
7.6–18.8) and IMERG 0.5° area mean **7.2096 mm** (target ≈7.2) for
2022-08-15. These targets were derived from a sample obtained through the
legacy MARS portal and are reproduced by data fetched through a completely
different API, which is good evidence the ingest is faithful.

---

## 3. Method

### 3.1 Labels

Per cell and window:

- **Event** `y_event = 1` where observed daily rainfall exceeds that cell's
  95th percentile, computed **from training months only**.
- **Signal** `signal = 1` where at least 20% of perturbed members exceed the
  same threshold. A fraction, never a count, so differing ensemble sizes
  remain comparable.
- **Two conditional targets.** Where `signal = 1`, `false_alarm = 1` if no
  event occurred. Where `signal = 0`, `miss = 1` if an event occurred. Each
  sample belongs to exactly one subset; outside its own subset a target is
  undefined rather than zero, so neither model is trained on fabricated
  negatives.

Thresholds are strongly spatial, ranging **0.00 to 30.48 mm** across the 195
cells. This confirms that a single fixed threshold would be wrong: 50 mm
exceeds the 95th percentile of every cell in the domain. It also creates a
problem discussed in Section 6.

### 3.2 Features

All computable at issue time; nothing looks at observations or at any later
date.

- **Ensemble:** mean, standard deviation, maximum, normalised spread,
  quantiles (25/50/75/90), fraction over threshold, fraction over half
  threshold
- **Neighbourhood:** mean and maximum of the ensemble mean over surrounding
  3×3 cells, with edge windows truncated rather than wrapped
- **Context:** lead day, latitude, longitude
- **Monsoon:** OMI PC1, PC2, amplitude, taken from the most recent entry
  **on or before** the issue date

Atmospheric fields (CAPE, CIN, total column water, MSLP, 850 hPa wind and
humidity) are specified in the design and supported by the code, but were
never downloaded — see Section 6.

### 3.3 Models and calibration

Three rungs, same split and metrics throughout: a climatological reference,
the raw ensemble fraction used directly, and logistic regression with
standardised features and balanced class weights.

Calibration is a **separate stage fitted on the calibration split only**,
using isotonic regression. This matters more than it might appear — see
Section 4.2.

---

## 4. Results

**Sections 4.1 to 4.3 report the in-regime evaluation** (train 2021,
calibrate 2022-06/07, test 2022-08/09). **Section 4.4 reports the held-out
year** (train 2021-2022, calibrate 2023-06, test 2024-06) and compares the
two. Both were run once on their own test set; both are kept because the
difference between them is the finding. Raw output for each is in
`reports/evaluation_results_2022split.json` and
`reports/evaluation_results_2024split.json`.

### 4.1 Forecast verification (in-regime test split)

How good is the underlying forecast, independent of our models:

| Signal level | POD | FAR | CSI | Miss rate | Bias |
|---|---|---|---|---|---|
| ≥10% | 0.793 | 0.618 | 0.347 | 0.207 | 2.077 |
| **≥20%** (frozen) | **0.740** | **0.566** | **0.377** | **0.260** | **1.704** |
| ≥30% | 0.694 | 0.531 | 0.389 | 0.306 | 1.480 |
| ≥50% | 0.612 | 0.471 | 0.397 | 0.388 | 1.156 |

At the frozen 20% level the forecast detects 74% of extreme-rainfall events
but **57% of its warnings do not verify**, and it over-forecasts by 70%
(bias 1.70). Raising the signal level trades detection for precision, as
expected. Results are nearly identical across lead days 1–3 (CSI 0.378,
0.373, 0.379), which is itself notable: within this 72-hour range, lead time
barely degrades performance.

### 4.2 The two error models

| Target | Model | Brier | Brier skill | ROC AUC | PR AUC |
|---|---|---|---|---|---|
| **false_alarm** | reference | 0.3107 | 0.000 | 0.500 | 0.566 |
| n=7,154, 56.6% positive | uncalibrated | 0.2088 | +0.328 | 0.736 | 0.780 |
| | **calibrated** | **0.2197** | **+0.293** | **0.735** | **0.769** |
| **miss** | reference | 0.0376 | 0.000 | 0.500 | 0.039 |
| n=27,946, 3.9% positive | uncalibrated | 0.2201 | **−4.862** | 0.705 | 0.094 |
| | **calibrated** | **0.0371** | **+0.012** | **0.702** | **0.090** |

**The false-alarm model works.** A Brier skill score of +0.29 over
climatology, with ROC AUC 0.735 and PR AUC 0.769 against a 56.6% base rate,
means ensemble spread genuinely carries information about which warnings will
fail to verify.

**The miss model discriminates but adds almost no skill.** ROC AUC 0.702 is
clearly above chance, so the features do rank missed events correctly. But the
Brier skill score of +0.012 says that converting that ranking into useful
probabilities adds essentially nothing over simply quoting the base rate. With
1,092 positives in 27,946 test rows, this is the regime the design warned
about.

**Calibration is not cosmetic.** For the miss model the uncalibrated Brier
skill score is **−4.86** — dramatically worse than climatology — and isotonic
calibration moves it to **+0.012**, with expected calibration error dropping
to 0.0055. Class weighting buys discrimination at the direct cost of
calibration, and without the separate calibration stage the model would have
looked catastrophic despite ranking well. This is a concrete demonstration of
why calibration belongs in the design as its own step.

### 4.3 Trust bands — the headline utility result

**False-alarm model:**

| Band | Samples | Share | Error rate | Share of all errors |
|---|---|---|---|---|
| HIGH | 0 | 0.0% | — | 0.0% |
| MEDIUM | 1,053 | 14.7% | **24.8%** | 6.4% |
| LOW | 6,101 | 85.3% | **62.1%** | 93.6% |

**Miss model:**

| Band | Samples | Share | Error rate | Share of all errors |
|---|---|---|---|---|
| HIGH | 27,686 | 99.1% | **3.8%** | 96.7% |
| MEDIUM | 258 | 0.9% | **14.0%** | 3.3% |
| LOW | 2 | 0.0% | 0.0% | 0.0% |

The bands do separate: within the false-alarm subset, forecasts labelled LOW
trust fail 62% of the time against 25% for MEDIUM — a **2.5× separation**. For
the miss subset, MEDIUM-trust cells miss events at 14.0% against 3.8% for HIGH
— a **3.7× separation**.

**But the bands are badly positioned, and this is a genuine negative result.**
The HIGH band is **completely empty** for false alarms, because the band
cutoffs (0.20/0.50) were frozen before we knew the false-alarm base rate would
be 57%. When more than half of all warnings are wrong, no calibrated model
will ever output p_wrong < 0.20, so "HIGH trust" becomes unreachable by
construction. Symmetrically, the miss model puts 99.1% of cells in HIGH and
only 2 in LOW. In both cases the bands are nearly degenerate: they carry far
less information than the underlying continuous `p_wrong`, which is why that
value is stored alongside the label in every record.

The honest reading is that the cutoffs should be derived from the observed
error distribution rather than fixed in advance at round numbers. Changing
them now, after seeing test results, would invalidate the protocol, so they
are reported as frozen and their inadequacy recorded as a finding.

### 4.4 The generalisation test, and why it matters most

Two evaluations were run. The second was only possible once `pf_2024_06`
arrived, and it tests the thing the first could not.

| | **In-regime** | **Held-out year** |
|---|---|---|
| train | 2021 JJAS (122 d) | 2021+2022 JJAS (244 d) |
| calibrate | 2022-06/07 | 2023-06 |
| **test** | **2022-08/09** (35,685) | **2024-06** (17,550) |
| false alarm: Brier skill | **+0.293** | **+0.544** |
| false alarm: ROC AUC | 0.735 | 0.740 |
| miss: Brier skill | +0.012 | **-0.009** |
| miss: ROC AUC | 0.702 | 0.645 |
| trust bands | MEDIUM 24.8% vs LOW 62.1% | **all 2,619 in one band** |

**The +0.544 is not an improvement.** It is easier to beat climatology when
the base rate is extreme: 96.5% of signal=1 samples in June 2024 were false
alarms, so the reference Brier is already tiny (0.0746) and a modest
absolute gain becomes a large relative one. Read it alongside the band table,
which is the honest summary: **the trust labels carry no information at all
on the held-out year**, because every sample is LOW.

**What went wrong is structural, not a coding error.** June is a different
rainfall regime from the season the thresholds were built on:

| Period | Event rate vs train p95 | Mean rainfall |
|---|---|---|
| Training JJAS 2021-22 | 5.33% | — |
| June only, training years | 3.14% | 1.42 mm |
| July-September, training years | 6.04% | 2.67 mm |
| **Test: June 2024** | **1.25%** | — |

Thresholds are the per-cell 95th percentile of the *whole* JJAS training
period, which is dominated by the wet July-September months. Applied to a dry
June, those thresholds are rarely exceeded by observations but still often
exceeded by at least 20% of ensemble members. The result is a frequency bias
of **11.3** — the forecast signals roughly eleven times more events than
occur — and a false alarm ratio of **0.965**, against 1.70 and 0.566
respectively in the in-regime test.

So the honest conclusion is narrower than the in-regime numbers suggest:

> Ensemble disagreement predicts forecast error **within the seasonal regime
> the thresholds were calibrated on**. It does not, as implemented here,
> transfer to monsoon onset — and the cause appears to be the
> season-aggregated threshold definition rather than the error model itself,
> since discrimination (ROC AUC 0.740) barely moves while calibration and
> band utility collapse.

That is a testable claim. A month-of-year or onset-aware threshold is the
obvious next experiment, and Section 7 lists it.

**Caveats on this conclusion.** The held-out test is a *single* month, so
"does not transfer" rests on one June. It is also the driest month in the
record relative to its threshold, which may make it an unusually harsh test.
A fair assessment needs full seasons for 2023 and 2024, which the archive did
not deliver in time.

---

### 4.5 Uncertainty

Test error rates with 95% confidence intervals from a **block bootstrap by
calendar week** (resampling whole weeks, not rows, because neighbouring cells
and consecutive days are strongly correlated):

| Subset | Error rate | 95% CI |
|---|---|---|
| false_alarm | 0.566 | [0.454, 0.834] |
| miss | 0.039 | [0.024, 0.056] |

The false-alarm interval is very wide — it spans 0.45 to 0.83 — because the
test split contains only **9 calendar weeks**. Any comparison of models or
configurations within that range is not statistically meaningful at this
sample size.

---

## 5. Audit layer

Records are hashed off-chain and verified against a registry. The record is
canonical JSON (sorted keys, no insignificant whitespace, UTF-8), and
**floating point numbers are rejected outright** — probabilities are integers
in basis points, rainfall in hundredths of a millimetre — because float text
representation is not stable across platforms and would break hash
reproducibility.

The registry enforces the timing rules that give the mechanism its meaning:
commits are rejected at or after the window opens, outcomes are rejected
before the window closes, duplicate ids and empty hashes are rejected, and
**the commit timestamp always comes from the registry clock, never the
caller**. Verification reports one of PENDING, COMMITTED, OUTCOME_RECORDED,
VERIFIED, MISMATCH or MISSED_COMMIT, and lists which individual checks passed
rather than only a verdict.

Only day 2 and day 3 forecasts are committed. A day 1 window opens at the
same 00 UTC the forecast is issued, so by the time the run is available the
window has already begun and a pre-commitment is impossible. The registry
refuses such commits rather than accepting a meaningless one.

**What this proves:** a specific record existed, unchanged, before its rain
window opened. **What it does not prove:** that the forecast was good, that
the model was sensible, or that every forecast made was submitted. An
owner-controlled registry cannot demonstrate the absence of omissions; a
public schedule and visible gaps reduce that risk without eliminating it.

The contract is deployed and exercised on a **local development chain**
(chain 31337): a real transaction, a real commit whose timestamp comes from
the chain rather than the caller, and tamper detection confirmed against it.
A Python client (`src/audit/chain_client.py`) reads and writes the deployed
contract over JSON-RPC.

**No public testnet deployment was reached**, so the contract's own address
is not something an outsider can call - a local chain is reproducible but
not externally verifiable, and the app labels it as such rather than
implying otherwise. Every faucet attempted required either an existing
Ethereum mainnet balance or a captcha.

The archived 2022 records remain on the mock registry, and must: the
contract correctly refuses any commit once a window has opened, so
historical forecasts cannot be committed live. That is the rule working, not
a gap. A timestamp written in 2026 could not prove a forecast for August
2022 came before the rain, and a contract that accepted one would be
inviting exactly the backdating it exists to prevent.

### 5.1 What *can* be proved publicly, and is

There is a weaker claim that holds for a replay: not "we said this first",
but "this exact set of results has not been edited since publication". That
one is worth proving, and costs nothing.

All 24 records are folded into a single Merkle root - leaves are the SHA-256
digests of the canonical record bytes in sorted id order, internal nodes are
keccak256 over the pair sorted as integers, so a proof carries no position
bits. The differing hash functions at leaf and node level are deliberate:
no leaf can be mistaken for an internal node, which is the second-preimage
defence. An odd node at any level is carried up unchanged rather than paired
with itself, closing the duplicate-leaf forgery.

| | |
|---|---|
| Merkle root | `0x0f2956487384a095fb211181fe3f0810940d4888a0923579f010fd047cea1561` |
| Bitcoin block | 972977, mined 2026-10-24 21:28 UTC |
| Leaves | 24 (12 forecasts, 12 outcomes) |
| Proof | `records/archive_manifest.json.ots` |

The root is published on chain by `ForecastRegistry.publishArchive`, and
anchored in Bitcoin itself through OpenTimestamps - free, because public
calendar servers batch thousands of unrelated hashes into one transaction
and share its fee. The proof is self-contained after confirmation: it
verifies against the Bitcoin blockchain alone and never needs those servers
again, which matters because nobody has promised to keep them running.

The tree is built in Python (`src/audit/merkle.py`) and verified by the
Solidity contract. Two implementations of one structure in two languages is
exactly where a silent mismatch hides, so `publish_archive.py --check`
pushes every leaf through the real contract before anything is published;
all 24 verify and a tampered leaf is rejected.

The honest caveat, stated because the alternative reads as overclaiming: the
Bitcoin block contains an *aggregated* root covering many unrelated
timestamps, not this project's hash in readable form. The `.ots` file
supplies the hash path linking the two. The defensible claim is "anchored in
block 972977, verifiable with the included proof" - not "our data is written
into block 972977".

---

## 6. Limitations

These are substantial and should be read before any result above.

**The test set is one season fragment, not an independent season.** The
design called for training on 2021–2023, calibrating on 2024 and testing on
2025. ECMWF's archive delivered 20 of 40 requested files over nine days and
**nothing at all for 2024 or 2025**; individual requests ranged from 0.8
minutes to 15.9 hours against a shared global tape queue. With a fixed
deadline the split was re-frozen — before any test result was examined — onto
available data: train 2021 JJAS, calibrate 2022-06/07, test 2022-08/09.

Consequences, in order of severity:

1. **No cross-season generalisation is demonstrated at all.** Train and test
   are adjacent periods in consecutive seasons. Nothing here shows the model
   transfers to a different year.
2. **The test period is the 2022 flood** — the most extreme event in the
   archive. Results are dominated by one exceptional episode and are
   explicitly *not* representative of a typical monsoon.
3. **Training is a single season**, so anything peculiar to 2021 is baked in.
4. **Nine calendar weeks** of test data make the confidence intervals wide
   enough that model comparisons are not meaningful.
5. The planned leave-2022-out case study is no longer independent, because
   2022 is now the calibration and test data.

**Atmospheric features were never obtained.** CAPE, CIN, total column water,
MSLP and 850 hPa fields are in the design and supported by the code, but the
download never reached them. The planned ablation (baseline vs. +MJO vs.
+spread and atmospheric) could therefore not be run, so **this report cannot
say whether atmospheric context adds skill** — one of the questions the
project set out to answer.

**Arid cells make "extreme" nearly meaningless in places.** Per-cell 95th
percentiles range down to 0.00 mm in the dry northwest. In 16 of 195 cells the
threshold is below 1 mm, so "exceeds the 95th percentile" means little more
than "any measurable rain fell". These are also the mountain and snow regions
where IMERG retrieval is least reliable, so the observed "truth" is weakest
exactly where the label is most questionable. No threshold floor was applied,
because changing the event definition after seeing results would invalidate
the protocol; it is recorded as an open issue.

**The trust bands are poorly positioned** (Section 4.3): one band is empty and
another holds 99% of samples.

**Other standing limits:** IMERG is a satellite estimate, not ground truth,
with largest errors over mountains and snow. Cells and days are strongly
correlated, so the ~159,000 rows are nowhere near 159,000 independent
observations. The signal rule changes what counts as a miss or false alarm;
sensitivity across 10/20/30/50% is reported in Section 4.1.

---

## 6a. Related work

Each reference below was checked before citing (CLAUDE.md section 12).

**Decentralized Weather Forecasting via Distributed Machine Learning and
Blockchain-Based Model Validation** (Umar et al., arXiv:2508.09299, 2025)
is the closest work on combining weather forecasting with a chain. They use
federated learning so participants train without sharing data, and an
Ethereum contract with reputation-weighted voting to validate submitted model
updates, with IPFS for off-chain storage.

The contrast is instructive. They put the **model** on chain, to decide whose
contribution to trust in a multi-party setting. We put the **claim** on chain,
to fix what a single forecaster asserted before the outcome was knowable. Our
registry deliberately holds no consensus mechanism and no voting: it is an
append-only timestamp, and we are explicit that it cannot prove a forecast was
good or that no forecast was omitted.

**Foresight Arena: An On-Chain Benchmark for Evaluating AI Forecasting
Agents** (Nechepurenko and Shuvalov, arXiv:2605.00420, 2026) is
methodologically very close to the audit half of this project. Agents submit
probabilistic forecasts through a **commit-reveal protocol** enforced by
Solidity contracts on Polygon, and are scored with the **Brier score** —
the same proper scoring rule we use, chosen for the same reason, that it
rewards honest probability reporting rather than confident guessing.

Two differences matter. Their outcomes resolve trustlessly through prediction
markets; ours resolve against a satellite product (IMERG) that is itself an
estimate with real error over mountains and snow, so our "ground truth" is
weaker than theirs by construction. And they benchmark agents competitively,
while we evaluate one pipeline against climatology. Their framing suggests a
natural extension: a permissionless version of this where multiple forecast
providers commit trust labels for the same region and window, and are scored
against each other on Brier skill.

**Ensemble calibration and verification.** The metrics here are the standard
ones from that literature — Brier score and Brier skill score, reliability
diagrams, expected calibration error, and the contingency-table measures
(POD, FAR, CSI, frequency bias). Isotonic regression as a post-hoc calibration
step on a class-weighted model is likewise standard practice; Section 4.2
shows why it was not optional here.

**AI weather models (ECMWF AIFS and related work)** are deliberately *not* a
comparison point. Those systems aim to produce better forecasts. This project
takes an existing forecast as given and asks when to distrust it, which is a
complementary question — a learned forecast model would also benefit from an
error model of this kind, and the method here is not specific to NCEP.

---

## 7. What would change the conclusions

In rough order of value:

1. **Month-conditioned or onset-aware thresholds.** Section 4.4 shows the
   season-aggregated 95th percentile is what breaks on a June test set. A
   threshold computed per calendar month, or relative to onset, is a direct
   test of that diagnosis and costs no new data.
2. **Obtain full 2023 and 2024 seasons** and re-run. One June is too thin to
   conclude the method does not generalise; full seasons would settle it.
3. **Download the atmospheric groups** (control forecast only is sufficient
   and costs roughly 1/30th of the full ensemble) and run the planned
   ablation.
3. **Derive trust band cutoffs from the error distribution** on a calibration
   set rather than fixing them in advance, and freeze them before testing.
4. **Resolve the arid-cell threshold question**, either with a floor or by
   masking, decided and recorded before evaluation.
5. Add gradient-boosted trees and SHAP, which the ladder specifies but which
   were not reached.

---

## 8. Reproducing this

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env          # add Earthdata and ECDS credentials
python scripts/download_imerg.py --all        # ~30 MB via OPeNDAP, ~30 min
python scripts/download_mjo.py
python scripts/download_tigge.py --all --group precip   # slow: tape archive
python scripts/build_labels.py
python scripts/run_evaluation.py
python scripts/build_records.py
pytest -q                     # 209 Python tests
npx hardhat test              # 33 contract tests
```

Every decision taken, including the ones that departed from the original
design and why, is in `docs/decisions.md` with dates. Data manifests with
SHA-256 checksums are in `data/manifests/`.

---

## 9. Honest summary

The pipeline is sound and independently validated against pre-specified
acceptance figures, and the audit layer does what it claims.

The scientific answer is conditional. **Within the seasonal regime the
thresholds were built on**, ensemble disagreement predicts forecast error
usefully: Brier skill +0.29, ROC AUC 0.735, and trust bands that separate
error rates 2.5-fold. **On a held-out year**, discrimination survives
(ROC AUC 0.740) but utility does not: every test sample falls in one trust
band and the miss model drops marginally below climatology.

The most useful thing we learned is *why*. The event threshold is a per-cell
percentile of the whole JJAS training period, dominated by the wet
July-September months. Applied to a dry June it produces a frequency bias of
11.3 and a false alarm ratio of 0.965. The error model is not obviously at
fault - its ranking ability barely changes - but the event definition does
not travel across seasonal regimes, and nothing downstream can repair that.

So: a method that works where it was calibrated, a clear and testable
explanation for where it stops working, and an obvious next experiment
(onset-aware or month-conditioned thresholds). What is missing is the data to
settle it - full 2023 and 2024 seasons, and the atmospheric features for the
ablation - none of which the archive delivered in time.
