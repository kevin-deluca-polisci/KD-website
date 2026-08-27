#!/usr/bin/env python3
"""
Stage 5 — assemble the single JSON the site reads.

Reads ONLY forecast/data/<cycle>/derived/, which is the published tier. It has
no access to parsed/ or raw/ by construction, so it cannot leak even if
someone later edits it carelessly.
"""
from __future__ import annotations
import argparse, csv, json, statistics, sys, datetime as dt
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts   # noqa: E402
import facets   # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"
NATL_HOUSE = "NATL_HOUSE_2026"
NATL_SENATE = "NATL_SENATE_2026"

# A race table of 35 rows in which 17 say ">99%" or "<1%" is mostly filler, and
# filler is not neutral: it trains a reader to skim, and the rows worth reading
# are in the middle. The cut is on probability rather than margin because
# probability is what the reader is being asked to weigh.
#
# One consequence is worth stating out loud rather than tuning around. Nebraska
# sits at P(D)=0.012 and survives this filter by a whisker — and it is the one
# race where our model is answering a question nobody asked, because there is
# no Democrat on the ballot. Dan Osborn is running as an independent. A
# two-party model cannot represent that at all, so Nebraska's number should be
# read as "what a generic Democrat would do here", which is not the race.
COMPETITIVE_LO, COMPETITIVE_HI = 0.01, 0.99
COMPETITIVE_NOTE = (
    "Races where the model puts the Democratic win probability between 1% and "
    "99%. The rest are not close under any assumption this model makes.")

def rd(p):
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []


# ---------------------------------------------------------------------------
# The category spread.
#
# Per race, what each FAMILY of forecast says — not one named forecaster
# against another. That framing was wrong twice over. It implied a benchmark
# where we have no evidence of one, and it only worked at all because exactly
# one outside per-race forecast happens to carry a permissive licence, which is
# a fact about paperwork rather than about quality.
#
# Categories also make the disclosure question disappear. A category average is
# already the published tier: aggregate.py decides what may appear in one and
# refuses to write if the answer is nothing. Nothing here re-derives that
# judgment, and nothing here can name a forecaster the aggregator withheld.
#
# THE ORDER IS AN ARGUMENT, so it is worth stating what the argument is.
#
# Left to right, the row runs from LEAST modelled to MOST modelled:
#
#   Polling        someone asked people and reported the answer. The only
#                  step between the respondent and the cell is an average.
#   Markets        no model either, but a price is not a raw response — it is
#                  many people's beliefs, already aggregated by money, and it
#                  reflects whatever those people read this morning.
#   Fundamentals   our first actual model: three variables, one equation, no
#                  polls at all.
#   Professional   people who forecast for a living, combining polls with
#                  fundamentals, race ratings and candidate quality.
#   Academic       published specifications with the most machinery of all,
#                  and the most explicit assumptions to argue with.
#
# The reader can therefore ask one question of the whole row — does adding
# modelling move the number, and in which direction — which is a better
# question than any single cell answers on its own.
#
# A NOTE FOR WHOEVER EDITS THIS NEXT. This list is the render order and nothing
# else: no averaging, no weighting and no privacy decision reads it. Reordering
# it is safe. Removing a name from it silently drops that family from the page,
# which is not.
# ---------------------------------------------------------------------------

# TWO FACETS, ONE FLAT LIST. `facets.py` is the taxonomy; these are its groups
# in reading order, type first then source, because that is the order the
# tracker offers them in.
#
# `market` is deliberately the one name that belongs to both facets — a traded
# price is both a method and a kind of forecaster — and its two averages are
# identical because they are taken over the same three exchanges. Keying by
# group name alone is therefore unambiguous, and that is not luck: no other
# group name is shared, and facets.py's audit fails if one ever is. Do not
# "fix" the duplicate by renaming one of them.
CATEGORY_ORDER = facets.TYPE_ORDER + [g for g in facets.SOURCE_ORDER
                                      if g not in facets.TYPE_ORDER]
CATEGORY_LABEL = {**facets.TYPE_LABEL, **facets.SOURCE_LABEL}
CATEGORY_FACET = {**{g: "type" for g in facets.TYPE_ORDER},
                  **{g: "source" for g in facets.SOURCE_ORDER
                     if g not in facets.TYPE_ORDER},
                  "market": "both"}


def build_model_index(d: Path, latest: str) -> dict:
    """Today's national numbers, keyed by source, for the methods page.

    Prose and links are editorial and live in the template; numbers are data
    and live here. A template that hardcodes "X says D+5.7" is a number that
    goes stale silently.

    Built from by_source_open.csv, which by construction holds only sources
    whose terms permit being quoted by name. A gated forecaster cannot appear
    here even if someone adds them to the template — the lookup simply misses.
    """
    idx: dict[str, dict] = defaultdict(dict)
    for r in rd(d / "by_source_open.csv"):
        if r["snapshot_date"] != latest or r["chamber"] != "national":
            continue
        if r["race_id"] not in (NATL_HOUSE, NATL_SENATE):
            continue
        try:
            v = float(r["value"])
        except (TypeError, ValueError):
            continue
        key = ("house" if r["race_id"] == NATL_HOUSE else "senate") + "_" + r["quantity"]
        idx[r["source_id"]][key] = v
    return dict(idx)


