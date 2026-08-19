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

    rows = [ctx.row(art, race_id=NATIONAL_HOUSE, quantity="margin_D",
                    value=round(statistics.fmean(raw), 3), unit="pct")]
    # The adjusted series is a different quantity and must not be averaged into
    # the raw one — the likely-voter screen alone was worth ~4 points in 2026.
    if adj:
        rows.append(ctx.row(art, race_id=NATIONAL_HOUSE, quantity="margin_D_adjusted",
                            value=round(statistics.fmean(adj), 3), unit="pct"))
    return rows
