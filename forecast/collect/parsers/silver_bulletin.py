"""
Silver Bulletin generic ballot. Publication: AGGREGATE_ONLY.

This is the polling category. Roughly 350 individual generic-ballot polls in a
published-to-web Google Sheet, refreshed near-daily.

WHY AGGREGATE_ONLY
    robots permits collection (GPTBot and Google-Extended are blocked; ClaudeBot
    is not named, and Google explicitly allows /spreadsheets/d/*/pub). But there
    is NO stated licence. He publishes the sheet for reuse; that is not the same
    as granting redistribution. So we may compute and publish a polling average;
    we may not republish his individual poll rows. The tier system enforces it.

THE ARCHIVE VALUE IS THE VINTAGE
    `adjusted_dem` / `adjusted_rep` are model output — house-effect and
    likely-voter corrected — and they are revised RETROACTIVELY as his model
    re-estimates. Nobody keeps the daily vintages, including him. Capturing every
    day is the one thing this archive can offer that the source cannot.

A NOTE ON WHAT THIS NUMBER MEANS
    The raw average is NOT a forecast. Bafumi, Erikson & Wlezien (2006) find
    generic-ballot leads are "effectively halved by Election Day"; Moskowitz
    finds a modern slope nearer 0.87. Shrinkage belongs in the model, not here.
    A parser reports what the source said.

WHICH NUMBER IS `margin_D`, AND WHY IT CHANGED (2026-08-27)
    It used to be OUR unweighted 21-day mean of his RAW poll rows. That is not
    what Silver publishes; it is arithmetic of ours wearing his name, and it is
    the same recipe `polling_reconstructed` already runs, so the polling
    category was carrying our own average twice and calling one of them him.
    Measured over the eight days both existed, it ran about 1.1 points more
    Republican than his published figure.

    `margin_D` is now the ADJUSTED mean: his house-effect and likely-voter
    corrected average, which is the number he publishes and the number the
    Wikipedia aggregator table reports for him (they agree to 0.13 on average,
    and ours is daily and unrounded where Wikipedia's is editor-updated every
    three days or so).

    The old raw mean is still emitted, as `margin_D_raw_poll_mean`. It is in
    NOT_A_FORECAST so it can never reach an average; it stays because the gap
    between raw and adjusted is the house-effect correction and that is worth
    having a daily series of.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import statistics

from . import Context, LoadedArtifact, Row, NATIONAL_HOUSE

# Only polls whose field period ends within this window feed the average.
WINDOW_DAYS = 21


def _f(x):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def _date(x):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(str(x).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    art = artifacts.get("generic_ballot_polls")
    if art is None:
        raise ValueError("generic_ballot_polls artifact missing")

    reader = csv.DictReader(io.StringIO(art.text()))
    cols = reader.fieldnames or []
    for need in ("dem", "rep", "enddate"):
        if need not in cols:
            raise ValueError(
                f"column {need!r} missing — the sheet's shape has changed. "
                f"Columns seen: {cols[:26]}")

    asof = _date(ctx.snapshot_date) or dt.date.today()
    raw, adj = [], []
    n_rows = 0
    for r in reader:
        n_rows += 1
        # "All polls" is his top-level subgroup; the others are LV/RV cuts that
        # would double-count the same poll.
        if (r.get("subgroup") or "All polls").strip() != "All polls":
            continue
        end = _date(r.get("enddate"))
        if end is None or (asof - end).days > WINDOW_DAYS or end > asof:
            continue
        d, rp = _f(r.get("dem")), _f(r.get("rep"))
        if d is not None and rp is not None:
            raw.append(d - rp)
        ad, ar = _f(r.get("adjusted_dem")), _f(r.get("adjusted_rep"))
        if ad is not None and ar is not None:
            adj.append(ad - ar)

    if not raw:
        raise ValueError(
            f"read {n_rows} rows but none fell inside the {WINDOW_DAYS}-day "
            f"window ending {asof}. Either the sheet stopped updating or the "
            f"date format changed.")

    if not adj:
        # HIS NUMBER OR NOTHING. Falling back to the raw mean here would put
        # our own arithmetic back under his name on exactly the days his
        # adjusted columns went missing, which is the failure this parser was
        # rewritten to stop. A missing day costs the polling average one
        # member; a wrong day costs it its meaning.
        raise ValueError(
            f"read {n_rows} rows and found {len(raw)} inside the "
            f"{WINDOW_DAYS}-day window, but none carried adjusted_dem/"
            f"adjusted_rep. margin_D is his ADJUSTED average, so there is "
            f"nothing to report today. Check whether the sheet renamed those "
            f"columns.")

    # HIS PUBLISHED AVERAGE. House-effect and likely-voter corrected; the
    # likely-voter screen alone was worth about 4 points in 2026.
    rows = [ctx.row(art, race_id=NATIONAL_HOUSE, quantity="margin_D",
                    value=round(statistics.fmean(adj), 3), unit="pct")]
    # Kept under its own name as well, because model/polling.py asks for it by
    # name and reads the pair to report what it used.
    rows.append(ctx.row(art, race_id=NATIONAL_HOUSE, quantity="margin_D_adjusted",
                        value=round(statistics.fmean(adj), 3), unit="pct"))
    # Our unweighted mean of his RAW rows. Diagnostic only — NOT_A_FORECAST
    # keeps it out of every average. See the header note.
    rows.append(ctx.row(art, race_id=NATIONAL_HOUSE,
                        quantity="margin_D_raw_poll_mean",
                        value=round(statistics.fmean(raw), 3), unit="pct"))
    return rows
