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
presidential approval, 2026 gas price, 2026 real income), runs it with
`Rscript`, and saves the results in `output/`. It does not change the model.

- Approval: the site's approval average (`forecast/data/2026/derived/approval.json`, series `aggregate`), latest value on or before the date.
- Gas: average of 2026 weekly prices up to the date (FRED `GASREGW`).
- Real income: average of the 2026 months released by the date (FRED `A229RX0`). Before any 2026 month is out, the 2025 value is used.
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
| `class_spec_coef.csv`, `class_spec_fit.csv` | The fitted coefficients, standard errors and error sizes, for the class model page. Written by a few lines `run_class.py` appends to the script; they only save what the script already computed. |
