# PLSC 2219 class model (site copy)

This folder runs the class model for the forecast site. It is shown on the
site only and is not part of the data archive (`forecast/data/`).

## Where the files come from

Copied from the course folder
(`Midterm Elections/cowork space/2026_assignments/class_model/`). The course
folder is the original; do not edit these copies. When the course script or
inputs change, copy them here again.

| File | Notes |
|---|---|
| `class_forecast.R` | Unmodified copy of the course script. sha256 `48577c8f6a6b37d866f3633192b67ba4f218a0bfa5cb84d2af1eb81ea1695a23`. |
| `inputs/elections_pooled.csv` | Past elections used to fit the national model. |
| `inputs/economy_sept.csv` | Yearly economic series (income, gas prices, approval and others). |
| `inputs/hw4_results_2012_2024.csv` | Past race results used to fit the race model. |
| `inputs/races_2026.csv` | 2026 races, candidates and partisan lean (the course's own calculation). |
| `inputs/senate_seats_not_up_2026.csv` | Senate seats not on the 2026 ballot. |

## How it runs

`run_class.py` sets the script's four inputs for a date (as-of date,
presidential approval, gas price, real income), runs it with
`Rscript`, and saves the results in `output/`. It does not change the model.

- Approval: the site's approval average (`forecast/data/2026/derived/approval.json`, series `aggregate`), latest value on or before the date.
- Gas: percent change in the average weekly gas price (FRED `GASREGW`), the latest 52 weeks dated on or before the date against the 52 weeks before.
- Real income: percent change in real disposable income per capita (FRED `A229RX0`), the latest 12 months released by the date against the 12 months before. A month counts as released at the end of the following month.
- The same one-year rule is used for every date from the start of the series, so the series is comparable throughout. The script receives each change as a 2026 value equal to the 2025 value times (1 + change). `--economy course` uses the course's own definition instead (2026 average to date against 2025); on November 1 the two differ because the course compares January to October 2026 with all of 2025.
- District maps: the course race file has each House district's partisan lean on the November 2026 lines. For a date when a state was using different lines (Alabama, Florida, Louisiana and Tennessee before their 2026 maps; Missouri's 2025 map until 2026-09-10), those districts' lean is shifted by the difference between the two maps, taken from the site's district data (`forecast/model/maps.py`). Candidates and incumbency are unchanged. The shifted file exists only in a temporary folder during the run. `class_inputs.csv` records which states were on other lines for each date.

It runs every day in the capture workflow. Past dates can be recomputed with
`--backfill`. Those use the approval data available on each date but today's
FRED data. The model stops updating after November 1, 2026.

## Outputs (`output/`)

| File | What it is |
|---|---|
| `class_timeseries.csv` | National results for each date: vote share, seats, chances of control. |
| `class_senate_history.csv` | Each Senate race for each date. |
| `class_inputs.csv` | The inputs used for each date and when it was computed. |
| `forecast_class_latest.csv` | Every race, latest date. |
| `seat_sims_class_latest.csv` | Simulated seat totals, latest date. |
| `class_national_variants.csv`, `class_national_variants_fit.csv` | Other versions of the national equation (consumer sentiment in place of income, and approval only), fit on the same elections and applied to each date's inputs, from `national_variants.R`. For comparison; not shown on the site. Consumer sentiment is FRED `UMCSENT`, the average of the latest 12 released months. |
| `class_spec_coef.csv`, `class_spec_fit.csv` | The fitted coefficients, standard errors and error sizes, for the class model page. Written by a few lines `run_class.py` appends to the script; they only save what the script already computed. |
