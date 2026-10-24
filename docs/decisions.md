# Decision log

Every change to the frozen defaults in CLAUDE.md section 3, and every choice
that affects the science, is recorded here with the date and the reason. All
entries must be made before test-year results are viewed.

---

## 2026-10-16 — Project configuration frozen

`configs/project.yaml` created with the CLAUDE.md section 3 defaults, unchanged:

- Region `pk_north_30_37_69_75`, 30-37 N / 69-75 E, 0.5 degree grid, 15 x 13 = 195 cells.
- IMERG crop box widened by 0.25 degrees on each side (68.75-75.25 E, 29.75-37.25 N)
  so that 0.5 degree cell averaging has full coverage at the region edges.
- Season JJAS, years 2021-2025 (610 days).
- Event threshold: per-cell 95th percentile of JJAS daily IMERG, training years only.
- Signal rule: at least 20 percent of perturbed members over the same threshold.
- Trust bands: HIGH < 0.20, MEDIUM 0.20-0.50, LOW >= 0.50.
- Split: train 2021-2023, calibrate 2024, test 2025.
- Seed 20260101.

Validated by `tests/test_config.py`. No departures from CLAUDE.md.

---

## 2026-10-16 — MJO index: use OMI, not RMM

**Decision.** Use the NOAA PSL OMI index as the MJO feature source. Keep RMM as a
secondary/cross-check variable for the years it covers, but do not depend on it.

**Reason.** The BoM RMM file is truncated at the source. Measured by
`scripts/download_mjo.py` on 2026-10-16:

| Index | Source | Coverage | Required JJAS days present |
|---|---|---|---|
| RMM | `bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt` | 1974-06-01 to 2024-02-24 | 366 of 610 |
| OMI | `psl.noaa.gov/mjo/mjoindex/omi.1x.txt` | 1991-01-01 to 2026-06-24 | 610 of 610 |

The RMM file ends on 2024-02-24, so it covers none of the 2024 calibration
season and none of the 2025 test season — 244 of the 610 required days. A freshly
downloaded copy is byte-identical to the one already in the project
(2,597,997 bytes), so this is the published file's actual extent, not a stale
local copy. The file also contains 290 sentinel/missing values across its full
history.

CLAUDE.md section 5.3 lists both indices and section 7.2 specifies
"RMM phase and amplitude (or OMI)", so this choice is within the existing
design rather than a departure from it.

**Consequences for later phases.**

- OMI as published has six columns: year, month, day, PC1, PC2, amplitude.
  There is **no phase column**. MJO phase must be derived from the two PCs in
  `src/core/io_mjo.py`, and the derivation must be unit-tested.
- OMI's PC sign and ordering conventions differ from RMM's. The two are not
  interchangeable term-for-term and must not be mixed in one feature column.
- The ablation in CLAUDE.md section 7.3 ("baseline plus MJO indicators") uses
  OMI-derived features.

**Note for the live ledger (phase 9).** OMI is published to 2026-06-24, which is
before the live demonstration window in October 2026. Whether OMI updates in
near-real time must be checked before the live job depends on it; if it lags,
the live records either omit the MJO feature or carry the most recent available
value with its date recorded. Decide and record that before any live commit.

---

## 2026-10-16 — IMERG downloader does not reuse the GES DISC generated script

**Decision.** `scripts/download_imerg.py` is written against `earthaccess`
rather than adapting `download_files_GPM_3IMERGDF_07.py`.

**Reason.** The generated script's bounding box is
`37.1,23.7,60.9,77.8`, which CMR reads as west,south,east,north — a box from
37.1 E to 60.9 E, entirely west of Pakistan, spanning 23.7 N to 77.8 N. Its
temporal filter covers 2022 JJAS only, it keeps every global granule
(~34 MB x 610 days, ~21 GB), and it writes no manifest. The replacement uses the
crop box from `configs/project.yaml`, crops and deletes in batches, and records
checksums.

The original script is kept in the repository root for provenance and is not
used by the pipeline.

---

## 2026-10-16 — IMERG downloads use OPeNDAP server-side subsetting

**Decision.** `scripts/download_imerg.py` defaults to `--route opendap`, fetching
only the needed index range from the GES DISC OPeNDAP server. The original
download-the-globe-and-crop-locally path is kept as `--route download` for when
OPeNDAP is unavailable.

