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

ELECTION_DAY = "2026-11-03"

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

MODELS = [
    {
        "key": "academic_bew",
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
        "name": "Referendum (approval + income, no exposure)",
        "citation": REFERENDUM_CITE,
        "attribution": "Our fit of the referendum specification. NOT Lewis-Beck "
                       "& Tien's published forecast.",
        "status": "implemented",
        "refit": True,
        "run": run_referendum,
    },
    {
        "key": "academic_state_approval_economy",
        "name": "State approval + state economy (Enns et al.)",
        "citation": ENNS_CITE,
        "attribution": "Not implemented. Listed so the gap is visible.",
        "status": "stub",
        "missing": ENNS_MISSING,
        "refit": None,
        "run": run_enns,
    },
]


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
            }
            why = spec.get("missing") or ["inputs unavailable"]
            print(f"\n  {spec['key'].upper()}  [not run]")
            for m in why:
                print(f"      missing: {m}")
            continue

        lo, hi = res.interval_80 or (None, None)
        out["models"][spec["key"]] = {
            "name": spec["name"],
            "citation": spec["citation"],
            "attribution": spec["attribution"],
            "category": "academic",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
