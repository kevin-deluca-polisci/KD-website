# U.S. Election Forecast Archive: 2026 Midterm Elections

Daily forecasts of the 2026 U.S. House and Senate elections from polling
averages, prediction markets, professional forecasters and published academic
models, collected by Kevin DeLuca (Yale University) for PLSC 2219.

**Version 0.1 (preliminary).** These files are updated every day until the
election. A final version with a DOI will be released after the election.

The data are shown on the Election Forecast Tracker:
https://kevinmdeluca.com/forecast/

## How to cite

> DeLuca, Kevin. 2026. "U.S. Election Forecast Archive: 2026 Midterm
> Elections." Version 0.1. https://github.com/kevin-deluca-polisci/KD-website/tree/main/forecast/archive/2026

Please also cite the original forecasters when you use their numbers.

## Files

| File | What it is |
|---|---|
| `timeline.csv` | The daily national series on the tracker's chart: one row per date, line and quantity. |
| `category_averages.csv` | Every published average, by date, line, race and quantity. |
| `suppressed.csv` | Averages held back under the three-forecast rule (see below). Same columns, with the values left blank. |
| `forecasts_by_source.csv` | Individual forecasts from sources whose terms allow republication by name. |
| `approval.csv` | Daily average of the published presidential approval aggregators, used as a model input. |
| `files.csv` | Row counts and SHA-256 hashes for the files above. |

## Coverage

- Dates: 2025-01-20 to the present, one snapshot per day. Daily collection
  began in August 2026. Earlier dates come from records of past forecasts
  (market price histories, past versions of Wikipedia pages) or were
  computed afterward; the `provenance` and `n_retrospective` columns say which.
- Contests: the national House vote, House and Senate control and seat
  totals, and individual Senate races. Individual House and governor races
  appear where a source forecasts them (mostly prediction markets).

## The lines

Each forecast belongs to one line, based on who made it.

| Line | Sources |
|---|---|
| `polling` | National: polling averages from Silver Bulletin, RealClearPolitics, Decision Desk HQ, FiftyPlusOne, VoteHub and Race to the WH (mostly as listed on Wikipedia), plus a simple average of individual generic ballot polls. Senate races: the polling averages listed in each race's Wikipedia article. |
| `market` | Kalshi, Polymarket, PredictIt. |
| `professional` | Race to the WH. More forecasters will be added. |
| `academic` | Bafumi, Erikson and Wlezien (generic ballot); a referendum model (Tufte; Lewis-Beck and Tien); Lockerbie (economic pessimism); Lewis-Beck and Quinlan (political history); Ray Fair's House vote equation. Except for Fair, this archive runs the published models on current data. |

`forecasts_by_source.csv` also includes inputs (`line` = `input`): economic
data from FRED, past election returns from the MIT Election Data and Science
Lab, and presidential approval polling from Wikipedia.

## Columns

**Identifiers used in several files**

- `snapshot_date` (or `date`): the day the data were collected, YYYY-MM-DD.
- `race_id`: `NATL_HOUSE_2026` and `NATL_SENATE_2026` for national quantities;
  `SEN_<state>_2026` for a Senate race; `HOU_<state>_<district>_2026` for a
  House district; `GOV_<state>_2026` for a governor's race.
- `chamber`, `state`, `district`: the race's chamber (`national`, `senate`,
  `house`, `governor`), two-letter state, and district number.
- `quantity` and `unit`:
  - `margin_D` (`pct`): Democratic minus Republican share of the vote, in
    points. For the national House, the two-party national vote.
  - `win_prob_D` / `win_prob_R` (`prob`): probability, 0 to 1. For national
    rows, the chance of controlling the chamber (218 House seats; 51 Senate
    seats, since a 50-50 Senate is Republican control).
  - `seats_D` (`seats`): expected Democratic seats in the chamber. Senate
    totals are for the full chamber of 100.

**`category_averages.csv` and `suppressed.csv`**

- `line`: the source line.
- `mean`, `min`, `max`, `sd`: the average of the forecasts in the line and
  their spread. Each source counts once.
- `n_sources`: number of forecasts in the average. `n_gated`: how many of
  them come from sources that restrict republication by name.
- `n_retrospective`: how many were computed afterward for a past date.
- `partial`, `n_withheld`: `partial` = 1 marks an average of only the sources
  that can be named, for a cell whose full average is held back;
  `n_withheld` is the number of other forecasts in the full average.
- `tier`: `open` if every forecast in the average can be published by name,
  `gated` otherwise.