**Reason.** Every IMERG daily granule is global (3600 x 1800 cells, ten
variables, 34 MB). The study box is 76 x 66 cells, 0.077 percent of the globe.
CMR's `bounding_box` only filters which granules match a search; it does not
subset their contents, so the straightforward route transfers ~20.7 GB to keep
~66 MB.

Measured on one granule (2022-08-01) on 2026-10-16:

| Route | Bytes/day | Seconds/day | Projected for 610 days |
|---|---|---|---|
| download + local crop | 33.99 MB | 138.1 | 20.7 GB, ~23.4 h |
| OPeNDAP subset | ~48 kB | ~4.8 | ~30 MB, ~50 min |

**Equivalence check.** For 2022-08-15, the OPeNDAP subset and a local crop of
the full granule were compared directly: identical shape (1, 66, 76), identical
lat and lon arrays, and `np.array_equal(..., equal_nan=True)` is True with a
maximum absolute difference of exactly 0. The two routes are interchangeable,
so the choice is purely about transfer cost.

**Endpoint.** `https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDF.07/<yyyy>/<mm>/<granule>.nc4`
with a DAP2 constraint expression. Note that IMERG stores `precipitation` as
(time, **lon**, **lat**), so the longitude index range comes first; swapping
them returns a transposed grid that still looks plausible. Index ranges are
lat 1197-1272 and lon 2487-2552, asserted in `tests/test_download_requests.py`
and re-validated against the returned coordinates on every fetch.

`randomError` is fetched alongside `precipitation`. It is IMERG's own
uncertainty estimate and supports the limitation in CLAUDE.md section 15 that
the observed "truth" is itself uncertain over mountains.

**Harmony was not tested.** OPeNDAP already reduces the transfer by ~650x and
needs no extra dependency, so the third route in CLAUDE.md section 5.1 was not
pursued.

---

## 2026-10-16 — Phase 2 acceptance check passed (August 2022)

`python scripts/download_imerg.py --year 2022 --month 8` completed with
31 of 31 calendar days, 1.67 MB transferred, 200 seconds, zero issues logged.

Verification of the written files: 31 unique SHA-256 checksums, dates
continuous and in order from 2022-08-01 to 2022-08-31, grid 76 x 66 at
29.75-37.25 N / 68.75-75.25 E, units mm/day, no NaN. Peak cell value
195.4 mm and peak area mean 13.4 mm both fall on 2022-08-17, consistent with
the mid-August 2022 flood period used for the case study in CLAUDE.md
section 9.

---

## 2026-10-16 — TIGGE access moves to ECDS (CLAUDE.md section 5.2 is out of date)

**Decision.** `scripts/download_tigge.py` is rewritten against the ECMWF Data
Store (ECDS) using `cdsapi`. The MARS-style request documented in CLAUDE.md
section 5.2 cannot be used.

**Reason.** The ECMWF Public Datasets service at `apps.ecmwf.int` was
**fully decommissioned on 27 May 2026**, and TIGGE retrieval migrated to ECDS,
which opened on 21 April 2026. Both notices are published on the ECMWF pages
themselves ("TIGGE Data Retrieval Migrated to ECDS on 27-05-2026";
"The ECMWF Public Datasets service was fully decommissioned on 27 May 2026").

This was discovered because the licence page CLAUDE.md points at
(`apps.ecmwf.int/datasets/licences/tigge`) serves licence text with no accept
control: the service behind it no longer exists. The legacy API at
`api.ecmwf.int/v1` still authenticates but returns 403 for `datasets/tigge`.

**What changed.**

| CLAUDE.md 5.2 (MARS, legacy) | ECDS (current) |
|---|---|
| `ecmwf-api-client`, `~/.ecmwfapirc` | `cdsapi>=0.7.7`, personal access token |
| `https://api.ecmwf.int/v1` | `https://ecds.ecmwf.int/api` |
| key + email | single token (`ECDS_PAT`) |
| `dataset: tigge` | `tigge-forecasts` |
| `origin: kwbc` | `origin: ncep` |
| `class`, `expver` | not used |
| `param: "228228"` | `variable: ["total_precipitation"]` |
| `type: pf` / `cf` | `forecast_type: perturbed_forecast` / `control_forecast` |
| `number: 1/to/30` | not used; the forecast type selects members |
| `step: 24/48/72` | `leadtime_hour: ["24","48","72"]` |
| `date: a/to/b` | `year`, `month`, `day[]` |
| `area: "N/W/S/E"` string | `area: [north, west, south, east]` list |
| `grid: "0.5/0.5"` | no grid key; native archive grid is returned |
| `levtype: sfc` / `pl` | `level_type: single_level` / `pressure` |
| `levelist: "850"` | `level_value: ["850"]` |

