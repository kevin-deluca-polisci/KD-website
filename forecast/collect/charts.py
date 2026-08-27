#!/usr/bin/env python3
"""
Timeline accumulation and chart geometry for the public page.

TWO JOBS, AND THE FIRST ONE IS THE IMPORTANT ONE.

1. ACCUMULATE. The point of this archive is watching methods move: do
   fundamentals, polling, the professionals and the markets converge as the
   election approaches, or does one of them sit systematically high all year?
   You cannot answer that from a snapshot, and until now nothing kept the
   history.

   category_averages.csv already accumulates by date, so the professional and
   market series have a past. Our OWN models did not: fundamentals_model.json
   and polling_model.json are overwritten on every run, so each day destroyed
   the previous day's answer. Every day that went by without this file was a
   day of the series permanently lost. That is why this exists.

   timeline.csv is therefore append-only and idempotent: one row per
   (date, series), rewritten if the same date is recomputed, never duplicated.

2. LAY OUT. Turn that series into plot coordinates here, in Python, rather
   than doing arithmetic in Go templates. The template should place elements,
   not compute scales.

COLOUR IS NOT DECORATION HERE. The four hues are assigned per ENTITY and fixed
for the life of the project, so "professional" is the same amber in the margin
panel and the probability panel. Rotating colour by position in the chart would
mean a series changed colour when another was added, which is the fastest way
to make a time series lie. The set was validated for colour-vision deficiency
(worst adjacent pair ΔE 16.2 protan) rather than chosen by eye. Amber and
magenta fall below 3:1 against the light surface, which is why every series is
directly labelled and a table view ships alongside.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import facets   # noqa: E402

TIMELINE_FIELDS = ["snapshot_date", "series", "panel", "unit",
                   "value", "low", "high", "band_kind", "n_sources", "label"]

# The panels the site knows how to draw, and the ONLY panels timeline.csv is
# allowed to keep. Anything else is a leftover from a previous shape of the
# file — the probability panel used to be called "prob" before it split by
# chamber — and a stale panel is worse than a missing one, because it sits in
# the accumulated history looking like data.
PANELS = ("margin", "house_seats", "house_prob", "senate_seats", "senate_prob")

# Where every series on the site begins: inauguration day, the start of the
# term these midterms are a referendum on. See model/academic.py, which carries
# the same constant on the generation side and must agree with this one.
#
# THIS IS A FRAME, NOT A DELETION. Dates before it stay in raw/, in parsed/ and
# in the published category_averages.csv; they are simply not drawn. Applying
# it here rather than in aggregate.py is deliberate — the archive keeps
# everything it ever computed, and only the chart takes a view about where the
# story starts.
#
# It is enforced in two places for one reason: the generation floor decides
# where NEW points begin, and this one drops points an earlier version already
# wrote. timeline.csv accumulates and its self-healing pass is additive, so
# without the pruning below, two rows from January 2025 would sit at the left
# edge of the chart forever.
SERIES_START = "2025-01-20"

# Chamber x metric, which is what the tracker toggles between. Kept here rather
# than in the template so the axis labels and the units travel with the data.
VIEWS = {
    "house_seats":  {"chamber": "house",  "metric": "seats",
                     "label": "Democratic House seats", "unit": "seats",
                     "reference": 218, "reference_label": "218 — a majority",
                     "total": 435},
    "house_prob":   {"chamber": "house",  "metric": "prob",
                     "label": "Chance of a Democratic House", "unit": "prob",
                     "reference": 0.5, "reference_label": "even"},
    "senate_seats": {"chamber": "senate", "metric": "seats",
                     "label": "Democratic Senate seats", "unit": "seats",
                     "reference": 51, "reference_label": "51 — a majority",
                     "total": 100},
    "senate_prob":  {"chamber": "senate", "metric": "prob",
                     "label": "Chance of a Democratic Senate", "unit": "prob",
                     "reference": 0.5, "reference_label": "even"},
}

# entity -> (light, dark). Fixed. Never reassign by position.
#
# ACADEMIC'S TEAL WAS CHOSEN BY THE VALIDATOR, NOT BY EYE, and the result is
# worth writing down because it constrains what may be added later.
#
# In LIGHT mode #0077a8 clears every check against the existing four on an
# all-pairs test: worst normal-vision separation 18.4, worst CVD 9.1 protan,
# inside the lightness band, above the chroma floor. That one is clean.
#
# In DARK mode it is NOT clean, and no fifth hue is. The dark ramp's lightness
# band is roughly L 0.48-0.67, four hues already occupy it, and a sweep of the
# full hue circle at three lightnesses and three chromas found nothing that
# clears the 15-point normal-vision floor against all four. #2f9fbd is the best
# available and sits 11.8 from polling's green. That is below the floor, so
# COLOUR ALONE DOES NOT SEPARATE THESE SERIES IN DARK MODE and the charts must
# not ask it to: every series is directly labelled at its last point, every
# comparison mark carries a distinct SHAPE from glyph.html, and a table view
# ships alongside. Those are the secondary encodings that make the pair legal.
#
# ALREADY BROKEN BEFORE ACADEMIC ARRIVED, and someone should fix it: in dark
# mode #d55181 (market) and #199e70 (polling) are ΔE 1.6 apart under
# deuteranopia — indistinguishable, not merely close. Academic did not cause
# this and cannot fix it; it needs one of those two hues re-stepped.
#
# THE REAL CONCLUSION: five is the ceiling for this ramp. A sixth category must
# come with a re-stepped dark palette or it must not come as a hue at all.
COLORS = {
    "fundamentals": ("#4a3aa7", "#9085e9"),
    "polling":      ("#008300", "#199e70"),
    "professional": ("#eda100", "#c98500"),
    "market":       ("#e87ba4", "#d55181"),
    "academic":     ("#0077a8", "#2f9fbd"),
}
# Labels name the METHOD, not who built it. A chart comparing four ways of
# forecasting the same number should put them on equal footing; tagging two of
# them "(class model)" in the legend made the axis look like it was about
# provenance rather than method.
# Both facets. See facets.py for what type and source mean and why the field
# had to be split; `market` appears in both by design and carries the same
# average either way.
LABELS = {**facets.TYPE_LABEL, **facets.SOURCE_LABEL}
FACET_OF = {**{g: "type" for g in facets.TYPE_ORDER},
            **{g: "source" for g in facets.SOURCE_ORDER
               if g not in facets.TYPE_ORDER},
            "market": "both"}
# Least modelled to most modelled, matching CATEGORY_ORDER in publish.py. The
# two lists are written out separately because charts.py and publish.py do not
# import each other; if you reorder one, reorder the other, or the legend and
# the table disagree about what order the reader is being asked to think in.
ORDER = facets.TYPE_ORDER + [g for g in facets.SOURCE_ORDER
                             if g not in facets.TYPE_ORDER]

# Time windows the tracker offers, and the one it opens on.
#
# THE DEFAULT IS AN ABSOLUTE DATE, NOT A ROLLING WINDOW, and that is the point
# of it. A rolling sixty days draws the same amount of chart in March as in
# October; "since 2026" grows as the cycle does, so the axis itself shows how
# much of the election year is left. It is also the window every other number
# on this site is about — the forecast is for November 2026, not for the last
# two months.
#
# The rolling windows stay, because "what has moved lately" is a different
# question and 30/60 answer it better. "All" stays because the backfill reaches
# to January 2025 and a reader should be able to see the whole thing. Neither
# is what the page should open on.
#
# An entry's second element is EITHER an int (days back from the newest date)
# or an ISO date string (an absolute floor). build_panel takes both.
RANGES = [("30", 30), ("60", 60), ("2026", "2026-01-01"), ("all", None)]
RANGE_LABELS = {"30": "30 days", "60": "60 days",
                "2026": "Since 2026", "all": "All"}
RANGE_DEFAULT = "2026"
RANGE_DEFAULT_DAYS = "2026-01-01"


def _rd(p: Path) -> list[dict]:
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def collect_today(derived: Path, snapshot: str) -> list[dict]:
    """Everything we can say about today, one row per series.

    VALUES come from category_averages.csv for every category, ours included.
    They used to be split: the two class models were read straight out of their
    JSON while professional and market came from the averages. That was fine
    only while a category held exactly one model — the moment fundamentals
    holds two, this chart would have plotted our model and the comparisons
    table would have shown the average of both, and the same page would have
    carried two different numbers under one name.

    BARS are the spread between contributing sources, and nothing else. This
    chart asks one question — how far apart are the methods today — and a bar
    that answered a different question while looking identical made the chart
    harder to read rather than richer. A category with one contributor gets no
    bar, which is the honest report: one source cannot disagree with itself.

    Model intervals are not lost, they have moved. Each model still states its
    own 80% interval in its JSON and the methods page renders it there, beside
    the assumptions that produce it, where it can be explained rather than
    silently compared against something it is not.
    """
    out = []

    # THE MODEL-INTERVAL LOOKUP THAT USED TO LIVE HERE IS GONE.
    #
    # It read each model's own 80% interval out of fundamentals_model.json,
    # polling_model.json and seat_projections.json so a single-source category
    # could be drawn with a bar. That bar answered a different question from
    # every other bar on the chart — see the note in the loop below — so this
    # panel no longer draws it, and the lookup has no remaining reader.
    # The intervals themselves are unchanged and still published: the methods
    # page reads them straight from those files, which is where a reader can
    # see them next to the assumptions that produce them.

    # The Senate probability here is the chance of CONTROL, which needs 51
    # seats — which is why class_model_rows emits prob_D_51_plus and not the
    # better-known 50+ figure. A chart is not allowed to put two different
    # events on one axis because they happen to share a name.
    PANEL_OF = {
        ("NATL_HOUSE_2026", "seats_D"): "house_seats",
        ("NATL_HOUSE_2026", "win_prob_D"): "house_prob",
        ("NATL_SENATE_2026", "seats_D"): "senate_seats",
        ("NATL_SENATE_2026", "win_prob_D"): "senate_prob",
        ("NATL_HOUSE_2026", "margin_D"): "margin",
    }
    for r in _rd(derived / "category_averages.csv"):
        # THE FRAME, APPLIED WHERE THE ROWS ARE MADE. This function reads the
        # whole averages file rather than one date — its rows carry their own
        # snapshot_date and the caller keys on it — so a floor applied only at
        # the caller is undone the moment the caller calls this again. See
        # SERIES_START.
        if r.get("snapshot_date", "") < SERIES_START:
            continue
        cat, q = r["category"], r["quantity"]
        # THE TIMELINE PLOTS THE SIMPLE MEAN. It used to plot the chained
        # level, and the reason for the change is worth keeping.
        #
        # Chaining answers a real question. Category membership changes —
        # models get added, a gated source crosses the disclosure floor, a
        # family gains a member because a model belongs to two — and every one
        # of those moves the simple mean without anyone's forecast having
        # changed. Chaining carries the level forward using only sources
        # present on both dates, so a movement on a chained line is a movement
        # in somebody's opinion. That is a genuinely better answer to "how much
        # of this is real".
        #
        # IT IS THE WRONG ANSWER TO "WHAT DOES THIS FAMILY PREDICT", and that
        # is the question a tracker is read for. The two coincide only while
        # membership is stable. On 2026-08-20 the fundamentals family went from
        # one member to five in a day; the chain, correctly by its own rules,
        # moved on the single source the two days had in common and held the
        # level where a one-member family had left it. Five days later the
        # published fundamentals margin was D+2.09 while the mean of its five
        # members was D+7.49 — a number that was no longer an average of
        # anything, sitting next to a seat count of 239 that no D+2 environment
        # produces.
        #
        # Two further faults, neither fixable by tuning the chain. Each
        # quantity is chained independently, and seats is a non-linear function
        # of margin, so a chained margin and a chained seat count stop
        # corresponding by construction. And the interval drawn beside the
        # point is the min and max of the UNCHAINED members, so the marker
        # could sit against the floor of its own band.
        #
        # So the level published everywhere is now the mean, the timeline and
        # the comparison table finally agree, and a composition change shows up
        # as the step it actually is rather than being smoothed away.
        #
        # The chain was retired outright on 2026-08-27 and `mean_chained` is no
        # longer a column, so there is nothing to fall back to: a row without a
        # mean is a row with no level, and it is skipped. aggregate.py carries
        # the full reasoning. about.html still explains why a step in one of
        # these lines is sometimes bookkeeping rather than news — read the
        # member count beside it.
        v = _f(r["mean"])
        if v is None or cat not in LABELS:
            continue
        panel = PANEL_OF.get((r["race_id"], q))
        if not panel:
            continue
        try:
            n = int(r.get("n_sources") or 1)
        except (TypeError, ValueError):
            n = 1
        # ONE KIND OF BAR ON THIS CHART: THE SPREAD BETWEEN SOURCES.
        #
        # This panel used to draw two different objects with identical weight,
        # and they are not comparable. A model's 80% interval says how unsure
        # one model is. A source spread says how far apart several sources are.
        # Drawn side by side they invite a reading that is not merely imprecise
        # but backwards: polling seats carried our model's interval, 208-255,
        # while polling margin carried the spread between four aggregators,
        # 5.9-6.4. A reader compares the two rows and concludes polling is
        # wildly unsure about seats and certain to a quarter-point about the
        # margin. In fact the same model puts an 80% interval of D+0.6 to
        # D+10.8 on that margin — forty times the width of the bar that was
        # drawn beside it. Four aggregators averaging the same polls agree
        # because they are reading the same data, and that agreement is not
        # evidence about November.
        #
        # So this chart now answers exactly one question — how much do the
        # methods disagree today — and every bar on it is the same object.
        # A model's own uncertainty is a different question and belongs where
        # it can be explained: the methods page states each model's interval
        # beside the assumptions that produce it.
        #
        # `band_kind` still travels with the row. Rows written before this
        # column existed carry no kind and the template declines to name them,
        # rather than relabelling old data as something it was not.
        low = high = ""
        kind = ""
        if n > 1:
            lo_, hi_ = _f(r.get("min")), _f(r.get("max"))
            if lo_ is not None and hi_ is not None:
                low, high, kind = lo_, hi_, "spread"
        out.append(dict(
            snapshot_date=r["snapshot_date"], series=cat, panel=panel,
            unit=("prob" if panel.endswith("prob")
                  else "pct" if panel == "margin" else "seats"),
            value=round(v, 4), low=low, high=high, band_kind=kind,
            n_sources=n, label=LABELS[cat]))
    return out


def _all_snapshot_dates(derived: Path) -> list[str]:
    return sorted({r["snapshot_date"] for r in _rd(derived / "category_averages.csv")
                   if r.get("snapshot_date")
                   and r["snapshot_date"] >= SERIES_START})


def update_timeline(derived: Path, snapshot: str, rebuild: bool = False) -> list[dict]:
    """Merge today's rows into timeline.csv. Idempotent on (date, series, panel).

    WHY REBUILD EXISTS, and it is a real gap rather than a convenience.

    This file accumulates. Every run adds one date and leaves the rest alone,
    which is exactly right for a tracker whose past cannot be recomputed. But
    it means the timeline can only ever know about dates on which a run
    happened — and when a category acquires HISTORY by some other route, the
    chart is the last thing to hear about it and never catches up on its own.

    That is precisely what the academic backfill did. academic.py reconstructs
    BEW back to January 2025 from the poll-level record, seats.py projects each
    of those dates, and aggregate.py writes them all into
    category_averages.csv. The comparison table saw them immediately. The
    "what has moved" card saw them immediately, because it reads the averages
    directly. The chart saw one point, because timeline.csv had only ever been
    told about today — so the site simultaneously reported a 598-day academic
    history in one card and a single dot in the panel above it.

    Rebuild replays collect_today() over every date the averages know about.
    It is pure CSV work with no simulation behind it, so it costs a second and
    is safe to re-run: each date's row is derived from that date's published
    averages, which is where the authority lives anyway.

    Not the default. On an ordinary day the accumulate-forward path is correct
    and cheaper, and a rebuild would quietly rewrite rows that a past run wrote
    from data we may no longer hold.
    """
    path = derived / "timeline.csv"
    rows = {(r["snapshot_date"], r["series"], r["panel"]): r for r in _rd(path)
            if r["panel"] in PANELS and r["snapshot_date"] >= SERIES_START}
    if rebuild:
        # FORCE: every date, existing rows overwritten from the averages.
        dates = _all_snapshot_dates(derived)
        print(f"  timeline: rebuilding from {len(dates)} snapshot date(s) in "
              f"category_averages.csv")
    else:
        # SELF-HEALING BY DEFAULT, and additively. Today's date always, plus
        # any date the averages know about that this file has never recorded.
        #
        # The academic backfill is the case this exists for, and leaving it to
        # a flag somebody has to remember was not good enough: the site spent a
        # day reporting a 598-day academic history in one card and a single dot
        # in the chart above it, because the two read different files and only
        # one of them had been told. A gap that a cheap CSV pass can close
        # should not wait for a human to notice it.
        #
        # ADDITIVE ONLY. Dates already in timeline.csv are left exactly as the
        # run that wrote them left them, because that run may have seen inputs
        # we no longer hold. This fills holes; it does not revise history. The
        # --rebuild-timeline flag is there for when revision is what you want.
        known = {d0 for d0, _s, _p in rows}
        missing = [d0 for d0 in _all_snapshot_dates(derived) if d0 not in known]
        dates = [snapshot] + missing
        if missing:
            print(f"  timeline: {len(missing)} date(s) present in the averages "
                  f"and absent here — filling them in "
                  f"({missing[0]} to {missing[-1]})")

    # AND IT PRUNES, WHICH IS NEW AND IS THE OTHER HALF OF SELF-HEALING.
    #
    # Filling holes was only ever half the job. A family can also STOP being
    # published — a licence changes, a source is withdrawn, a contributor drops
    # below the disclosure floor — and an append-only file has no way to say so.
    # Its last few points simply stay on the chart, at the right-hand edge,
    # looking like the newest thing we know rather than the last thing we knew.
    #
    # The professional family is the case this exists for. Its last publishable
    # contributor moved to the private tier on 2026-08-22, aggregate.py stopped
    # emitting a professional average that same run, and without this the
    # professional line would have gone on being drawn from four stale rows
    # indefinitely.
    #
    # THE RULE, and it is narrow on purpose: a row is dropped only when the
    # averages HAVE that date and do not have that series on it. A date the
    # averages do not cover at all is left completely alone, because those rows
    # cannot be recomputed and a past run may have seen inputs we no longer
    # hold — which is the objection the additive-only note above records, and it
    # still stands for exactly those rows.
    #
    # What makes this safe rather than reckless is upstream: aggregate.py
    # refuses to write a category_averages.csv that loses a category from a date
    # unless someone passes --force. So a series can only disappear from the
    # averages deliberately, and this follows that decision rather than making
    # one of its own.
    truth = {(r["snapshot_date"], r["series"], r["panel"])
             for r in collect_today(derived, snapshot)}
    covered = set(_all_snapshot_dates(derived))
    stale = [k for k in rows if k[0] in covered and k not in truth]
    if stale:
        by_series: dict[str, int] = {}
        for _d0, series, _p in stale:
            by_series[series] = by_series.get(series, 0) + 1
        print("  timeline: dropping " + ", ".join(
            f"{n} stale {s} row(s)" for s, n in sorted(by_series.items()))
            + " no longer present in the averages")
        for k in stale:
            rows.pop(k, None)
    for d0 in dates:
        for r in collect_today(derived, d0):
            rows[(r["snapshot_date"], r["series"], r["panel"])] = r
    ordered = sorted(rows.values(), key=lambda r: (r["panel"], r["snapshot_date"],
                                                   ORDER.index(r["series"])
                                                   if r["series"] in ORDER else 9))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TIMELINE_FIELDS)
        w.writeheader()
        for r in ordered:
            w.writerow({k: r.get(k, "") for k in TIMELINE_FIELDS})
    return ordered


# --------------------------------------------------------------------------
# Geometry. Output is in a 0-100 x 0-100 box; the template scales it.
# --------------------------------------------------------------------------


def _date_ticks(dates: list[str], X) -> list[dict]:
    """Calendar ticks on a time axis: the two ends, plus month boundaries.

    Interior ticks used to be placed at thirds of the INDEX, which on an
    unevenly sampled series put the middle label nowhere near the middle of the
    time span — on the 578-day view it read 2026-02-21 at x=33, thirteen months
    in. On a time axis the tick can be any calendar date, so it may as well be
    a round one.

    The step widens until the labels stop crowding: month, two months, quarter,
    half-year, year. Interior ticks landing within 7% of an end are dropped,
    since the end labels are anchored outward and would collide.

    The year is printed only when it changes, which is the difference between
    "2025-01-22 2025-04-01 2025-07-01" and "22 Jan 2025 · Apr · Jul".
    """
    d0, dn = dt.date.fromisoformat(dates[0]), dt.date.fromisoformat(dates[-1])
    ends = [
        {"date": dates[0], "x": round(X(dates[0]), 2), "anchor": "start",
         "label": d0.strftime("%b %Y")},
        {"date": dates[-1], "x": round(X(dates[-1]), 2), "anchor": "end",
         "label": dn.strftime("%b %Y")},
    ]
    if (dn - d0).days < 45:
        # Short window: the ends carry it, and month boundaries would be one
        # tick or none.
        return ends if len(dates) > 1 else [{**ends[0], "x": 50.0,
                                             "anchor": "middle"}]

    for step in (1, 2, 3, 6, 12):
        marks, y, m = [], d0.year, d0.month
        # First month boundary strictly after the start.
        m += 1
        if m > 12:
            m, y = 1, y + 1
        while True:
            if (m - 1) % step:
                m += 1
                if m > 12:
                    m, y = 1, y + 1
                continue
            cur = dt.date(y, m, 1)
            if cur >= dn:
                break
            marks.append(cur)
            m += step
            while m > 12:
                m, y = m - 12, y + 1
        if len(marks) <= 5:
            break

    out, last_year = list(ends[:1]), d0.year
    for cur in marks:
        x = X(cur.isoformat())
        if x < 7 or x > 93:
            continue                      # would collide with an end label
        label = cur.strftime("%b %Y") if cur.year != last_year else cur.strftime("%b")
        last_year = cur.year
        out.append({"date": cur.isoformat(), "x": round(x, 2),
                    "anchor": "middle", "label": label})
    out.append(ends[1])
    return out


def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Round tick values strictly INSIDE [lo, hi].

    REWRITTEN when the axis was tightened, because the old version was built
    for a loose one. It generated ticks with half a step of slack past both
    ends, on the assumption that the caller would widen the domain to meet
    them — which is exactly what the caller used to do. Once the domain was
    fitted to the data and out-of-range ticks were dropped instead, that slack
    turned into missing labels: a panel could end up with two ticks, or one,
    placed wherever the rounding happened to land.

    It also picked the magnitude with `len(str(int(raw)))`, which collapses to
    0.1 for anything below 1. On a probability panel spanning eight points that
    offered a choice between a 10-point step and a 20-point step, so a reader
    got one gridline and no sense of scale.

    Now: magnitude by log10, the usual 1/2/2.5/5 progression, ticks generated
    only where they fall inside the domain, and a step down to a finer interval
    if the first choice yields fewer than three labels.
    """
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(n, 1)
    if raw <= 0:
        return [lo]
    mag = 10.0 ** math.floor(math.log10(raw))
    # Coarse to fine, so the first match is the largest step that still gives
    # about n intervals, and the fallbacks below get progressively finer.
    candidates = [m * mag for m in (10, 5, 2.5, 2, 1)]
    candidates += [m * mag / 10.0 for m in (5, 2.5, 2, 1)]
    chosen = next((c for c in candidates if c >= raw), candidates[-1])

    def gen(step: float) -> list[float]:
        if step <= 0:
            return []
        first = math.ceil(lo / step - 1e-9) * step
        out, v = [], first
        # A hard cap: a pathological step must not spin here.
        while v <= hi + 1e-9 and len(out) < 40:
            out.append(round(v, 6))
            v += step
        return out

    ticks = gen(chosen)
    if len(ticks) < 3:
        for c in candidates[candidates.index(chosen) + 1:]:
            finer = gen(c)
            if len(finer) >= 3:
                ticks = finer
                break
            if finer:
                ticks = finer
    return ticks


