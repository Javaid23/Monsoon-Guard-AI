# MonsoonGuard AI

Full title: MonsoonGuard AI: Detecting Unreliable Extreme Rainfall Forecasts in Pakistan

Project for the IEEE Global ClimateChain Hackathon 2026 (submissions close 25 October 2026, 11:59 PM UTC).
Plan to submit one day early. Count the remaining days from the deadline backwards and cut scope using Section 14.

This file is the single source of truth for Claude Code. Read all of it before writing code. If this file and a request ever conflict, say so and ask.

---

## 1. What the project is

Weather models forecast heavy rain, but they are sometimes wrong. This project asks:

> Can forecast disagreement (ensemble spread, differences between members) and atmospheric and monsoon indicators help identify when extreme rainfall forecasts are likely to be unreliable during Pakistan's monsoon season?

The system has two separate parts:

1. Scientific part: compare archived ensemble forecasts with satellite observed rainfall, then train models that estimate when a forecast decision will be wrong (a miss or a false alarm).
2. Audit part: commit a tamper evident record of each forecast and its trust label to a blockchain before the rain window begins, attach the observed outcome afterwards, and let anyone verify the record.

The science must work completely without the blockchain. The blockchain only supports the audit trail.

### What this project does NOT claim
- It is not a new weather model and does not beat operational forecasts.
- It is not an operational early warning system.
- The blockchain proves when a record was committed. It does not prove the data or model was correct.
- Results may be negative (the indicators may not help). Report them honestly either way.

---

## 2. Working agreements for Claude Code

Follow these in every session.

1. Plan first. Before editing, state the plan in a few lines. Make small, testable changes.
2. Never fabricate data, results, metrics, citations, or file contents. If something is missing, say so.
3. Never overwrite or edit anything in `data/raw/`. It is append only. Derived data goes in `data/interim/` or `data/processed/`, and must be rebuildable from raw by a script.
4. Ask before any download larger than 2 GB or any action that spends time or money (for example deploying a contract).
5. Every random process uses a fixed seed from `configs/project.yaml`.
6. Never use test period data for any choice (features, thresholds, model selection, calibration). See Section 8.
7. Temporal correctness is a hard rule: a feature may only use information available at the forecast issue time.
8. No secrets in the repo. Keys and passwords live in `.env` (gitignored) or the credential files named in Section 5.
9. Keep scientific code independent of blockchain code. The `audit` package may import from `core`, never the reverse.
10. Write a short test for every function that touches time, units, or labels.
11. Log every pipeline run (what ran, input versions, output paths, row counts) to `logs/`.
12. Report gaps and failures visibly. Do not silently skip missing days.
13. Keep the writing style plain and concise in documentation and reports. No marketing language.

---

## 3. Fixed design decisions and open decisions

### Already settled
- Region: northern Pakistan monsoon belt, bounding box 30 N to 37 N, 69 E to 75 E (confirmed working in the sample files).
- Season: June to September (JJAS).
- Primary forecast source: TIGGE, NCEP (originating centre code kwbc), perturbed members (30 in the sample file) plus control forecast if available.
- Observations: NASA GPM IMERG Final Run, daily, version 07 (`precipitation`, mm/day, 0.1 degree).
- Evaluation grid: the 0.5 degree TIGGE grid, 15 latitudes (37 to 30 N) by 13 longitudes (69 to 75 E) = 195 cells.
- Issue time: 00 UTC runs only.
- Lead windows: day 1 = steps 0 to 24 h, day 2 = steps 24 to 48 h, day 3 = steps 48 to 72 h.
- Error design: two conditional error models (false alarm model and miss model), see Section 7.
- Historical evaluation and the live ledger are kept strictly separate.

