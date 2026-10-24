# MonsoonGuard AI

Detecting unreliable extreme rainfall forecasts in Pakistan.

Can forecast disagreement (ensemble spread) and monsoon indicators identify when
an extreme rainfall forecast is likely to be wrong? The project compares archived
TIGGE NCEP ensemble forecasts against IMERG satellite rainfall over northern
Pakistan (30-37 N, 69-75 E) during JJAS, and commits a tamper-evident record of
each forecast and its trust label before the rain window opens.

The science works without the blockchain. The blockchain supports the audit
trail only, and proves commit timing, not correctness.

See `CLAUDE.md` for the full specification and `docs/decisions.md` for every
decision made and why.

## Results at a glance

Can ensemble spread tell us when a forecast will be wrong?

| Error model | Brier skill vs climatology | ROC AUC | Verdict |
|---|---|---|---|
| **False alarm** | **+0.29** | 0.735 | real skill |
| **Miss** | +0.012 | 0.702 | discriminates, but adds ~nothing |

Trust bands separate error rates by 2.5-3.7x, but are badly positioned (the
HIGH band is empty for false alarms). The test set is two months inside a
single exceptional season, so **no cross-season generalisation is shown**.
Read [reports/report.md](reports/report.md) - especially Section 6 - before
using any number here.

## Status

| Phase | Work | State |
|---|---|---|
| 0 | Repo, environment, config, tests | done |
| 1 | Loaders, alignment, acceptance checks | done |
| 2 | Download scripts, manifests | done |
| 3 | Full archive download | **partial: 19/40 TIGGE files; no 2024/2025** |
| 4 | Thresholds, signals, labels | done |
| 5 | Features, baselines, logistic regression | done |
| 6 | Calibration, trust bands | done (ablations and SHAP not reached) |
| 7 | Frozen evaluation, case study | evaluation done on reduced split |
| 8 | Records, hashing, mock registry, verification | done |
| 9 | Testnet deployment, live ledger | not reached (mock registry documented) |
| 10 | Report, README | done |

The split had to be re-frozen onto available data because ECMWF never
delivered 2024 or 2025 before the deadline. That decision, and everything it
costs, is recorded in [docs/decisions.md](docs/decisions.md).

## Setup

Python 3.11. From the repository root:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# .venv/bin/pip install -r requirements.txt       # Linux / macOS
```

Credentials (accounts are free; none are stored in the repository):

```bash
cp .env.example .env
```

then fill in:

- `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` — https://urs.earthdata.nasa.gov
  A `~/.netrc` entry for `urs.earthdata.nasa.gov` works instead.
- `ECDS_PAT` — personal access token from https://ecds.ecmwf.int/how-to-api
  The TIGGE licence must also be accepted once while signed in at
  https://ecds.ecmwf.int/datasets/tigge-forecasts?tab=download , or requests
  return 403. ECDS replaced the apps.ecmwf.int service, which was
  decommissioned on 2026-05-27; the old key/email pair no longer works.

Check the configuration loads and the invariants hold:

```bash
.venv/Scripts/python -m src.core.config
.venv/Scripts/python -m pytest tests/ -q
```

## Data download

All three scripts write a manifest (source, request parameters, file sizes,
SHA-256) to `data/manifests/`, log the run to `logs/`, and report gaps rather
than skipping them silently. `data/raw/` is append-only: files already present
are never re-downloaded or overwritten.

### Rainfall observations (IMERG Final daily)

IMERG daily granules are global (3600 x 1800, ~34 MB). The study box is
76 x 66 cells. By default the script asks the GES DISC OPeNDAP server for just
that index range: ~48 kB and ~5 s per day, about 30 MB for all 610 days.
`--route download` fetches whole granules and crops locally instead
(~34 MB and ~138 s per day, ~20.7 GB in total); both routes were verified to
return bitwise-identical values. The crop box is 68.75-75.25 E /
29.75-37.25 N, a quarter degree wider than the evaluation region so that
0.5 degree cell averaging has full coverage at the edges.

```bash
python scripts/download_imerg.py --year 2022 --month 8 --dry-run   # count granules
python scripts/download_imerg.py --year 2022 --month 8             # one month
python scripts/download_imerg.py --all                             # 2021-2025 JJAS
python scripts/download_imerg.py --all --route download            # fallback route
```

### Ensemble forecasts (TIGGE NCEP)

One request per parameter group, forecast type and month, submitted to the
ECMWF Data Store (ECDS). Requests are queued and served asynchronously.
Precipitation first, as the other variables are useless without it.

```bash
python scripts/download_tigge.py --year 2022 --month 8 --dry-run   # print requests
python scripts/download_tigge.py --all --group precip              # precipitation
python scripts/download_tigge.py --all --group surface             # tcw, cape, cin, msl, u10, v10
python scripts/download_tigge.py --all --group pressure            # u, v, q at 850 hPa
```

### MJO indices

```bash
python scripts/download_mjo.py                 # RMM and OMI
python scripts/download_mjo.py --check-only    # re-check local copies
```

## Running the science

```bash
python scripts/build_labels.py          # thresholds, signals, error targets
python scripts/run_evaluation.py        # the frozen evaluation (run once)
python scripts/build_records.py         # hashed records + mock registry
pytest -q                               # 147 tests
```

`scripts/provisional_baseline.py` runs the model ladder on training months
only, for use while data is still downloading. It refuses to run once
calibration or test data exists, so it cannot be confused with the frozen
evaluation.

This script exits non-zero when an index does not cover the configured seasons.
It currently does: the BoM RMM file ends 2024-02-24 and covers none of the 2024
calibration or 2025 test seasons, so OMI is the MJO source. See
`docs/decisions.md`.

## Repository layout

```
configs/project.yaml   every frozen decision, path and seed
src/core/              science code, no blockchain imports
src/core/features.py   ensemble, neighbourhood, context, MJO features
src/core/models.py     the two conditional error models and the model ladder
src/core/calibrate.py  isotonic/Platt calibration, trust bands, reliability
src/core/evaluate.py   POD/FAR/CSI verification, block bootstrap by week
src/audit/             record hashing, mock registry, verification
reports/report.md      the technical report
records/               public forecast records + mock registry
scripts/               data download
tests/                 invariants: splits, grids, units, time, hashes
data/raw/              append-only downloads (gitignored)
data/manifests/        checksums and request parameters (committed)
docs/decisions.md      decision log
logs/                  one log file per pipeline run
```