def build_panel(rows: list[dict], panel: str,
                window: int | str | None = None) -> dict | None:
    """One panel's geometry. `window` trims the series before any geometry.

    It is EITHER an int — that many days back from the newest date in the
    panel, a rolling window — OR an ISO date string, an absolute floor. The two
    kinds are not interchangeable and the tracker offers both: rolling for
    "what moved lately", absolute for "this election year".

    THE WINDOW IS WHY THE AXIS CAN BE TIGHT. Fitting the scale to the data was
    only half the problem: once the backfill gave some series six hundred days,
    the honest full-range axis had to span every value any method has held
    since January 2025, and the last fortnight — the part anyone is actually
    reading — was compressed into a band a few pixels tall. Trimming the dates
    first makes the axis narrow because the DATA is narrow, which is a better
    fix than squeezing the frame around a wide series.

    Panels are built once per window and the page hides all but one, the same
    way the chamber and metric toggles already work. The arithmetic stays in
    Python; the template only shows and hides.
    """
    rs = [r for r in rows if r["panel"] == panel]
    if not rs:
        return None
    if window is not None:
        all_dates = sorted({r["snapshot_date"] for r in rs})
        if all_dates:
            if isinstance(window, str):
                cutoff = window
            else:
                import datetime as _dt
                cutoff = (_dt.date.fromisoformat(all_dates[-1])
                          - _dt.timedelta(days=window)).isoformat()
            trimmed = [r for r in rs if r["snapshot_date"] >= cutoff]
            # A window that would empty the panel is ignored rather than
            # rendered blank: better to show the whole short series than an
            # empty frame captioned "last 30 days".
            if trimmed:
                rs = trimmed
    dates = sorted({r["snapshot_date"] for r in rs})
    di = {d: i for i, d in enumerate(dates)}   # for the hover payload, below
    vals = [_f(r["value"]) for r in rs if _f(r["value"]) is not None]
    # Only the LAST point's interval widens the axis, because only the last
    # point's interval is drawn. Letting every historical band into the range
    # calculation blew the axis out to EVEN..D+20 for data that lived between
    # D+5 and D+13, squashing the entire story into the middle third of the
    # plot. Found by rendering 74 simulated days and looking at it.
    bands = []
    for key in {r["series"] for r in rs}:
        pts = sorted((r for r in rs if r["series"] == key), key=lambda r: r["snapshot_date"])
        for k in ("low", "high"):
            v = _f(pts[-1].get(k))
            if v is not None:
                bands.append(v)
    view = VIEWS.get(panel, {})
    unit = view.get("unit", "pct")
    lo, hi = min(vals + bands), max(vals + bands)
    # A reference line only widens the axis when it is close enough to be worth
    # drawing. Forcing 218 into view when every series sits near 250 would push
    # the interesting part of the chart into the top third; leaving it out when
    # a series is about to cross it would hide the only thing that matters.
    # THE AXIS IS AS TIGHT AS THE DATA ALLOWS, and that is a deliberate change
    # from the generous version this replaced.
    #
    # This chart exists to show how far apart the methods are. A House seats
    # axis running 190-274 for data that lives between 201 and 263 spends a
    # quarter of its height on emptiness, and every gap between series shrinks
    # in proportion. The old settings padded by 18% of the span on each side
    # and then let the tick rounding widen it again — two independent reasons
    # for the plot to be bigger than its data.
    #
    # Now: one seat of headroom on a seats panel, one point on a probability
    # panel, and the ticks are fitted INSIDE the domain rather than allowed to
    # stretch it. The proportional term stays small so that a genuinely wide
    # spread is not squeezed against the frame.
    data_lo, data_hi = lo, hi
    ref = view.get("reference")

    # THE REFERENCE LINE NO LONGER GETS TO WIDEN THE AXIS MUCH. 218 seats is
    # the most meaningful annotation on a seats chart and it used to be pulled
    # into view from up to 60% of the span away, which on a tight axis means
    # dragging the whole plot sideways to draw one line. It now joins only when
    # it is genuinely near the data; otherwise it is dropped and the panel says
    # so, which is more honest than a chart silently missing its own baseline.
    ref_offscreen = False
    if ref is not None:
        span = max(hi - lo, 1e-9)
        if lo - span * 0.2 <= ref <= hi + span * 0.2:
            lo, hi = min(lo, ref), max(hi, ref)
        else:
            ref_offscreen = True

    # One seat, one point, three tenths of a margin point. The proportional
    # term is only a floor for very wide ranges and is deliberately tiny — it
    # exists so a series sitting exactly at the extreme still has room for its
    # direct label, not to give the plot breathing space it does not need.
    pad = max((hi - lo) * 0.02,
              {"pct": 0.3, "prob": 0.01, "seats": 1.0}.get(unit, 0.3))
    lo, hi = lo - pad, hi + pad
    if unit == "prob":
        lo, hi = max(0.0, lo), min(1.0, hi)
        # SNAP TO THE CERTAINTY BOUND WHEN THE SERIES GETS NEAR IT.
        #
        # A probability axis has two ends that mean something, and a reader
        # judging "how close to certain is this" needs the certain end
        # labelled. Fitting the domain to the data alone loses it: the House
        # panel peaked at 0.966, the padded top came to 0.976, the 100% tick
        # fell outside and was dropped, and the chart showed a line running
        # into the top of the frame with 80% as its last reference. That reads
        # as "somewhere above 80" when the answer is "almost certain".
        #
        # So when the domain already reaches within one tick step of 0 or 1,
        # the bound is pulled in and the label comes with it. The guard is the
        # step rather than a fixed distance, because the step is what decides
        # whether a label would appear anyway. A series living between 40% and
        # 60% is untouched -- it is nowhere near either end, and stretching its
        # axis to the full range would flatten the movement the panel exists
        # to show.
        #
        # THE THRESHOLD IS IN PROBABILITY, NOT IN TICK STEPS. The first
        # version tested "within one tick step of the bound", which sounds
        # principled and is not: the step on a wide probability panel is 0.25,
        # so the guard reached down from 0.75 and would snap a panel whose
        # whole range sat in the low eighties. Five points is a threshold in
        # the units of the thing being measured, and a probability five points
        # from certain is near enough to certain that a reader needs the
        # ceiling to judge against.
        #
        # data_lo and data_hi INCLUDE THE LAST POINT'S INTERVAL, and that is
        # the right quantity rather than an oversight. The band is drawn, so
        # the axis has to hold it; on 2026-08-24 the House probability band
        # ran 0.31 to 0.9855 while the four point estimates sat between 0.51
        # and 0.81. Testing the points alone would have left the drawn band
        # running into an unlabelled top edge, which is the fault this is
        # here to fix.
        #
        # Each bound is tested on its own, so a panel that hugs zero does not
        # also get a ceiling it never approaches. The Senate panel's band runs
        # 0.057 to 0.784 and is left exactly as it was.
        NEAR_CERTAIN = 0.05
        if data_hi >= 1.0 - NEAR_CERTAIN:
            hi = 1.0
        if data_lo <= NEAR_CERTAIN:
            lo = 0.0
    # A flat series would otherwise give a zero-height domain and a division by
    # zero in Y(). One point of room either side is enough to draw a line.
    if hi - lo < 1e-6:
        step = {"prob": 0.01, "seats": 1.0}.get(unit, 0.3)
        lo, hi = lo - step, hi + step
    # Ticks are FITTED, not stretched to. Anything outside the domain would
    # render off-canvas anyway, so it is dropped rather than allowed to pull
    # the axis out to meet it.
    ticks = [t for t in _nice_ticks(lo, hi) if lo <= t <= hi]

    # THE X AXIS IS TIME, NOT THE RANK OF A DATE AMONG THE DATES WE HAPPEN TO
    # HOLD. It used to be the rank, and on a series sampled unevenly that is a
    # distortion rather than a rounding: in the "all" view on 2026-08-23 the
    # left half of the axis carried 441 days and the right half 137, because
    # 2025 is reconstructed weekly and 2026 is captured daily. Thirteen months
    # were squeezed into a third of the width and the last three months took
    # another third, so a two-point move looked three times faster in 2025 than
    # the same move in 2026.
    #
    # It also meant the SHAPE OF HISTORY MOVED whenever the archive gained
    # dates. Every day the Wikipedia backfill recovers redistributes every
    # earlier point sideways, which is not a property a tracker should have.
    #
    # Linear in days fixes both, and it costs nothing: uneven sampling is then
    # drawn honestly as long flat segments early and dense detail late, which
    # is what the data actually is.
    _d0 = dt.date.fromisoformat(dates[0])
    _span = (dt.date.fromisoformat(dates[-1]) - _d0).days

    def X(d):
        # A single date, or a whole series inside one day, sits in the middle
        # rather than at x=0, where it would look like a truncated chart
        # instead of a young one.
        if _span <= 0:
            return 50.0
        return (dt.date.fromisoformat(d) - _d0).days / _span * 100
    def Y(v):
        return 100 - (v - lo) / (hi - lo) * 100

    series = []
    for key in ORDER:
        pts = sorted((r for r in rs if r["series"] == key),
                     key=lambda r: r["snapshot_date"])
        if not pts:
            continue
        coords = [{"x": round(X(r["snapshot_date"]), 2),
                   "y": round(Y(_f(r["value"])), 2),
                   "date": r["snapshot_date"], "v": _f(r["value"])}
                  for r in pts if _f(r["value"]) is not None]
        if not coords:
            continue
        last = pts[-1]
        band = None
        # A degenerate band is not an interval. A single-source category
        # average has min == max, and drawn as a bar it claimed a precision of
        # exactly zero — "80% 5.7 to 5.7" — where the truth is that nobody
        # stated one.
        if (_f(last.get("low")) is not None and _f(last.get("high")) is not None
                and abs(_f(last["high"]) - _f(last["low"])) > 1e-9):
            band = {"y_low": round(Y(_f(last["low"])), 2),
                    "y_high": round(Y(_f(last["high"])), 2),
                    "low": _f(last["low"]), "high": _f(last["high"]),
                    # "interval" = one model's own 80%. "spread" = the distance
                    # between disagreeing sources. Defaulting to "interval"
                    # would relabel every historical row written before this
                    # column existed as something it is not, so an unlabelled
                    # band says so and the template declines to name it.
                    "kind": last.get("band_kind") or "unlabelled",
                    "n": int(last.get("n_sources") or 1)}
        # A series can STOP. It happened the day a second professional
        # forecaster came online: the category then held one open source and
        # one gated one, which is below the disclosure floor, so the average
        # became unpublishable and the line simply ended. Drawn without a mark,
        # the last point still sits at the right-hand edge next to a current
        # date and reads as today's number. It is not.
        series.append({
            "key": key, "label": LABELS.get(key, key),
            "color": COLORS.get(key, ("#666", "#999"))[0],
            "color_dark": COLORS.get(key, ("#666", "#999"))[1],
            "points": coords,
            "polyline": " ".join(f"{c['x']},{c['y']}" for c in coords),
            "last": coords[-1], "band": band,
            "last_date": coords[-1]["date"],
            "stale": coords[-1]["date"] != dates[-1],
            "n_sources": last.get("n_sources", 1),
        })
    # Direct-label de-collision.
    #
    # Two series can sit on top of each other, and here they actually do: the
    # class polling model says D+5.67 and the professionals say D+5.73. That is
    # a real and interesting agreement, but drawn naively the second label lands
    # exactly on the first and one of the two numbers silently disappears — the
    # reader sees two series in the legend and one value on the chart.
    #
    # So push labels apart vertically until they clear each other, keeping their
    # order. The MARKERS stay where the data is; only the text moves.
    gap = 8.0 if unit == "pct" else 13.0
    for s_ in sorted(series, key=lambda x: x["last"]["y"]):
        s_["label_y"] = s_["last"]["y"]
    ordered_lbl = sorted(series, key=lambda x: x["label_y"])
    for i in range(1, len(ordered_lbl)):
        prev, cur = ordered_lbl[i - 1], ordered_lbl[i]
        if cur["label_y"] - prev["label_y"] < gap:
            cur["label_y"] = prev["label_y"] + gap
    for s_ in series:
        s_["label_nudged"] = abs(s_["label_y"] - s_["last"]["y"]) > 0.5

    # Above ~20 snapshots a marker per point stops being a marker and becomes a
    # dot cloud that hides the line it is supposed to annotate. Past that the
    # line carries the series and only the final point keeps a dot.
    dense = len(dates) > 20

    date_ticks = _date_ticks(dates, X)

    # Horizontal strip layout: the same current values laid out along a shared
    # value axis instead of against time. This is the "where do the methods
    # stand today" view, and it is the one that reads at a glance — a reader
    # sees the spread between methods without having to trace three lines.
    for s_ in series:
        s_["sx"] = round((s_["last"]["v"] - lo) / (hi - lo) * 100, 2)
        if s_["band"]:
            s_["sx_low"] = round((s_["band"]["low"] - lo) / (hi - lo) * 100, 2)
            s_["sx_high"] = round((s_["band"]["high"] - lo) / (hi - lo) * 100, 2)

    def fmt(t: float) -> str:
        if unit == "prob":
            return f"{t * 100:.0f}%"
        if unit == "seats":
            return f"{t:.0f}"
        return f"D+{t:.0f}" if t > 0 else ("EVEN" if abs(t) < 1e-9 else f"R+{abs(t):.0f}")

    return {
        "panel": panel,
        "unit": unit,
        "label": view.get("label", ""),
        "chamber": view.get("chamber", ""),
        "metric": view.get("metric", "margin"),
        "total": view.get("total"),
        "dates": dates, "n_dates": len(dates), "dense": dense,
        "date_ticks": date_ticks,
        # The plotted domain, exported so a change to the padding can be
        # checked against the data it is supposed to frame rather than against
        # the tick labels, which are a subset of it.
        "y_domain": [round(lo, 4), round(hi, 4)],
        "x_ticks": [{"v": t, "x": round((t - lo) / (hi - lo) * 100, 2),
                     "label": fmt(t)} for t in ticks],
        "y_ticks": [{"v": t, "y": round(Y(t), 2), "label": fmt(t)} for t in ticks],
        # One generalised reference line. Was two special cases — zero for the
        # margin panel, one-half for the probability panel — which had no room
        # for "218 seats" and would have needed a third.
        "ref_y": (round(Y(ref), 2) if ref is not None and lo < ref < hi else None),
        "ref_label": view.get("reference_label", ""),
        "ref_value": ref,
        # True when the reference exists but sits too far from the data to be
        # worth widening the axis for. The template says which side it is on
        # rather than leaving the reader to wonder where the majority line went.
        "ref_offscreen": ref_offscreen,
        "ref_below": bool(ref is not None and ref < data_lo),
        "zero_y": round(Y(0.0), 2) if unit == "pct" and lo < 0 < hi else None,
        "series": series,
        # WHAT THE HOVER LAYER READS, and why it is a separate compact block
        # rather than the series themselves.
        #
        # The template could jsonify `series` straight into the page and the
        # tooltip would have everything it needs. That costs about 50KB per
        # panel and there are sixteen panels — four metrics by four ranges,
        # all pre-rendered and toggled with CSS — so the honest version of
        # "just serialise it" is eight hundred kilobytes of duplicated
        # coordinates on a page whose whole point is to load fast.
        #
        # So: dates once, and per series only the value and which date it
        # belongs to. The x and y a marker needs are already in the DOM, in the
        # polyline's `points` attribute, at the same ordinal position — the
        # tooltip reads them from there. That also means the marker cannot
        # drift away from the drawn line, because it is placed on the drawn
        # line rather than on a second copy of the arithmetic.
        "hover": {
            "unit": unit,
            "dates": dates,
            "series": [
                {"k": sr["key"], "l": sr["label"],
                 "i": [di[pt["date"]] for pt in sr["points"]],
                 "v": [pt["v"] for pt in sr["points"]]}
                for sr in series],
        },
    }