def _cat_cells(avgs: list[dict], latest: str) -> dict:
    """(race_id, quantity, category) -> the published average, or its absence.

    Carries the suppressed cells too. A withheld number is not the same as a
    missing one, and a page that renders both as an empty cell is telling the
    reader that nobody has an opinion when in fact we have one and may not
    show it.
    """
    out: dict[tuple, dict] = {}
    for r in avgs:
        if r["snapshot_date"] != latest:
            continue
        try:
            v = float(r["mean"])
        except (TypeError, ValueError):
            continue
        def _num(field):
            try:
                return float(r[field])
            except (KeyError, TypeError, ValueError):
                return None

        # MIN, MAX AND SD COME ALONG NOW. aggregate.py has always written them;
        # nothing read them, so the comparison table could say "Polling, 4
        # sources" without saying whether those four agreed to a tenth of a
        # point or spanned five. Those are completely different claims and the
        # table was making the weaker one look like the stronger.
        #
        # This carries no disclosure risk that the mean does not already carry.
        # A range over a category that clears MIN_N still names nobody, and
        # min/max are the same aggregate over the same contributors. The one
        # place it WOULD leak is a two-source category, where min and max are
        # the two contributors exactly — so the renderer must not print a range
        # for n < 3, and _spread_of() below is where that rule lives.
        out[(r["race_id"], r["quantity"], r["category"])] = {
            "value": v, "n": int(r["n_sources"]),
            "n_gated": int(r.get("n_gated") or 0),
            "min": _num("min"), "max": _num("max"), "sd": _num("sd"),
            "display": r.get("display", "ok"),
            "sole_source": r.get("sole_source", ""),
            "withheld": False,
        }
    return out


# How many contributors a category needs before its RANGE may be shown.
#
# Three, when any contributor is GATED. With n=2 the min and the max ARE the
# two contributors: printing them republishes both forecasts individually under
# a label that promises an aggregate, which is the disclosure floor defeated by
# a different route than the one MIN_N guards.
#
# THE GATE IS ON GATED CONTRIBUTORS, NOT ON THE HEADCOUNT, and getting that
# backwards costs the site the most interesting number it has. Academic holds
# exactly two models, both of them ours, both already published by name in
# academic_models.json with their coefficients and their citations. A range
# over two things a reader can already look up individually reveals nothing —
# and refusing to print it would have hidden the fact that the two academic
# models sit four points apart while the four polling aggregators sit within
# one, which is the single most informative comparison on the page.
#
# So: a range needs two contributors, and needs three only when one of them is
# gated. This is related to MIN_N in aggregate.py and is deliberately NOT the
# same rule — MIN_N asks "may we publish an average at all", this asks "does
# the range hand back an individual forecast we may not show".
SPREAD_MIN_N_GATED = 3


def _spread_of(cell: dict | None) -> dict | None:
    """min/max/sd for a cell, or None where a range would name people."""
    if not cell or cell.get("withheld"):
        return None
    n = int(cell.get("n") or 0)
    n_gated = int(cell.get("n_gated") or 0)
    lo, hi = cell.get("min"), cell.get("max")
    if n < 2 or lo is None or hi is None:
        return None
    if n_gated and n < SPREAD_MIN_N_GATED:
        return None
    return {"min": round(lo, 3), "max": round(hi, 3),
            "range": round(hi - lo, 3),
            "sd": round(cell["sd"], 3) if cell.get("sd") is not None else None,
            "n": n, "all_open": n_gated == 0}


def _withheld_cells(supp: list[dict], latest: str) -> set:
    return {(r["race_id"], r["quantity"], r["category"]) for r in supp
            if r["snapshot_date"] == latest}


def _seat_markers(chamber: str, proj: dict | None, avgs: list[dict],
                  latest: str) -> dict:
    """Every method's seat count, for the ladder's tick marks.

    All four read through the published category average, ours included. They
    used to be split — the class models from the projection, the outside ones
    from the average — which was fine only while a category held exactly one
    model. The moment fundamentals holds two, the ladder's mark and the table's
    number would have come from different places and quietly disagreed.

    A category that is withheld today simply has no marker, which is correct
    and visibly different from a category that has no opinion.
    """
    out: dict[str, float] = {}
    rid = NATL_SENATE if chamber == "senate" else NATL_HOUSE
    for r in avgs:
        if (r["snapshot_date"] == latest and r["race_id"] == rid
                and r["quantity"] == "seats_D"):
            try:
                out[r["category"]] = float(r["mean"])
            except (TypeError, ValueError):
                pass
    return out