- `display`: `ok` (three or more forecasts), `thin` (two) or `single` (one).
- `sole_source`: the source, when there is only one and it can be named.
- `oldest_as_of`, `n_carried`: the publication date of the oldest forecast
  in the average, and how many forecasts were carried forward from the
  source's most recent earlier forecast.
- `reason` (`suppressed.csv` only): why the average is held back.

**`forecasts_by_source.csv`**

- `source_id`, `line`, the identifiers above, `value` and `unit`.
- `as_of`: the date the source published the value.
- `provenance`: `captured` (collected on that day), `archival` (recovered
  from a dated historical record, such as a market's price history or a past
  version of a Wikipedia page), `computed` (calculated by this archive from
  data collected on that day), or `retrospective` (calculated afterward for a
  past date from data as it exists now).

**`timeline.csv`**

- `date`, `line`.
- `quantity`: `margin` (national House margin), `house_seats`,
  `house_prob`, `senate_seats`, `senate_prob`.
- `value`, `low`, `high`: the line's average and the range across its
  forecasts (`band_kind` = `spread`; blank when there is only one forecast).
  `n_sources` as above.

**`approval.csv`**

- `approve`: average approval (%) across the aggregators listed on
  Wikipedia's page of second Trump presidency approval polling;
  `n_aggregators`, `low`, `high` describe that set.

## How the data were collected

Each source is fetched once or twice a day by an automated job. The files
are saved exactly as received, with a SHA-256 hash recorded in
`forecast/data/2026/raw_manifest.csv`, and are kept in a private archive.
Numbers are then read from those saved copies in a separate step, so any
day can be re-read later if a reading error is found.

Sources that publish a web page are read from the page; markets are read
from their public price data; Wikipedia pages are read through the
Wikipedia API. Past values were recovered from market price histories and
from the revision history of Wikipedia pages.

The collection and processing code is in this repository under
`forecast/collect/` and `forecast/model/`.

## Assumptions and processing

- **Averages.** A line's value is the simple mean of the forecasts in it on
  that date, one value per source, using each source's most recent forecast
  as of that date (`n_carried`). The average also moves when a source is
  added or stops updating.
- **Sources that enter partway through.** For DDHQ, RealClearPolitics,
  VoteHub, FiftyPlusOne, Silver Bulletin, 270toWin, Ray Fair and the
  economic pessimism model, the first national margin in the archive is also
  used on the dates before it, so a line does not jump when the source is
  added. These rows have `provenance` = `retrospective`, an `as_of` later than
  the date, and are counted in `n_retrospective`. Seat counts and chances of
  control on those dates are calculated from that margin with the district
  maps in force on each date.
- **Seat counts from a national margin.** Some sources publish only a
  national House vote margin (the polling averages and most academic models).
  For those, this archive computes seat counts and chances of control: each
  district's or state's expected margin is the national margin adjusted by
  that seat's partisan lean, with a normal error, simulated 20,000 times with
  one national error shared by all races. Senate state lean is estimated from
  past election returns; House district lean from district-level election
  results on the current lines.
- **Polling line, Senate.** For the polling line's Senate seats and chance
  of control, a race with its own polling averages (see the next item) uses
  their mean as its expected margin: each aggregator's most recent value
  within the past 60 days. Other races use the national margin and state
  lean as above. The errors are the same for both. From 2026-10-01; earlier
  dates were recomputed the same way.
- **Race-level polling.** A Senate race's polling value is the average of
  the polling averages listed in that race's Wikipedia article.
- **District maps.** Seat counts use the district map in force on each date.
  Several states redrew their maps in 2025 and 2026; Missouri's 2025 map was
  in force from 2025-09-28 until the U.S. Supreme Court blocked it on
  2026-09-10, after which the 2022 lines are used. The dates are in
  `forecast/conditions/redistricting_effective.csv`.
- **Academic models** use the authors' published equations and coefficients
  with inputs collected here (approval, income, the generic ballot), except
  the referendum model, which is refit on past midterms.

## The three-forecast rule

Some forecasters allow their numbers to be collected but restrict
republication during the election. Their forecasts are used inside averages,
and an average that includes any of them is published once at least three
such forecasts are in it. Averages below that number are listed in
`suppressed.csv`.

## License

The data in this folder are released under the Creative Commons Attribution
4.0 International License (CC BY 4.0); see `LICENSE.md`. Values taken from
Wikipedia are also subject to Wikipedia's CC BY-SA 4.0 license. Forecasts
remain the work of their original authors; please credit them. The code
that produces these files is released under the MIT License
(`forecast/LICENSE`).

## Contact

Kevin DeLuca, Department of Political Science, Yale University.
Corrections are welcome.

## Changes

- **0.1** (2026-09-29): first public version.
