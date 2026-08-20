"""
FRED — real disposable personal income per capita. Publication: individual.

WHY THIS SOURCE EXISTS

The fundamentals model takes three inputs and one of them was the number 1.5,
hardcoded, with a comment saying PLACEHOLDER. That number was reaching the
public page as part of "D+9.5", presented with an 80% interval and the words
"fit on 20 midterms, 1946-2022". The fit is real; one of the inputs was not.

WHAT QUANTITY, EXACTLY

The model was fitted on the election year's year-over-year percentage change in
FRED series A229RX0A048NBEA, Real Disposable Personal Income: Per Capita,
annual, chained 2017 dollars. Verified rather than assumed:

    2021 annual  51,888
    2022 annual  48,716
    change       (48716 - 51888) / 51888 * 100 = -6.113

and HISTORY in fundamentals.py records 2022 as -6.11. That is the definition,
to two decimal places.

THE AWKWARD PART, AND WHY IT IS NOT FUDGED HERE

The 2026 annual observation will not exist until 2027. So the election-year
value the model wants is, by construction, unavailable on election day — for
2026 and for every cycle this model will ever be run in. Something has to
stand in for it, and the choice matters more than it looks:

  · last COMPLETE year (2025) — a real annual figure, but measures the year
    before the election, which is not the fitted quantity
  · latest MONTH year-over-year — current, but a point reading against an
    annual average, and noisy
  · year-to-date average vs the prior full year — same shape as the fitted
    quantity (an average against an average), uses everything known so far

This parser emits ALL THREE, as separate quantities, and lets the model choose.
That is deliberate: the substitution is a judgment call, and burying it inside
one number labelled "income" is how a placeholder becomes invisible in the
first place. The model uses income_growth_ytd and says so in its output.

MONTHLY IS SEASONALLY ADJUSTED, ANNUAL IS NOT. A229RX0 is SAAR; the annual
A229RX0A048NBEA is NSA. Averaging twelve SA months approximates the NSA annual
figure closely but not identically, so the ytd estimate carries a little slop
that the last-complete-year figure does not. Worth a sentence on the methods
page rather than a footnote here.

LICENCE: the underlying data is U.S. Bureau of Economic Analysis, tagged on
FRED as "Public Domain: Citation Requested". Redistributable with attribution.
ROBOTS: fred.stlouisfed.org allows the wildcard with Crawl-delay 1 and
disallows only /graph/graph-landing.php, /graph/image.php, /graph/fredgraph.png,
/searchresults and /seriesBeta. The .csv endpoint we use is NOT disallowed.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict

from . import Context, LoadedArtifact, Row

# FRED has used both "DATE" and "observation_date" as the first column header
# over the years, and the value column is named after the series id. So bind by
# POSITION rather than by name: column 0 is the date, column 1 is the value.
# A missing observation is a literal "." — not an empty string, not a NaN.
_MISSING = {".", "", "NA", "NaN"}
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _series(art: LoadedArtifact) -> dict[str, float]:
    """date-string -> value, for one fredgraph.csv artifact."""
    text = art.text()
    rdr = csv.reader(io.StringIO(text))
    try:
        header = next(rdr)
    except StopIteration:
        return {}
    if len(header) < 2:
        return {}
    out: dict[str, float] = {}
    for row in rdr:
        if len(row) < 2:
            continue
        d, v = row[0].strip(), row[1].strip()
        if not _DATE.match(d) or v in _MISSING:
            continue
        try:
            out[d] = float(v)
        except ValueError:
            continue
    return out


def _annual_growth(series: dict[str, float]) -> dict[int, float]:
    """year -> YoY % change, from an annual series."""
    by_year = {int(d[:4]): v for d, v in series.items()}
    return {y: (by_year[y] - by_year[y - 1]) / by_year[y - 1] * 100
            for y in sorted(by_year) if (y - 1) in by_year and by_year[y - 1]}


def _monthly_by_year(series: dict[str, float]) -> dict[int, list[float]]:
    out: dict[int, list[float]] = defaultdict(list)
    for d, v in sorted(series.items()):
        out[int(d[:4])].append(v)
    return out


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    annual: dict[str, float] = {}
    monthly: dict[str, float] = {}
    origin: LoadedArtifact | None = None
    for art in artifacts.values():
        obs = _series(art)
        if not obs:
            continue
        origin = origin or art
        # Annual observations are all dated January; a monthly series is not.
        months = {d[5:7] for d in obs}
        (annual if months <= {"01"} else monthly).update(obs)

    if not annual and not monthly:
        raise ValueError(
            f"read {len(artifacts)} FRED artifact(s) but parsed no observations. "
            f"fredgraph.csv should be date,value with '.' for missing. "
            f"Check the stored file — a login wall or an error page would land here.")

    rows: list[Row] = []

    def add(q, v, unit="pct"):
        rows.append(ctx.row(origin, race_id="NATL_HOUSE_2026", chamber="national",
                            state="", district="", quantity=q,
                            value=round(v, 3), unit=unit))

    # 1. Last COMPLETE calendar year's growth. A real annual figure.
    if annual:
        g = _annual_growth(annual)
        if g:
            add("income_growth_last_full_year", g[max(g)])

    # 2. Year-to-date average vs the prior full year. Same shape as the fitted
    #    quantity, and the one the model actually uses.
    if monthly:
        by_year = _monthly_by_year(monthly)
        years = sorted(by_year)
        if len(years) >= 2:
            cur = years[-1]
            prev_full = cur - 1
            if prev_full in by_year and by_year[prev_full]:
                cur_avg = sum(by_year[cur]) / len(by_year[cur])
                prev_avg = sum(by_year[prev_full]) / len(by_year[prev_full])
                if prev_avg:
                    add("income_growth_ytd", (cur_avg - prev_avg) / prev_avg * 100)
                    # How much of the year is in hand? A 2-month YTD is a much
                    # weaker estimate than an 11-month one, and the model
                    # should be able to say so rather than the reader guessing.
                    add("income_ytd_months", float(len(by_year[cur])), "count")

        # 3. Latest month vs the same month a year earlier.
        ds = sorted(monthly)
        if ds:
            latest = ds[-1]
            prior = f"{int(latest[:4]) - 1}{latest[4:]}"
            if prior in monthly and monthly[prior]:
                add("income_growth_yoy_latest_month",
                    (monthly[latest] - monthly[prior]) / monthly[prior] * 100)

    if not rows:
        raise ValueError(
            "FRED artifacts parsed but produced no growth figures — need at "
            "least two consecutive periods in at least one series.")
    return rows