def build_ladders(senate: dict | None, proj: dict | None,
                  avgs: list[dict] | None = None, latest: str = "") -> dict:
    """One ladder per chamber, both off the polling projection.

    The Senate ladder counts from the seats not on the ballot; the House has no
    such block, so its caps are the safe seats either side of the drawn window.
    Same function, different bookkeeping — which is the reason build_ladder
    stopped taking a polling-model dict and started taking a plain list.
    """
    out: dict[str, dict | None] = {}
    if senate and senate.get("races") and senate.get("holdover_D") is not None:
        hold_D = int(senate["holdover_D"])
        n_up = len(senate["races"])
        out["senate"] = charts.build_ladder(
            senate["races"], chamber="senate",
            fixed_left=hold_D, fixed_right=100 - hold_D - n_up, total=100,
            thresholds=((50, "a tie, broken by the vice-president"),
                        (51, "an outright majority")),
            expected=senate.get("expected_D_total"),
            markers=_seat_markers("senate", proj, avgs or [], latest),
            max_drawn=n_up,
            left_label=f"{hold_D} D seats not on the ballot",
            right_label=f"{100 - hold_D - n_up} R seats not on the ballot")

    # The district ladder is drawn from the polling model's own district
    # margins. Projections are keyed by SOURCE now, not by category, so this
    # asks for the model by name; the fallback keeps an older payload working.
    _pr = (proj or {}).get("projections") or {}
    p = _pr.get("class_polling") or _pr.get("polling") or {}
    districts = p.get("districts") or []
    house = p.get("house") or {}
    if districts and house:
        rows = [{**d,
                 "label": f"{d['state']}-{d['district']}" if d.get("district") else d["state"],
                 "competitive": COMPETITIVE_LO < d["win_prob_D"] < COMPETITIVE_HI}
                for d in districts]
        out["house"] = charts.build_ladder(
            rows, chamber="house", fixed_left=0, fixed_right=0,
            total=len(rows),
            thresholds=((house.get("majority_at", 218), "a majority"),),
            expected=house.get("expected_D_seats"),
            markers=_seat_markers("house", proj, avgs or [], latest),
            max_drawn=45,
            left_label="safe Democratic seats",
            right_label="safe Republican seats")
    return out


# ---------------------------------------------------------------------------
# Movement — where each family was, and where it is now
# ---------------------------------------------------------------------------
# A tracker that only ever shows today's number answers "what do they think"
# and never "what changed", which is the question a reader coming back next
# week actually has. The timeline answers it visually; this answers it in
# words, which is what you can read on a phone or paste into a lecture.
#
# THREE RULES, all of them about not inventing a comparison.
#
# 1. NEVER INTERPOLATE. The baseline is the newest published value at or
#    before the target date, and the card states the date it actually used. A
#    30-day change measured from 34 days ago is fine and honest; a 30-day
#    change measured from a number we made up by drawing a line between two
#    real ones is not.
#
# 2. NO BASELINE, NO DELTA. If a family has nothing on or before the target,
#    it gets no cell. A family that started collecting last week has not moved
#    zero over ninety days; it has no ninety-day movement, which is different.
#
# 3. FLAG A CHANGE OF RECIPE. The academic family's history before the generic
#    ballot started being captured is RECONSTRUCTED from Silver's poll-level
#    file using raw poll margins, while everything from the capture onward uses
#    his house-effect-adjusted average. Those are different recipes, measured
#    at about half a point apart. A delta that straddles that join is part
#    movement and part method, and this says so rather than letting the reader
#    assume it is all news.
MOVEMENT_HORIZONS = [("30d", 30), ("90d", 90)]

# How far past a horizon the baseline may sit before the cell is dropped.
#
# Rule 1 says take the newest value at or before the target and report the date
# actually used. On a dense series that is right: a 30-day change measured from
# 34 days ago is a 30-day change. On a SPARSE one it is not. A family whose
# only earlier value is eighteen months old would otherwise have that number
# printed under a "30 days" heading with a small "(568d)" beside it, and small
# print does not undo a wrong heading.
#
# So the baseline must land within the horizon plus this much slack, which is
# sized for the backfill's weekly grid plus a missed run or two. Past that the
# family has no movement at that horizon, which is a true statement, unlike the
# alternative.
MOVEMENT_TOLERANCE_DAYS = 21


