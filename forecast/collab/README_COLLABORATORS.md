# U.S. Election Forecast Archive: 2026 Midterm Elections

**Version 0.1 (pre-release, for collaborators only).** Please read
`DATA_USE.md` first.

Daily forecasts of the 2026 U.S. House and Senate elections from polling
averages, prediction markets, professional forecasters and published academic
models, collected by Kevin DeLuca (Yale University).

This is an early, private version shared with a few researchers. It is still
being collected and checked, and files may change between copies. After the
election we plan to release a complete, documented version with a DOI (on
Dataverse), with as many forecasters' series as they allow us to publish.

A public subset is at
https://github.com/kevin-deluca-polisci/KD-website/tree/main/forecast/archive/2026,
whose README describes collection, processing and assumptions in full. This
version adds each forecaster's own series, every line average, the race
ratings, and the raw files the data were read from.

The build date is in `version.txt`. Dated copies of the processed tables are
in `snapshots/`.

## Files

| File | What it is |
|---|---|
| `forecasts_by_source.csv` | Every forecaster's own series: one row per date, source, race and quantity. The `permission` column is `public` (can be published by name) or `pending` (research use only until the forecaster agrees to public release). |
| `category_averages.csv` | Every line average by date, line, race and quantity. `published` = 1 for averages in the public archive, 0 for averages of fewer than three permission-pending forecasts. |
| `ratings.csv` | Race ratings (for example Cook, Inside Elections, Sabato) as listed on Wikipedia, one row per date, race and rater. `value` is `rater:rating`. |
| `timeline.csv` | The daily national series on the tracker's chart. |
| `approval.csv` | Daily average of the published presidential approval aggregators. |
| `files.csv` | Row counts and SHA-256 hashes of the tables above. |
| `raw/` | Every file the archive captured, exactly as received: `raw/<source>/<date>/<file>`, each with a `.meta.json` recording the URL, time and hash. |
| `parsed/` | One CSV per date with every value read from the raw files. `raw_path` and `raw_sha256` link each row to its raw file. |
| `raw_files.csv` | Every file in `raw/` with its size and SHA-256. |
| `version.txt` | Version, build date, and the versions of the data it was built from. |

Not included in `raw/` and `parsed/`: Cook Political Report PVI, Dave's
Redistricting and one individual's model (Grant Williams). Their terms do not
allow sharing.

## Studying how forecasts change over time

Use `forecasts_by_source.csv`, one source at a time. The line averages mix
different sets of forecasters on different days, so their changes include
sources joining and leaving.

The `provenance` column says how each value was obtained:

- `captured`: collected on that date.
- `archival`: recovered from a dated record of what the source showed on that
  date (a market's price history, a past version of a Wikipedia page).
- `computed`: calculated by the archive on that date from data collected
  that day.
- `retrospective`: calculated later for a past date, using data as it
  exists now.

For real-time analysis, use `captured`, `archival` and `computed` rows.

Each source has one value per day (`snapshot_date`); `as_of` is the date the
source published it.

## Columns

Column definitions are the same as in the public README. The additions:

- `permission` (`forecasts_by_source.csv`): `public` or `pending`.
- `published` (`category_averages.csv`): 1 or 0, as above.
- `line` = `input` in `forecasts_by_source.csv`: FRED economic data, MIT
  Election Data and Science Lab returns, and approval polling.

## How to cite

For this version:

> DeLuca, Kevin. 2026. "U.S. Election Forecast Archive: 2026 Midterm
> Elections." Version 0.1 (pre-release), [build date from `version.txt`].
> Unpublished data, Yale University.

Once the public version is released, please cite that version instead; we
will send the citation and DOI.
