#!/usr/bin/env python3
"""
Stage 3 — the class fundamentals model.

Predicts the president's party share of the national two-party House vote from
approval, real income growth, and seats defended. No polls, no markets, no
candidate quality. Fitted on all 20 midterms 1946-2022 and scored by
leave-one-election-out, because with n=20 an in-sample R-squared is meaningless.

The headline finding, which is worth a class session: approval ALONE has an
R-squared of 0.02. The referendum story only appears once you condition on
exposure. Much of "the midterm penalty" is mean reversion in seat exposure, not
a verdict on the president.

Seats come from a deterministic curve given district baselines, so all the
uncertainty lives in one number (the national margin) and no simulation is
needed — which matters, because the course does not cover simulation.

Publication: `individual`. It is our model; we can publish whatever we like.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, glob, json, statistics, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# year, pres_party, gallup_approval, pres_party_house_2pv, seats_before, d_real_income
# Sources: Brookings Vital Statistics on Congress tables 2-2 and 2-4;
# Gallup presidential approval; FRED A229RX0A048NBEA.
HISTORY = [
 (1946,"D",33,45.3,242,-2.16),(1950,"D",39,50.0,263, 7.44),(1954,"R",61,47.4,221,-0.34),
 (1958,"R",57,44.0,201,-0.61),(1962,"D",61,52.5,262, 3.26),(1966,"D",44,51.3,295, 4.12),
 (1970,"R",58,45.6,192, 3.42),(1974,"R",54,41.5,192,-2.03),(1978,"D",49,54.4,292, 3.44),
 (1982,"R",42,44.0,192, 1.21),(1986,"R",63,45.0,182, 2.87),(1990,"R",58,46.0,175, 0.93),
 (1994,"D",46,46.4,258, 1.49),(1998,"D",66,49.5,207, 4.67),(2002,"R",63,52.4,221, 2.10),
 (2006,"R",38,46.7,232, 2.65),(2010,"D",45,46.6,257, 1.04),(2014,"D",44,47.3,201, 2.64),
 (2018,"R",41,45.5,241, 3.02),(2022,"D",41,48.7,222,-6.11),
]

def _lstsq(X, y):
    """Normal equations via Gaussian elimination. No numpy dependency."""
    k = len(X[0]); n = len(y)
    A = [[sum(X[i][a]*X[i][b] for i in range(n)) for b in range(k)] + 
         [sum(X[i][a]*y[i] for i in range(n))] for a in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(A[r][c])); A[c], A[p] = A[p], A[c]
        if abs(A[c][c]) < 1e-12: raise ValueError("singular design matrix")
        for r in range(k):
            if r == c: continue
            f = A[r][c]/A[c][c]
            for j in range(c, k+1): A[r][j] -= f*A[c][j]
    return [A[i][k]/A[i][i] for i in range(k)]

def fit(winsorise_2022=True):
    inc = [r[5] for r in HISTORY]
    floor = min(v for r, v in zip(HISTORY, inc) if r[0] != 2022)
    rows = [(r[2], (floor if (winsorise_2022 and r[0]==2022) else r[5]), r[4], r[3])
            for r in HISTORY]
    X = [[1.0, a, i, s] for a, i, s, _ in rows]
    y = [v for *_, v in rows]
    b = _lstsq(X, y)
    # leave-one-election-out
    errs = []
    for i in range(len(y)):
        Xi = [x for j, x in enumerate(X) if j != i]; yi = [v for j, v in enumerate(y) if j != i]
        bi = _lstsq(Xi, yi)
        errs.append(y[i] - sum(c*x for c, x in zip(bi, X[i])))
    loeo = (sum(e*e for e in errs)/len(errs)) ** 0.5
    ybar = statistics.fmean(y)
    resid = [y[i] - sum(c*x for c, x in zip(b, X[i])) for i in range(len(y))]
    r2 = 1 - sum(e*e for e in resid)/sum((v-ybar)**2 for v in y)
    return b, loeo, r2

# Which of the three FRED variants the model consumes, and why, is argued in
# collect/parsers/fred.py. Short version: the fitted quantity is an annual
# average against an annual average, and income_growth_ytd is the only variant
# with that shape.
INCOME_QUANTITY = "income_growth_ytd"


# ---------------------------------------------------------------------------
# Presidential approval, as of a date
# ---------------------------------------------------------------------------
# THE FALLBACK IS THE POINT OF THIS FUNCTION, not an afterthought.
#
# Approval was a typed-in constant for the whole of this archive's life, and a
# constant is invisible: nothing downstream could tell "38.0 because the polls
# say so on this date" from "38.0 because nobody has wired up a feed". Both
# produced the same number and the same chart.
#
# So the two cases are now different objects. A value read from dated polls
# carries the window it was computed over and how many polls were in it. The
# constant carries a source string that says it is a constant, and every model
# that uses it propagates that string into its own output. A backfilled line
# built on the constant is therefore self-identifying: the reason it is flat is
# written on it.
from pathlib import Path as _Path

_WA_CACHE: dict[int, list] = {}
_FRED_CACHE: dict = {}

# LAST-RESORT CONSTANT, and it is on the ADULTS scale like everything else
# here. It was 38.0 chosen as a Gallup-ish level when approval was typed in;
# on the adults scale that same moment reads about 35, and a fallback quietly
# sitting three points off the series it stands in for is the exact bug class
# the approval work was done to remove. It should now essentially never fire —
# the Silver poll list covers every date in the archive — and anything driven
# by it says so in its own source string.
APPROVAL_FALLBACK = 35.0
APPROVAL_WINDOW_DAYS = 14


# GALLUP HAS STOPPED POLLING PRESIDENTIAL APPROVAL, and the model was fit on
# Gallup. That is the whole problem this constant exists to solve.
#
# The coefficients come from a Gallup-only column running back to 1946, so the
# model wants a number on the GALLUP SCALE. It no longer matters whether we
# would prefer Gallup; there is no more Gallup to prefer. The choice is between
# feeding it something else and pretending the scales match, or measuring the
# gap and correcting for it.
#
# MEASURED, on the 128 dated polls in the archive. Gallup published three
# readings before it stopped, and for each one we can compute what the rest of
# the field said in the same fortnight:
#
#     2025-01-27   gallup 47.0   field 50.53 (n=32)   -3.53
#     2025-02-16   gallup 45.0   field 49.47 (n=36)   -4.47
#     2025-03-16   gallup 43.0   field 47.08 (n=24)   -4.08
#                                        mean -4.03, sd 0.39
#
# Three points is a small sample and the spread across them is a third of a
# point, which is about as stable as a house effect ever looks. The approval
# coefficient is 0.1338 on the president's-party vote share, so 4.03 points of
# approval is 0.54 of share and about 1.08 points of D margin. That is the size
# of the bias we would carry by swapping the series without adjusting, and it
# is the same order as the per-cycle translation error we already carry
# knowingly. Too big to ignore, small enough that the n=3 uncertainty around it
# does not dominate.
#
# WHAT WOULD CHANGE THIS. The offset is measured on early-2025 data and a house
# effect can drift, particularly for a pollster that has since left the field
# and cannot be re-measured. If a better-attested Gallup-to-aggregate offset
# turns up, use it and say so. If the fundamentals model is ever refit on a
# non-Gallup column, delete this and the adjustment together.
GALLUP_HOUSE_EFFECT = -4.03     # gallup minus the field, in approval points
GALLUP_HOUSE_EFFECT_N = 3


# THE HISTORICAL COLUMN IS GALLUP, AND THAT DECIDES WHAT MAY BE FED IN.
#
# Both models that take approval — this one and the referendum model — were fit
# on a Gallup-only approval series running back to 1946. A pollster's house
# effect on presidential approval is worth a couple of points, this model's
# approval coefficient is large, and so substituting a multi-pollster average
# for Gallup does not make the model better informed. It makes it a different
# model reported under the same name, which is the one thing this file exists
# not to do. The CLI help has warned about it since the model was written; the
# archive lookup has to obey the same warning.
#
# Measured on the 2026-08-25 capture: 128 dated approval polls in the archive,
# of which THREE are Gallup, all from early 2025. So the basis we need is
# exactly the basis we do not have, and the honest response is to keep the
# constant and say why rather than quietly swap the series.
# GALLUP POLLS APPROVAL MONTHLY, so the window that suits a dense
# multi-pollster feed is the wrong window for it. Fourteen days would reject
# the very readings the basis check exists to prefer: one Gallup poll in a
# month IS the Gallup series, not a thin sample of it.
APPROVAL_WINDOW_GALLUP = 35


def field_approval(cycle: int, asof: str | None = None):
    """(value, n) over the WHOLE field — the number published trackers show.

    NOT A MODEL INPUT, and it exists so the page does not have to leave that
    difference unexplained. Our approval is the average of adults-sample polls,
    because the coefficients were fit on a Gallup column and Gallup interviewed
    adults. Published trackers average every population, and likely-voter
    samples run seven to eight points better for this president than adult
    samples do. The two numbers are both right about different questions, and a
    reader who sees only ours will reasonably conclude we have made a mistake.
    """
    import sys as _sys
    _sys.path.insert(0, str(REPO / "forecast" / "collect"))
    try:
        import sb_approval as _sb
    except ImportError:
        return None
    import datetime as _dt
    hist = _sb.load_history(cycle)
    if not hist:
        return None
    return _sb.field_average(hist, asof or _dt.date.today().isoformat())


def aggregate_approval(cycle: int):
    """The published aggregators' consensus, for display beside our input.

    NOT AN INPUT, for two reasons that are worth keeping distinct. It carries
    the same whole-field offset as any other tracker, so it is 4 to 5 points
    off the instrument these coefficients were fitted on. And it has no
    history: an aggregator overwrites its average in place, so a past value is
    recoverable only if we happened to read it that day, and the backfill needs
    a value for every date back to January 2025.

    It is the number most readers will recognise, which is exactly why it
    belongs on the page. model/approval.py writes the full series.
    """
    import json as _json
    f = DATA / str(cycle) / "derived" / "approval.json"
    if not f.exists():
        return None
    try:
        latest = _json.loads(f.read_text())["series"]["aggregate"]["latest"]
    except (KeyError, ValueError):
        return None
    if not latest:
        return None
    return {"approval_aggregate": latest["approve"],
            "approval_aggregate_n": latest["n"],
            "approval_aggregate_low": latest["low"],
            "approval_aggregate_high": latest["high"],
            "approval_aggregate_date": latest["date"]}


# THE DEFAULT BASIS IS "adults", AND IT USED TO BE A SHIFTED ONE. The reasoning
# for the change is written out in collect/sb_approval.py and comes down to
# this: the population is the large term and the house effect is the small one,
# the small one drifts, and it cannot be re-measured now that Gallup has
# stopped polling. GALLUP_HOUSE_EFFECT below is kept for the Wikipedia fallback
# route it was measured on, which is reached only when the Silver capture is
# missing.
def approval_from_archive(cycle: int, asof: str | None = None,
                          basis: str = "adults") -> tuple[float, str, int]:
    """(approval, source, n). Three tiers, and each says which one it used.

    1. INDIVIDUAL POLLS whose field period ended on or before `asof`, from the
       raw Wikipedia captures. This is the only tier that makes a past value a
       computation rather than a reconstruction, and it is the one the model
       backfill needs.
    2. THE AGGREGATOR TABLE, if no poll falls in the window. Their numbers are
       real and dated, but they are somebody's model output rather than raw
       interviews, and for a past date they are only as good as the day we
       happened to capture them.
    3. THE CONSTANT, which announces itself.

    COVERAGE, measured on 2026-08-25: 123 individual polls in 2025 and 5 in
    2026. The page has largely stopped carrying monthly nationwide tables for
    the election year. So tier 1 supports a 2025 backfill and does NOT support
    a 2026 one, which is the half the fundamentals models actually need. Silver
    Bulletin publishes the same polls densely and is the intended second
    source; nothing here assumes Wikipedia is the only one.
    """
    import datetime as _dt
    import sys as _sys
    _sys.path.insert(0, str(REPO / "forecast" / "collect"))
    try:
        import wiki_approval as _wa
    except ImportError:
        return (APPROVAL_FALLBACK,
                "hand-set constant — collect/wiki_approval.py not importable",
                0)

    # SILVER BULLETIN FIRST, and this is the tier that made the others
    # theoretical. Wikipedia's nationwide tables carry 123 polls for 2025 and
    # five for 2026, so tier 1 below supported a 2025 backfill and not a 2026
    # one — which meant every date from August 2025 to May 2026 fell through to
    # the constant, and both approval-driven models drew a flat line across ten
    # months because of a missing feed rather than a still electorate. Silver's
    # sheet carries 1,323 headline polls across both years with no month below
    # 38, and collect/sb_approval.py does the reading.
    #
    # IT ALSO CORRECTS THE OFFSET THIS FILE MEASURED. GALLUP_HOUSE_EFFECT below
    # is -4.03, measured on the three overlapping readings Wikipedia carried.
    # Silver's file carries twelve, running to Gallup's last poll on
    # 2025-12-15, and on those twelve the gap against the whole field is -5.35
    # and drifting, while the gap against ADULTS-POPULATION polls alone is
    # -1.87 and roughly flat. Gallup interviews adults; three quarters of the
    # "house effect" was a population effect. The reasoning is written out in
    # sb_approval.py, and the constants below are kept for the Wikipedia route
    # they were measured on rather than being quietly restated.
    _sb = None
    try:
        import sb_approval as _sb
    except ImportError:
        pass
    if _sb is not None:
        hist = _sb.load_history(cycle)
        if hist:
            got = _sb.approval_on(hist, asof or _dt.date.today().isoformat(),
                                  basis=basis)
            if got:
                return got

    if cycle not in _WA_CACHE:
        _WA_CACHE[cycle] = _wa.load_history(cycle)
    everything = _WA_CACHE[cycle]
    gallup = [p for p in everything
              if "gallup" in (p.get("pollster") or "").lower()]
    field = [p for p in everything
             if "gallup" not in (p.get("pollster") or "").lower()]

    # THE END DATE IS THE DATE BEING ASKED ABOUT, never "the last date this
    # particular subset happens to hold". Defaulting it per-subset is how the
    # first version of this returned March 2025's Gallup reading as today's
    # approval: Gallup stopped publishing, so `gallup[-1]` is seventeen months
    # old, and the model ran on it without a word. `asof=None` means now.
    end = asof or _dt.date.today().isoformat()

    # ROUTE ONE IS OFF BY DEFAULT, and the reason is consistency rather than
    # accuracy. Where a real Gallup reading exists it is unarguably the better
    # number: it is the exact basis the coefficients were fit on and it needs
    # no adjustment. But Gallup has left the field, so it exists for the first
    # three months of 2025 and nowhere else, and preferring it would put a
    # visible seam in the middle of every backfilled series — unadjusted Gallup
    # until March 2025, shifted field thereafter, with the join reading as a
    # change in the world rather than a change in the instrument.
    #
    # One instrument for the whole series, even where a better one exists for
    # part of it, is the same trade the chained-index decision made and it goes
    # the same way. `basis="gallup_raw"` still gets the unadjusted readings for
    # anyone comparing the two.
    if basis == "gallup_raw" and gallup:
        cut = (_dt.date.fromisoformat(end)
               - _dt.timedelta(days=APPROVAL_WINDOW_GALLUP)).isoformat()
        win = [p["approve"] for p in gallup if cut <= p["date"] <= end]
        if win:
            return (round(sum(win) / len(win), 2),
                    f"mean of {len(win)} Gallup reading(s) in the "
                    f"{APPROVAL_WINDOW_GALLUP} days to {end} — the basis the "
                    f"coefficients were fit on, unadjusted "
                    f"(Wikipedia, CC BY-SA)", len(win))

    # ROUTE TWO: the rest of the field, shifted onto the Gallup scale.
    polls = field if basis in ("adults", "gallup") else everything
    adj = GALLUP_HOUSE_EFFECT if basis in ("adults", "gallup") else 0.0
    if polls and end:
        cut = (_dt.date.fromisoformat(end)
               - _dt.timedelta(days=APPROVAL_WINDOW_DAYS)).isoformat()
        win = [p["approve"] for p in polls if cut <= p["date"] <= end]
        if win:
            raw = sum(win) / len(win)
            note = (f" then shifted {GALLUP_HOUSE_EFFECT:+.2f} onto the Gallup "
                    f"scale the model was fit on (offset measured on "
                    f"{GALLUP_HOUSE_EFFECT_N} overlapping readings)"
                    if adj else "")
            return (round(raw + adj, 2),
                    f"mean of {len(win)} approval poll(s) in the "
                    f"{APPROVAL_WINDOW_DAYS} days to {end}{note} "
                    f"(Wikipedia, CC BY-SA)", len(win))

    # TIER 2 GETS THE SAME SHIFT. An aggregator's average is multi-pollster by
    # definition, so it sits on the field scale rather than the Gallup one and
    # needs the identical correction. An earlier version of this refused tier 2
    # on a Gallup basis entirely, which was right while the plan was to feed
    # the model actual Gallup and wrong once Gallup stopped publishing: it left
    # the model on a hand-set constant forever rather than on the best
    # available number, correctly scaled.
    #
    # Tier 2 only fires for a date at or after the capture that holds it: an
    # aggregator row is stamped with the day it was read, and reaching back
    # with it would put August's reading on a March projection.
    import glob as _glob
    import json as _json
    files = sorted(f for f in _glob.glob(str(
        DATA / str(cycle) / "raw" / "wiki_approval" / "*" / "*.json"))
        if not f.endswith(".meta.json"))
    for f in reversed(files):
        day = _Path(f).parent.name
        if asof and day > asof:
            continue
        try:
            got = _wa.extract(_wa.read_capture(_Path(f)))
        except Exception:
            continue
        vals = [g["approve"] for g in got["aggregators"]
                if g.get("approve") is not None]
        if vals:
            adj = GALLUP_HOUSE_EFFECT if basis in ("adults", "gallup") else 0.0
            note = (f", shifted {GALLUP_HOUSE_EFFECT:+.2f} onto the Gallup "
                    f"scale" if adj else "")
            return (round(sum(vals) / len(vals) + adj, 2),
                    f"mean of {len(vals)} published aggregator(s) as read on "
                    f"{day}{note} — aggregates, NOT individual polls "
                    f"(Wikipedia, CC BY-SA)", len(vals))

    if basis in ("adults", "gallup"):
        return (APPROVAL_FALLBACK,
                f"hand-set constant — the archive holds {len(polls)} Gallup "
                f"reading(s), and this model was fit on a Gallup-only column, "
                f"so a multi-pollster average would be a different model under "
                f"the same name. Anything driven by this is flat by "
                f"construction and not by evidence", len(polls))
    return (APPROVAL_FALLBACK,
            "hand-set constant — no dated approval in the archive, so anything "
            "driven by this is flat by construction and not by evidence", 0)


def income_from_archive(cycle: int,
                        asof: str | None = None) -> tuple[float, str] | None:
    """(value, provenance) from the newest parsed date at or before `asof`.

    `asof` IS WHAT MAKES A BACKFILL HONEST. Without it this returns today's
    FRED reading for every date it is asked about, and a reconstructed series
    would carry August's income growth back to January — a number nobody could
    have had. With it, each past date gets the last reading published by then,
    which is exactly what the model would have run on.
    """
    # Read every parsed file ONCE and keep only the dates that carry FRED. The
    # backfill calls this per date and the archive is hundreds of files.
    if cycle not in _FRED_CACHE:
        found = []
        for f in sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv"))):
            vals = {}
            with open(f, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r["source_id"] == "fred":
                        try:
                            vals[r["quantity"]] = float(r["value"])
                        except (TypeError, ValueError):
                            pass
            if INCOME_QUANTITY in vals:
                months = vals.get("income_ytd_months")
                where = Path(f).stem
                found.append((where, vals[INCOME_QUANTITY],
                              f"FRED {INCOME_QUANTITY} as of {where}"
                              + (f", {int(months)} month(s) of the year in hand"
                                 if months else "")))
        _FRED_CACHE[cycle] = found
    for where, v, prov in reversed(_FRED_CACHE[cycle]):
        if asof and where > asof:
            continue
        return v, prov
    return None


# ---------------------------------------------------------------------------
# Income growth, as of a date
# ---------------------------------------------------------------------------
# THREE ROUTES, IN DESCENDING ORDER OF HOW MUCH THEY DESERVE TO BE BELIEVED,
# and each one says which it is rather than all three returning a bare float.
#
#   1. CAPTURED   the parsed FRED row from a snapshot at or before the date.
#                 A commitment we made on the day. Available from 2026-08-20,
#                 when the FRED capture started.
#   2. ARCHIVAL   an ALFRED vintage: BEA's figure AS PUBLISHED on that date,
#                 stored by collect/alfred_income.py. Not our commitment, but
#                 the source's own dated one, which is the next best thing and
#                 the thing score/RULES.md ss10 calls archival.
#   3. RETROSPECTIVE  today's monthly series, truncated to the months that had
#                 been RELEASED by that date. The truncation is right and the
#                 revisions are not: a month published in June 2025 has been
#                 revised since, and this route reads the revised figure. It
#                 is today's data restricted to yesterday's calendar.
#
# WHY ROUTE 3 EXISTS AT ALL, given route 2 is strictly better. ALFRED is
# reachable from a normal terminal and not from every environment this repo
# gets edited in, so the vintages have to be captured deliberately rather than
# arriving with the daily run. Route 3 keeps the backfill working in the
# meantime and LABELS ITSELF, and the moment the vintages land route 2 takes
# over with no change to any caller. Anything scored as real-time should be
# checking `income_basis`, not assuming.
#
# THE RELEASE RULE, checked against three known vintages rather than assumed:
# month M (the observation dated M-01) is on the record from the first day of
# month M+2. BEA publishes personal income for month M near the end of M+1.
#     ALFRED vintage 2025-06-02  ->  last observation 2025-04-01   consistent
#     ALFRED vintage 2026-08-03  ->  last observation 2026-06-01   consistent
#     FRED capture  2026-08-25   ->  last observation 2026-06-01   consistent
INCOME_RELEASE_LAG_MONTHS = 2

# --------------------------------------------------------------------------
# THE JANUARY HOLE, AND WHY THE ANSWER IS TO CARRY RATHER THAN TO RECOMPUTE
#
# income_as_of returns year-to-date growth: this year's months averaged against
# last year's. _ytd_growth returns None when NO month of the current calendar
# year has been released yet, and with a two-month release lag that is true of
# every 1 January through to roughly 1 March.
#
# So the quantity went undefined for the first two months of every year, and
# both models that read it — class_fundamentals and academic_referendum —
# simply declined to run. Measured on the real history: class_fundamentals was
# absent for 59 consecutive days (2025-12-31 to 2026-03-01) and
# academic_referendum for 83 days across the Januaries and Februaries of both
# years, in a weekly on-off flicker because the FIRST route in income_as_of
# (income_from_archive) still worked on days a capture happened to exist.
#
# The visible damage was not the gap. It was that the fundamentals average is
# a mean over whoever reported that day, so the line sawtoothed by about three
# seats every week through both Januaries and then stepped 232.1 -> 236.7 in a
# single day on 2026-03-01 when the fifth member came back. None of that was a
# model changing its mind.
#
# WHAT WAS REJECTED. Redefining income as trailing-twelve-month growth would
# remove the hole and is arguably the better statistic, but it is a different
# variable from the one the models are fitted on — an annual average against an
# annual average — so it is a change of specification, which by MODEL_ID above
# is a change of model identity and a rebuild of the whole history.
#
# WHAT THIS DOES INSTEAD. On a date where no route resolves, fall back to the
# last date at which one did, which is deterministically 31 December of the
# prior year: the last day the completed year-to-date figure existed. The
# value is unchanged, the specification is unchanged, and the models keep
# reporting a constant number through January.
#
# THE LINE GOES FLAT, AND THAT IS THE HONEST SHAPE. In January these models
# have no new information; a flat line says so, where a gap said something
# false about the roster. The provenance string records the carry and the basis
# is `carried`, so nothing downstream mistakes it for a fresh reading.
#
# ANCHORED, NOT ITERATIVE. The fallback always targets 31 December of the prior
# year rather than "the last date this worked", so it is path-independent: the
# answer for 2026-01-15 is the same whether computed on that day or rebuilt in
# November, and it can never chain one carry onto another.
#
# The cap is a tripwire, not a policy. Two months is the expected carry; if
# this is ever reaching back further than a third of a year the income capture
# has been broken for a long time and the right outcome is the gap plus the
# operator noticing, not a value from last spring plotted as today's.
INCOME_CARRY_MAX_DAYS = 120


def _ytd_growth(monthly: dict[str, float], asof: str) -> tuple[float, int] | None:
    """(year-to-date growth vs the prior full year, months in hand).

    The same arithmetic collect/parsers/fred.py does for income_growth_ytd —
    an average of the year's months against an average of the prior year's —
    restricted to observations released by `asof`. Deliberately the same shape
    as the fitted quantity, which is an annual average against an annual
    average; a last-month-over-last-month figure is a different variable.
    """
    y, m = int(asof[:4]), int(asof[5:7])
    cutoff = y * 12 + (m - 1) - INCOME_RELEASE_LAG_MONTHS
    by_year: dict[int, list[float]] = {}
    for d0, v in monthly.items():
        yy, mm = int(d0[:4]), int(d0[5:7])
        if yy * 12 + (mm - 1) > cutoff:
            continue
        by_year.setdefault(yy, []).append(v)
    if y not in by_year or (y - 1) not in by_year:
        return None
    cur = sum(by_year[y]) / len(by_year[y])
    prev = sum(by_year[y - 1]) / len(by_year[y - 1])
    if not prev:
        return None
    return (cur - prev) / prev * 100, len(by_year[y])


def _newest_fred_monthly(cycle: int) -> dict[str, float]:
    if "monthly" in _FRED_CACHE:
        return _FRED_CACHE["monthly"]
    out: dict[str, float] = {}
    base = DATA / str(cycle) / "raw" / "fred"
    if base.exists():
        days = sorted((d for d in base.iterdir() if d.is_dir()), reverse=True)
        for day in days:
            f = day / "income_monthly.csv"
            if not f.exists():
                continue
            for row in csv.reader(f.open(encoding="utf-8", errors="replace")):
                if len(row) < 2 or not row[0][:1].isdigit():
                    continue
                try:
                    out[row[0].strip()] = float(row[1])
                except ValueError:
                    continue
            break
    _FRED_CACHE["monthly"] = out
    return out


def income_as_of(cycle: int, asof: str | None = None, carry: bool = True):
    """(value, provenance string, basis) or None. basis names the route taken.

    carry=False disables the January carry-forward described at
    INCOME_CARRY_MAX_DAYS. It exists so the carry itself can recurse exactly
    once, into a date where a real reading is expected, and never onto another
    carried value.
    """
    end = asof or dt.date.today().isoformat()

    got = income_from_archive(cycle, asof)
    if got:
        return got[0], got[1], "captured"

    import sys as _sys
    _sys.path.insert(0, str(REPO / "forecast" / "collect"))
    try:
        import alfred_income as _al
    except ImportError:
        _al = None
    if _al is not None:
        vs = [v for v in _al.vintages_on_disk(cycle) if v <= end]
        if vs:
            obs = _al.read_vintage(cycle, vs[-1])
            g = _ytd_growth(obs, end)
            if g:
                return (round(g[0], 3),
                        f"ALFRED vintage {vs[-1]} of {_al.SERIES} — BEA's "
                        f"figure as published on that date, {g[1]} month(s) of "
                        f"{end[:4]} in hand", "archival")

    monthly = _newest_fred_monthly(cycle)
    if monthly:
        g = _ytd_growth(monthly, end)
        if g:
            return (round(g[0], 3),
                    f"today's FRED capture truncated to the {g[1]} month(s) of "
                    f"{end[:4]} released by {end} — RETROSPECTIVE: the calendar "
                    f"is right and the revisions are not, because a month "
                    f"published then has been revised since. Run "
                    f"collect/alfred_income.py --capture to replace this with "
                    f"BEA's own dated figure", "retrospective")

    # Every route is dead. Before giving up, carry the last figure that
    # existed — see INCOME_CARRY_MAX_DAYS above for why this is a carry and
    # not a recomputation.
    if carry:
        anchor = f"{int(end[:4]) - 1}-12-31"
        if anchor < end:
            days = (dt.date.fromisoformat(end)
                    - dt.date.fromisoformat(anchor)).days
            if days <= INCOME_CARRY_MAX_DAYS:
                got = income_as_of(cycle, anchor, carry=False)
                if got:
                    return (got[0],
                            f"{got[1]} — CARRIED FORWARD {days} day(s) from "
                            f"{anchor}: year-to-date growth for {end[:4]} does "
                            f"not exist yet, because no month of {end[:4]} has "
                            f"been released. Not a new reading",
                            "carried")
    return None


def predict(b, approval, income, seats_before):
    return b[0] + b[1]*approval + b[2]*income + b[3]*seats_before


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
# WHY THIS IS ALLOWED HERE AND REFUSED ELSEWHERE. seats.py carries a standing
# rule that a projection cannot be recomputed for a past date, because the
# inputs are overwritten every run and a rebuilt number is today's model on
# today's evidence wearing an old date. It names THIS FILE as one of the
# reasons: "polling_model.json and fundamentals_model.json are overwritten
# every run".
#
# That was true of the file and never true of the model. This equation has
# three inputs and all three are now datable:
#
#     approval        the poll list, by each poll's own end date
#     income growth   the FRED capture, by the date it was parsed
#     seats_before    220, a fact about 2024 that does not move
#
# So a past point is a computation over the evidence that existed on that date,
# not a reconstruction of one, which is the same standard academic.py's BEW
# backfill had to meet. The coefficients are today's, and they are today's for
# every date including this one — they are fit on 1946-2022 and do not change
# within a cycle.
#
# STAMPED `backfilled`, NOT `captured`, all the same. The evidence is the
# evidence of the day; the code that read it is the code of today. Anyone
# scoring this series should know which points the model actually emitted at
# the time (from 2026-08-19, when the run went daily) and which it did not.
BACKFILL_PROVENANCE = "backfilled"


# ---------------------------------------------------------------------------
# MODEL IDENTITY AND VERSIONING
# ---------------------------------------------------------------------------
# The class model is going to change. Each homework adds to it — candidate
# quality, incumbency, whatever comes next — and the point of putting it on the
# site beside the professionals is to watch what each addition does.
#
# THAT ONLY WORKS IF A CHANGE OF SPECIFICATION IS A CHANGE OF IDENTITY.
#
# If v2 overwrites `class_fundamentals`, the archive holds one line whose
# recipe silently changed halfway along. Every comparison the exercise is for
# — did adding candidate quality move the forecast, and by how much — becomes
# invisible, because the before and the after are the same series. publish.py
# already carries a `mixed_recipe` flag for exactly this failure in the
# academic family, so we know what it looks like when it happens.
#
# So: CHANGING THE SPECIFICATION MEANS A NEW MODEL_ID. The old id keeps its
# history untouched, the new one starts its own, and both draw. Under the
# frozen-alpha roster adjustment a new id is held out of the category line
# until it has fourteen days of overlap, then gets its own lean and joins —
# which is the correct treatment for a model nobody was running before.
#
# What counts as a new version: any change to the right-hand side, the
# estimation sample, or the functional form. What does not: a bug fix that
# makes the model compute what it always claimed to, or a new input VINTAGE
# for the same variable.
#
# Bump MODEL_VERSION and MODEL_ID together, and add the new id to
# collect/facets.py so it lands on the right two lines rather than falling
# through to a default.
MODEL_ID = "class_fundamentals"
MODEL_VERSION = 1
MODEL_SPEC = "approval + real income growth + seats defended; OLS on 1946-2022"


def backfill_dates(cycle: int) -> list[str]:
    return sorted(Path(f).stem for f in
                  glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")))


def backfill(a) -> int:
    """Run the equation on every archived date and write the history file."""
    b, loeo, _ = fit()
    z = 1.2816
    dates = backfill_dates(a.cycle)

    # ALIGN TO THE ACADEMIC GRID BY DEFAULT, because two families plotted on
    # different date grids read as two families disagreeing. academic.py picks
    # its dates from where the poll record can actually support a
    # reconstruction; borrowing that grid means every backfilled day on the
    # chart carries both, and a gap in one is a gap in both for the same
    # reason. --every overrides it for anyone who wants the dense version.
    ah = DATA / str(a.cycle) / "model_private" / "academic_models_history.json"
    if a.every == 1 and ah.exists():
        want = set(json.loads(ah.read_text()))
        aligned = sorted(want & set(dates))
        if len(aligned) >= 10:
            print(f"  aligned to the academic grid: {len(aligned)} date(s) "
                  f"({len(want - set(dates))} academic date(s) have no parsed "
                  f"file and are skipped)")
            dates = aligned
    if a.every > 1:
        # Keep the two ends: the first date anchors the series and the last is
        # today, which the daily run writes anyway. Thinning the middle is a
        # cost decision, not a modelling one.
        dates = sorted(set(dates[::a.every]) | {dates[0], dates[-1]})
    hist: dict[str, dict] = {}
    priv = DATA / str(a.cycle) / "model_private"
    p = priv / "fundamentals_model_history.json"
    if p.exists():
        hist = json.loads(p.read_text())

    flat = 0
    no_income = 0
    no_income_eg: list[str] = []
    for date in dates:
        ap_v, ap_src, ap_n = approval_from_archive(a.cycle, date)
        got = income_as_of(a.cycle, date)
        if got is None:
            # COUNTED, NOT SILENT. This branch dropped 97 of 582 dates on the
            # first daily backfill without saying a word, which put a
            # two-month hole in the middle of the class fundamentals line
            # (2025-12-31 to 2026-03-01) that no line of the run log
            # explained. The skip itself is right — no income vintage means no
            # forecast, and inventing one would be the same sin as the flat
            # approval constant below — but a gap the reader can see has to be
            # a gap the operator was told about.
            # Since the carry-forward at INCOME_CARRY_MAX_DAYS this branch
            # should be close to empty: it now means no income figure existed
            # even at the end of the PRIOR year, which is a real hole in the
            # capture rather than a January calendar artefact. A large count
            # here is worth chasing.
            no_income += 1
            if len(no_income_eg) < 3:
                no_income_eg.append(date)
            continue
        # A DATE WITH NO REAL APPROVAL IS SKIPPED, not filled with the
        # constant. That is the whole lesson of the ten flat months this
        # backfill exists to remove: a fallback value plotted as a point is a
        # claim that approval sat still, and the chart cannot tell the reader
        # otherwise. No point is better than a flat one.
        if ap_n == 0 or "hand-set" in ap_src:
            flat += 1
            continue
        income, income_prov, income_basis = got
        pp = predict(b, ap_v, income, a.seats_before)
        hist[date] = {
            "margin_D": round(100 - 2 * pp, 2),
            "margin_D_80_low": round(100 - 2 * (pp + z * loeo), 2),
            "margin_D_80_high": round(100 - 2 * (pp - z * loeo), 2),
            "inputs": {"approval": ap_v, "approval_source": ap_src,
                       "approval_n": ap_n, "income_growth": income,
                       "income_source": income_prov,
                       "income_basis": income_basis,
                       "seats_before": a.seats_before},
            "provenance": BACKFILL_PROVENANCE,
            # Identity travels with the row. seats.py keys the projection on
            # `model_id`, so bumping it here is the whole of shipping a new
            # version — nothing downstream needs editing.
            "model_id": MODEL_ID,
            "model_version": MODEL_VERSION,
            "model_spec": MODEL_SPEC,
        }

    priv.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hist, indent=2, sort_keys=True))
    vals = [h["margin_D"] for h in hist.values()]
    print(f"  wrote model_private/fundamentals_model_history.json   PRIVATE — "
          f"{len(hist)} date(s)")
    if flat:
        print(f"  skipped {flat} date(s) with no dated approval — a fallback "
              f"constant is not a data point")
    if no_income:
        print(f"  skipped {no_income} date(s) with no income vintage in hand "
              f"(e.g. {', '.join(no_income_eg)}) — these are the holes in the "
              f"line. More ALFRED vintages would close them; see "
              f"collect/alfred_income.py and INCOME_RELEASE_LAG_MONTHS.")
    if vals:
        ks = sorted(hist)
        print(f"  {ks[0]} .. {ks[-1]}   margin_D {min(vals):+.2f} .. "
              f"{max(vals):+.2f}")
        for k in (ks[0], ks[len(ks)//2], ks[-1]):
            h = hist[k]
            print(f"      {k}  D{h['margin_D']:+6.2f}   approval "
                  f"{h['inputs']['approval']:.2f} (n={h['inputs']['approval_n']})"
                  f"   income {h['inputs']['income_growth']:+.2f}")
    return 0

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", type=int, default=2026)
    # WHEN THE LIVE APPROVAL FEED LANDS, THIS DEFAULT AND ITS CONSUMERS MOVE
    # TOGETHER. Approval is the single largest lever in this family: 0.27
    # margin points per approval point here, and the referendum model in
    # model/academic.py reads the same number through its own --approval.
    # Three things to do, not one:
    #   1. replace this default with the feed, and record its source in
    #      `approval_source` so the methods page stops saying "hand-set";
    #   2. re-run the academic backfill, which currently holds approval
    #      CONSTANT across the whole reconstructed history and says so — any
    #      line driven by approval is flat by construction until it is re-run
    #      against a real approval series;
    #   3. check the referendum model separately. Its fitted approval
    #      coefficient is 0.026, so a live feed will move this model and barely
    #      touch that one, and that difference is the finding rather than a bug.
    ap.add_argument("--approval", type=float, default=None,
                    help="president's approval, ADULTS basis. The historical "
                         "column is Gallup and Gallup interviewed adults, so a "
                         "whole-field or likely-voter average is 4 to 5 points "
                         "off the instrument. See derived/approval.json for the "
                         "three constructions side by side.")
    ap.add_argument("--income", type=float, default=None,
                    help="real income growth, pct. Default: read from the FRED "
                         "capture (income_growth_ytd). Pass a value to override.")
    ap.add_argument("--seats-before", type=float, default=220,
                    help="seats the president's party won last time (R won 220 in 2024)")
    ap.add_argument("--date", default=None,
                    help="run the model as of a past date: approval from the "
                         "polls that had ended by then, income from the last "
                         "FRED reading published by then.")
    ap.add_argument("--backfill", action="store_true",
                    help="run the equation on every archived date and write "
                         "model_private/fundamentals_model_history.json, which "
                         "seats.py --backfill-history projects into seats. "
                         "Dates with no dated approval are SKIPPED, not filled "
                         "with the fallback constant.")
    ap.add_argument("--every", type=int, default=1,
                    help="with --backfill, keep every Nth date. The two ends "
                         "are always kept.")
    a = ap.parse_args(argv)

    b, loeo, r2 = fit()
    if a.backfill:
        # BACKFILL THEN CONTINUE, rather than returning here.
        #
        # This used to `return backfill(a)`, so `fundamentals.py --backfill`
        # wrote the history and left derived/fundamentals_model.json untouched
        # — still holding whatever the last live run produced. A rebuild
        # sequence that runs the backfill in place of a live run therefore ends
        # with a stale live model, and sanity.py catches it as "model was built
        # with income_growth=-0.039 but the archive now says -0.096" and
        # publishes nothing. Correct of sanity, and an avoidable trap: the
        # backfill and the live run read the same inputs, so doing both is
        # free and the command sequence becomes order-independent.
        backfill(a)
        print()
    if a.income is not None:
        income, income_prov, placeholder = a.income, "command line", False
    else:
        got = income_from_archive(a.cycle, a.date)
        if got:
            income, income_prov, placeholder = got[0], got[1], False
        else:
            # No FRED capture yet. Fall back, but say so loudly and mark it in
            # the output so the page can label it rather than implying the
            # number is grounded.
            income, income_prov, placeholder = 1.5, "PLACEHOLDER (no FRED capture found)", True
    a.income = income

    # APPROVAL COMES FROM THE ARCHIVE UNLESS FORCED. --approval still wins, so
    # a class can ask what the model says at 45%; what changed is which way
    # round the default runs.
    if a.approval is None:
        a.approval, approval_src, approval_n = approval_from_archive(
            a.cycle, a.date)
    else:
        approval_src = (f"forced to {a.approval} on the command line, not read "
                        f"from the archive")
        approval_n = 0
    pp = predict(b, a.approval, a.income, a.seats_before)   # president's party share
    margin_d = 100 - 2*pp                                    # D minus R
    z = 1.2816                                               # 80%
    out = {
        "cycle": a.cycle,
        "fitted_on": f"{len(HISTORY)} midterms, {HISTORY[0][0]}-{HISTORY[-1][0]}",
        "coefficients": {"intercept": round(b[0],4), "approval": round(b[1],4),
                         "income_growth": round(b[2],4), "seats_before": round(b[3],4)},
        "r2": round(r2,3), "loeo_rmse": round(loeo,3),
        "inputs": {"approval": a.approval, "approval_source": approval_src,
                   "approval_n": approval_n, "income_growth": a.income,
                   "seats_before": a.seats_before,
                   "income_is_placeholder": placeholder,
                   "income_source": income_prov,
                   # For display only. Nothing multiplies by this; it is here
                   # so the page can print the number every other approval
                   # tracker is showing beside the one the model eats, and
                   # name the difference as a population difference.
                   **({"approval_field": _fa[0], "approval_field_n": _fa[1]}
                      if (_fa := field_approval(a.cycle, a.date)) else {}),
                   **(_ag if (_ag := aggregate_approval(a.cycle)) else {})},
        # NOTE: `approval_source` used to appear TWICE in this dict, the second
        # one a literal "hand-set; no live approval feed yet". A later key wins
        # in a Python literal, so the real source string — window, poll count,
        # adjustment and all — was computed, passed in, and thrown away on the
        # next line. Every consumer of this file, including the methods page,
        # read "hand-set" no matter what the model had actually done.
        "pres_party_two_party_vote": round(pp,2),
        "margin_D": round(margin_d,2),
        "margin_D_80_low": round(100-2*(pp+z*loeo),2),
        "margin_D_80_high": round(100-2*(pp-z*loeo),2),
        # CENTRED ON THE ACTUAL VALUE, not on a hard-coded 38. The ladder used
        # to run 34 to 42, which was a sensible bracket when approval was a
        # typed-in 38 and is the wrong bracket now that it is read from polls
        # on the adults scale. A sensitivity table whose centre drifts away
        # from the model's own input stops answering the question it is for.
        "sensitivity": [{"approval": round(a.approval + k, 1),
                         "margin_D": round(100-2*predict(
                             b, a.approval + k, a.income, a.seats_before), 2)}
                        for k in (-4, -2, 0, 2, 4)],
    }
    d = DATA / str(a.cycle) / "derived"; d.mkdir(parents=True, exist_ok=True)
    (d / "fundamentals_model.json").write_text(json.dumps(out, indent=2))
    print(f"  fundamentals: D+{out['margin_D']:.1f} "
          f"(80% D+{out['margin_D_80_low']:.1f} to D+{out['margin_D_80_high']:.1f}), "
          f"LOEO {loeo:.2f}")
    if out["inputs"]["income_is_placeholder"]:
        print("  WARNING: income growth is still the 1.5 placeholder — the FRED")
        print("           capture has not run yet. ./forecast/run.sh will fix it.")
    else:
        print(f"  income growth {a.income:+.2f}%  ({income_prov})")
    print(f"  approval {a.approval:.2f} — {approval_src}")
    fa = out["inputs"].get("approval_field")
    if fa is not None:
        print(f"  for comparison, the whole field (every population) reads "
              f"{fa:.2f} over {out['inputs']['approval_field_n']} poll(s). "
              f"The {fa - a.approval:.1f}-point gap is population, not error.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