def build_movement(avgs: list[dict], latest: str) -> dict | None:
    """Per category: today, and the change over each horizon."""
    # (category, quantity) -> {date: (value, n, n_gated)}
    hist: dict[tuple, dict] = defaultdict(dict)
    for r in avgs:
        if r["race_id"] not in (NATL_HOUSE, NATL_SENATE):
            continue
        # The same frame the chart uses. "Since it began" is a claim about the
        # line the reader is looking at, so it has to be measured from the same
        # first point — otherwise the card reports a move from a date the chart
        # does not draw. See charts.SERIES_START.
        if r["snapshot_date"] < charts.SERIES_START:
            continue
        if r["quantity"] not in ("margin_D", "seats_D", "win_prob_D"):
            continue
        try:
            v = float(r["mean"])
        except (TypeError, ValueError):
            continue
        key = (r["category"], r["race_id"], r["quantity"])
        hist[key][r["snapshot_date"]] = v

    if not hist:
        return None

    latest_d = dt.date.fromisoformat(latest)

    # When the polling category first published anything. Academic values from
    # before this date were reconstructed rather than captured — see rule 3.
    poll_dates = sorted(d0 for (cat, _r, _q), days in hist.items()
                        if cat == "polling" for d0 in days)
    capture_start = poll_dates[0] if poll_dates else None

    def at_or_before(days: dict, target: dt.date) -> tuple[str, float] | None:
        got = [d0 for d0 in days if dt.date.fromisoformat(d0) <= target]
        if not got:
            return None
        d0 = max(got)
        return d0, days[d0]

    rows = []
    for cat in CATEGORY_ORDER:
        entry = {"category": cat, "label": CATEGORY_LABEL[cat],
                 "facet": CATEGORY_FACET.get(cat, "type"), "metrics": {}}
        for race, qty, name in (
            (NATL_HOUSE, "margin_D", "house_margin"),
            (NATL_HOUSE, "seats_D", "house_seats"),
            (NATL_SENATE, "seats_D", "senate_seats"),
        ):
            days = hist.get((cat, race, qty))
            if not days or latest not in days:
                continue
            now = days[latest]
            m = {"now": round(now, 2), "changes": {}}
            for label, n in MOVEMENT_HORIZONS:
                got = at_or_before(days, latest_d - dt.timedelta(days=n))
                if got is None:
                    continue
                d0, v0 = got
                gap = (latest_d - dt.date.fromisoformat(d0)).days
                if gap > n + MOVEMENT_TOLERANCE_DAYS:
                    continue
                m["changes"][label] = {
                    "from": round(v0, 2), "from_date": d0,
                    "delta": round(now - v0, 2),
                    "days_actual": (latest_d - dt.date.fromisoformat(d0)).days,
                    "mixed_recipe": bool(cat == "academic" and capture_start
                                         and d0 < capture_start),
                }
            # Since the series began. Not a horizon — it is however long this
            # family has been on the site, which differs per family and is
            # worth showing precisely because it differs.
            first = min(days)
            if first != latest:
                m["changes"]["all"] = {
                    "from": round(days[first], 2), "from_date": first,
                    "delta": round(now - days[first], 2),
                    "days_actual": (latest_d - dt.date.fromisoformat(first)).days,
                    "mixed_recipe": bool(cat == "academic" and capture_start
                                         and first < capture_start),
                }
            if m["changes"]:
                entry["metrics"][name] = m
        if entry["metrics"]:
            rows.append(entry)

    return {
        "as_of": latest,
        "horizons": [lbl for lbl, _ in MOVEMENT_HORIZONS] + ["all"],
        "horizon_labels": {"30d": "30 days", "90d": "90 days",
                           "all": "since it began"},
        "rows": rows,
        "capture_start": capture_start,
        "note": ("Each change is measured against the newest published value at "
                 "or before that date — never an interpolation — and the date "
                 "actually used is shown. A family with no value that far back "
                 "gets no figure rather than a zero."),
    }


