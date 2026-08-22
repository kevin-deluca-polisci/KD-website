#!/usr/bin/env python3
"""
Stage 3 — published ACADEMIC forecasting models, reimplemented.

WHY THIS IS ITS OWN FAMILY, AND NOT MORE FUNDAMENTALS.

The site already carries a fundamentals model and a polling model, both ours.
This file carries other people's models: specifications published in the
political-science forecasting literature, rebuilt here from their papers and
run on our data.

The licence position is completely different from every other outside source on
this site, and that is the point. Decision Desk HQ, The Economist and 50+1
publish NUMBERS, and a number is theirs; republishing it needs permission, which
is why the professional category is stuck one gated contributor short of MIN_N
and why four permission letters are the only route open. An academic model
publishes an EQUATION. Reimplementing a published equation and running it on
inputs we captured ourselves is ordinary scholarship, not redistribution. There
is nothing to ask for and nobody to wait on.

That asymmetry is worth a class session on its own: the most restrictive data on
this site is the commercial forecast, and the least restrictive is the
peer-reviewed one.

WHAT THIS FILE IS CAREFUL NOT TO CLAIM. A model here is OUR IMPLEMENTATION of a
published specification, fitted on OUR history, fed OUR inputs. Where the
authors have published a 2026 number of their own we record it as a benchmark
and print the gap, but our figure is not their forecast and must never be
labelled as one. `attribution` on each model carries the wording the site is
allowed to use.

Publication: `individual` throughout. The equations are published, the inputs
are ours, the arithmetic is ours.

Adding a model: append to MODELS. Each entry is a dict with a `run` callable
that takes a Ctx and returns a Result, or `None` when its inputs are missing.
seats.py picks up whatever lands in academic_models.json and pushes each tide
through the same seat machinery as everything else, so a new model needs no
changes anywhere downstream.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fundamentals  # noqa: E402  — reuse HISTORY and _lstsq rather than fork them
import polling       # noqa: E402  — for generic_ballot(), reused not reimplemented

ELECTION_DAY = "2026-11-03"

# The first date any series on the site begins: inauguration day, the start of
# the term these midterms are a referendum on.
#
# It is a choice about the FRAME rather than about the data. The poll record
# runs back further than this and the reconstruction could too, but a line that
# starts in December 2024 invites the reader to compare a forecast of this
# midterm against a period when the administration it judges had not taken
# office. Every family starting on the same day also means the chart's left
# edge is one date rather than five, so a family that begins later is visibly
# late rather than merely differently scaled.
#
# NOTHING IS DELETED FOR THIS. Earlier dates stay in the archive — in raw/, in
# parsed/, and in the published category_averages.csv — and only the site's
# series start here. A frame is a display decision and must not be able to
# destroy evidence.
#
# collect/charts.py carries the same constant for the display side and the two
# must agree. It is repeated rather than imported because model/ and collect/
# share no module, and a cross-directory import for one string would be a
# worse dependency than a duplicated line with this comment attached.
SERIES_START = "2025-01-20"

# 2026: Republican president. BEW code this +1 for a Democratic president and
# -1 for a Republican one. Everything in this file that needs the president's
# party reads it from here rather than hard-coding a sign at the point of use,
# because a sign error here is silent and enormous.
PRESIDENT_PARTY = "R"
PRES_PARTY_BEW = +1 if PRESIDENT_PARTY == "D" else -1


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class Ctx:
    """Everything a model may read. Assembled once, passed to each run()."""
    cycle: int
    date: str
    days_to_election: int
    approval: float | None = None
    approval_source: str = ""
    income: float | None = None
    income_source: str = ""
    generic_ballot_D: float | None = None      # D minus R margin, UNSHRUNK
    generic_ballot_source: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class Result:
    margin_D: float                 # national two-party House margin, D minus R
    interval_80: tuple[float, float] | None = None
    inputs: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _read_polling(cycle: int) -> tuple[float, str] | None:
    """The RAW generic ballot, before our polling model shrinks it.

    BEW's coefficient IS the shrinkage — 0.49 on a poll 61-120 days out is the
    published estimate of how much of a lead survives to November. Feeding it a
    tide our own model has already shrunk by lambda would apply the discount
    twice and hand BEW a number no version of their paper describes.
    """
    p = DATA / str(cycle) / "derived" / "polling_model.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    gb = m.get("generic_ballot") or {}
    if gb.get("value") is None:
        return None
    return float(gb["value"]), f"{gb.get('used', 'generic ballot')} via polling_model.json"


def build_ctx(cycle: int, date: str, approval: float) -> Ctx:
    days = (dt.date.fromisoformat(ELECTION_DAY) - dt.date.fromisoformat(date)).days
    c = Ctx(cycle=cycle, date=date, days_to_election=days,
            approval=approval,
            approval_source="hand-set (Gallup basis); no live approval feed yet")

    got = fundamentals.income_from_archive(cycle)
    if got:
        c.income, c.income_source = got[0], got[1]
    else:
        c.notes.append("no FRED capture found — income-based models skipped")

    gb = _read_polling(cycle)
    if gb:
        c.generic_ballot_D, c.generic_ballot_source = gb
    else:
        c.notes.append("no polling_model.json — poll-based models skipped")
    return c


# ---------------------------------------------------------------------------
# MODEL 1 — Bafumi, Erikson & Wlezien
# ---------------------------------------------------------------------------
# Published coefficients, applied directly. This is the one model in the file
# we do NOT refit: the paper reports the table, the table is the model, and
# refitting it on a shorter history would produce something that is no longer
# theirs while still wearing their name.
#
# Both equations regress a Democratic share expressed as a DEVIATION FROM 50 on
# (a) the generic-ballot Democratic share, also as a deviation from 50, and
# (b) the president's party, +1 D / -1 R. Fitted on 15 midterms, 1946-2002.
#
#   vote  dev = c + b_poll * poll_dev + b_pres * pres_party
#   seats dev = c + b_poll * poll_dev + b_pres * pres_party
#
# Coefficients vary by how far out the poll was taken, which is the paper's
# actual finding: an early lead is roughly halved by November, and the
# presidential-party penalty shrinks as Election Day approaches.
#
# NOTE ON UNITS. Our generic ballot is a MARGIN (D minus R). BEW want a SHARE
# deviation, which is half the margin. Getting this wrong doubles the model's
# sensitivity to the polls and is invisible in the output.
BEW_CITE = ("Bafumi, Erikson & Wlezien, 'Balancing, Generic Polls and Midterm "
            "Congressional Elections', Journal of Politics 72(3), 2010")

# (max_days_out, poll, pres_party, constant, root_mse)
BEW_VOTE = [
    (30,  0.51, -1.09,  0.02, 1.90),
    (60,  0.48, -1.33, -0.43, 1.57),
    (120, 0.49, -1.46, -1.12, 1.79),
    (180, 0.50, -1.96, -1.30, 1.88),
    (240, 0.49, -2.15, -1.33, 1.82),
    (300, 0.46, -2.52, -1.68, 1.87),
]
BEW_SEATS = [
    (30,  1.06, -1.09,  1.98, 4.04),
    (60,  1.00, -1.59,  1.03, 3.30),
    (120, 1.01, -1.86, -0.45, 3.71),
    (180, 1.01, -2.90, -0.58, 4.19),
    (240, 1.04, -3.30, -0.92, 3.70),
    (300, 1.03, -4.13, -2.11, 3.22),
]


def _bew_window(table: list[tuple], days: int) -> tuple:
    """The published row whose window contains `days`.

    Outside the estimated range we clamp to the nearest row and say so. The
    paper does not fit polls taken more than 300 days out or after Election Day,
    and extrapolating a fitted coefficient past its support is the kind of thing
    that produces a confident number with nothing behind it.
    """
    for max_days, *coefs in table:
        if days <= max_days:
            return (max_days, *coefs)
    return table[-1]


def run_bew(c: Ctx) -> Result | None:
    if c.generic_ballot_D is None:
        return None
    days = c.days_to_election
    poll_dev = c.generic_ballot_D / 2.0          # margin -> share deviation

    vw, v_poll, v_pres, v_const, v_rmse = _bew_window(BEW_VOTE, days)
    sw, s_poll, s_pres, s_const, s_rmse = _bew_window(BEW_SEATS, days)

    vote_dev = v_const + v_poll * poll_dev + v_pres * PRES_PARTY_BEW
    margin_D = 2.0 * vote_dev                    # share deviation -> margin

    seat_dev = s_const + s_poll * poll_dev + s_pres * PRES_PARTY_BEW
    d_seat_share = 50.0 + seat_dev
    d_seats_direct = d_seat_share / 100.0 * 435.0

    z = 1.2816
    notes = []
    if days > 300:
        notes.append(f"{days} days out is beyond the paper's 300-day window; "
                     "coefficients clamped to the 241-300 day row")
    if days < 0:
        notes.append("election day has passed; coefficients clamped to 1-30 days")

    # THE TWO EQUATIONS DISAGREE, AND WE SHOW IT RATHER THAN PICKING ONE.
    #
    # BEW's vote equation and seats equation are estimated separately, so the
    # seat share their seats equation returns is not the seat share you get by
    # running their vote number through a seats-votes curve. On a 1946-2002
    # sample that gap was small. Applied to 2026 it is not, because the seats
    # equation was fitted before the current districting era and carries the
    # average pro-Democratic seat bonus of the mid-century House. Our own seat
    # machinery, which reads district baselines directly, has no such bonus.
    #
    # We publish the VOTE equation as the tide and let the site's shared seat
    # curve turn it into seats, because that is what makes this model
    # comparable with every other line on the page. The direct seats number
    # rides along as a diagnostic so the gap is visible instead of quietly
    # resolved.
    notes.append("tide is BEW's vote equation; their separate seats equation is "
                 "reported as a diagnostic and is not used downstream")

    return Result(
        margin_D=margin_D,
        interval_80=(margin_D - z * 2 * v_rmse, margin_D + z * 2 * v_rmse),
        inputs={
            "generic_ballot_D": c.generic_ballot_D,
            "generic_ballot_share_deviation": round(poll_dev, 3),
            "generic_ballot_source": c.generic_ballot_source,
            "days_to_election": days,
            "president_party": PRESIDENT_PARTY,
            "pres_party_coded": PRES_PARTY_BEW,
        },
        diagnostics={
            "vote_window_max_days": vw,
            "vote_coefficients": {"poll": v_poll, "pres_party": v_pres,
                                  "constant": v_const, "root_mse": v_rmse},
            "seats_window_max_days": sw,
            "seats_coefficients": {"poll": s_poll, "pres_party": s_pres,
                                   "constant": s_const, "root_mse": s_rmse},
            "bew_direct_D_seat_share": round(d_seat_share, 2),
            "bew_direct_D_seats": round(d_seats_direct, 1),
        },
        notes=notes,
    )


# ---------------------------------------------------------------------------
# MODEL 2 — the referendum specification
# ---------------------------------------------------------------------------
# Tufte's (1975) claim, still the backbone of the midterm literature: a midterm
# is a referendum on the administration, and two things carry it — how the
# president is doing and how the economy is doing. Lewis-Beck & Tien have run a
# version of this for decades and published a 2026 figure of roughly 28
# Republican seats lost.
#
# WHAT WE FIT AND WHAT WE DO NOT. Their published dependent variable is the
# president's party SEAT CHANGE. Ours is the president's party two-party House
# VOTE share, because that is the column our HISTORY table actually carries with
# a source behind it (Brookings Vital Statistics tables 2-2 and 2-4). Fitting
# their DV would mean typing in twenty midterm seat outcomes from memory, and a
# fabricated column is worse than a different one.
#
# So this is the referendum SPECIFICATION — approval and income, no exposure —
# fitted on our history and pushed through the site's shared seat curve. It is
# not Lewis-Beck & Tien's forecast and must not be labelled as one. Their number
# is recorded below as a benchmark to print alongside ours.
#
# WHY IT EARNS ITS PLACE NEXT TO OUR OWN FUNDAMENTALS MODEL. Our model is this
# model plus seats_before. The site's headline teaching finding is that approval
# ALONE explains almost nothing (R-squared 0.02) and that the referendum story
# only appears once you condition on exposure. Running the unconditioned
# specification beside the conditioned one turns that claim from an assertion on
# a methods page into two numbers a reader can watch diverge.
REFERENDUM_CITE = ("Tufte, 'Determinants of the Outcomes of Midterm "
                   "Congressional Elections', APSR 69(3), 1975; the modern "
                   "version is Lewis-Beck & Tien")
REFERENDUM_BENCHMARK = {
    "claim": "Republicans lose about 28 House seats and the House",
    "authors": "Charles Tien and Michael S. Lewis-Beck",
    "published": "2025-10-13",
    "where": "LSE USAPP blog",
    "note": "their DV is seat change, ours is vote share — the comparison is "
            "directional, not a reproduction",
}


def fit_referendum():
    """approval + income growth -> president's party two-party House vote.

    Same history, same estimator, same 2022 winsorisation as fundamentals.py.
    The ONLY difference from our own model is the missing seats_before column,
    which is exactly the contrast this model exists to draw.
    """
    inc = [r[5] for r in fundamentals.HISTORY]
    floor = min(v for r, v in zip(fundamentals.HISTORY, inc) if r[0] != 2022)
    rows = [(r[2], (floor if r[0] == 2022 else r[5]), r[3])
            for r in fundamentals.HISTORY]
    X = [[1.0, a, i] for a, i, _ in rows]
    y = [v for *_, v in rows]
    b = fundamentals._lstsq(X, y)

    errs = []
    for i in range(len(y)):
        Xi = [x for j, x in enumerate(X) if j != i]
        yi = [v for j, v in enumerate(y) if j != i]
        bi = fundamentals._lstsq(Xi, yi)
        errs.append(y[i] - sum(cf * x for cf, x in zip(bi, X[i])))
    loeo = (sum(e * e for e in errs) / len(errs)) ** 0.5

    ybar = statistics.fmean(y)
    resid = [y[i] - sum(cf * x for cf, x in zip(b, X[i])) for i in range(len(y))]
    r2 = 1 - sum(e * e for e in resid) / sum((v - ybar) ** 2 for v in y)
    return b, loeo, r2


def run_referendum(c: Ctx) -> Result | None:
    if c.approval is None or c.income is None:
        return None
    b, loeo, r2 = fit_referendum()
    pp = b[0] + b[1] * c.approval + b[2] * c.income
    margin_D = 100.0 - 2.0 * pp
    z = 1.2816
    return Result(
        margin_D=margin_D,
        interval_80=(100 - 2 * (pp + z * loeo), 100 - 2 * (pp - z * loeo)),
        inputs={
            "approval": c.approval, "approval_source": c.approval_source,
            "income_growth": c.income, "income_source": c.income_source,
        },
        diagnostics={
            "coefficients": {"intercept": round(b[0], 4),
                             "approval": round(b[1], 4),
                             "income_growth": round(b[2], 4)},
            "r2": round(r2, 3), "loeo_rmse": round(loeo, 3),
            "fitted_on": f"{len(fundamentals.HISTORY)} midterms, "
                         f"{fundamentals.HISTORY[0][0]}-{fundamentals.HISTORY[-1][0]}",
            "pres_party_two_party_vote": round(pp, 2),
            "benchmark": REFERENDUM_BENCHMARK,
        },
        notes=["no exposure term — this is the unconditioned referendum "
               "specification, and the gap against our fundamentals model IS "
               "the finding"],
    )


# ---------------------------------------------------------------------------
# MODEL 3 — state approval + state economy  (NOT IMPLEMENTED)
# ---------------------------------------------------------------------------
# Enns, Lagodny, Colner & Kumar's state presidential approval-state economy
# model called all fifty states in 2024 and would have called about 95% of
# states since 2000. It is the most interesting model on this list and the only
# one that would give the site a genuinely state-level academic forecast rather
# than a national tide pushed through our own geography.
#
# It is a STUB on purpose. The model needs two inputs we do not capture:
#
#   1. STATE-LEVEL PRESIDENTIAL APPROVAL. Their method extrapolates state
#      approval from national polls back to 1980 using MRP-style estimation.
#      We hold no state approval series and no national poll microdata to build
#      one from. This is the hard part and it is not a scraping problem.
#
#   2. STATE-LEVEL ECONOMIC CONDITIONS. Reachable — FRED carries state personal
#      income and state unemployment — but we capture neither today, and the
#      right series is whichever one their paper uses, which we have not read.
#
#   3. A MIDTERM DV. The published model forecasts presidential vote by state.
#      Senate races are not presidential races and the mapping is a research
#      question, not a port.
#
# Writing a plausible-looking version of this without those inputs would produce
# fifty numbers that look like a forecast and are not one. The stub records what
# is missing so the gap stays visible in the output instead of living in
# somebody's memory.
ENNS_CITE = ("Enns, Lagodny, Colner & Kumar, 'Understanding Biden's Exit and "
             "the 2024 Election: The State Presidential Approval-State Economy "
             "Model', PS: Political Science & Politics, October 2024")

ENNS_MISSING = [
    "state-level presidential approval estimates (MRP from national poll microdata)",
    "state-level economic series (FRED has state personal income and unemployment; "
    "we capture neither, and the paper's choice of series is unread)",
    "a specification for Senate races — the published DV is presidential vote by state",
]


def run_enns(c: Ctx) -> Result | None:
    return None


# ---------------------------------------------------------------------------
# MODEL 4 — economic pessimism  (NOT IMPLEMENTED: one input missing)
# ---------------------------------------------------------------------------
# Lockerbie's House model, and the most interesting of the unbuilt ones because
# its input is unlike anything else on this site. Every economic term we carry
# is REALISED — FRED's disposable income, actually earned, already measured.
# This one is EXPECTED: the share of people who say they will be worse off
# financially a year from now. Those two can point in opposite directions, and
# when they do, the disagreement is the finding.
#
# THE SPECIFICATION IS FULLY IN HAND, which is why the coefficients sit here as
# data rather than in a comment. Published in PS in 2024, fitted 1954-2024 on
# 35 observations:
#
#     seat change = 5.36 - 0.78 * (% expecting to be worse off)
#                        + 0.41 * (open seats, signed by the year's direction)
#
# with R-squared .42 and standard errors of .07 on the pessimism term. It
# forecast the incumbent party losing 12 seats in 2024.
#
# WHAT IS MISSING, precisely:
#
#   1. THE PESSIMISM SERIES. Michigan's Surveys of Consumers, Table 8 —
#      "Expected Change in Financial Situation in a Year" — carries the
#      better/same/worse split monthly back to 1960 and is downloadable as CSV.
#      We do not capture it, and the site has a usage agreement we have not
#      read. That is a capture job plus a licence question, not modelling.
#      Lockerbie takes the JUNE reading of the election year specifically, so a
#      monthly series is enough; we do not need it daily.
#
#   2. THE OPEN-SEAT TERM. This is the awkward one and it is not a data
#      problem. The variable is the number of open seats multiplied by -1 in a
#      bad year for the president's party, +1 in a good year, 0 in a neutral
#      one. Which kind of year 2026 is, is a judgment about the outcome we are
#      trying to forecast. Lockerbie makes that call himself; we would be
#      guessing, and a term whose sign we choose can carry the forecast
#      wherever we like. Any implementation must either publish the sign as an
#      explicit assumption and show the answer under all three, or drop the
#      term and say the model has been changed.
#
# So this stays a stub with its coefficients recorded, the same shape as the
# Enns entry. When the Michigan series is captured, run() has everything it
# needs except a decision about (2), and that decision belongs to a person.
LOCKERBIE_CITE = ("Lockerbie, 'The Challenge of Forecasting the 2024 "
                  "Presidential and House Elections: Economic Pessimism and "
                  "Election Outcomes', PS: Political Science & Politics, 2024")

# Table 1, "Forecasting Equations 1954-2024", House Seat Change column.
#
# THE PARENTHESES ARE SIGNIFICANCE LEVELS, NOT STANDARD ERRORS. The table's own
# note says so. This matters because .001 beside a coefficient of .41 is an
# absurd standard error and a perfectly ordinary p-value — reading it as the
# former is what made this model look untrustworthy on first pass. It is not.
LOCKERBIE_COEF = {
    "constant": 5.36,
    "pct_expect_worse": -0.78,      # p = .07
    "open_seats_signed": 0.41,      # p = .001
    "fitted_on": "1954-2024, n=35",
    "r2": 0.42,
    "r2_adj": 0.38,
    "dv": "incumbent party House seat change",
    "input_month": "June of the election year",
    # Table 3, out-of-sample. This is the number that matters most for reading
    # the forecast: the mean absolute error across 35 elections is 16.8 SEATS.
    # Individual misses run far larger — 2010 was forecast at -11 against an
    # actual -64. Any interval on this model has to be wide enough to say so.
    "out_of_sample_mae_seats": 16.8,
    "published_2024_forecast": -12,
}

LOCKERBIE_MISSING = [
    "Michigan Surveys of Consumers Table 8, 'Expected Change in Financial "
    "Situation in a Year' — the WORSE column, June 2026. ONE NUMBER, not a "
    "series. Set LOCKERBIE_INPUTS['pct_expect_worse'].",
    "the number of open House seats in 2026 — seats with no incumbent running. "
    "One number. Set LOCKERBIE_INPUTS['open_seats'].",
]

# THE SIGN IS THE AUTHOR'S RULE, NOT OUR GUESS — corrected after reading the
# paper rather than a summary of it.
#
# Lockerbie's second term multiplies the number of open seats by the DIRECTION
# of the year: -1 in a bad year for the incumbent president's party, +1 in a
# good one, 0 in neither. On first pass that looked like a judgment we would
# have to make ourselves about the very thing being forecast, and this comment
# said so at length.
#
# The paper settles it in one sentence: "Midterms are, by definition, a bad
# year for the incumbent party." The -1 is not our reading of 2026. It is the
# specification, and it is fixed for every midterm the model has ever been run
# on. The judgment only bites in on-year elections, which Lockerbie himself
# calls "a little more complicated" and handles by asking whether the
# electorate expects one party to win big.
#
# So this is now a constant with a citation rather than an assumption with a
# defence. The sensitivity table below stays, because showing what the term
# contributes is still worth doing — the open-seat term is carrying about half
# this forecast — but it is labelled as the counterfactual it is, not as a
# choice we made and might have made differently.
LOCKERBIE_MIDTERM_RULE = ("Lockerbie: 'Midterms are, by definition, a bad year "
                          "for the incumbent party.' The -1 is his rule, not "
                          "our assumption.")
LOCKERBIE_YEAR_DIRECTION = -1
LOCKERBIE_DIRECTION_LABEL = {
    -1: "a bad year for the president's party — the midterm rule",
    0:  "a neutral year",
    1:  "a good year for the president's party",
}

# Both are single numbers, not series: Lockerbie reads the June figure of the
# election year, and the open-seat count is fixed once filing closes. Fill
# these in and the model runs; leave them None and it declares itself unbuilt.
LOCKERBIE_INPUTS: dict = {
    # Michigan Surveys of Consumers, Table 8, "Expected Change in Financial
    # Situation in a Year", WORSE OFF column, June 2026. Read off the published
    # table, n=1,380.
    "pct_expect_worse": 37.0,
    "open_seats": 60,
}

# THE JUNE READING IS LOWER THAN THE MONTHS AROUND IT, and we use it anyway.
#
# The 2026 run of the WORSE OFF column goes Mar 39, Apr 41, May 45, Jun 37. The
# June figure is eight points below May and the lowest since January. It may
# well be survey noise rather than a real collapse in pessimism.
#
# Lockerbie's specification says June of the election year. Not a spring
# average, not a smoothed series — June. Substituting a three-month mean
# because we distrust one month would be estimating a DIFFERENT model and
# reporting it under his name, which is the thing this whole file exists not to
# do. The month is part of the specification, so the month is what we feed it.
#
# What we do instead is price the worry. MONTHS_SENSITIVITY runs the model at
# each recent reading so the reader can see what the choice of month is worth
# — about six seats between May and June — and compare that with what the
# year-direction assumption is worth, which is far more.
LOCKERBIE_RECENT_MONTHS = [("Mar 2026", 39.0), ("Apr 2026", 41.0),
                           ("May 2026", 45.0), ("Jun 2026", 37.0)]

# Seats the president's party held going in — the base the seat CHANGE applies
# to. Same figure fundamentals.py uses.
LOCKERBIE_SEATS_BEFORE = 220


def _published_tide_seat_pairs(cycle: int) -> list:
    """(tide, expected D seats) from today's projections, for inversion.

    WHY INTERPOLATE RATHER THAN SOLVE. Lockerbie forecasts SEATS; every other
    line on this site is a national tide pushed through one shared seat curve.
    To put him on the same axis, that curve has to run backwards. Bisecting on
    house_forecast() would do it exactly, at the price of a 20,000-draw Monte
    Carlo per iteration.

    Exact is not needed. seats.py already evaluates that curve at eight or nine
    tides every run and writes the pairs down, and across the range those tides
    span it is monotone and close to linear. Interpolating between the two
    nearest published points is accurate to a fraction of a seat, costs
    nothing, and — the argument that actually matters — uses the SAME curve the
    reader sees everywhere else on the page, rather than a second one that
    could quietly drift from it.
    """
    for path in (DATA / str(cycle) / "model_private" / "seat_projections.json",
                 DATA / str(cycle) / "derived" / "seat_projections.json"):
        if not path.exists():
            continue
        try:
            pr = json.loads(path.read_text()).get("projections") or {}
        except json.JSONDecodeError:
            continue
        pairs = []
        for m in pr.values():
            t = m.get("tide_D")
            h = (m.get("house") or {}).get("expected_D_seats")
            if t is not None and h is not None:
                pairs.append((float(t), float(h)))
        if len(pairs) >= 2:
            return sorted(set(pairs))
    return []


def implied_tide_for_seats(pairs: list, seats: float):
    """The tide our curve says produces `seats`. Returns (tide, extrapolated)."""
    if len(pairs) < 2:
        return None
    if seats <= pairs[0][1]:
        (t1, s1), (t2, s2), out = pairs[0], pairs[1], True
    elif seats >= pairs[-1][1]:
        (t1, s1), (t2, s2), out = pairs[-2], pairs[-1], True
    else:
        out = False
        for (t1, s1), (t2, s2) in zip(pairs, pairs[1:]):
            if s1 <= seats <= s2:
                break
    if s2 == s1:
        return None
    return t1 + (seats - s1) * (t2 - t1) / (s2 - s1), out


def run_lockerbie(c: Ctx) -> Result | None:
    worse = LOCKERBIE_INPUTS.get("pct_expect_worse")
    opens = LOCKERBIE_INPUTS.get("open_seats")
    if worse is None or opens is None:
        return None

    k = LOCKERBIE_COEF

    def change_for(direction: int) -> float:
        return (k["constant"] + k["pct_expect_worse"] * worse
                + k["open_seats_signed"] * opens * direction)

    dirn = LOCKERBIE_YEAR_DIRECTION
    seat_change = change_for(dirn)
    pres_seats = LOCKERBIE_SEATS_BEFORE + seat_change
    d_seats = 435.0 - pres_seats          # the president's party is Republican

    pairs = _published_tide_seat_pairs(c.cycle)
    got = implied_tide_for_seats(pairs, d_seats)
    if got is None:
        # No curve to invert against means no tide, and a seats-only result has
        # nowhere to go in a pipeline built on tides. Decline rather than
        # publish a number the rest of the site cannot place beside the others.
        return None
    tide, extrapolated = got

    notes = ["forecasts SEAT CHANGE directly — the margin shown is our own seat "
             "curve run backwards from that seat count, and is not a number "
             "Lockerbie publishes"]
    if extrapolated:
        notes.append("that seat count falls outside the range of tides the "
                     "other models produced today, so the inversion is "
                     "extrapolated rather than interpolated")
    notes.append(LOCKERBIE_MIDTERM_RULE)
    notes.append(f"published out-of-sample mean absolute error is "
                 f"{k['out_of_sample_mae_seats']} seats — the interval is his, "
                 f"not ours, and it is very wide")

    # AN INTERVAL BUILT FROM HIS OWN OUT-OF-SAMPLE ERROR, in seats, converted
    # back to a tide through the same curve. Table 3's mean absolute error is
    # 16.8 seats over 35 elections; for a normal error MAE ~= 0.798 sigma, so
    # sigma is about 21 seats and an 80% band is roughly +/- 27. That is an
    # enormous interval and it is the honest one: this model has been wrong by
    # fifty seats in living memory.
    mae = k["out_of_sample_mae_seats"]
    sigma_seats = mae / 0.7979
    band = 1.2816 * sigma_seats
    lo_t = implied_tide_for_seats(pairs, d_seats - band)
    hi_t = implied_tide_for_seats(pairs, d_seats + band)
    interval = ((lo_t[0], hi_t[0]) if lo_t and hi_t else None)

    return Result(
        margin_D=tide,
        interval_80=interval,
        inputs={
            "pct_expect_worse": worse,
            "pct_expect_worse_source": "Michigan Surveys of Consumers, Table 8, "
                                       "June of the election year",
            "open_seats": opens,
            "year_direction": dirn,
            "year_direction_label": LOCKERBIE_DIRECTION_LABEL[dirn],
            "seats_before": LOCKERBIE_SEATS_BEFORE,
        },
        diagnostics={
            "coefficients": {kk: vv for kk, vv in k.items()
                             if isinstance(vv, (int, float))},
            "pres_party_seat_change": round(seat_change, 1),
            "implied_D_seats": round(d_seats, 1),
            "inversion_extrapolated": extrapolated,
            "curve_points_used": len(pairs),
            # WHAT THE MONTH IS WORTH. June is the specification; this shows
            # the answer at each recent reading so "the June number looks
            # noisy" becomes a number rather than a misgiving. It is NOT an
            # alternative forecast and the page must not render it as one.
            "sensitivity_month": [
                {"month": mlab, "pct_expect_worse": mval,
                 "pres_party_seat_change": round(
                     k["constant"] + k["pct_expect_worse"] * mval
                     + k["open_seats_signed"] * opens * dirn, 1),
                 "is_specification": mval == worse}
                for mlab, mval in LOCKERBIE_RECENT_MONTHS],
            "midterm_rule": LOCKERBIE_MIDTERM_RULE,
            "out_of_sample_mae_seats": mae,
            "sensitivity_year_direction": [
                {"direction": dd,
                 "label": LOCKERBIE_DIRECTION_LABEL[dd],
                 "pres_party_seat_change": round(change_for(dd), 1),
                 "implied_D_seats": round(435.0 - (LOCKERBIE_SEATS_BEFORE
                                                   + change_for(dd)), 1)}
                for dd in (-1, 0, 1)],
        },
        notes=notes,
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MODEL 5 — political history (Lewis-Beck & Quinlan)
# ---------------------------------------------------------------------------
# THE ONLY MODEL ON THIS SITE THAT FORECASTS THE SENATE IN ITS OWN RIGHT.
#
# Everything else here produces a national tide, which seats.py pushes through
# one shared partisan-lean curve to get Senate numbers. That means every Senate
# figure the site publishes leans on a single piece of machinery, and if the
# machinery is wrong they are all wrong together. This model counts Senate
# seats directly from institutional facts and never touches a poll or a price,
# so it is a genuinely independent check rather than another opinion routed
# through the same pipe.
#
# It also uses no economy and no popularity. Its inputs are all COUNTABLE
# FACTS ABOUT THE STATE OF PLAY — who holds what, who is retiring, which states
# are safe — known months ahead and not revised. There is nothing to nowcast,
# which is why it can be run in July and stand.
#
# Table 1, PS 58(2), OLS on 39 elections 1946-2022. Coefficients unstandardised.
LBQ_CITE = ("Lewis-Beck & Quinlan, 'A Political History Forecast of the 2024 "
            "US Congressional Elections', PS: Political Science & Politics "
            "58(2), 2025, Table 1")

# Democrat seats = constant + sum(beta * x). Standard errors in comments.
LBQ_SENATE = {
    "constant": 25.153,             # (4.674)
    "dem_federal_dominance": -5.057,   # (1.498)  1 if Dems hold all three
    "dem_governors": 0.236,            # (0.135)  count, 6 months out
    "strong_gop_senate_states": -0.416,  # (0.157)
    "dem_holdover_senate": 0.851,      # (0.154)  D seats NOT up
    "post_vra_1965": -4.623,           # (1.434)  1 for elections post-1966
    "_fit": {"n": 39, "adj_r2": 0.72, "rmse": 3.684,
             "out_of_sample_mae": 2.7, "calls_control": "72%"},
}
LBQ_HOUSE = {
    "constant": 208.999,            # (17.618)
    "dem_federal_dominance": -29.723,  # (6.137)
    "dem_governors": 2.900,            # (0.583)
    "strong_gop_senate_states": -2.254,  # (0.747)
    "dem_senate_retirements": -4.978,  # (1.300)  D senators not contesting
    "post_1994_revolution": -13.358,   # (7.846)  1 for contests post-1994
    "_fit": {"n": 39, "adj_r2": 0.74, "rmse": 15.732,
             "out_of_sample_mae": 12.0, "calls_control": "87%"},
}

# 2026 values. The two dummies and the dominance term are settled facts; the
# three counts are not, and are left None until somebody counts them properly.
# A plausible guess in any of these slots would be indistinguishable from a
# measurement once it is in the JSON, which is exactly how a made-up number
# ends up on a public page.
#
#   dem_federal_dominance   0. The dichotomy is 1 only when DEMOCRATS hold the
#                           presidency, Senate and House together. Republicans
#                           hold all three in 2026, so this is 0 — the variable
#                           does not flip sign for the other party, it simply
#                           is not triggered.
#   post_vra_1965           1. Every election after 1966.
#   post_1994_revolution    1. Every contest after 1994.
#   dem_holdover_senate     Democratic seats NOT up in 2026. The site already
#                           holds this as polling.HOLDOVER_D_DEFAULT and uses
#                           it in every Senate simulation, so taking it from
#                           there keeps the two from disagreeing.
LBQ_INPUTS: dict = {
    "dem_federal_dominance": 0,
    "post_vra_1965": 1,
    "post_1994_revolution": 1,
    "dem_holdover_senate": polling.HOLDOVER_D_DEFAULT,
    # Counted for 2026 and supplied by hand. None of the three is on a feed,
    # and none of them moves once filing closes, so they are constants for this
    # cycle rather than anything to capture daily.
    "dem_governors": 24,              # 24 D, 26 R nationwide
    "strong_gop_senate_states": 14,   # two GOP senators, voted R in 2024,
                                      # and holding a 2026 Senate contest
    "dem_senate_retirements": 4,      # D incumbents not contesting
}

LBQ_MISSING = [
    "number of Democratic governors nationwide six months before Election Day "
    "(≈ 3 May 2026). Set LBQ_INPUTS['dem_governors'].",
    "number of states that have TWO Republican senators, voted Republican for "
    "president in 2024, AND hold a Senate contest in 2026. Set "
    "LBQ_INPUTS['strong_gop_senate_states'].",
    "number of incumbent Senate Democrats not contesting the 2026 election, as "
    "of five months before polling day. Set "
    "LBQ_INPUTS['dem_senate_retirements'].",
]


def _lbq_predict(spec: dict, inputs: dict) -> float | None:
    total = spec["constant"]
    for var, beta in spec.items():
        if var in ("constant", "_fit"):
            continue
        v = inputs.get(var)
        if v is None:
            return None
        total += beta * v
    return total


def run_lbq(c: Ctx) -> Result | None:
    house = _lbq_predict(LBQ_HOUSE, LBQ_INPUTS)
    senate = _lbq_predict(LBQ_SENATE, LBQ_INPUTS)
    if house is None:
        return None

    pairs = _published_tide_seat_pairs(c.cycle)
    got = implied_tide_for_seats(pairs, house)
    if got is None:
        return None
    tide, extrapolated = got

    notes = ["forecasts SEAT COUNTS directly from institutional facts — no "
             "polls, no economy, no popularity. The margin shown is our seat "
             "curve run backwards from its House number, not theirs"]
    if senate is not None:
        notes.append(f"its Senate figure of {senate:.0f} D seats is computed "
                     f"from a SEPARATE published equation, not from our "
                     f"partisan-lean machinery — the only Senate number on this "
                     f"site that is independent of it")
    if extrapolated:
        notes.append("the House number falls outside today's range of tides, "
                     "so the inversion is extrapolated")

    return Result(
        margin_D=tide,
        interval_80=None,
        inputs={k: v for k, v in LBQ_INPUTS.items()},
        diagnostics={
            "house_D_seats": round(house, 1),
            "senate_D_seats": round(senate, 1) if senate is not None else None,
            "house_coefficients": {k: v for k, v in LBQ_HOUSE.items() if k != "_fit"},
            "senate_coefficients": {k: v for k, v in LBQ_SENATE.items() if k != "_fit"},
            "house_fit": LBQ_HOUSE["_fit"],
            "senate_fit": LBQ_SENATE["_fit"],
            "inversion_extrapolated": extrapolated,
            "benchmark": {
                "claim": "Democrats lose Senate control with a net loss of three "
                         "seats; House a knife-edge race at 215 D seats",
                "authors": "Lewis-Beck & Quinlan",
                "for_cycle": "2024",
                "note": "their 2024 forecast, quoted so our implementation can "
                        "be sanity-checked against the published one",
            },
        },
        notes=notes,
    )


# ---------------------------------------------------------------------------
# WHICH FAMILIES A MODEL BELONGS TO
# ---------------------------------------------------------------------------
# "Academic" is not the same KIND of label as the other four, and pretending
# otherwise put every model in this file in exactly one box when several belong
# in two.
#
# Fundamentals, polling, markets and professional are claims about METHOD: what
# the forecast looks at. Academic is a claim about PROVENANCE: who published it
# and where. Those are orthogonal. A referendum model published in APSR is an
# academic forecast AND a fundamentals forecast, in the plain sense that it
# predicts from the economy and presidential approval and knows nothing about
# polls. Filing it under one and not the other made the fundamentals line on
# the tracker narrower than the evidence we actually hold.
#
# So a model declares a LIST. The first entry is its PRIMARY family, which is
# the one the across-family average uses; the rest are additional memberships,
# which the category averages and the timeline honour in full.
#
#   BEW              academic + polling. Its only moving input is the generic
#                    ballot. It is a poll-based forecast that happens to have
#                    been published in the Journal of Politics.
#   Referendum       academic + fundamentals. Approval and income, no polls.
#   Lockerbie        academic + fundamentals. Economic expectations, no polls.
#   Lewis-Beck &     academic + fundamentals. No economy and no polls, but the
#   Quinlan          site's own definition of fundamentals is "conditions
#                    rather than opinion", and governorships held, seats not up
#                    and retirements are conditions.
#
# WHAT DUAL MEMBERSHIP COSTS, stated plainly because it is a real cost and the
# comment here used to claim it had been solved.
#
# Category averages are computed within a category, so a dual member is counted
# once in each — which is right, because "what does this way of knowing say" is
# a different question for each family and the model genuinely answers both.
#
# The across-family row is where it bites. That row averages FAMILY MEANS, and
# a model sitting in two families influences two of them. The referendum model
# is one of four in academic and one of eight in fundamentals, so it carries
# (1/5)(1/4) + (1/5)(1/8) of the headline instead of one share. It is not
# double-counted in any category, but it is over-weighted in the total.
#
# We accept that for now rather than building a second, primary-only set of
# averages purely to feed one row. The effect is small and the alternative is a
# parallel aggregation that could drift from the real one. But it IS a thumb on
# the scale, it is here in writing, and it belongs in the same conversation as
# the panel-composition question — a headline that moves because a model joined
# a second family is exactly the kind of movement that is not news.
#
# THIS IS ITSELF A COMPOSITION CHANGE, and it will move the polling and
# fundamentals lines on the day it lands, without anybody's forecast having
# changed. See the note on panel composition in collect/publish.py.

MODELS = [
    {
        "key": "academic_bew",
        "categories": ["academic", "polling"],
        "name": "Generic ballot (Bafumi, Erikson & Wlezien)",
        "citation": BEW_CITE,
        "attribution": "Our implementation of BEW's published midterm equations, "
                       "run on our generic-ballot average.",
        "status": "implemented",
        "refit": False,
        "run": run_bew,
    },
    {
        "key": "academic_referendum",
        "categories": ["academic", "fundamentals"],
        "name": "Referendum (approval + income, no exposure)",
        "citation": REFERENDUM_CITE,
        "attribution": "Our fit of the referendum specification. NOT Lewis-Beck "
                       "& Tien's published forecast.",
        "status": "implemented",
        "refit": True,
        "run": run_referendum,
    },
    {
        "key": "academic_economic_pessimism",
        "categories": ["academic", "fundamentals"],
        "name": "Economic pessimism (Lockerbie)",
        "citation": LOCKERBIE_CITE,
        "attribution": "Our implementation of Lockerbie's published House "
                       "equation. The open-seat term's SIGN is our assumption, "
                       "not his — see the sensitivity table.",
        # IMPLEMENTED, not a stub — it declines at runtime while its two
        # numbers are missing, which is a different and more useful state than
        # not existing. The moment LOCKERBIE_INPUTS is filled in, it appears.
        "status": "implemented",
        "missing": LOCKERBIE_MISSING,
        "coefficients_published": LOCKERBIE_COEF,
        "refit": False,
        "run": run_lockerbie,
    },
    {
        "key": "academic_political_history",
        "categories": ["academic", "fundamentals"],
        "name": "Political history (Lewis-Beck & Quinlan)",
        "citation": LBQ_CITE,
        "attribution": "Our implementation of their published Table 1 "
                       "equations. Forecasts both chambers directly.",
        "status": "implemented",
        "missing": LBQ_MISSING,
        "refit": False,
        "run": run_lbq,
    },
    {
        "key": "academic_state_approval_economy",
        "categories": ["academic", "fundamentals"],
        "name": "State approval + state economy (Enns et al.)",
        "citation": ENNS_CITE,
        "attribution": "Not implemented. Listed so the gap is visible.",
        "status": "stub",
        "missing": ENNS_MISSING,
        "refit": None,
        "run": run_enns,
    },
]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
# WHY THIS IS POSSIBLE HERE AND NOT FOR MOST SOURCES. The project's standing
# rule on backfill is "timestamped sources only": we reconstruct a past value
# only where the past value is genuinely recoverable, never by inventing one.
#
# BEW qualifies in the strongest possible way. Its only moving input is the
# generic ballot, and the generic ballot for every archived date is sitting in
# parsed/<date>.csv exactly as it was captured on the day. The coefficients are
# published constants. The date arithmetic is a subtraction. So running BEW
# over the archive does not estimate what it "would have said" — it computes
# what it did say, from the same bytes it would have read.
#
# THE REFERENDUM MODEL QUALIFIES MUCH MORE WEAKLY, and the difference matters.
# Its income term comes from the FRED capture and is genuinely dated, but its
# approval term is a hand-set constant with no live feed behind it. Backfilling
# it therefore produces a line that is flat by construction, not because
# approval held steady. A flat line on a chart is a claim about the world.
# So it is backfilled only when asked for explicitly, and every backfilled
# point is stamped `provenance: backfilled` with the reason.
BACKFILL_PROVENANCE = "backfilled"
LIVE_PROVENANCE = "captured"


# ---------------------------------------------------------------------------
# Reconstructing the generic ballot for a past date
# ---------------------------------------------------------------------------
# THE SNAPSHOT ARCHIVE CANNOT DO THIS, which is why this function exists. Our
# parsed archive goes back to 2025-01-01, but the generic ballot does not: the
# Silver Bulletin capture only began on 2026-08-19, and every parsed date
# before that carries race_to_the_wh's seat numbers and nothing else. Running
# BEW over the snapshot archive therefore fills in two days and stops.
#
# The poll list itself is a different story. Silver's CSV is not an average —
# it is ~350 INDIVIDUAL polls, each with its own enddate, going back to August
# 2025. Every one of those polls existed on its enddate. So the average as of
# any past date is not an estimate of what the polls said; it is a computation
# over exactly the polls that had been published by then.
#
# RAW `net`, NOT `adjusted_net`, AND THIS IS THE WHOLE GAME. The adjusted
# columns are Silver's model output — house-effect and likely-voter corrected —
# and the registry notes that they are REVISED RETROACTIVELY. Today's file
# carries today's opinion of what a poll from last November really said. Using
# them to reconstruct last November would leak nine months of hindsight into a
# number presented as a contemporaneous forecast, and the backfilled line would
# look better than the model deserves. The raw columns are the pollster's own
# published figures and do not move.
#
# The live daily path keeps preferring `adjusted`, because for TODAY there is
# no hindsight to leak. The two therefore differ slightly, and main() prints
# the size of the gap at the join date rather than letting a step in the line
# pass for a change in the polls.
RECONSTRUCT_WINDOW_DAYS = 21


def _silver_poll_file(cycle: int) -> Path | None:
    """The newest captured Silver Bulletin poll list.

    The NEWEST one is correct even for reconstructing old dates: each file is a
    superset of the ones before it, since he adds polls rather than rotating
    them out. Reading the newest gets the longest history.
    """
    base = DATA / str(cycle) / "raw" / "silver_bulletin"
    if not base.exists():
        return None
    days = sorted(d for d in base.iterdir() if d.is_dir())
    for day in reversed(days):
        f = day / "generic_ballot_polls.csv"
        if f.exists():
            return f
    return None


def _parse_us_date(v: str) -> dt.date | None:
    v = (v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def load_poll_history(cycle: int) -> list[tuple[dt.date, float]]:
    """[(enddate, D-minus-R margin)] from the raw poll list, oldest first."""
    f = _silver_poll_file(cycle)
    if f is None:
        return []
    out: list[tuple[dt.date, float]] = []
    with f.open(encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            # "All polls" is his own subgroup label for the headline set; the
            # file also carries LV-only and RV-only cuts of the SAME polls, and
            # averaging across subgroups would count several of them twice.
            if (r.get("subgroup") or "").strip().lower() not in ("all polls", ""):
                continue
            d0 = _parse_us_date(r.get("enddate", ""))
            if d0 is None:
                continue
            try:
                net = float(r["net"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append((d0, net))
    out.sort()
    return out


def reconstruct_generic_ballot(polls, on: dt.date,
                               window: int = RECONSTRUCT_WINDOW_DAYS):
    """Unweighted mean of poll margins in the `window` days ending on `on`.

    UNWEIGHTED, deliberately. The file carries Silver's own per-poll `weight`
    and `influence`, and both are model output that moves when he re-fits. An
    unweighted mean of published margins is a number we can state the recipe
    for in one sentence and that nobody has to trust us about. It is not his
    average and this file never calls it that.

    Returns (margin, n_polls) or None when the window is empty — no widening,
    no carrying forward. A gap in the polling is a real fact about the cycle
    and filling it invents data.
    """
    lo = on - dt.timedelta(days=window)
    vals = [m for d0, m in polls if lo < d0 <= on]
    if not vals:
        return None
    return statistics.fmean(vals), len(vals)


def parsed_dates(cycle: int) -> list[str]:
    return [Path(f).stem for f in
            sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")))]


def read_generic_ballot_on(cycle: int, date: str) -> tuple[float, str] | None:
    """The generic ballot as it stood on `date`, from that day's parsed file.

    polling.generic_ballot() does the picking — adjusted over raw, and the
    reasoning for that preference lives there. Reimplementing the choice here
    would give us two definitions of "the generic ballot" that could drift, and
    the backfilled line would slowly stop matching the live one.
    """
    p = DATA / str(cycle) / "parsed" / f"{date}.csv"
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    try:
        gb = polling.generic_ballot(rows)
    except SystemExit:
        # generic_ballot() exits rather than returning None, because in the
        # live path a missing generic ballot IS fatal. Here it is ordinary: the
        # archive holds plenty of dates whose capture never got that far, and a
        # backfill that dies on the first one is useless.
        return None
    if gb.get("value") is None:
        return None
    return float(gb["value"]), f"{gb['used']} from parsed/{date}.csv"


def backfill(cycle: int, approval: float, include_referendum: bool,
             step_days: int = 1) -> dict:
    """Recompute BEW for every date the poll record can honestly support.

    Dates come from the POLL RECORD, not from the snapshot archive, for the
    reason set out above reconstruct_generic_ballot(): our snapshots only start
    carrying a generic ballot on 2026-08-19, while Silver's poll list runs back
    to August 2025. Walking the poll record gives roughly a year of line;
    walking the snapshot archive gives two days.
    """
    polls = load_poll_history(cycle)
    if not polls:
        print("  no Silver Bulletin poll list in raw/ — nothing to reconstruct "
              "from. Run capture first.")
        return {}

    first_poll, last_poll = polls[0][0], polls[-1][0]
    print(f"  poll record: {len(polls)} polls, {first_poll} to {last_poll}")

    # Start a full window in, so the first point is an average over a full
    # window rather than over whichever one poll happens to be earliest — and
    # never before SERIES_START, which is where every series on the site
    # begins. Taking the later of the two also puts the first point exactly on
    # inauguration day rather than wherever the weekly grid happened to land
    # after it.
    d0 = max(first_poll + dt.timedelta(days=RECONSTRUCT_WINDOW_DAYS),
             dt.date.fromisoformat(SERIES_START))
    today = dt.date.fromisoformat(newest_parsed_date(cycle)
                                  or dt.date.today().isoformat())
    end = min(last_poll, today)

    out: dict[str, dict] = {}
    empty_windows = 0
    cur = d0
    while cur <= end:
        got = reconstruct_generic_ballot(polls, cur)
        if got is None:
            empty_windows += 1
            cur += dt.timedelta(days=step_days)
            continue
        margin, n_polls = got
        c = Ctx(cycle=cycle, date=cur.isoformat(),
                days_to_election=(dt.date.fromisoformat(ELECTION_DAY) - cur).days,
                approval=approval,
                approval_source="hand-set (Gallup basis) — CONSTANT across the "
                                "backfill, so anything driven by it is flat by "
                                "construction and not by evidence",
                generic_ballot_D=margin,
                generic_ballot_source=(f"reconstructed: unweighted mean of "
                                       f"{n_polls} raw poll margin(s) in the "
                                       f"{RECONSTRUCT_WINDOW_DAYS} days to "
                                       f"{cur.isoformat()}"))
        day: dict[str, dict] = {}
        res = run_bew(c)
        if res is not None:
            lo, hi = res.interval_80 or (None, None)
            day["academic_bew"] = {
                "name": "Generic ballot (Bafumi, Erikson & Wlezien)",
                "category": "academic", "publication": "individual",
                "margin_D": round(res.margin_D, 2),
                "margin_D_80_low": round(lo, 2) if lo is not None else None,
                "margin_D_80_high": round(hi, 2) if hi is not None else None,
                "inputs": {**res.inputs, "n_polls_in_window": n_polls},
                "diagnostics": res.diagnostics,
                "provenance": BACKFILL_PROVENANCE,
            }
        # The referendum model is NOT backfilled here at all, whatever the
        # flag says, and the flag's help text says why: its approval term is a
        # hand-set constant and its income term is not re-read per date, so
        # every point would be identical. A flat line is a claim that approval
        # and the economy did not move, which we have no evidence for. It is
        # better to have no academic referendum history than a fabricated one.
        if day:
            out[cur.isoformat()] = {"snapshot_date": cur.isoformat(),
                                    "models": day}
        cur += dt.timedelta(days=step_days)

    if include_referendum:
        print("  --backfill-referendum: IGNORED. Its inputs do not vary by "
              "date, so the line would be flat by construction. See the note "
              "in backfill().")

    print(f"  reconstructed {len(out)} date(s)")
    if empty_windows:
        print(f"  {empty_windows} date(s) had no poll in the trailing "
              f"{RECONSTRUCT_WINDOW_DAYS} days and were left out rather than "
              f"carried forward")
    if out:
        ks = sorted(out)
        f0 = out[ks[0]]["models"]["academic_bew"]
        l0 = out[ks[-1]]["models"]["academic_bew"]
        print(f"  BEW {ks[0]} D{f0['margin_D']:+.2f}"
              f"  ->  {ks[-1]} D{l0['margin_D']:+.2f}")
    return out


def newest_parsed_date(cycle: int) -> str | None:
    files = sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")))
    return Path(files[-1]).stem if files else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Published academic models, reimplemented.")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--date", default=None)
    ap.add_argument("--approval", type=float, default=38.0,
                    help="president's approval, Gallup basis — same caveat as "
                         "fundamentals.py, do not feed a poll average")
    ap.add_argument("--step-days", type=int, default=7,
                    help="spacing of backfilled points, in days. Default 7. "
                         "The reconstruction is a 21-day trailing average, so "
                         "consecutive daily points share 20 of 21 days of "
                         "polls and mostly repeat each other; weekly points "
                         "carry nearly all the signal at a seventh of the "
                         "cost, which matters because seats.py runs one Monte "
                         "Carlo per point. Pass 1 for a daily line.")
    ap.add_argument("--backfill", action="store_true",
                    help="also recompute every archived date into "
                         "model_private/academic_models_history.json. Safe to "
                         "re-run: it rewrites the whole file from the parsed "
                         "archive, which is the authority.")
    ap.add_argument("--backfill-referendum", action="store_true",
                    help="include the referendum model in the backfill. OFF by "
                         "default: its approval input is a hand-set constant "
                         "and its income input is not re-read per date, so the "
                         "line it draws is flat by construction. Read the "
                         "comment above backfill() before turning this on.")
    a = ap.parse_args(argv)

    date = a.date or newest_parsed_date(a.cycle) or dt.date.today().isoformat()
    c = build_ctx(a.cycle, date, a.approval)

    print("=" * 68)
    print(f"academic models · snapshot {date} · {c.days_to_election} days to "
          f"{ELECTION_DAY}")
    print("=" * 68)
    for n in c.notes:
        print(f"  note: {n}")

    out = {
        "cycle": a.cycle,
        "snapshot_date": date,
        "days_to_election": c.days_to_election,
        "category": "academic",
        "publication": "individual",
        "models": {},
        "not_implemented": {},
    }

    for spec in MODELS:
        res = spec["run"](c)
        if res is None:
            out["not_implemented"][spec["key"]] = {
                "name": spec["name"], "citation": spec["citation"],
                "attribution": spec["attribution"],
                "missing": spec.get("missing")
                           or ["required inputs unavailable in this snapshot"],
                # Where we hold the published coefficients already, ship them.
                # A stub that names its equation is a specific piece of unfinished
                # work; one that only names the paper is a wish.
                "coefficients_published": spec.get("coefficients_published"),
            }
            why = spec.get("missing") or ["inputs unavailable"]
            print(f"\n  {spec['key'].upper()}  [not run]")
            for m in why:
                print(f"      missing: {m}")
            continue

        lo, hi = res.interval_80 or (None, None)
        out["models"][spec["key"]] = {
            "name": spec["name"],
            # PRIMARY first. seats.py and aggregate.py both rely on that order.
            "categories": spec.get("categories") or ["academic"],
            "citation": spec["citation"],
            "attribution": spec["attribution"],
            "category": (spec.get("categories") or ["academic"])[0],
            "publication": "individual",
            "refit_on_our_history": spec["refit"],
            "margin_D": round(res.margin_D, 2),
            "margin_D_80_low": round(lo, 2) if lo is not None else None,
            "margin_D_80_high": round(hi, 2) if hi is not None else None,
            "inputs": res.inputs,
            "diagnostics": res.diagnostics,
            "notes": res.notes,
        }
        band = (f" (80% D{lo:+.1f} to D{hi:+.1f})" if lo is not None else "")
        print(f"\n  {spec['key'].upper()}  D{res.margin_D:+.2f}{band}")
        for n in res.notes:
            print(f"      {n}")

    d = DATA / str(a.cycle) / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / "academic_models.json").write_text(json.dumps(out, indent=2))
    print(f"\n  wrote {len(out['models'])} model(s), "
          f"{len(out['not_implemented'])} not implemented")

    # THE HISTORY IS PRIVATE, like every other model_private/ file, and for the
    # same structural reason rather than a licence one: it is model state that
    # the runner would otherwise lose, and the workflow already restores and
    # re-pushes this directory. Nothing in it is gated — both models are ours —
    # but splitting the directory by sensitivity would mean two restore steps
    # and one of them would eventually be forgotten.
    #
    # REWRITTEN WHOLE, not merged. Unlike seat_projections_history.json, which
    # accumulates because a past seat projection cannot be recomputed, every
    # entry here is a pure function of parsed/<date>.csv and published
    # coefficients. Rebuilding from the archive is therefore always correct and
    # a merge would only preserve stale values from an older coefficient table.
    if a.backfill:
        hist = backfill(a.cycle, a.approval, a.backfill_referendum,
                        step_days=max(1, a.step_days))
        if hist:
            # Today's live values overwrite their backfilled twin, so the one
            # date that has a genuine capture is not replaced by a
            # reconstruction of itself.
            # MEASURE THE JOIN, do not just make it. The backfilled points
            # use raw poll margins in a 21-day window; today's live point uses
            # Silver's own house-effect-adjusted average. Those are different
            # recipes, so the line has a seam at this date, and a seam in a
            # time series reads as news unless it is labelled. Print its size
            # so it is a known quantity rather than a surprise on the chart.
            # The weekly grid rarely lands exactly on today, so compute the
            # reconstruction AT the live date purely for this comparison. It is
            # not stored; its only job is to measure the seam.
            recon = hist.get(date, {}).get("models", {}).get("academic_bew")
            if recon is None:
                _polls = load_poll_history(a.cycle)
                _got = reconstruct_generic_ballot(
                    _polls, dt.date.fromisoformat(date)) if _polls else None
                if _got:
                    _c = Ctx(cycle=a.cycle, date=date,
                             days_to_election=c.days_to_election,
                             approval=a.approval,
                             generic_ballot_D=_got[0],
                             generic_ballot_source="reconstructed (comparison only)")
                    _r = run_bew(_c)
                    if _r:
                        recon = {"margin_D": round(_r.margin_D, 2)}
            livebew = out["models"].get("academic_bew")
            if recon and livebew:
                gap = livebew["margin_D"] - recon["margin_D"]
                print(f"  JOIN at {date}: reconstructed D{recon['margin_D']:+.2f}"
                      f" vs live D{livebew['margin_D']:+.2f}"
                      f"  ({gap:+.2f} points)")
                if abs(gap) > 1.0:
                    print("    NOTE: over a point. The seam will be visible on "
                          "the chart. It is a change of recipe, not of polls.")
            live = {k: {**v, "provenance": LIVE_PROVENANCE}
                    for k, v in out["models"].items()}
            if live:
                hist[date] = {"snapshot_date": date, "models": live}
            priv = DATA / str(a.cycle) / "model_private"
            priv.mkdir(parents=True, exist_ok=True)
            (priv / "academic_models_history.json").write_text(
                json.dumps(hist, indent=2))
            print(f"  wrote model_private/academic_models_history.json"
                  f"   PRIVATE — {len(hist)} date(s)")
            print(f"  next: python3 forecast/model/seats.py --cycle {a.cycle} "
                  f"--backfill-academic   (projects each of those dates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
