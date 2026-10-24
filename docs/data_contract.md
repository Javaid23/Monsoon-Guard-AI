# Data contract

What is on disk, where it came from, and how to read it. Written by the
data-collection work on 2026-10-24. Every claim here was verified by reading
the files, not by counting them; the QC reports are in `logs/qc_*.txt`.

## Summary: what is usable

| Season | Precipitation | Atmospheric | IMERG | MJO | Usable |
|---|---|---|---|---|---|
| 2021 | complete (pf + cf) | complete | complete | complete | **yes** |
| 2022 | complete (pf + cf) | complete | complete | complete | **yes** |
| 2023 | 5 of 8 months missing | complete | complete | complete | no |
| 2024 | 7 of 8 months missing | complete | complete | complete | no |
| 2025 | all 8 missing | complete | complete | complete | no |

**2021 and 2022 are complete across all four sources: 726 of 732
(issue date, lead day) pairs, i.e. 141,570 samples at 195 cells.** The 6
missing pairs are issue dates of 29-30 September whose verification window
falls in October, outside JJAS - see "Season boundary" below.

## Sources

### 1. Precipitation forecasts - TIGGE NCEP

- `data/raw/tigge_ncep/precip_{pf,cf}_YYYY_MM.grib`
- Read with `src.core.io_tigge.load_file`, which returns daily window totals
  in mm as `(number, time, lead_day, latitude, longitude)`.
- Perturbed files: **30 members**. Control files: 1 member. Consistent across
  every year present, so member counts never need special handling - but
  features should still use member *fractions*, not counts (CLAUDE.md 5.2).
- Grid 15 x 13, latitudes descending 37.00 to 30.00, longitudes 69.00 to
  75.00. Steps 24/48/72 h, `stepType = accum`, units `kg m**-2` (= mm).
- Accumulations are from forecast start, so daily windows are differences:
  day 1 = step24, day 2 = step48 - step24, day 3 = step72 - step48.
- Negative differences from GRIB quantisation are clipped to zero. Measured:
  **1-2% of step differences per file, worst value -0.0625 mm**, all values
  exact binary fractions (1/64, 1/32, 1/16). Rounding, not a step-ordering
  fault.

### 2. Atmospheric context - NOAA GEFS, *not* TIGGE

**This is a different source from the precipitation.** ECDS never delivered
the TIGGE `surface` or `pressure_level` groups (0 of 16 months for either),
so these fields come from NOAA's open GEFS archive. See `docs/decisions.md`,
2026-10-24, "Atmospheric context fields come from NOAA GEFS, not TIGGE".

- `data/raw/gefs_atmos/atmos_cf_YYYY_MM.nc` - NetCDF, one file per month.
- Config path: `cfg.get_path("raw_gefs_atmos")`.
- Dimensions `(time, lead_day, latitude, longitude)`; `lead_day` 1/2/3 maps to
  f024/f048/f072. Control run only (`gec00`), so there is no member dimension
  and no spread feature can be built from these fields.
- **Grid is identical to TIGGE** - verified by direct comparison against a
  TIGGE file: same shape, same coordinate values, same orientation. No
  regridding needed.
- Variables, with units as published:

  | Variable | GRIB field | Units |
  |---|---|---|
  | `total_column_water` | PWAT, entire atmosphere | mm |
  | `convective_available_potential_energy` | CAPE, 180-0 mb above ground | J/kg |
  | `convective_inhibition` | CIN, 180-0 mb above ground | J/kg |
  | `mean_sea_level_pressure` | PRMSL | **Pa** |
  | `10_m_u_component_of_wind` | UGRD, 10 m above ground | m/s |
  | `10_m_v_component_of_wind` | VGRD, 10 m above ground | m/s |
  | `u_component_of_wind_850` | UGRD, 850 mb | m/s |
  | `v_component_of_wind_850` | VGRD, 850 mb | m/s |
  | `specific_humidity_850` | SPFH, 850 mb | kg/kg |

- Values are instantaneous fields at each forecast valid time, i.e. part of
  the issued forecast. Nothing here is a later observation, so using them is
  temporally legitimate (CLAUDE.md 7.2).
- Zero missing values across all 20 months. Observed ranges, which the report
  can quote as plausibility evidence: PWAT 1-82 mm, CAPE 0-2413 J/kg,
  CIN -686 to 0.5 J/kg, PRMSL 98.8-102.8 kPa, 850 hPa q 0.0015-0.0247 kg/kg.