def build_spread(d: Path, latest: str, proj: dict | None, avgs: list[dict],
                 supp: list[dict], senate: dict | None) -> dict | None:
    """National and per-race, four categories each."""
    cells = _cat_cells(avgs, latest)
    withheld = _withheld_cells(supp, latest)
    projections = (proj or {}).get("projections") or {}

    def cat_cell(race, qty, cat):
        got = cells.get((race, qty, cat))
        if got:
            return got
        if (race, qty, cat) in withheld:
            return {"value": None, "withheld": True}
        return None

    # ---- national: four categories, two chambers, three quantities ----
    #
    # EVERY category now answers through the published average, ours included.
    # The class models used to be read straight out of seat_projections.json
    # and shown as their own thing, which meant "Fundamentals" was our model
    # and a second fundamentals model would have appeared beside it rather
    # than in it. aggregate.class_model_rows() emits them as ordinary
    # contributors, so this loop no longer needs to know which categories are
    # ours — and when Ray Fair arrives, nothing here changes.
    #
    # Intervals are the exception and stay keyed to the model. An 80% interval
    # is not an averageable quantity: the mean of two intervals is not the
    # interval of the mean, and a category holding two models has no single
    # interval to state. They are carried per model, and the page labels them
    # as belonging to a model rather than to the category.
    # category -> the single projection in it, where there is exactly one.
    # Intervals belong to a model, so a category holding two models has no
    # interval to state and gets none.
    by_cat: dict[str, list] = defaultdict(list)
    for _sid, _p in projections.items():
        if _p.get("category"):
            by_cat[_p["category"]].append(_p)

    national = []
    for cat in CATEGORY_ORDER:
        only = by_cat.get(cat) or []
        p = only[0] if len(only) == 1 else {}
        s, h = p.get("senate") or {}, p.get("house") or {}
        row = {
            "category": cat, "label": CATEGORY_LABEL[cat],
            # Intervals: from the model, and only while the category has
            # exactly one. n_sources is filled in below.
            "house_seats_80": h.get("D_seats_80pct"),
            "senate_seats_80": s.get("D_total_80pct"),
            "n_sources": None, "sole_source": "",
        }
        for key, (race, qty) in (
            ("house_margin", (NATL_HOUSE, "margin_D")),
            ("house_seats", (NATL_HOUSE, "seats_D")),
            ("house_prob", (NATL_HOUSE, "win_prob_D")),
            ("senate_seats", (NATL_SENATE, "seats_D")),
            ("senate_prob", (NATL_SENATE, "win_prob_D")),
        ):
            got = cat_cell(race, qty, cat)
            row[key] = (got or {}).get("value")
            row[key + "_withheld"] = bool((got or {}).get("withheld"))
            row[key + "_spread"] = _spread_of(got)
            if got and got.get("sole_source"):
                row["sole_source"] = got["sole_source"]
            if got and got.get("n"):
                row["n_sources"] = max(row["n_sources"] or 0, got["n"])
        # Once a category holds more than one model, whose interval would it
        # be? Drop them rather than attribute one model's uncertainty to the
        # whole category.
        if (row["n_sources"] or 0) > 1:
            row["house_seats_80"] = row["senate_seats_80"] = None
        if any(row.get(k) is not None for k in
               ("house_margin", "house_seats", "house_prob",
                "senate_seats", "senate_prob")):
            national.append(row)

    # ---- the across-family average -------------------------------------
    #
    # ONE VOTE PER FAMILY, not one per contributor. Polling currently holds
    # four aggregators and fundamentals holds one model; averaging contributors
    # would make the headline number four-fifths polling and call it a
    # consensus of everything. The families are the unit this site reasons in,
    # so the families are what get averaged.
    #
    # THE SUBTRACTION PROBLEM, which is why this is not simply a mean of the
    # rows above. A category can be withheld — below MIN_N, or holding only
    # gated contributors. If we averaged the withheld cell in and published the
    # result beside the categories that ARE shown, the withheld one comes
    # straight back out: with k families averaged and k-1 visible, the hidden
    # one is k*mean - sum(visible). Exactly. That is the disclosure floor
    # defeated by arithmetic a reader can do in their head, and it would be our
    # own new code that did it.
    #
    # So the average runs over the VISIBLE cells only, and says how many it
    # used. A number computed from four families is honestly a number computed
    # from four families; it is not a consensus of five with one kept quiet.
    def _across(key: str) -> dict:
        vals, used = [], []
        for row in national:
            if row.get(key + "_withheld"):
                continue
            v = row.get(key)
            if v is None:
                continue
            vals.append(float(v))
            used.append(row["category"])
        if not vals:
            return {"value": None, "n_categories": 0, "categories": [],
                    "spread": None}
        return {
            "value": round(statistics.fmean(vals), 2),
            "n_categories": len(vals),
            "categories": used,
            # The spread, not a confidence interval. These are point forecasts
            # from unrelated methods; the distance between them is disagreement
            # between families, which is a different thing from any one model's
            # uncertainty and must never be drawn as an error bar.
            "spread": [round(min(vals), 2), round(max(vals), 2)]
                      if len(vals) > 1 else None,
        }

    across = {k: _across(k) for k in
              ("house_margin", "house_seats", "house_prob",
               "senate_seats", "senate_prob")}
    across["label"] = "All families"
    across["basis"] = ("unweighted mean of the family averages that are "
                       "published today; a family withheld below the "
                       "disclosure floor is left out rather than averaged in, "
                       "because averaging it in would let a reader recover it "
                       "by subtraction")
    withheld_families = sorted({row["category"] for row in national
                                if any(row.get(k + "_withheld") for k in
                                       ("house_margin", "house_seats",
                                        "house_prob", "senate_seats",
                                        "senate_prob"))})
    across["families_withheld"] = withheld_families

    # ---- per race: the Senate, one row per seat ----
    races = []
    for r in (senate or {}).get("races", []):
        st, rid = r["state"], f"SEN_{r['state']}_2026"
        entry = {"state": st, "race_id": rid,
                 "competitive": r.get("competitive", False), "cats": {}}
        for cat in CATEGORY_ORDER:
            # Same rule as the national table: every category answers through
            # the published average, ours included. class_model_rows() emits
            # the per-race margin and probability for both class models, so
            # there is no longer a branch here for "our" categories.
            m = cat_cell(rid, "margin_D", cat)
            w = cat_cell(rid, "win_prob_D", cat)
            if m or w:
                entry["cats"][cat] = {
                    "margin": (m or {}).get("value"),
                    "prob": (w or {}).get("value"),
                    "withheld": bool((m or {}).get("withheld")
                                     or (w or {}).get("withheld")),
                    "n": (m or w or {}).get("n"),
                }
        have = [c for c, v in entry["cats"].items() if v.get("prob") is not None]
        entry["n_cats"] = len(have)
        if len(have) >= 2:
            ps = [entry["cats"][c]["prob"] for c in have]
            entry["prob_spread"] = round(max(ps) - min(ps), 4)
        else:
            entry["prob_spread"] = None
        races.append(entry)

    covered = sorted({c for r in races for c in r["cats"]},
                     key=CATEGORY_ORDER.index)
    missing = [c for c in CATEGORY_ORDER if c not in covered]
    return {
        "national": national,
        "across_families": across,
        "races": races,
        "plot": _spread_plot(races),
        "categories": CATEGORY_ORDER,
        "labels": CATEGORY_LABEL,
        "categories_per_race": covered,
        "categories_missing_per_race": missing,
    }