# --------------------------------------------------------------------------
# The seat ladder: which race is the fiftieth Democratic seat.
# --------------------------------------------------------------------------

# Margins are clipped for display. The Senate's expected margins span D+34 to
# R+41, and on a scale that wide the entire competitive band — every race that
# actually decides control — collapses into a few pixels around zero. Clipping
# at ±20 keeps 22 of 35 races at true length and costs nothing, because the
# question "how safe is Wyoming" has no bearing on where the majority lands.
# Clipped bars are drawn with a notch so the chart never implies R+41 and R+23
# are the same number.
LADDER_CLIP = 20.0

# The x-axis is seat number, but it is NOT linear across all hundred seats.
#
# Drawn linearly, the sixty-five seats that are not on the ballot eat two thirds
# of the width and the eighteen races that decide anything are squeezed into a
# hundred pixels — too narrow to label, which is how the first version of this
# chart came out: a legible structure with an illegible roster underneath it.
# So the two blocks of holdover seats are compressed to a fixed cap at each end,
# marked with a break, and the thirty-five contested seats get the rest. The
# fifty- and fifty-one-seat lines are still drawn in the right place relative to
# the seats, which is the only thing the axis has to get right.
LADDER_CAP = 8.0        # display units given to each block of holdover seats


# The ladder's diverging scale, as a step index rather than a colour: the hexes
# live in the stylesheet so the two themes can differ, and this file stays out
# of the business of knowing what blue looks like.
#
# WHY A RAMP AND NOT TWO COLOURS. The bars were solid party blue and solid party
# red at a flat opacity, with a second, binary opacity for "competitive". That
# spent the whole colour channel re-encoding which side of the EVEN line the bar
# was already on, and said nothing about the thing the chart is for — how SURE
# any of it is. The ramp encodes certainty: a safe seat is vivid, a toss-up is a
# muted purple, and the run of purple through the middle of the chart is the
# set of seats that decide control.
#
# It is a diverging scale in the strict sense — two hues and a near-neutral
# midpoint — and not a rainbow. The hue takes the short path from blue to red,
# so the middle passes through purple, and chroma is pulled down there (0.068 in
# light mode, against 0.13-0.17 at the poles) so the midpoint reads as a mix of
# the two ends rather than as a third category. Lightness moves the same way in
# both themes in PERCEPTUAL terms: the midpoint is the lowest-contrast step
# against its own surface either way, 4.2:1 light and 4.5:1 dark, while the
# poles sit at 5.7-7.4:1. Every step clears 3:1.
#
# The poles separate under simulated colour-vision deficiency by dE 19.9
# (protanopia, light) and 17.8 (deuteranopia, dark), well past the 8 target —
# and blue against red is the pair that survives CVD best. Direction and bar
# length carry the same information anyway, so colour here reinforces rather
# than carries.
LADDER_SHADES = 9