**Nothing in the scientific design changes.** The ECDS form schema (read from
`/api/catalogue/v1/collections/tigge-forecasts/form.json`) still offers NCEP,
years 2006-2026, 00:00 runs, lead times 24/48/72 h, perturbed and control
forecasts, area subsetting, and every variable CLAUDE.md section 7.2 requires.
Only the request syntax and the client library differ.

**Consequence to verify.** ECDS exposes no grid key, so files arrive on the
archive's native grid. For NCEP TIGGE that should be 0.5 degrees, giving the
15 x 13 evaluation grid, but this is now an assumption rather than a request
parameter. `src/core/io_tigge.py` must assert the grid resolution and shape of
every file it reads (`forecast.expected_grid_deg` in the config).

**Credentials.** `.env` now carries `ECDS_URL` and `ECDS_PAT`; the legacy
`ECMWF_API_URL` / `ECMWF_API_KEY` / `ECMWF_API_EMAIL` entries were removed.
Licence acceptance is verified before each run via
`/profiles/v1/account/licences`, which reports `tigge-licence` revision 2 as
accepted for this account.

**Note for the live ledger (phase 9).** The TIGGE licence grants access with a
48-hour delay after forecast initial time. That cannot satisfy the section 10.3
rule that a record is committed before its window opens, confirming that the
live ledger needs the separate near-real-time source already planned in
section 10.6.

---

## 2026-10-16 — Atmospheric features come from the control forecast only

**Decision.** Precipitation is downloaded with the full 30-member perturbed
ensemble plus the control run. The atmospheric context fields (total column
water, CAPE, CIN, mean sea level pressure, 10 m wind, and 850 hPa u, v and
specific humidity) are downloaded from the **control forecast only**.

**Reason.** ECDS runs one job at a time per account. Measured on 2026-10-16:
four small test requests submitted at once all stayed in `accepted` for seven
minutes while a single bulk job ran, and never started. So requests are
strictly serialised and wall-clock equals the sum of all request durations.

One month of perturbed precipitation (31 days x 30 members x 3 lead times,
~2,800 fields) took 403.6 s. Cost scales with field count, which is dominated
by the 30 members:

| Request | Fields/month | 20 months |
|---|---|---|
| precipitation, perturbed | ~2,800 | 2.2 h (measured) |
| surface 6 vars, perturbed | ~16,700 | ~13.5 h |
| surface 6 vars, control | ~560 | ~0.5 h |
| 850 hPa 3 vars, control | ~280 | ~0.25 h |

Full ensembles for every variable would take ~24 h of serialised downloading
with 8 days to the deadline. Control-only for the atmospheric fields brings the
total to ~3.5 h.

CLAUDE.md section 7.2 lists these fields as monsoon and atmospheric context and
does not ask for their ensemble spread; the ensemble features it specifies
(mean, standard deviation, maximum, quantiles, member fractions) are all
precipitation features, which are unaffected. The section 7.3 ablation remains
possible in full: baseline, baseline plus MJO, and baseline plus spread and
atmospheric features.

**What is given up.** Features of the form "spread of CAPE across members"
cannot be built. If a later experiment suggests those matter, the group can be
re-requested for a subset of seasons; `data/raw` is append-only and the
downloader skips files already present, so nothing already fetched is wasted.

**Risk accepted.** If ensemble spread of an atmospheric field were the single
strongest predictor of an unreliable forecast, this choice would hide it. That
is judged an acceptable trade against a 6x increase in download time, given
that section 7.1 expects few positives in the miss subset and section 15 notes
strong spatial and temporal correlation, both of which argue against adding
many collinear predictors.

---

## 2026-10-16 — IMERG to TIGGE grid averaging is area-weighted at box edges

**Decision.** `src/core/align.py` averages IMERG 0.1 degree cells onto the
0.5 degree TIGGE grid with **half weight for cells whose centres lie exactly on
a box edge**.