def _spread_plot(races: list[dict]) -> dict | None:
    """The per-race comparison as positions on one probability axis.

    This was a table: a column per method, a percentage in every cell, and a
    spread column at the end. Reading it meant holding four numbers in your
    head and doing the subtraction yourself, for every row — and the answer
    the page exists to give, which method sits where relative to the others,
    was the one thing the format never showed. A row is now a line: a band
    from the lowest estimate to the highest, with each method's mark on it.
    Whether the methods cluster or scatter is then visible without reading a
    single number.

    Sorted by the consensus probability, safest Democratic seat at the top.
    It used to be sorted widest-band-first, which put the loudest argument at
    the top and made a fair case for itself — but it also meant two adjacent
    rows had nothing to do with each other, and the order churned daily. Seat
    order gives the card a spine: the bands slide right as you read down, and
    a wide band is legible AS a wide band because its neighbours are narrow.
    The spread is still printed in its own column and the widest is still named
    in the note underneath, so nothing that ordering used to say is lost.
    """
    rows = []
    for r in races:
        if not r.get("competitive"):
            continue
        pts = [{"key": c, "label": CATEGORY_LABEL[c],
                "prob": v["prob"], "x": round(v["prob"] * 100, 2)}
               for c in CATEGORY_ORDER
               if (v := r["cats"].get(c)) and v.get("prob") is not None]
        # One mark is a value, not a comparison. A single-method row on a
        # card about disagreement is noise, and its band would be zero wide.
        if len(pts) < 2:
            continue
        xs = [p["x"] for p in pts]
        rows.append({
            "state": r["state"], "race_id": r["race_id"],
            "points": pts, "n_cats": len(pts),
            "x_lo": min(xs), "x_hi": max(xs),
            "spread": round(max(xs) - min(xs), 1),
        })
    if not rows:
        return None
    # The MEDIAN across methods, not the midpoint of the band. A midpoint is
    # dragged by whichever single method is furthest out, which is exactly the
    # method this card exists to show as an outlier — sorting by it would let
    # one disagreeing source decide where the row goes.
    for _r in rows:
        _xs = sorted(p["x"] for p in _r["points"])
        _n = len(_xs)
        _r["x_mid"] = round((_xs[_n // 2] if _n % 2
                             else (_xs[_n // 2 - 1] + _xs[_n // 2]) / 2), 2)
    rows.sort(key=lambda r: (-r["x_mid"], r["state"]))
    used = [c for c in CATEGORY_ORDER
            if any(p["key"] == c for r in rows for p in r["points"])]
    return {
        "rows": rows,
        "categories": used,
        "labels": {c: CATEGORY_LABEL[c] for c in used},
        "ticks": [{"x": v, "label": f"{v}%"} for v in (0, 25, 50, 75, 100)],
        "n_rows": len(rows),
        "widest": max(r["spread"] for r in rows),
        "median_spread": round(
            sorted(r["spread"] for r in rows)[len(rows) // 2], 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--rebuild-timeline", action="store_true",
                    help="replay every snapshot date in category_averages.csv "
                         "into timeline.csv instead of adding only today. Use "
                         "after a backfill gives a category history the chart "
                         "has never been told about. Cheap — pure CSV, no "
                         "simulation — and safe to repeat.")
    a = ap.parse_args(argv)
    d = DATA / str(a.cycle) / "derived"

    avgs = rd(d / "category_averages.csv")
    supp = rd(d / "suppressed.csv")
    model = json.loads((d / "fundamentals_model.json").read_text()) \
            if (d / "fundamentals_model.json").exists() else None

    # THE APPROVAL PANEL. Three constructions of the same polls, published
    # together because the model's input sits several points below every
    # familiar tracker and a reader who sees only one of them will reasonably
    # conclude we have made a mistake. model/approval.py builds it and its
    # docstring carries the argument.
    #
    # The per-aggregator members ride along only on the LATEST point. Ten names
    # a day for the rest of the cycle would be seventy rows a week in a file
    # every visitor downloads, to say the same thing the spread already says.
    approval = None
    ap_f = d / "approval.json"
    if ap_f.exists():
        approval = json.loads(ap_f.read_text())
        for k, v in approval.get("series", {}).items():
            pts = v.get("points") or []
            for pt in pts[:-1]:
                pt.pop("members", None)
    polling = json.loads((d / "polling_model.json").read_text()) \
              if (d / "polling_model.json").exists() else None
    proj = json.loads((d / "seat_projections.json").read_text()) \
           if (d / "seat_projections.json").exists() else None
    # Carried whole, including `not_implemented`. The methods page states each
    # academic model's coefficients, citation and inputs, and this file's
    # standing rule is that a figure on that page is never hardcoded — a
    # hardcoded number goes stale silently. Shipping the model's own payload is
    # what lets the page name a coefficient and stay honest when it changes.
    #
    # The unimplemented ones travel too, deliberately. A methods page that
    # lists only what we managed to build reads as a complete account of the
    # literature, which it is very much not.
    academic = json.loads((d / "academic_models.json").read_text()) \
               if (d / "academic_models.json").exists() else None

    # Snapshot dates the SITE shows, which is what the "N snapshots" counter in
    # the nav is a count of. Dates before the common start are still in the
    # archive and still in the published averages; they are outside the frame,
    # so counting them would advertise a history the chart does not draw.
    dates = sorted({r["snapshot_date"] for r in avgs
                    if r["snapshot_date"] >= charts.SERIES_START}) or [
        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")]
    latest = dates[-1]

    # The headline panel: one row per method category, national House margin.
    headline = []
    for r in avgs:
        if r["snapshot_date"] == latest and r["race_id"] == NATL_HOUSE \
                and r["quantity"] == "margin_D":
            headline.append({"category": r["category"], "margin_D": float(r["mean"]),
                             "n_sources": int(r["n_sources"]),
                             "low": float(r["min"]), "high": float(r["max"]),
                             "tier": r["tier"],
                             # "ok" = a real category average; "thin" = n=2 by
                             # exception; "single" = ONE contributor, and the
                             # page must say whose rather than calling it an
                             # average of anything.
                             "display": r.get("display", "ok"),
                             "sole_source": r.get("sole_source", ""),
                             # When the stalest contributor last published, and
                             # how many are being quoted from an earlier day.
                             # The page needs both to say "2 sources, Fair last
                             # published Jul 31" rather than implying everyone
                             # spoke this morning.
                             "as_of": r.get("oldest_as_of") or latest,
                             "n_carried": int(r.get("n_carried") or 0)})

    # THE CLASS MODEL IS NOT APPENDED HERE ANY MORE.
    #
    # It used to be, from the days when publish.py read fundamentals_model.json
    # directly because our model was not a contributor to anything. Since
    # aggregate.py began emitting the class models as ordinary rows, this block
    # was adding a SECOND fundamentals entry carrying the same number as the
    # category average that already contained it — the site.json headline held
    # two fundamentals rows reading 10.48, one of them labelled "class model",
    # a label that had already been asked for twice to be taken off the page.
    #
    # The 80% interval that only this block knew about has not been lost: it is
    # on fundamentals_model.json, which is published alongside, and the band is
    # a property of our model rather than of the category, so the category
    # average is the wrong place to have been carrying it anyway.

    series = defaultdict(list)
    for r in avgs:
        if r["race_id"] == NATL_HOUSE and r["quantity"] == "margin_D":
            series[r["category"]].append([r["snapshot_date"], float(r["mean"])])

    # The polling model's Senate table, trimmed to what the page renders.
    # Deliberately NOT the whole file: site.json is public, and shipping every
    # intermediate invites someone to read a diagnostic as a forecast.
    senate = None
    if polling and polling.get("senate", {}).get("races"):
        s_ = polling["senate"]
        senate = {
            # The nowcast. Older polling_model.json files predate the key,
            # so fall back rather than crashing on a replayed snapshot.
            "tide_D": polling.get("nowcast_tide_D",
                                  polling.get("election_day_tide_D")),
            "election_day_tide_D": polling.get("election_day_tide_D"),
            "generic_ballot": polling["generic_ballot"]["value"],
            "shrink_lambda": polling["shrink_lambda"],
            "sigma": s_["sigma_total"],
            "expected_D_seats_up": s_["expected_D_seats_up"],
            "D_seats_up_80pct": s_["D_seats_up_80pct"],
            "holdover_D": s_.get("holdover_D_assumed"),
            # Total chamber seats, not just the ones on the ballot. "15 of 35"
            # is the modelling quantity; "49 of 100" is the thing a reader
            # actually wants, because 50 is the number that decides control.
            # Computed here rather than in the template so the arithmetic is
            # testable and the page only ever displays.
            "expected_D_total": (round(s_["expected_D_seats_up"] + s_["holdover_D_assumed"], 2)
                                 if s_.get("holdover_D_assumed") is not None else None),
            "D_total_80pct": ([s_["D_seats_up_80pct"][0] + s_["holdover_D_assumed"],
                               s_["D_seats_up_80pct"][1] + s_["holdover_D_assumed"]]
                              if s_.get("holdover_D_assumed") is not None else None),
            "prob_D_50_plus": s_.get("prob_D_50_plus"),
            # 50+ is a tie the vice-president breaks; 51+ is a majority. Both
            # ship, because every outside forecast and market this page is
            # compared against prices the 51+ event.
            "prob_D_51_plus": s_.get("prob_D_51_plus"),
            # Carried so the page can show it. A one-seat change in the
            # baseline moves this by ~20 points, which is more than any
            # modelling choice in the model — publishing the headline without
            # it would be publishing false precision.
            "sensitivity": s_.get("prob_D_50_plus_sensitivity"),
            "races": [{"state": k, **v, "competitive": COMPETITIVE_LO < v["win_prob_D"] < COMPETITIVE_HI}
                      for k, v in sorted(s_["races"].items(),
                                         key=lambda kv: -kv[1]["expected_margin_D"])],
        }
        senate["n_competitive"] = sum(1 for r in senate["races"] if r["competitive"])
        senate["competitive_note"] = COMPETITIVE_NOTE

        # The same competitive races, split by which way they lean and ordered
        # closest-first within each side.
        #
        # One list ordered by margin runs from safe D through the interesting
        # middle to safe R, which buries the races worth looking at in the
        # centre of a long table and puts the two closest seats — one leaning
        # each way — as far apart as the ordering can put them. Two columns
        # ordered by distance from even bring both to the top of the page,
        # beside each other, and the shape of each column is then a readable
        # thing in itself: how many seats each party is actually defending.
        comp = [r for r in senate["races"] if r["competitive"]]
        senate["lean_D"] = sorted(
            (r for r in comp if r["expected_margin_D"] > 0),
            key=lambda r: (abs(r["expected_margin_D"]), r["state"]))
        senate["lean_R"] = sorted(
            (r for r in comp if r["expected_margin_D"] <= 0),
            key=lambda r: (abs(r["expected_margin_D"]), r["state"]))

    # Accumulate the timeline and lay out every panel. This is the only part of
    # publish.py that WRITES to derived/ rather than only reading it, because
    # the history has to survive tomorrow's overwrite of the model files.
    #
    # The old expert_ratings_panel is gone. It pivoted 1,836 rows into a
    # thirty-row table of comma-separated labels, and the ratings are now drawn
    # as a spread instead — charts.build_ratings_spread. Same data, and the
    # count has since grown to 4,206 rows across twelve raters, which is well
    # past what any table was going to carry.
    chart_data = charts.build(d, latest, a.rebuild_timeline)
    chart_data["ladders"] = build_ladders(senate, proj, avgs, latest)
    spread = build_spread(d, latest, proj, avgs, supp, senate)
    movement = build_movement(avgs, latest)
    model_index = build_model_index(d, latest)

    out = {
        "cycle": a.cycle,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "latest_snapshot": latest,
        "snapshot_count": len(dates),
        "headline": sorted(headline, key=lambda x: x["category"]),
        "series": {k: sorted(v) for k, v in series.items()},
        "fundamentals_model": model,
        "approval": approval,
        "polling_model": senate,
        "academic_models": academic,
        # Without the district arrays. All 435 of them tripled the size of a
        # file every visitor downloads, to feed a chart that draws 45 — and the
        # full set is already published in derived/seat_projections.json for
        # anyone who wants it.
        "seat_projections": (None if not proj else {
            **proj,
            "projections": {k: {kk: vv for kk, vv in v.items() if kk != "districts"}
                            for k, v in (proj.get("projections") or {}).items()},
        }),
        "charts": chart_data,
        "spread": spread,
        "movement": movement,
        "model_index": model_index,
        "suppressed_cells": len(supp),
        "display_note": (
            "A row with display='single' has ONE contributing source and is not "
            "a category average. Render it named, never as a consensus."),
        "disclosure_note": (
            "Category averages only for sources whose terms do not permit "
            "per-forecaster republication during the cycle. Averages containing "
            "any such source are shown only with at least 3 contributors. "
            "Full per-forecaster data will be released as a documented archive "
            "after the election."),
    }
    p = DATA / str(a.cycle) / "site.json"
    p.write_text(json.dumps(out, indent=2))
    print(f"  wrote {p.relative_to(REPO)}  "
          f"({len(headline)} categories, {len(dates)} snapshots)")

    # Hugo reads this at build time from assets/, NOT from data/.
    #
    # Two reasons, and the second one is load-bearing:
    #   1. never point Hugo at forecast/data/ — that is the whole archive
    #      including the private tiers, and Hugo would ingest all of it.
    #   2. a template that reads .Site.Data forces Hugo to assemble the entire
    #      data map, and this site's data/*.csv load as [][]string, which Hugo
    #      0.123 cannot merge. One .Site.Data reference from the forecast page
    #      broke the whole build with "unexpected data type [][]string in file
    #      media.csv". Loading from assets/ via resources.Get avoids the data
    #      map entirely.
    hugo = REPO / "assets" / f"forecast_{a.cycle}.json"
    hugo.parent.mkdir(parents=True, exist_ok=True)
    hugo.write_text(json.dumps(out, indent=2))
    print(f"  wrote {hugo.relative_to(REPO)}  (Hugo build-time data)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