def _shade(win_prob_D: float) -> int:
    """Certainty -> ramp index. 0 is safest D, 8 is safest R, 4 is even."""
    try:
        p = float(win_prob_D)
    except (TypeError, ValueError):
        return LADDER_SHADES // 2
    p = max(0.0, min(1.0, p))
    return int(round((1.0 - p) * (LADDER_SHADES - 1)))


def build_ladder(races: list[dict] | None, *, chamber: str,
                 fixed_left: int = 0, fixed_right: int = 0,
                 total: int = 100, thresholds: tuple = (),
                 expected: float | None = None,
                 markers: dict | None = None,
                 max_drawn: int = 45,
                 left_label: str = "", right_label: str = "") -> dict | None:
    """Seats ordered most-Democratic to most-Republican, majority line marked.

    Deliberately the DETERMINISTIC picture — every race to its expected winner
    — and not a simulation. The point it makes is structural: control does not
    turn on the average race, it turns on one specific race in the middle of
    the order. The probabilistic answer sits beside it as a number.

    Two axes worth of compression, both forced by the same problem: a chamber
    has far more safe seats than interesting ones, and drawn to scale the
    interesting ones vanish.

      x  Seats outside the drawn window are collapsed into a fixed cap at each
         end, marked with an axis break. For the Senate that cap is the seats
         not on the ballot; for the House it is the safe seats, of which there
         are hundreds. Without it, 435 districts across 640 pixels gives each
         bar one and a half pixels and no room for a single label.

      y  Margins are clipped at +/-LADDER_CLIP. The question "how safe is
         Wyoming" has no bearing on where the majority lands, and drawing it at
         true length squashes every race that does. Clipped bars carry a notch
         so the chart never implies R+41 and R+23 are the same number.

    `thresholds` is a tuple of (seat_number, label). The Senate passes two —
    fifty is a tie, fifty-one is a majority, and a tie is broken by a
    vice-president who is a Republican this cycle. A chart drawing only the
    fifty-line quietly assumes a friendly one.
    """
    if not races:
        return None
    ordered = sorted(races, key=lambda r: -r["expected_margin_D"])
    n = len(ordered)
    if fixed_left + n + fixed_right != total:
        return None

    # Which slice to draw at full width. Centred on the tightest thresholds we
    # were given, so the window always contains the seat that decides control
    # even when the expected result is nowhere near it.
    focus = [s for s, _ in thresholds] or [total // 2 + 1]
    lo_seat, hi_seat = min(focus), max(focus)
    # Every method's seat count has to fall inside the window, not just ours.
    # A marker outside it is a method whose answer the chart cannot show, which
    # is the one case where the reader most needs to see how far apart they are.
    for v in [expected] + [v for v in (markers or {}).values() if v is not None]:
        if v is not None:
            lo_seat, hi_seat = min(lo_seat, int(v)), max(hi_seat, int(v) + 1)
    span = max_drawn - (hi_seat - lo_seat)
    first = max(1, lo_seat - fixed_left - span // 2)          # 1-based index into `ordered`
    last = min(n, first + max_drawn - 1)
    first = max(1, last - max_drawn + 1)
    drawn = ordered[first - 1:last]

    hidden_left = fixed_left + (first - 1)
    hidden_right = fixed_right + (n - last)

    # Clip to the DRAWN window rather than to a fixed cap. The Senate's drawn
    # window is the whole ballot and spans D+34 to R+41, so it wants the cap;
    # the House's forty-five districts around the majority line all sit inside
    # eight points, and drawing them against a twenty-point axis left the chart
    # four fifths empty with every bar a stub. Rounded up to a multiple of five
    # so the gridlines stay readable.
    widest = max((abs(r["expected_margin_D"]) for r in drawn), default=LADDER_CLIP)
    clip = min(LADDER_CLIP, max(5.0, 5.0 * math.ceil(widest / 5.0)))

    inner = 100.0 - 2 * LADDER_CAP
    slot = inner / max(1, len(drawn))

    def X(k: float) -> float:
        """k counts DRAWN seats from the left cap, 0 = the cap boundary."""
        return LADDER_CAP + k * slot

    def seat_x(seat_no: float) -> float:
        return X(seat_no - hidden_left)

    seats, tipping = [], {}
    thresh_at = {s: lab for s, lab in thresholds}
    for k, r in enumerate(drawn, start=1):
        m = r["expected_margin_D"]
        seat_no = hidden_left + k        # D seat count if they win this one
        row = {
            "label": r.get("label") or r.get("state", ""),
            "state": r.get("state", ""), "district": r.get("district", ""),
            "margin": round(m, 2), "win_prob_D": r["win_prob_D"],
            "seat_no": seat_no,
            "x": round(X(k - 0.5), 2),   # centred in its own slot
            "y": round(_ladder_y(m, clip), 2),
            "clipped": abs(m) > clip,
            "competitive": bool(r.get("competitive")),
            "lead": "D" if m > 0 else "R",
            # Where this seat sits on the diverging ramp: 0 = safest
            # Democratic, 8 = safest Republican, 4 = a coin flip. See
            # LADDER_SHADES for why the scale is built the way it is.
            "shade": _shade(r["win_prob_D"]),
        }
        if seat_no in thresh_at:
            row["threshold"] = seat_no
            tipping[seat_no] = {
                "seat_no": seat_no, "ordinal": _ordinal(seat_no),
                "label": row["label"],
                "state": row["state"], "district": row["district"],
                "margin": row["margin"], "win_prob_D": r["win_prob_D"],
                # How far a uniform national move would carry this one race to
                # the other side. Zero if it is already there.
                "swing_needed": round(max(0.0, -m), 2),
                # The line sits at the RIGHT edge of this seat's slot: winning
                # it is what takes the count to the threshold.
                "x": round(X(k), 2),
                "note": thresh_at[seat_no],
            }
        seats.append(row)

    step = 5 if len(drawn) <= 45 else 10
    ticks = [-clip, -clip / 2, 0, clip / 2, clip]

    # ---- vertical geometry below the axis, derived rather than hardcoded ----
    #
    # The seat labels are rotated -90, so they hang BELOW the axis by their own
    # text length. The Senate's are two characters ("OH"); the House's are five
    # ("FL-25") at a smaller size but still half again as deep. Fixed offsets
    # tuned on the Senate put the method labels straight through the House's
    # district names — which is exactly what shipped.
    #
    # So measure the deepest label and stack everything below it. 0.62em per
    # character is the rough advance width of this sans at these sizes; it only
    # has to be close, because every consumer of it is a stack offset and not
    # an alignment.
    maxlab = max((len(s["label"]) for s in seats), default=2)
    fs = 8.5 if maxlab > 4 else 10.0
    label_drop = round(7 + maxlab * fs * 0.62, 1)
    marks = _ladder_markers(markers or {}, seat_x, hidden_left, len(drawn))
    axis_dy = round(label_drop + 16, 1)      # the seat-count numbers
    foot_dy = round(axis_dy + 20, 1)         # the cap labels and axis title

    return {
        "label_drop": label_drop,
        "axis_dy": axis_dy, "foot_dy": foot_dy,
        "svg_h": round(foot_dy + 14, 1),
        "chamber": chamber, "clip": clip, "total": total,
        "n_races": n, "n_drawn": len(drawn),
        "hidden_left": hidden_left, "hidden_right": hidden_right,
        "left_label": left_label, "right_label": right_label,
        "hold_D_x": [0, LADDER_CAP], "hold_R_x": [100 - LADDER_CAP, 100],
        "slot_w": round(slot, 3),
        # Seat-number ticks, so the compressed ends never leave a reader
        # guessing where they are on the chamber.
        "seat_ticks": [{"seat_no": s, "x": round(seat_x(s - 0.5), 2)}
                       for s in range(hidden_left + step, hidden_left + len(drawn), step)],
        "seats": seats,
        "thresholds": [tipping[s] for s, _ in thresholds if s in tipping],
        "tipping": tipping.get(min((s for s, _ in thresholds), default=0)),
        # Where the simulation actually lands, as its own line. The ladder is
        # the deterministic order; this is the answer the model gives when it
        # is allowed to be uncertain, and the gap between the two is worth
        # seeing on one chart.
        "expected": (None if expected is None or not (hidden_left < expected <= hidden_left + len(drawn))
                     else {"seats": round(expected, 1), "x": round(seat_x(expected), 2)}),
        # One tick per method, laid out on the same seat axis. Replaces the
        # single "model:" line, which showed one of four answers and implied
        # the other three did not exist. De-collided horizontally so two
        # methods a seat apart do not print over each other; the MARK stays
        # where the number is, only the label moves.
        "markers": marks,
        "y_zero": round(_ladder_y(0.0, clip), 2),
        "y_ticks": [{"v": v, "y": round(_ladder_y(v, clip), 2),
                     "label": ("EVEN" if v == 0 else
                               (f"D+{v:g}" if v > 0 else f"R+{abs(v):g}")) +
                              ("+" if abs(v) == clip else "")}
                    for v in ticks],
    }


def _ladder_markers(markers: dict, seat_x, hidden_left: int,
                    n_drawn: int) -> list[dict]:
    """Each method's seat count as a position on the ladder's own axis.

    Position only. The names used to be printed on the plot beside their
    marks, which needed de-collision, leader lines and alternating rows, and
    on the House still ran into the district labels underneath — a lot of
    machinery to answer "which line is which". The names now sit in a legend
    below the chart, where four short labels take one row and read in order.
    Each entry carries its seat count so the legend can state the number
    outright rather than making a reader hover for it.
    """
    out = []
    for key in ORDER:
        v = markers.get(key)
        if v is None or not (hidden_left < v <= hidden_left + n_drawn):
            continue
        out.append({"key": key, "label": LABELS.get(key, key),
                    "seats": round(v, 1), "x": round(seat_x(v), 2)})
    out.sort(key=lambda m: m["x"])
    return out


def _ordinal(n: int) -> str:
    """51 -> '51st'. The naive suffix table gives '51th', which shipped."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _ladder_y(m: float, clip: float = LADDER_CLIP) -> float:
    m = max(-clip, min(clip, m))
    return 50 - (m / clip) * 50


# --------------------------------------------------------------------------
# Ratings spread: where the raters disagree, drawn rather than tabulated.
# --------------------------------------------------------------------------

# The ordinal scale as the parser numbers it: 0 is Safe D, 10 is Safe R.
# Drawn with Democrats on the RIGHT, matching every other value axis on the
# site, where D+ is the positive direction.
RATING_TICKS = [(0.0, "Safe D"), (3.0, "Lean D"), (5.0, "Toss-up"),
                (7.0, "Lean R"), (10.0, "Safe R")]

# Source ids are for joins; people have names. Printing `fox_power_rankings` at
# a reader is a database column escaping onto a page.
RATER_NAMES = {
    "cook": "Cook Political Report",
    "inside_elections": "Inside Elections",
    "sabato": "Sabato's Crystal Ball",
    "ddhq": "Decision Desk HQ",
    "economist": "The Economist",
    "race_to_the_wh": "Race to the WH",
    "rcp": "RealClearPolitics",
    "fox_power_rankings": "Fox News Power Rankings",
    "votehub": "VoteHub",
    "fiftyplusone": "FiftyPlusOne",
    "split_ticket": "Split Ticket",
    "silver_bulletin": "Silver Bulletin",
    "cnalysis": "CNalysis",
    "elections_daily": "Elections Daily",
    "jhk_forecasts": "JHK Forecasts",
}


def rater_name(sid: str) -> str:
    return RATER_NAMES.get(sid, sid.replace("_", " ").title())


def _rating_x(numeric: float) -> float:
    return round((10.0 - max(0.0, min(10.0, numeric))) / 10.0 * 100.0, 2)


def build_ratings_spread(derived: Path, snapshot: str, chamber: str,
                         top_n: int = 24) -> dict | None:
    """One row per seat, one dot per rater, sorted by how far apart they are.

    This replaces a table that had grown to thirty rows of comma-separated
    labels — "cook: Lean R · ddhq: Toss-up · economist: Lean D · ..." — which
    is a format that hides the only thing it is trying to show. Twelve raters
    on one seat is a distribution, and a distribution should be drawn.

    Dots at the same value are stacked rather than overplotted, so a column of
    six at Toss-up reads as six and not as one.
    """
    rows = [r for r in _rd(derived / "expert_ratings.csv")
            if r["snapshot_date"] == snapshot and r["chamber"] == chamber]
    if not rows:
        return None

    by_seat: dict[str, dict[str, str]] = {}
    meta: dict[str, dict] = {}
    for r in rows:
        v = r.get("value", "")
        if ":" not in v:
            continue
        who, label = v.split(":", 1)
        by_seat.setdefault(r["race_id"], {})[who] = label
        meta[r["race_id"]] = {"state": r["state"], "district": r.get("district", "")}

    # Label -> position, derived from the labels themselves rather than from a
    # second copy of the scale. rating_numeric rows carry the number but not
    # which rater said it, so the join would be by row order — fragile.
    NUM = {"Safe D": 0.0, "Solid D": 0.0, "Likely D": 1.5, "Lean D": 3.0,
           "Tilt D": 4.0, "Toss-up": 5.0, "Tossup": 5.0,
           "Tilt R": 6.0, "Lean R": 7.0, "Likely R": 8.5,
           "Safe R": 10.0, "Solid R": 10.0}

    seats = []
    for rid, who in by_seat.items():
        vals = {k: NUM[v] for k, v in who.items() if v in NUM}
        if len(vals) < 2:
            continue
        lo, hi = min(vals.values()), max(vals.values())

        # One dot per distinct RATING, sized by how many raters chose it —
        # not one dot per rater stacked upward. Twelve raters agreeing makes a
        # twelve-high column, and a chart whose row height depends on how much
        # its raters agree is unreadable in exactly the rows where agreement
        # is the finding. Size carries the count; every row is one line tall.
        groups: dict[float, list[str]] = {}
        for k in sorted(vals):
            groups.setdefault(vals[k], []).append(k)
        biggest = max(len(v) for v in groups.values())
        dots = [{"v": v, "x": _rating_x(v), "n": len(ks),
                 "label": who[ks[0]],
                 "forecasters": [rater_name(k) for k in ks],
                 # Area, not radius, tracks the count — a radius that doubles
                 # looks four times as big and would read as four raters.
                 "r": round(2.6 + 2.4 * (len(ks) / biggest) ** 0.5, 2)}
                for v, ks in sorted(groups.items())]

        seats.append({
            "race_id": rid, **meta[rid],
            "n": len(vals), "spread": round(hi - lo, 1),
            "mean": round(sum(vals.values()) / len(vals), 2),
            "x_lo": _rating_x(hi), "x_hi": _rating_x(lo),   # hi numeric = more R = left
            "x_mean": _rating_x(sum(vals.values()) / len(vals)),
            "n_distinct": len(groups),
            "dots": dots,
        })
    if not seats:
        return None
    # Unanimous seats carry no spread to show. They are counted, not drawn.
    contested = [s for s in seats if s["spread"] > 0]
    # WHICH seats appear is still decided by disagreement — the card shows 24
    # of however many are contested, and the 24 worth showing are the 24 the
    # raters argue about hardest. Sorting the SELECTION and sorting the DISPLAY
    # are separate questions, and they were conflated here.
    contested.sort(key=lambda s: (-s["spread"], s["race_id"]))
    shown = contested[:top_n]
    # Drawn safe-D at the top to safe-R at the bottom. The dots then march
    # left-to-right down the card instead of scattering, so a reader sees WHERE
    # in the chamber the disagreements sit and not only how loud each one is —
    # and the row order stops reshuffling every time one rater moves one seat.
    # `mean` runs 0 (Safe D) to 10 (Safe R), so ascending is D first.
    shown = sorted(shown, key=lambda s: (s["mean"], s["race_id"]))
    if not shown:
        return None
    return {
        "chamber": chamber,
        "n_seats": len(seats),
        "n_contested": len(contested),
        "n_unanimous": len(seats) - len(contested),
        "n_shown": len(shown),
        "n_raters": len({f for s in seats for d in s["dots"]
                         for f in d["forecasters"]}),
        "raters": sorted({f for s in seats for d in s["dots"]
                          for f in d["forecasters"]}, key=str.lower),
        "ticks": [{"v": v, "x": _rating_x(v), "label": lab} for v, lab in RATING_TICKS],
        "seats": shown,
    }


def build(derived: Path, snapshot: str, rebuild: bool = False) -> dict:
    rows = update_timeline(derived, snapshot, rebuild)
    out = {p: build_panel(rows, p, RANGE_DEFAULT_DAYS) for p in PANELS}
    out["views"] = VIEWS
    # Every panel at every window. Three windows times five panels is fifteen
    # small dicts of coordinates — a few kilobytes of text — which is the price
    # of letting the reader change the time range without a round trip or any
    # geometry in the browser.
    out["ranges"] = {key: {p: build_panel(rows, p, days) for p in PANELS}
                     for key, days in RANGES}
    out["range_keys"] = [k for k, _ in RANGES]
    out["range_labels"] = RANGE_LABELS
    out["range_default"] = RANGE_DEFAULT
    # Governors ride along here and nowhere else. They are not modelled — a
    # national tide carried through partisan lean describes them badly — so
    # there is no forecast of ours to put beside them. What there IS is a
    # dozen raters' ordinal calls, which is exactly what this chart draws, and
    # a third tab costs nothing while a card of its own on the contests page
    # implied a gubernatorial forecast the course does not make.
    out["ratings"] = {c: build_ratings_spread(derived, snapshot, c)
                      for c in ("senate", "house", "governor")}
    return out