### Defaults to confirm before looking at test results (freeze in `configs/project.yaml`)
| Decision | Default | Notes |
|---|---|---|
| Seasons | 2021 to 2025 | Check member counts per year first. Add earlier years only if the ensemble size is the same. |
| Extreme event | Observed rainfall at a cell exceeds that cell's 95th percentile of JJAS daily rainfall, computed on the training period only | Percentile based, not a fixed mm threshold. A fixed 50 mm is wrong for this box. |
| Forecast signal rule | Signal = 1 if at least 20 percent of perturbed members exceed the same absolute threshold at that cell and window | Sensitivity runs at 10, 30, 50 percent. Also run a quantile mapped variant. |
| Trust bands | HIGH if p_wrong < 0.20, MEDIUM if 0.20 to 0.50, LOW if p_wrong >= 0.50 | Cutoffs fixed before test. |
| Split | Train 2021 to 2023, calibrate 2024, test 2025 | Plus a separate leave 2022 out case study (Section 9). Adjust if data coverage differs. |
| Sample unit | (issue date, lead day, cell) | About 610 days x 3 leads x 195 cells. Samples are strongly correlated in space and time. |

Any change to these must be written in `docs/decisions.md` with the date and reason, and made before test results are viewed.

---

## 4. Repository layout

```
monsoonguard/
  CLAUDE.md
  README.md
  .env.example
  .gitignore
  requirements.txt
  pyproject.toml
  configs/
    project.yaml            # all frozen decisions, paths, seeds
  data/
    raw/
      tigge_ncep/           # GRIB files as downloaded
      imerg_final/          # cropped NetCDF per day or per month
      mjo/                  # RMM / OMI text files
    interim/                # aligned, regridded arrays
    processed/              # feature tables, labels (parquet)
    manifests/              # dataset manifests (checksums, versions)
  src/
    core/                   # science code, no blockchain imports
      config.py
      io_tigge.py
      io_imerg.py
      io_mjo.py
      align.py              # time windows, regridding, matching
      labels.py             # events, signals, miss/false alarm targets
      features.py
      models.py
      calibrate.py
      evaluate.py
      plots.py
    audit/
      record.py             # canonical record build and hash
      registry_client.py    # chain client plus local mock registry
      verify.py
      schedule.py           # daily ledger job
    app/
      streamlit_app.py      # verification page and results
  contracts/
    ForecastRegistry.sol
    test/
    scripts/
  notebooks/                # exploration only, never the source of results
  reports/
    figures/
    report.md
  docs/
    decisions.md
    data_contract.md
    evaluation_protocol.md
  tests/
  logs/
  records/                  # public forecast and outcome JSON records
```

Environment: Python 3.11, a virtual environment in `.venv`. Core packages: numpy, pandas, xarray, netCDF4, h5netcdf, cfgrib, eccodes, scikit-learn, xgboost, lightgbm, matplotlib, shap, pyyaml, earthaccess, streamlit, web3, pytest. Add `ecmwf-api-client` if the TIGGE scripted route is used. Pin versions in `requirements.txt` once the environment works.

Note: `cfgrib` needs the ecCodes library. `pip install eccodes` provides it on most systems. On Windows, if GRIB reading fails, try conda (`conda install -c conda-forge eccodes cfgrib`).

---

## 5. Data sources and download instructions

The accounts already exist. Do not store credentials in code.