**Two substitutions that must appear in the report:**

1. `total_column_water` is GEFS **PWAT** (precipitable water) - the vapour
   column only. TIGGE's total column water also includes cloud condensate.
   Close, but not the same quantity.
2. These are **operational GEFS** fields, not the TIGGE archive copy. GEFS v12
   has been operational since September 2020, so 2021-2025 is one consistent
   model version.

**Mixed provenance.** Precipitation (labels, ensemble spread, member
fractions) is TIGGE NCEP; atmospheric context is NOAA GEFS. Both are NCEP
GEFS at 0.5 degrees from the same 00 UTC initialisation, so the features are
physically consistent - but the dataset must not be described as purely
TIGGE.

### 3. Observations - GPM IMERG Final v07

- `data/raw/imerg_final/3B-DAY.MS.MRG.3IMERG.YYYYMMDD-*.V07B.nc4`
- 610 files: 122 days per season for **2021-2025**, no gaps, no short files.
- Every file verified to carry `precipitation`, units `mm/day`, on an
  identical 76 x 66 grid with extent exactly
  `[29.75, 37.25, 68.75, 75.25]` - the configured crop box, which includes
  the 0.25 degree margin needed for 0.5 degree cell averaging.
- Values 0 to 245.7 mm, no negatives, **no all-missing days**. Per-day maximum:
  median 52.9 mm, 95th percentile 124.8 mm.

### 4. Monsoon state - MJO

- `data/raw/mjo/omi_*.txt` - **OMI is the index to use** (decided 2026-10-16).
  Covers 1991-01-01 to 2026-06-24: all 488 JJAS days of 2021-2024 present,
  zero gaps, zero sentinel values.
- `data/raw/mjo/rmm.74toRealtime_*.txt` - cross-check only, and **it ends
  2024-02-24**. A fresh fetch on 2026-10-24 still ended there, so this is an
  upstream gap at the Bureau of Meteorology, not a download failure. RMM
  cannot cover 2024 JJAS. Report this as a limitation if RMM is cited.
  Note its 8th column is a text method label, not a value.

## Season boundary

A forecast issued on 29 or 30 September verifies on 1-2 October, outside
JJAS. Those samples are **excluded deliberately**: labels compare the
observation to that cell's JJAS 95th percentile, and early October is
post-monsoon and typically drier, so scoring an out-of-season observation
against an in-season climatology would bias those samples toward "no event"
and distort the miss and false-alarm rates. Out-of-season IMERG was therefore
not downloaded.

Effect per season: lead day 1 loses 0 dates, lead day 2 loses 1, lead day 3
loses 2. Across 2021-2022 that is the 6 pairs noted above. **Downstream code
should drop these explicitly rather than meet them by accident.**

## Provenance and reproducibility

- 53 manifests in `data/manifests/`, recording 641 files with source, product
  and version, request parameters, download date, size and SHA-256.
- All 31 non-IMERG files (TIGGE, GEFS, MJO) re-hashed and **every checksum
  matches**; all 610 IMERG files are recorded. Zero missing manifests, zero
  mismatches.
- `data/manifests/tigge_jobs.json` is local scheduling state (ECDS job ids),
  not a dataset manifest, and is gitignored.

## What is missing, and why

**12 TIGGE precipitation months**: 2023 (pf 07/08/09, cf 07/09) and 2024
(pf 07/08/09, cf 06/07/08/09).

ECDS serves TIGGE from tape, runs **one job at a time per account** during
heavy usage, and caps in-flight requests at 4 - over the cap it returns HTTP
201 with a job id and then silently flips the job to `rejected`. Measured on
2026-10-24: **two jobs completed in nine hours**, one of them taking 6.66 h,
and a separate job sat in `running` for 5 h 44 m before being dismissed. At
that rate the 12 remaining months are not retrievable before the submission
deadline. This matches behaviour documented on the ECMWF forum.

The atmospheric and observational data for 2023-2025 is already complete, so
each of those seasons is only its precipitation months away - but those months
can come from nowhere else. GEFS was evaluated as a substitute and rejected
for precipitation: it publishes 6-hour accumulation buckets rather than totals
from forecast start (4x the fetches), and more importantly, taking the test
season's ensembles from a different pipeline than the training seasons' would
confound a source change with a change in forecast reliability - the very
thing the study measures.