**Reason.** IMERG V07 cell centres sit at 0.05 degree offsets (29.75, 29.85,
29.95, ...). A 0.5 degree box centred on a TIGGE point spans centre +/- 0.25,
so its edges land exactly on IMERG cell centres. Those cells are shared between
two adjacent boxes and must not be counted fully in both.

Measured for 2022-08-15 against the CLAUDE.md section 13 target of
"about 7.2 mm":

| Scheme | Effective cells per box | Area mean |
|---|---|---|
| inclusive bounds (double-counts edges) | 36.0 | 7.2890 mm |
| half-open `[c-0.25, c+0.25)` | 25.0 | 7.1795 mm |
| **area-weighted, edge cells at 0.5** | **25.0** | **7.2096 mm** |

Area weighting is both the closest to the documented value and the only scheme
that is dimensionally correct: a 0.5 degree box contains exactly 5 x 5 cells of
0.1 degree, and 25.0 effective cells is what the weights must sum to. The
inclusive scheme inflates every box to 36 cells and double-counts edges.

---

## 2026-10-16 — Phase 1 acceptance check passed

Both targets in CLAUDE.md section 13 are reproduced from data downloaded
through the new ECDS and OPeNDAP routes:

| Required | Measured |
|---|---|
| ensemble area mean range 7.6 to 18.8 mm | **7.62 to 18.78 mm** |
| IMERG 0.5 degree area mean about 7.2 mm | **7.2096 mm** |

This is a strong cross-check. The acceptance figures in CLAUDE.md were derived
from a sample GRIB obtained through the old apps.ecmwf.int MARS route, and they
are reproduced exactly by a file fetched through ECDS with a completely
different request syntax. The two routes return the same data.

**GRIB reading works on Windows via pip.** `pip install cfgrib eccodes` gives
cfgrib 0.9.15.1 with the ecCodes 2.50.0 binary library; the conda fallback
noted in CLAUDE.md section 4 was not needed.

**File structure confirmed** for `precip_pf_2022_08.grib`: dimensions
(number 30, time 31, step 3, latitude 15, longitude 13), variable `tp`,
units `kg m**-2`, stepType `accum`, latitudes descending 37.0 to 30.0,
longitudes 69.0 to 75.0. 15 x 13 = 195 cells as specified. No NaN.
So ECDS does return the native 0.5 degree grid, resolving the open question
raised when the `grid` request key disappeared.

**Accumulation differencing verified.** Day 1 = step 24, day 2 = step 48 minus
step 24, day 3 = step 72 minus step 48. Negative differences from GRIB packing
are present and bounded, as CLAUDE.md section 5.2 anticipates:

| Window | Negatives | Share | Most negative |
|---|---|---|---|
| day 1 | 0 | 0 % | n/a |
| day 2 | 3,230 of 181,350 | 1.78 % | -0.0625 mm |
| day 3 | 1,848 of 181,350 | 1.02 % | -0.0625 mm |

All negatives are bounded at -0.0625 mm, the GRIB packing precision (1/16 mm),
so they are rounding artefacts rather than a decoding error. The loader must
clip them to zero and log the count per file.

---

## 2026-10-16 — Open issue: arid cells have a near-zero p95 threshold

**Not yet decided. Flagged before any test-year result is seen.**

Per-cell p95 thresholds computed from the training years (2021-2023, 366 JJAS
days) range from **0.09 mm to 32.89 mm**, median 12.09 mm. The distribution
has a clear spatial structure: the dry northwest (Hindu Kush and Karakoram,
around 36-37 N / 69-70 E) sits below 1 mm, while the monsoon-exposed southeast
plains reach 20-33 mm.

| Threshold | Cells | Share of 195 |
|---|---|---|
| < 0.5 mm | 6 | 3.1 % |
| < 1.0 mm | 16 | 8.2 % |
| < 2.0 mm | 31 | 15.9 % |
| < 5.0 mm | 54 | 27.7 % |

In the 16 cells below 1 mm, "observed rainfall exceeds the cell's 95th
percentile" means little more than "any measurable rain fell". Calling that an
extreme rainfall event is not defensible, and those are also the cells where
CLAUDE.md section 15 warns IMERG is least reliable, because the retrieval is
poorest over mountains and snow.

This confirms the CLAUDE.md section 3 note that a fixed 50 mm threshold would
be wrong for this box: 50 mm exceeds the p95 of every one of the 195 cells.
The percentile rule is right; the question is only what to do at the dry end.