### 5.1 IMERG Final daily (observations)
- Product: GPM_3IMERGDF_07, Final Run, daily, 0.1 degree.
- Dataset page: https://disc.gsfc.nasa.gov/datasets/GPM_3IMERGDF_07/summary
- Auth: NASA Earthdata login (https://urs.earthdata.nasa.gov). Use the `earthaccess` library, which reads credentials from environment variables `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD` or a `~/.netrc` file.
- File name pattern seen in the sample: `3B-DAY_MS_MRG_3IMERG_YYYYMMDD-S000000-E235959_V07B.nc4`. The variable is `precipitation`, dimensions (time, lon, lat), units mm/day (daily mean rate, so equal to the daily total in mm).
- Dates: 1 June to 30 September for each of 2021 to 2025 (about 610 files).
- Important: each daily file is global (about 34 MB). Bulk download gives global files, so crop and delete in a loop:
  1. Download one month of granules to a temporary folder.
  2. For each file, subset to lat 30 to 37 and lon 69 to 75 (use a slightly larger crop, 29.75 to 37.25 and 68.75 to 75.25, so 0.5 degree cell averaging has full coverage).
  3. Write the cropped result to `data/raw/imerg_final/`.
  4. Delete the global file.
  5. Record the product version, file name, and checksum in the manifest.
- If the Harmony subsetting service is available through `earthaccess` or `harmony-py`, it may return cropped files directly. This has not been verified here, so test it on one day first.
- For the live ledger only: IMERG Late or Early daily products from the same site. Exact product names must be checked on the GES DISC site.

Starting template (adjust after testing on one day):

```python
import earthaccess
earthaccess.login(strategy="environment")   # or "netrc"
results = earthaccess.search_data(
    short_name="GPM_3IMERGDF",
    version="07",
    temporal=("2022-06-01", "2022-06-30"),
    bounding_box=(68.75, 29.75, 75.25, 37.25),  # west, south, east, north
)
files = earthaccess.download(results, "tmp_imerg")
```

### 5.2 TIGGE forecasts (NCEP)
- Portal: https://apps.ecmwf.int/datasets/data/tigge/
- Request settings (match the working sample file):
  - Origin: NCEP (U.S.A.), forecast type: perturbed forecast (all members), and a second request for control forecast
  - Level type: single level
  - Variable: Total precipitation (accumulated)
  - Time: 00:00
  - Leadtime hours: 24, 48, 72
  - Area: North 37, West 69, South 30, East 75
  - Format: GRIB
  - Dates: JJAS 2021 to 2025, one month or one year per request
- Extra features from the same run (single level and pressure level, same area): total column water, convective available potential energy, convective inhibition, mean sea level pressure, 10 m wind, and at 850 hPa the u and v wind and specific humidity. Do these as separate requests after the precipitation requests are submitted.
- Scripted route: the portal can show a request that can be run with the ECMWF web API client. This has not been verified here, so use the portal's own generated request as the template. The structure usually looks like this (verify every key against the portal):

```python
from ecmwfapi import ECMWFDataServer
server = ECMWFDataServer()   # needs ~/.ecmwfapirc with your own key
server.retrieve({
    "class": "ti", "dataset": "tigge", "expver": "prod",
    "origin": "kwbc", "levtype": "sfc", "param": "228228",   # total precipitation
    "type": "pf", "number": "1/to/30",                      # use "cf" for control
    "time": "00:00:00", "step": "24/48/72",
    "date": "2022-08-01/to/2022-08-31",
    "grid": "0.5/0.5", "area": "37/69/30/75",
    "target": "data/raw/tigge_ncep/tp_pf_2022_08.grib",
})
```

- Facts from the sample file (read with ecCodes): centre kwbc, short name `tp`, units kg m**-2 (equal to mm), step type accum, step range 0 to 24, 30 perturbed members, grid 15 x 13 at 0.5 degrees, initialization 2022-08-15 00 UTC. The WMO code in the file reads as a rate, but the units and values are accumulations. Always check units and step type when loading a new file.
- Precipitation is accumulated from the start of the forecast. Daily window totals: day 1 = value at step 24, day 2 = step 48 minus step 24, day 3 = step 72 minus step 48. Clip tiny negative differences caused by rounding to zero and log how many occurred.
- Check the member count per year. If it changes (for example 20 members in earlier years), record it in the manifest and do not mix years with different ensemble sizes without handling it (use fraction of members, not counts).
- Optional second centre: ECMWF (50 members) with identical settings. Only if time allows. It enables an inter centre disagreement feature.

### 5.3 MJO indices
- RMM text file (Bureau of Meteorology): http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt . Columns are date, RMM1, RMM2, phase, amplitude. Check the last date covers 2025.
- OMI from NOAA PSL: https://psl.noaa.gov/mjo/mjoindex . Use the current version (based on CPC blended OLR), not the original one that is no longer updated.
- Use values dated on or before the issue date only (temporal correctness).
- Handle missing values (RMM uses large sentinel values when missing). Convert to NaN and log the count.

### 5.4 Optional
- ERA5 (Copernicus CDS, https://cds.climate.copernicus.eu) only to help define climatology if needed. Not required, because TIGGE already provides the forecast's own atmospheric fields.

### 5.5 Dataset manifest
For every raw file store: source, product and version, request parameters, download date, file name, file size, SHA 256 checksum, and (for TIGGE) member count and step ranges. Save as `data/manifests/*.json`. The hashed forecast records point to the manifest version.

---

## 6. Data processing

### 6.1 Time conventions
- Everything in UTC. Issue time is 00 UTC on date D.
- Forecast day 1 covers D 00 UTC to D+1 00 UTC and is verified by the IMERG daily file for date D.
- Forecast day 2 is verified by the IMERG file for D+1, and day 3 by D+2.
- Keep explicit columns: `issue_date`, `lead_day`, `window_start_utc`, `window_end_utc`, `valid_date`.

### 6.2 Aligning forecasts and observations
- Observation on forecast grid: average IMERG 0.1 degree cells that fall within a 0.5 degree box centered on each TIGGE grid point (plus or minus 0.25 degrees). The sample test did this and gave a 15 x 13 grid with no missing cells.
- Never regrid the forecast to the observation grid.
- Quality control report per run: missing days, missing members, NaN counts, units, value ranges, and number of negative differences clipped.

### 6.3 Labels
Computed per cell:
1. Observed event `y_event` = 1 if the 0.5 degree IMERG daily rainfall exceeds that cell's threshold. The threshold is the 95th percentile of JJAS daily IMERG values over the training years only, per cell.
2. Forecast signal `signal` = 1 if the fraction of perturbed members exceeding the same threshold is at least the signal level (default 0.20).
3. Error targets:
   - If `signal` = 1: `false_alarm` = 1 if `y_event` = 0, else 0.
   - If `signal` = 0: `miss` = 1 if `y_event` = 1, else 0.
4. Never compute thresholds using validation or test years.

---

## 7. Modeling

### 7.1 The two conditional error models
- Model A (false alarm model): trained only on samples where `signal` = 1. Target `false_alarm`.
- Model B (miss model): trained only on samples where `signal` = 0. Target `miss`.
- Both are converted to one common scale: `p_wrong`, the calibrated probability that the forecast decision is wrong. Calibrate each model separately on its own subset and check reliability diagrams per subset.
- Trust label from `p_wrong` using the frozen bands. Store both the numeric `p_wrong` and the label.
- The miss subset will have few positives. Report the counts. If positives are too few for a stable model, say so and report the model as exploratory.

### 7.2 Features (only information available at issue time)
- Ensemble: mean, standard deviation, maximum, selected quantiles (for example 25, 50, 75, 90), fraction of members above the threshold, fraction above half the threshold.
- Spread measures normalized by the ensemble mean (with a small constant to avoid division by zero).
- Neighbourhood features: mean and max over the surrounding 3 x 3 cells.
- Atmospheric fields from the same run: total column water, CAPE, CIN, mean sea level pressure, 850 hPa wind speed and direction, 850 hPa humidity (all at issue time or the forecast lead, because they are part of the issued forecast).
- Monsoon state: RMM phase and amplitude (or OMI) at issue date.
- Context: lead day, day of year, cell latitude and longitude.
- Optional: ECMWF versus NCEP disagreement (difference of ensemble means, difference in signal fraction).

### 7.3 Model ladder (same split and metrics for all)
1. Reference: the climatological base rate of errors.
2. Raw forecast reliability: use the ensemble signal fraction directly as the probability.
3. Logistic regression (with standardized features).
4. Random forest.
5. XGBoost or LightGBM with class weighting.
6. Optional if time allows: a small LSTM or temporal CNN over several days of inputs. Compare fairly or leave out.
- Hyperparameter search only on the training years, using time ordered (blocked) cross validation. Never random shuffling.
- Calibration (fit on the calibration year only): isotonic regression or Platt scaling. Optional: conformal prediction.
- Explainability: SHAP for tree models, permutation importance as a cross check. Report which signals matter, for example ensemble spread, total column water, MJO phase.
- Ablation: baseline features only versus baseline plus MJO indicators, versus baseline plus spread and atmospheric features. This answers the research question.

---

## 8. Evaluation protocol (freeze before running on test data)

### 8.1 Splits
- Train years: 2021 to 2023 (default). Calibration year: 2024. Test year: 2025.
- The test year is used once, after every choice is final. Write the final configuration to `docs/evaluation_protocol.md` before running the test.
- Because samples are correlated, assess uncertainty with a block bootstrap by calendar week (resample whole weeks, not single cells).

### 8.2 Metrics
- Probability quality for `p_wrong`: Brier score, Brier skill score versus the climatological reference, reliability diagram, expected calibration error.
- Discrimination: ROC AUC and precision recall AUC (the second matters more for rare errors).
- Forecast verification of the underlying forecast: probability of detection, false alarm ratio, critical success index, miss rate, frequency bias. Report for several signal levels (10, 20, 30, 50 percent).
- Group results: signal = 1 group and signal = 0 group separately, plus by lead day.
- Trust utility: among forecasts labelled LOW trust, what share of errors do they capture? How does the error rate in HIGH versus LOW trust bands differ? This is the headline result.

### 8.3 Sensitivity checks
- Signal level, threshold percentile (90, 95, 97.5), and absolute threshold versus quantile mapped threshold.
- Area average versus cell level event definitions (optional).

### 8.4 Honest reporting
- Report where the new features do not help. Report data limits: five seasons, few extreme events, spatial correlation, and IMERG error over mountains and snow.
- Do not tune on the test year. Do not describe cell level results as independent samples.

---

## 9. Case study: the 2022 floods replay

- Chronological split cannot test 2022 if it is inside the training years, so use a separate leave 2022 out run: train on other years, calibrate on a held out block of other years, test on JJAS 2022. Label this clearly as a leave one season out check, not as the main chronological test.
- Replay the days around the major flood period (mid August 2022 in the sample file region). For each day show: the forecast ensemble, the signal, the trust label, the committed record (when available), and the observed IMERG rainfall.
- Build the replay as a static view from saved results. It does not need live computation.

---

## 10. Audit layer (blockchain)

### 10.1 Design principles
- Off chain records, on chain hashes. The record JSON is public (in `records/`); the chain holds its SHA 256 hash and timestamps.
- The chain is optional for science. If the chain is down, queue records as PENDING (never show them as verified) and retry. A local mock registry must implement the same interface.
- Append only. Corrections are new records that reference the old one.
- Commit every scheduled forecast. Missing commits are visible gaps, reported openly.

### 10.2 Record format (JSON, canonical)
Use canonical serialization (sorted keys, no extra whitespace, UTF 8, as in RFC 8785). Avoid floating point numbers inside the hashed record so the hash reproduces exactly. Use integers: probability in basis points (7800 means 78.00 percent), rainfall in hundredths of a millimetre.

```json
{
  "schema_version": "1.0",
  "forecast_id": "PK-NCEP-20260721-D2-REGION1",
  "region_id": "pk_north_30_37_69_75",
  "forecast_source": "TIGGE_NCEP_kwbc",
  "issue_time_utc": "2026-07-21T00:00:00Z",
  "window_start_utc": "2026-07-22T00:00:00Z",
  "window_end_utc": "2026-07-23T00:00:00Z",
  "lead_hours": 48,
  "event_definition": {
    "variable": "daily_precipitation",
    "threshold_rule": "p95_jjas_training_period_per_cell",
    "threshold_manifest": "thresholds_v1",
    "signal_rule": "member_fraction_ge_2000bp"
  },
  "signal_bp": 3500,
  "p_wrong_bp": 4200,
  "trust_label": "MEDIUM",
  "trust_band_rule": "bands_v1",
  "model_version": "monsoonguard_v0.1",
  "input_data_manifest": "manifest_live_v1",
  "code_commit": "<git commit hash>"
}
```

(Illustrative only, not a real forecast. The example uses region level values. If the ledger commits cell level results, commit a summary per region and store the cell table in the public record file.)

Outcome record (separate, linked by `forecast_id`): observation product and version (Final or Late or Early), accumulation window, observed value in hundredths of mm, event flag, retrieval time, and the hash of the forecast record it refers to.

### 10.3 Timing rule (important design constraint)
A forecast issued at 00 UTC for day 1 (the window starting at 00 UTC) cannot be committed before its window starts, because the forecast data arrives hours after 00 UTC. The contract requires `commit time < window start`. Therefore:
- The live ledger commits day 2 and day 3 windows only (steps 24 to 48 and 48 to 72), shortly after the 00 UTC run becomes available.
- Historical evaluation may use day 1, 2, and 3. Do not describe day 1 as pre committed.

### 10.4 Contract requirements (ForecastRegistry)
- Owner only `commit` and `recordOutcome`. No public writes.
- `commit(forecastId, forecastHash, windowStart, windowEnd)`: rejects duplicates and empty hashes, requires `windowStart < windowEnd` and `block.timestamp < windowStart`. Stores `block.timestamp` as the commit time. The caller never supplies the commit time.
- `recordOutcome(forecastId, outcomeHash)`: requires an existing forecast, no earlier outcome, and `block.timestamp >= windowEnd`.
- Events for both functions so the history can be listed.
- Optional: batch commit with a Merkle root per day.
- Tests (Hardhat or Foundry): duplicate id, empty hash, late commit, early outcome, double outcome, non owner call.
- Network: a low cost EVM testnet (for example Polygon Amoy). Check the network's current status and the correct EVM version before deploying. A cheaper fallback with no contract is OpenTimestamps.
- Keep the private key in `.env` only. Never commit it.

### 10.5 Verification flow
1. Fetch the public record by forecast id.
2. Recompute SHA 256 from the canonical bytes.
3. Compare with the on chain hash.
4. Confirm commit time is before the window start.
5. Show the outcome record, with its hash check, if present.
6. Report states: PENDING, COMMITTED, OUTCOME_RECORDED, VERIFIED, MISMATCH, MISSED_COMMIT.

### 10.6 Live ledger
- Daily job (scheduled with cron or a simple loop): fetch the latest 00 UTC forecast from a live source, compute features, produce the trust label, write the record, commit, and later fetch the IMERG Late or Early value and record the outcome.
- The live source (for example NOAA open GEFS data) may differ from the TIGGE archive in model version and member count. Treat live results as a separate dataset in every table and figure. State that live outcomes use a faster, less accurate IMERG run than the Final run used for historical evaluation.
- The monsoon is nearly over by late October, so most days will be dry. Present the live ledger as a pipeline demonstration, not as evidence of skill.
- Limit to what is stated: an owner controlled registry cannot prove that every forecast was submitted. A public schedule and visible gaps are the defence.

---

## 11. Application and demo

Keep the interface thin. Impressive parts are the evaluation and the verification, not the charts.

1. Verification page: paste a forecast id, recompute the hash, compare with the chain, show timing and outcome.
2. Trust view: for a chosen date and cell or region, show the ensemble, the signal, `p_wrong`, the trust label, and a plain language explanation based on SHAP features.
3. Evaluation view: reliability diagrams, band error rates, ablation table, and the group results.
4. 2022 replay with a date slider.
5. Ledger status: last commit time, count of committed and missing forecasts.

Suggested stack: Streamlit for the app, matplotlib or plotly for figures, web3.py for chain reads. The app only reads from saved results and the chain. It never trains or writes.

---

## 12. Deliverables

1. Public repository with a reproducible README, one command per stage, pinned dependencies, and the data manifests (not the data).
2. Technical report (about 4 to 6 pages): question, data, method, results with confidence intervals, limitations, related work.
3. Working verification page.
4. Smart contract with tests and a testnet deployment (or documented mock fallback).
5. A 3 minute demo video: problem, 2022 replay, trust label, verification.
6. Devpost submission text.

Related work to cite and position against (check before citing):
- ECMWF operational AI forecasting (AIFS) and the AI weather model literature.
- A decentralized weather forecasting paper with blockchain model validation (arXiv 2508.09299).
- On chain forecast benchmarks such as Foresight Arena (arXiv 2605.00420).
- Ensemble forecast calibration and verification literature.
- Reports on AI monsoon forecasts for farmers in India (monsoon onset, a different problem).

---

## 13. Build order and acceptance checks

Do these in order. Each phase ends with a check that must pass before the next starts.

| Phase | Work | Done when |
|---|---|---|
| 0 | Repo, environment, config file, `.gitignore`, `.env.example` | `pytest` runs, config loads |
| 1 | Loaders for the two sample files (GRIB, IMERG), alignment, unit tests | Sample day reproduces: ensemble area mean range 7.6 to 18.8 mm, IMERG 0.5 degree area mean about 7.2 mm |
| 2 | Download scripts for IMERG and TIGGE, manifests | One full month loads, counts match the calendar, no unexpected gaps |
| 3 | Full download and alignment for 2021 to 2025 | QC report passes, member counts per year recorded |
| 4 | Thresholds, signals, labels (training years only) | Label table builds, base rates printed, leakage test passes |
| 5 | Features, baselines, logistic regression, boosted trees | Models train on the training years, results logged on the calibration year only |
| 6 | Calibration, bands, ablations, SHAP | Reliability diagrams for both subsets |
| 7 | Freeze protocol, run test once, 2022 case study | Results saved with config hash |
| 8 | Record builder, hashing, mock registry, contract, tests | Verification passes and fails correctly on tampered records |
| 9 | Testnet deployment, daily ledger job | At least a few committed live records |
| 10 | App, report, README, demo video, submission | Submitted one day before the deadline |

Phases 8 and 9 can run in parallel with 5 to 7 once phase 4 is done.

Required tests:
- Temporal leakage: no feature timestamp later than the issue time. Train, calibration, and test periods do not overlap.
- Threshold leakage: thresholds computed from training years only.
- Units: precipitation in mm, differences of accumulated steps are non negative after clipping, counts of clipped values logged.
- Grid: forecast and observation grids have the same shape and orientation (latitudes descending 37 to 30 in the TIGGE sample).
- Hash: the same record always gives the same hash, and any field change breaks it.

---

## 14. Cut list if time runs short

Drop in this order, never cutting the first items:

Must keep: data pipeline, labels, baseline and one tree model, calibration, test results, hashing and verification with the mock registry, report, demo.

Cut first: LSTM, ECMWF second centre, conformal prediction, Merkle batching, SHAP polish.

Then cut: extra seasons (keep 2022 plus two more), lead days (keep day 2 only), the live ledger (replace with a handful of committed demo records).

If archived forecasts cannot be obtained at all: fall back to a baseline model using IMERG and reanalysis features, keep the same labels, evaluation design, and audit layer, and state clearly that forecast reliability could not be assessed.

---

## 15. Known risks and limits (state them in the report)

- Five seasons contain few extreme events per cell, and neighbouring cells and days are strongly correlated.
- IMERG is a satellite estimate with larger errors over mountains and snow, so the "truth" is uncertain.
- The monsoon indicators may not improve reliability. A null result is acceptable and must be reported.
- The ensemble signal rule changes what counts as a miss or false alarm. Report sensitivity.
- Retrospective and live forecasts may differ in model version and ensemble size.
- The blockchain proves commit timing only. It does not prove correctness of data or model.
- Owner controlled registry: omission risk is reduced by a public schedule, not eliminated.

---

## 16. First tasks for Claude Code (start here)

1. Create the repository structure in Section 4, `requirements.txt`, `.gitignore` (include `data/`, `.env`, `logs/`, `.venv/`), `.env.example`, and `configs/project.yaml` with the Section 3 defaults.
2. Write `io_tigge.py` and `io_imerg.py` and test them on the two sample files already downloaded. Print: variable, units, step range, member count, grid shape, value ranges.
3. Write `align.py` to average IMERG onto the TIGGE grid and reproduce the sample day comparison from Phase 1.
4. Write the download scripts for IMERG (crop and delete loop) and TIGGE (monthly requests with a manifest), run on one month, and report file sizes and timing before downloading everything.
5. Stop and report to the user after each phase's acceptance check.