**Options, none yet chosen.**

1. Change nothing. Report the issue and show results split by threshold band.
   Keeps the frozen definition intact.
2. Apply a floor, `threshold = max(p95, F)` for some F (1 or 2 mm). Makes
   "extreme" mean something everywhere, but changes the frozen event
   definition and needs justifying.
3. Mask cells below a floor out of the evaluation, reporting how many were
   dropped. Cleanest scientifically, but discards part of the domain and
   makes the sample smaller.

The decision must be recorded here **before** any test-year result is viewed
(CLAUDE.md section 3). Option 1 plus a sensitivity run is the default if no
decision is taken, since it does not alter the frozen design.

---

## 2026-10-16 — Phase 4 acceptance check passed

`python scripts/build_labels.py` runs end to end on the real archive.

- IMERG: 610 of 610 days, no NaN, regridded to (610, 15, 13) in 2.3 s.
- Thresholds: p95 per cell from 2021-2023 only. **Leakage check passes** - no
  calibration or test year contributes. A unit test additionally multiplies
  all 2025 observations by 1000 and asserts the thresholds are bit-identical.
- Forecasts available at the time of the run: 2021-06 and 2022-08 only, which
  the run reports as a warning rather than treating as complete.
- Negative differences from GRIB packing: 10,825 of 1,070,550 values (1.01 %),
  most negative -0.0625 mm, all clipped to zero and counted.

Base rates over the two months available (35,685 samples):

| Quantity | Value |
|---|---|
| observed event rate | 7.97 % |
| signal = 1 share | 17.3 % |
| false alarms within signal = 1 | 64.0 % (3,947 of 6,165) |
| misses within signal = 0 | 2.12 % (626 of 29,520) |

By lead day, the false alarm rate rises monotonically (61.7 %, 64.5 %, 65.6 %
for days 1 to 3) while the miss rate is roughly flat. Forecast quality
degrading with lead time is the expected behaviour and is a useful sanity
check on the window arithmetic.

Class balance is workable for both conditional models: the false alarm subset
is near balanced, and the miss subset has 626 positives from two months, so
the section 7.1 concern about too few positives looks manageable once the full
archive is present.

**These are not results.** Both months fall inside the training years and the
sample is 2 of 20 months.

---

## 2026-10-18 — Provisional baseline run while the archive downloads

**Decision.** Rather than wait for the full 40-request TIGGE archive (queue
position climbing, ~days not hours), `src/core/features.py`,
`src/core/models.py` and `scripts/provisional_baseline.py` were built and run
against whatever training-year months are already on disk. This validates the
full pipeline end to end and gives honest provisional numbers; it is
explicitly **not** the CLAUDE.md section 8 evaluation, which needs the
calibration year (2024) and test year (2025).

The script refuses to run if any calibration or test year data is present, so
it cannot be mistaken for, or accidentally produce, the frozen evaluation.

**Method.** Leave-one-month-out cross-validation across the training-year
months present (9 months: all of 2021 and 2022 JJAS, plus 2023-06). For each
held-out month, the model trains on the other months and predicts on the
held-out one; metrics are pooled across all held-out folds rather than
averaged per fold, since a single month's held-out sample is often too small
or too imbalanced for a stable per-fold AUC.

**Result, 2026-10-18, 159,120 samples (9 months x 3 lead days x 195 cells,
minus rows with no verifying IMERG observation at month boundaries):**

| Target | Model | Brier | Brier skill | ROC AUC | PR AUC |
|---|---|---|---|---|---|
| false_alarm (n=21,360, 76.5% positive) | reference | 0.1884 | 0.000 | 0.289 | 0.672 |
| | raw signal fraction | 0.3630 | -0.927 | 0.617 | 0.824 |
| | logistic regression | 0.2001 | -0.062 | **0.763** | 0.900 |
| miss (n=137,760, 2.84% positive) | reference | 0.0277 | 0.000 | 0.313 | 0.020 |
| | raw signal fraction | 0.0430 | -0.553 | 0.661 | 0.065 |
| | logistic regression | 0.1740 | -5.280 | **0.794** | 0.118 |

**Reading these numbers.** Logistic regression discriminates clearly above
chance on both targets (ROC AUC 0.76 and 0.79), which is the first real
evidence that ensemble spread and the other section 7.2 features carry signal
about when a forecast decision will be wrong - the project's actual research
question. Its Brier scores are currently *worse* than the reference baseline
on both targets; this is expected and not a concern at this stage. The model
is fit with `class_weight="balanced"` (reasonable given the class imbalance,
especially 2.84% positive in the miss subset), which improves ranking at the
direct expense of calibration. CLAUDE.md section 7.3 places calibration (fit
on the calibration year only) as its own later pipeline stage for exactly this
reason; comparing uncalibrated Brier scores against the reference is not yet
meaningful and the test suite (`test_models.py`) checks discrimination here,
not Brier, for the same reason.

The false alarm rate (76.5% of signal=1 samples) is notably higher here than
in the 2-month spot check run earlier (64.0%, logged above). The miss rate
(2.84%) is lower than that same spot check (4.02%). Both numbers will keep
moving as more months arrive; neither is stable yet at 9 of 20 months.

**A build detail worth recording.** The feature table (160,290 rows) and label
table (159,120 rows) differ by 1,170 rows: the feature table includes every
(issue_date, lead_day, cell) combination regardless of whether a verifying
observation exists, while the label table drops combinations whose verifying
date falls outside the downloaded IMERG range (lead day 2 or 3 from a 30
September issue date lands in October, which is outside JJAS and not
downloaded). The inner join in `provisional_baseline.py` reconciles this
correctly; it is not a bug, but worth knowing before extending either table.

**What happens next.** Re-running `scripts/provisional_baseline.py` after more
training months land will use the larger set automatically; nothing is
hard-coded to 9 months. Once 2024 data exists, this script must stop being
used (and will refuse to run) in favour of the real train/calibrate/test
protocol.

---

## 2026-10-24 — Split re-frozen on available data (deviation from CLAUDE.md section 3)

**Recorded BEFORE any test result was viewed, as CLAUDE.md section 3 requires.**

**Decision.** The train/calibration/test split moves from years to months:

| Split | Months | Days |
|---|---|---|
| train | 2021-06, 2021-07, 2021-08, 2021-09 | 122 |
| calibration | 2022-06, 2022-07 | 61 |
| test | 2022-08, 2022-09 | 61 |

Chronological, non-overlapping, and enforced by assertions in
`src/core/config.py` (`max(train) < min(calibration) < min(test)`).

**Reason.** The original design (train 2021-2023, calibrate 2024, test 2025)
is unreachable. ECDS delivered 19 of 40 precipitation requests over eight days
and **nothing at all for 2024 or 2025**. Individual perturbed-month requests
took between 0.8 minutes and 15.9 hours, driven by a shared global MARS tape
queue whose position grew while we were idle. With the submission deadline on
2026-10-25 there is no prospect of the calibration and test years arriving.

CLAUDE.md section 3 anticipates exactly this: the split row ends "Adjust if
data coverage differs." Section 14's cut list also permits reducing seasons.
Without this change there is no calibration set and no test set, so there is
no evaluation at all - strictly worse than a smaller but complete one.

**What is preserved.** Everything structural: a chronological split that never
trains on the future, thresholds fitted on train months only, calibration
fitted on the calibration split only, and a test split touched exactly once.
The leakage checks still run and still pass.

**What is lost, and must be stated in the report.**

- The test set is 61 days in one season, not a full independent season. It
  cannot show year-to-year generalisation at all.
- Train is a single season (2021), so anything peculiar to 2021 is baked in.
- The test months are August and September 2022 - the catastrophic flood
  period. That is the most extreme part of the archive, so the test set is
  deliberately *not* representative of a typical monsoon. Results will be
  dominated by one exceptional event and must not be read as typical skill.
- The section 9 leave-2022-out case study is no longer separable from the
  test split, since 2022 now *is* the calibration and test data. The case
  study becomes a description of the test period rather than an independent
  check.
- Sample sizes per CLAUDE.md section 15 were already small; they are now
  smaller, and the block bootstrap by calendar week has roughly 9 blocks in
  the test split rather than dozens.

**The original design stays recorded** in `configs/project.yaml` under
`split.original_design` with `reachable: false`, so the deviation remains
visible rather than quietly overwritten.

**If the remaining data arrives** before submission, the correct action is to
restore the original split and re-run; the test split would then be genuinely
untouched, since the models trained under this reduced split would be
discarded rather than carried over.
