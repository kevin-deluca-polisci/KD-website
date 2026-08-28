#!/usr/bin/env python3
"""
Polling-based forecast: generic ballot as national tide, PVI to translate.

    python3 forecast/model/polling.py
    python3 forecast/model/polling.py --calibrate      # show the sigma fit
    python3 forecast/model/polling.py --house          # include the House run

THE CHAIN

    generic ballot average          Silver Bulletin, 21-day window
      -> shrink toward even         because early leads overstate (BEW)
      -> election-day national tide
      -> per-state expected margin  tide + 2 x state PVI
      -> per-state win probability  normal CDF around that margin
      -> seat distribution          simulation with CORRELATED errors

WHY THE FACTOR OF TWO KEEPS APPEARING
    PVI is expressed in points of VOTE SHARE; margins are points of MARGIN.
    A state whose D share runs 5 points above the nation has a D margin about
    10 points above it, because every vote that switches sides moves the margin
    twice. state_pvi.py shipped this wrong once and the --compare check caught
    it. Every conversion here goes through _pvi_to_margin() so there is exactly
    one place to get it wrong.

WHAT IS PUBLISHABLE AND WHAT IS NOT — READ BEFORE ADDING OUTPUTS

    The SENATE run is publishable. It uses our own state PVI, reconstructed
    from MEDSL's CC0 returns by a documented method, so nothing in the output
    encodes anyone's proprietary index.

    The HOUSE run publishes district MARGINS but never the district INDEX.
    That distinction is the whole of the policy, and it is a licensing
    judgment, not an arithmetic one: given the national tide, PVI =
    (margin - tide) / 2 exactly, so a published district margin does yield
    the index to anyone who cares to divide. The call taken on 2026-08-21 is
    that publishing our own derived forecast is not redistributing someone
    else's dataset. The `pvi` quantity itself stays in NEVER_PUBLISH and
    appears in no published file.

    An earlier version of this docstring said the House run could never be
    published because the inverse made it impossible. That was a policy
    dressed up as a theorem. The arithmetic has not changed; the policy has.

THE JUDGMENT CALLS, STATED PLAINLY
    Every number below that is a choice rather than a measurement is a named
    constant with its reasoning attached. There are four: the shrinkage
    asymptote and time constant, the national error, and the seat baseline.
    Anyone who disagrees can change one line and re-run, which is the point of
    writing the method down rather than publishing a number.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# The module's own directory too, so `maps` imports whether polling is run
# directly, imported by seats.py, or imported as forecast.model.polling.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import maps  # noqa: E402  — dated district baselines
from forecast.collect.parsers import is_state          # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

ELECTION_DAY = "2026-11-03"

# ---------------------------------------------------------------------------
# JUDGMENT CALL 1 & 2 — how hard to shrink the generic ballot, and how fast
# that shrinkage relaxes as the election approaches.
#
# Bafumi, Erikson & Wlezien (2010) regress the November House vote on generic
# ballot polls taken at various points in the cycle. Early-cycle leads are
# heavily discounted — their summary is that an early lead is effectively
# halved by Election Day. Later work on the modern, more nationalised era puts
# the slope near 0.87 for polls close to the election.
#
# So: lambda(d) = A + (1 - A) * exp(-d / TAU), which is 1.0 on Election Day and
# decays toward A far out. TAU is set so lambda(80 days) ~= 0.87, matching the
# modern near-election estimate, given A = 0.5 from BEW.
#
# This is a stylised reading of published findings, NOT a re-estimation — we
# hold no historical generic-ballot series to fit. It is the single largest
# judgment call in the model and should be labelled as such on the methods
# page. Anyone preferring pure BEW sets SHRINK_ASYMPTOTE = 0.5 and a long TAU;
# anyone who thinks polls are unbiased now sets SHRINK_ASYMPTOTE = 1.0.
SHRINK_ASYMPTOTE = 0.50
SHRINK_TAU_DAYS = 265.7

# NO FIXED BIAS CORRECTION IS APPLIED, deliberately. The generic ballot's
# historical bias is not a constant to subtract: Cassino et al. find it is
# conditional on presidential approval and close to zero around 40% approval.
# Baking in "polls understate Republicans by N" would be fitting the last cycle
# and calling it a law.

# JUDGMENT CALL 3 — the national error. Even after shrinkage the tide itself
# can be wrong, and that error hits every state at once. It is the difference
# between "each race is a coin flip" and "they all move together", and it
# dominates the seat distribution's tails: on the 2026 lines it carries about
# 85% of the variance of the seat total, which makes it the most consequential
# number in this file by a wide margin.
#
# IT IS 538'S, NOT OURS, AND THAT IS DELIBERATE.
#
# It used to be 4.0, described in this comment as "a deliberately generous
# reading of recent final-poll misses". That was a guess. It was never
# estimated against anything, it could not be, and a guess sitting on 85% of
# the variance is not a judgment call so much as an unexamined one.
#
# 538's 2024 House model publishes a full error decomposition: about 3 points
# at the national level, 2 each at the regional, state and demographic-cluster
# levels, 6 at the district level, plus "about 2 points on the margin" of
# extra error for unforeseen problems in generic-ballot polling. Their stated
# per-seat total of "about 8" confirms those combine in quadrature —
# sqrt(9+4+4+4+36+4) = 7.81 — so the generic-ballot buffer belongs at the
# national level and the national term is
#
#     3.0 (+) 2.0 = sqrt(13) = 3.606
#
# WHY BORROWING THIS ONE IS LEGITIMATE AND BORROWING THE REST IS NOT. A sigma
# is either a statement about the DATA or a statement about the MODEL, and
# only the first kind travels. 3.0 describes how far a poll-based estimate of
# the national environment lands from the actual national House vote. That is
# a property of the generic ballot as an instrument, and we read the same
# instrument they do, so their number describes our tide as well as theirs.
#
# Their 6.0 at the district level does NOT travel, and calibrate_sigma_house
# keeps measuring ours instead. It is the residual of a district forecast
# built from district polls, fundraising, candidate quality and expert
# ratings; ours is tide + slope x 2 x baseline + incumbency. Taking their
# residual for our prediction is the same error the comment in
# house_forecast() describes — claiming an accuracy the projection has not
# earned — and it would move the point estimate too, not just the spread,
# because the seat curve is convex where our mass currently sits.
#
# A SIDE EFFECT WORTH NAMING: this number carries no horizon. 538 state one
# figure for an election-day forecast, so adopting it retires the question of
# how sigma should shrink between now and November. That question was worth
# asking — the answer, from our own returns, is that most of the national
# error is terminal polling bias, which does not shrink with time at all, and
# the parts that do shrink are small enough that a decaying sigma would be
# modelling a mechanism that is not there.
#
# WHAT WOULD CHANGE THIS. Two things. If 538 publish a revised decomposition
# for 2026, follow it and say so. If we ever hold enough historical
# generic-ballot series to fit the terminal error ourselves — see
# model/sigma_sweep.py for the market cross-checks and the argument — then
# estimate it and stop borrowing.
SIGMA_NATIONAL = 3.606      # 538 2024 House: 3.0 national (+) 2.0 generic-ballot

# ---------------------------------------------------------------------------
# THE SAME SIGMA WAS BEING USED TWENTY MONTHS OUT AND ON ELECTION EVE
# ---------------------------------------------------------------------------
# SIGMA_NATIONAL is 538's TERMINAL error — what the national number is worth
# once the campaign has happened. Applying it to a projection dated 2025-03-05,
# 608 days from the election, claims we knew as much then as we will on the
# morning of the third. That is how the backfilled fundamentals line came to
# sit at 93% in March 2025 and stay pinned above 90% for a year: not because
# the models were confident, but because nothing widened their error at range.
#
# THE SHAPE IS A RANDOM WALK, which is why variance is linear in time and sigma
# therefore grows as the square root. A tide that drifts a little each day
# accumulates variance in proportion to the days remaining; that is the same
# reasoning every published model uses to fan its cone out toward the horizon.
# A straight line in sigma has no such story behind it, and — measured on this
# archive — lands within 0.003 of the square root at these endpoints anyway, so
# nothing is bought by the version that cannot be justified.
#
#     sigma(d) = sqrt( TERMINAL^2 + (FAR^2 - TERMINAL^2) * min(d, FAR_DAYS)/FAR_DAYS )
#
# FAR is 6.0, roughly a two-in-three chance of landing within six points of the
# eventual national margin a year and a half out. It is a judgment call and is
# recorded as one; the honest alternative was to keep asserting 3.606, which is
# also a judgment call and a worse one.
#
# WHAT IT DOES NOT FIX, said here so nobody expects it to: most of the flatness
# in the academic and fundamentals lines is SATURATION, not overconfidence.
# Four of the five models sit far enough above 218 that probability cannot
# respond to them, and Lockerbie is pinned at 1.00 under every sigma tried.
# Widening the horizon removes false precision from the early series. Giving
# each model its own published error is the separate change that would give
# those lines shape.
SIGMA_NATIONAL_FAR = 6.0
SIGMA_WIDEN_DAYS = 600.0
ELECTION_DAY_ISO = "2026-11-03"

_SIGMA_NAT = SIGMA_NATIONAL     # the ACTIVE value; set_horizon moves it


def sigma_national_on(asof: str | None) -> float:
    """The national term for a projection dated `asof`. None means today."""
    import datetime as _dt
    if asof is None:
        asof = _dt.date.today().isoformat()
    try:
        days = (_dt.date.fromisoformat(ELECTION_DAY_ISO)
                - _dt.date.fromisoformat(asof)).days
    except (TypeError, ValueError):
        return SIGMA_NATIONAL
    f = min(max(float(days), 0.0), SIGMA_WIDEN_DAYS) / SIGMA_WIDEN_DAYS
    return round(math.sqrt(SIGMA_NATIONAL ** 2
                           + (SIGMA_NATIONAL_FAR ** 2 - SIGMA_NATIONAL ** 2) * f), 4)


def set_horizon(asof: str | None, floor: float | None = None) -> float:
    """Point the simulations at `asof`'s national sigma. Returns what was set.

    A MODULE-LEVEL SWITCH RATHER THAN A THREADED ARGUMENT, deliberately and
    not happily. SIGMA_NATIONAL is read in eight places across two simulation
    functions and their outputs; threading a parameter through all of them is
    the cleaner design and a larger change than is wise two days before a
    freeze. seats.py sets it once per projection date, immediately before
    projecting. If a caller forgets, the value stays at whatever the previous
    call left, which is the failure mode this comment exists to warn about —
    so seats.project() sets it unconditionally, including for today.
    """
    global _SIGMA_NAT
    _SIGMA_NAT = sigma_national_on(asof)
    # A MODEL THAT PUBLISHES ITS OWN ERROR GETS ITS OWN ERROR.
    #
    # The horizon term is what OUR national number is worth; it is 538's
    # polling error, and it is the right instrument for a model whose input is
    # a poll average. It is the wrong one for a forecaster who states his own
    # uncertainty and whose is larger. Fair writes "the standard error is about
    # 3 percentage points" of vote share, which is 6 of margin — wider than
    # anything the horizon curve reaches — and projecting him through 3.6 gave
    # his forecast a precision he does not claim.
    #
    # A FLOOR RATHER THAN A REPLACEMENT, so the horizon can still widen a
    # source beyond its published error at long range if it ever exceeds it.
    # Whichever is larger is the honest one: a published error does not shrink
    # because the election is close, and the horizon term does not shrink
    # because a forecaster is confident.
    if floor is not None:
        _SIGMA_NAT = max(_SIGMA_NAT, float(floor))
    return _SIGMA_NAT
SIGMA_STATE_FLOOR = 3.0     # idiosyncratic error never falls below this

N_SIMS = 20000
SEED = 20261103             # fixed: the same archive date must reproduce

# JUDGMENT CALL 4 — the seat baseline. Not a forecast at all: pure bookkeeping,
# and by far the most leveraged number in this file.
#
#   Senate before the election      53 R - 47 D (2 independents caucus D)
#   Up in 2026                      33 Class 2 seats (20 R, 13 D)
#                                   + 2 specials, both R-held (OH, FL)
#                                   = 35 up: 22 R, 13 D
#   Therefore not up                D 47 - 13 = 34,  R 53 - 22 = 31
#   Check                           34 + 31 + 35 = 100
#
# This matches grant_williams' own senate_forecast.json, which independently
# reports dem_defending 13, rep_defending 22, dem_not_up 34, rep_not_up 31.
#
# WHY IT IS FLAGGED SO LOUDLY: moving this by ONE seat moves the headline
# probability by about twenty points — 34 gives P(D reach 50+) = 0.39, and 35
# gives 0.59. It arrives as a constant, carries no uncertainty, and would sail
# through every check in this pipeline while silently dominating the answer.
# 270toWin's page says 23 of the 35 are R-held, which would imply 35; that
# appears to miscount one seat, but the disagreement is the point. main()
# always prints the one-seat sensitivity next to the headline so the number
# can never be read as more precise than its weakest input.
HOLDOVER_D_DEFAULT = 34


def _pvi_to_margin(pvi: float) -> float:
    """PVI (vote-share points) -> expected margin shift (margin points)."""
    return 2.0 * pvi


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _days_to_election(today: str) -> int:
    import datetime as dt
    a = dt.date.fromisoformat(today)
    b = dt.date.fromisoformat(ELECTION_DAY)
    return (b - a).days


def shrink_lambda(days_out: int) -> float:
    d = max(0, days_out)
    return SHRINK_ASYMPTOTE + (1.0 - SHRINK_ASYMPTOTE) * math.exp(-d / SHRINK_TAU_DAYS)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def latest_parsed(cycle: int) -> tuple[str, list[dict]]:
    """
    The newest parsed date that actually carries a generic ballot.

    Not simply the newest file. Dates roll over at UTC midnight and parse.py
    defaults to today, so the moment the day ticks over there is a fresh CSV
    holding nothing but the static MEDSL artifacts — 84 rows, no polling, no
    forecasts. Taking the newest file blindly means that from midnight until
    the day's capture completes, this model reads a stub and dies claiming the
    polling category has vanished. Walking back to the last usable snapshot is
    both correct and what a human would do.
    """
    files = sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")))
    if not files:
        raise SystemExit(
            "no parsed data found. Run:  ./forecast/run.sh --from parse")
    skipped: list[str] = []
    for f in reversed(files):
        p = Path(f)
        with p.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if any(r["source_id"] == "silver_bulletin" for r in rows):
            if skipped:
                print(f"  note: skipped {len(skipped)} date(s) with no polling data "
                      f"({', '.join(skipped[:3])}) — today's capture may not have run yet")
            return p.stem, rows
        skipped.append(p.stem)
    raise SystemExit(
        f"no parsed date contains generic ballot data (checked {len(files)}).\n"
        f"  silver_bulletin is the polling category — run:  ./forecast/run.sh")


def generic_ballot(rows: list[dict]) -> dict:
    """
    The national tide input.

    Prefers Silver's house-effect + likely-voter adjusted figure over the raw
    average. That is his model output rather than a raw poll aggregate, which
    is a dependency worth stating: our polling model is not independent of his.
    grant_williams already declares silver_bulletin as an input for the same
    reason, and pretending otherwise would make the dependency graph lie.
    """
    got = {}
    for r in rows:
        if r["source_id"] != "silver_bulletin":
            continue
        # `margin_D` IS the adjusted figure as of 2026-08-27 — see the header
        # of collect/parsers/silver_bulletin.py. The raw poll mean moved to its
        # own quantity, and Wikipedia's rounded copy of him moved to
        # margin_D_wikipedia_reported, which is why neither is read here: both
        # used to arrive as `margin_D` and whichever row came last won.
        if r["quantity"] in ("margin_D", "margin_D_adjusted",
                             "margin_D_raw_poll_mean"):
            got[r["quantity"]] = float(r["value"])
    if not got:
        raise SystemExit(
            "no generic ballot rows in the latest parsed file.\n"
            "  silver_bulletin is the polling category — check it captured.")
    used = "margin_D_adjusted" if "margin_D_adjusted" in got else "margin_D"
    return {"raw": got.get("margin_D_raw_poll_mean"),
            "adjusted": got.get("margin_D_adjusted", got.get("margin_D")),
            "used": used, "value": got[used]}


def reconstructed_state_pvi(cycle: int) -> dict[str, float]:
    p = DATA / str(cycle) / "derived" / "state_pvi_reconstructed.json"
    if not p.exists():
        raise SystemExit(
            "state PVI not built yet. Run:  python3 forecast/model/state_pvi.py")
    obj = json.loads(p.read_text())
    return {k: v["pvi"] for k, v in obj["states"].items()}


def senate_states_up(rows: list[dict]) -> list[str]:
    """
    Which states hold a Senate race this cycle, read off the archive rather
    than hardcoded. Any forecaster covering a race is evidence it exists, and
    a list in a constant would quietly rot when a special election is called.
    """
    out = set()
    for r in rows:
        if r["chamber"] == "senate" and is_state(r["state"]) and r["quantity"] in (
                "win_prob_D", "margin_D", "rating_numeric"):
            out.add(r["state"].upper())
    if len(out) < 20:
        # Refuse rather than quietly forecasting a handful of races. A Senate
        # cycle has ~33-35 contests; anything far below that means the archive
        # is not carrying them, not that the map shrank. This check is here
        # because the first run of this model reported "SENATE · 0 races" in
        # the middle of otherwise healthy output, and a zero is far too easy
        # to read past.
        raise SystemExit(
            f"only {len(out)} Senate states found in the archive ({sorted(out)}).\n"
            f"  A 2026 Senate map has ~35. The sources that carry Senate races are\n"
            f"  race_to_the_wh (needs the live-data capture) and grant_williams.\n"
            f"  Run:  ./forecast/run.sh        then re-run this model.")
    return sorted(out)


# ---------------------------------------------------------------------------
# Calibration — how far do real Senate margins fall from a partisan baseline?
# ---------------------------------------------------------------------------

def _medsl_rows(cycle: int, needle: str) -> list[dict]:
    root = DATA / str(cycle) / "raw" / "medsl"
    if not root.is_dir():
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for day in sorted(root.iterdir(), reverse=True):
        if not day.is_dir():
            continue
        for f in sorted(day.iterdir()):
            if not f.is_file() or f.name.endswith(".meta.json"):
                continue
            if needle not in f.name:
                continue
            if f.name.split(".")[0] in seen:
                continue
            seen.add(f.name.split(".")[0])
            try:
                out += list(csv.DictReader(
                    f.read_text(encoding="utf-8-sig").splitlines()))
            except Exception:
                continue
    return out


MAJOR_PARTY_FLOOR = 0.90    # D+R share of all votes cast, below which we drop


def _two_party_margin(rows: list[dict], year: int, office: str,
                      require_major: bool = False) -> tuple[dict[str, float], float]:
    """
    Per-state two-party margin, and the VOTE-WEIGHTED national margin.

    Two things here were wrong in the first version and both mattered:

    1. The national margin was the unweighted mean of state margins, which
       gives Wyoming the same say as California. It put the calibration's
       intercept at -11 points. The nation's margin is total D minus total R.

    2. `require_major` drops states where the two major parties are not the
       contest. Vermont 2024 is Sanders (I) versus a Republican; Nebraska 2024
       is Ricketts (R) versus Osborn (I). Both have a token or absent Democrat,
       so their "two-party margin" computes to -100, and two such states in a
       sample of 33 were single-handedly inflating the estimated sigma to 28
       points — a number that would have made every race a coin flip and the
       whole model useless. They are not outliers to be robustly downweighted;
       they are a different question being answered.
    """
    tally: dict[str, dict[str, float]] = defaultdict(lambda: {"D": 0.0, "R": 0.0})
    total: dict[str, float] = defaultdict(float)
    for r in rows:
        try:
            if int(float(r.get("year") or 0)) != year:
                continue
        except (TypeError, ValueError):
            continue
        if office not in (r.get("office") or "").upper():
            continue
        if (r.get("stage") or "GEN").upper() not in ("GEN", ""):
            continue
        po = (r.get("state_po") or "").strip().upper()
        if len(po) != 2:
            continue
        party = (r.get("party_simplified") or r.get("party_detailed") or "").upper()
        try:
            v = float(r.get("candidatevotes") or r.get("votes") or 0)
        except (TypeError, ValueError):
            continue
        if party.startswith("DEMOCRAT"):
            tally[po]["D"] += v
        elif party.startswith("REPUBLICAN"):
            tally[po]["R"] += v
        total[po] += v

    per: dict[str, float] = {}
    for k, v in tally.items():
        two = v["D"] + v["R"]
        if two <= 0:
            continue
        if require_major and total[k] > 0 and (two / total[k]) < MAJOR_PARTY_FLOOR:
            continue
        per[k] = (v["D"] - v["R"]) / two * 100

    nd = sum(tally[k]["D"] for k in per)
    nr = sum(tally[k]["R"] for k in per)
    natl = (nd - nr) / (nd + nr) * 100 if (nd + nr) else 0.0
    return per, natl


def calibrate_sigma(cycle: int) -> dict:
    """
    Estimate the spread of Senate margins around a PVI baseline, from returns.

    A prior pulled from the air would do here, but we hold the data to do
    better: regress each state's actual Senate margin on twice its PVI, where
    the PVI is built from the two presidential cycles BEFORE that Senate race,
    so nothing from the outcome leaks into the predictor.

    The intercept absorbs the fact that the presidential and Senate national
    baselines differ, so the residual SD measures what we actually want: how
    much candidate quality, incumbency and local conditions move a Senate race
    away from its state's partisan lean. The fitted SLOPE is a bonus diagnostic
    — near 1.0 means uniform swing on PVI is a fair description, which is the
    assumption the forecast rests on.

    Thin by construction right now: MEDSL's per-cycle repo gives us 2024 only,
    so n is about 33 from a single year, and a single year cannot separate a
    national miss from state-level noise. Capturing the multi-cycle Senate
    bundle from Dataverse would fix that, and this function already uses every
    Senate year it finds.
    """
    pres = _medsl_rows(cycle, "president")
    sen = _medsl_rows(cycle, "senate")
    if not pres or not sen:
        return {"ok": False, "why": "need both MEDSL presidential and Senate returns"}

    sen_years = sorted({int(float(r["year"])) for r in sen
                        if (r.get("year") or "").strip()})
    pres_years = sorted({int(float(r["year"])) for r in pres
                         if (r.get("year") or "").strip()})

    xs: list[float] = []
    ys: list[float] = []
    used_years: list[int] = []
    for y in sen_years:
        prior = [p for p in pres_years if p < y][-2:]
        if len(prior) < 2:
            continue
        # PVI from the two presidential cycles strictly before this race.
        lean: dict[str, list[float]] = defaultdict(list)
        for w, py in zip((0.25, 0.75), prior):     # older first, then recent
            per, natl = _two_party_margin(pres, py, "PRESIDENT")
            if not per:
                continue
            for po, m in per.items():
                lean[po].append(w * (m - natl) / 2.0)
        pvi = {po: sum(v) for po, v in lean.items() if len(v) == 2}
        actual, _ = _two_party_margin(sen, y, "SENATE", require_major=True)
        n0 = len(xs)
        for po, m in actual.items():
            if po in pvi:
                xs.append(_pvi_to_margin(pvi[po]))
                ys.append(m)
        if len(xs) > n0:
            used_years.append(y)

    if len(xs) < 10:
        return {"ok": False, "why": f"only {len(xs)} usable state-years"}

    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 1.0
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    sigma = statistics.pstdev(resid) * math.sqrt(len(resid) / max(1, len(resid) - 2))
    return {"ok": True, "n": len(xs), "years": used_years,
            "slope": round(slope, 3), "intercept": round(intercept, 2),
            "sigma_total": round(sigma, 2),
            "thin": len(used_years) < 2}


# ---------------------------------------------------------------------------
# The forecast
# ---------------------------------------------------------------------------

def senate_forecast(tide: float, pvi: dict[str, float], states: list[str],
                    sigma_total: float, holdover_D: int | None) -> dict:
    sigma_state = math.sqrt(max(sigma_total ** 2 - _SIGMA_NAT ** 2,
                                SIGMA_STATE_FLOOR ** 2))
    races = {}
    for st in states:
        if st not in pvi:
            continue
        mu = tide + _pvi_to_margin(pvi[st])
        races[st] = {
            "expected_margin_D": round(mu, 2),
            "pvi": round(pvi[st], 2),
            "win_prob_D": round(_norm_cdf(mu / sigma_total), 4),
        }

    rng = random.Random(SEED)
    order = sorted(races)
    wins = []
    for _ in range(N_SIMS):
        nat = rng.gauss(0.0, _SIGMA_NAT)
        w = 0
        for st in order:
            m = races[st]["expected_margin_D"] + nat + rng.gauss(0.0, sigma_state)
            if m > 0:
                w += 1
        wins.append(w)
    wins.sort()

    out = {
        "n_races": len(races),
        "sigma_total": round(sigma_total, 2),
        "sigma_national": _SIGMA_NAT,
        "sigma_state": round(sigma_state, 2),
        "expected_D_seats_up": round(statistics.fmean(wins), 2),
        "D_seats_up_80pct": [wins[int(0.10 * N_SIMS)], wins[int(0.90 * N_SIMS)]],
        "races": races,
    }
    if holdover_D is not None:
        maj = sum(1 for w in wins if w + holdover_D >= 50) / N_SIMS
        out["holdover_D_assumed"] = holdover_D
        out["prob_D_50_plus"] = round(maj, 4)
        # Both thresholds, because they are answers to different questions and
        # the gap between them is large. Fifty is a tie; a tie is broken by the
        # vice-president, who is a Republican this cycle, so fifty-one is the
        # first number that is actually a majority. Prediction markets and
        # forecasters that publish "chance of Democratic control" are pricing
        # 51+, so comparing them against our 50+ figure compares two different
        # events — and this cycle they differ by about fifteen points.
        out["prob_D_51_plus"] = round(
            sum(1 for w in wins if w + holdover_D >= 51) / N_SIMS, 4)
        # Always carried in the output, never optional. The seat baseline is
        # bookkeeping rather than a forecast, so it has no error bar of its
        # own, and without this the headline reads as if it had none either.
        out["prob_D_50_plus_sensitivity"] = {
            str(h): round(sum(1 for w in wins if w + h >= 50) / N_SIMS, 4)
            for h in (holdover_D - 1, holdover_D, holdover_D + 1)}
        out["NOTE_holdover"] = (
            "prob_D_50_plus depends on holdover_D, which is an ASSUMPTION passed "
            "in, not something derived from the archive. 50+ is stated rather "
            "than 'majority' because control at 50 turns on the vice-presidency.")
    return out


# ---------------------------------------------------------------------------
# HOUSE SIGMA. Calibrated on House districts, against the baseline the House
# model actually uses, and split three ways rather than two.
#
# `calibrate_sigma` above estimates the spread of SENATE margins around a
# presidential PVI, and until now the House model borrowed that number. Two
# different offices and two different predictors. Senate races are statewide
# campaigns with more money, more name recognition and far more split-ticket
# voting than a House district sees, so there was never a reason to expect the
# residuals to transfer, and measurement showed they do not.
#
# THE THIRD LEVEL IS THE PART THAT WAS MISSING ENTIRELY. The old structure was
# a national error shared by all 435 plus an independent district error. But a
# baseline can be wrong for a whole STATE at once -- New Hampshire's composite
# has both its seats about seven points too Republican, because a governor's
# personal vote sits inside the index -- and a shared error does not average
# away across a delegation. It moves the whole delegation together, which is
# what fattens the tails of a seat total, and the tails are where P(majority)
# lives. Treating it as independent district noise understates exactly the
# quantity the site leads with.
#
#     margin_i = mu_i + nat + state_s + eps_i
#     nat     ~ N(0, SIGMA_NATIONAL)      shared by all 435
#     state_s ~ N(0, sigma_state)         shared within one state
#     eps_i   ~ N(0, sigma_district)      independent
#
# SIGMA_NATIONAL is NOT estimated here, and cannot be. It is uncertainty about
# the national tide -- a property of the polling forecast, not of the district
# residuals -- and the cycle intercept in the calibration absorbs it by
# construction. Trying to read it off these residuals would double-count it.
# It comes from 538's published decomposition instead; see the constant.
#
# THE OTHER TWO ARE OURS AND STAY OURS. 538 also publish district and
# state-level terms, and they are smaller than what this function measures.
# They describe a richer district model than ours and adopting them would
# claim an accuracy the projection has not earned. The gap between their
# numbers and these is not an error to be corrected; it is the price of a thin
# predictor, and the way to close it is to add predictors, not to assert a
# smaller residual.
HOUSE_SIGMA_FALLBACK = {"sigma_state": 3.4, "sigma_district": 6.1}


def house_incumbency(cycle: int = 2026) -> dict[str, int]:
    """{race_id: +1 D incumbent, -1 R incumbent, 0 open}, for the 2026 lines.

    WHERE THIS COMES FROM AND WHY THAT IS ALLOWED. The roster is read from the
    hand-entered Cook table, which also carries an `incumbent` name and an
    `open_seat` flag beside the index. Cook's INDEX is proprietary and never
    leaves this machine. Which party currently holds a district is not: it is
    on the Clerk of the House's roster and in every almanac, and it was checked
    against public returns before being used here -- Cook's incumbent party
    agrees with the 2024 MEDSL winner in 248 of 248 comparable districts, every
    one of them.

    A fact that is independently verifiable from a public source is a fact we
    may use, whatever table our copy happened to sit in. The index is not, and
    is not touched.
    """
    base = DATA / str(cycle) / "raw" / "cook_pvi"
    caps = sorted(base.glob("*/manual.json")) if base.exists() else []
    if not caps:
        return {}
    out: dict[str, int] = {}
    for r in json.loads(caps[-1].read_text(encoding="utf-8")).get("rows", []):
        st, d = str(r.get("state", "")).upper(), r.get("district")
        if not st or d is None:
            continue
        rid = f"HOU_{st}_{int(d):02d}_{cycle}"
        # An open seat has no incumbent to advantage, whoever used to hold it.
        if str(r.get("open_seat")).strip().lower() in ("true", "1", "yes"):
            out[rid] = 0
            continue
        # AN INCUMBENT WITH AN UNEXPECTED PARTY CODE IS STILL AN INCUMBENT.
        #
        # This used to read `1 if D else -1 if R else 0`, and 0 means OPEN
        # everywhere downstream. So a party code that was neither D nor R
        # silently converted a held seat into an open one: no incumbency term
        # in the projection, and one extra seat in every open-seat count built
        # off this roster. It produced a perfectly plausible number and no
        # error, which is why it survived.
        #
        # CA-06 is the live case and it is not a typo. Kevin Kiley was elected
        # as a Republican and is seeking re-election as an INDEPENDENT, so
        # Cook's table carries him as `I` with `open_seat` unset — correctly,
        # because the seat is not open. He holds it and he is running for it.
        #
        # The sign is the awkward part rather than the openness. An
        # independent's incumbency advantage does not accrue to either party in
        # a D-minus-R margin, and pretending otherwise would be a guess. What
        # is NOT in doubt is that the seat is harder for a Democrat than an
        # open seat would be, so the advantage sits on the non-Democratic side,
        # which is where he was elected from. Recorded here rather than
        # smuggled in as a party relabel.
        p = str(r.get("party") or "").strip().upper()[:1]
        if p == "D":
            out[rid] = 1
        elif p == "R":
            out[rid] = -1
        elif p:
            out[rid] = -1        # incumbent, not open; see the note above
        else:
            out[rid] = 0
    return out


def calibrate_sigma_house(cycle: int = 2026, baseline: str = "dra") -> dict:
    """Three-level House sigma, from house_calibration.py. Never raises.

    `baseline` MUST name the index the forecast is built from. A sigma fitted
    against a different index is not this model's sigma, and a slope fitted
    against a different index shrinks the wrong thing by the wrong amount.
    Measured on 2024, Cook's index and the DRA composite give sigmas of 4.55
    and 6.96 for the same districts -- not a rounding difference.

    Both specifications come back. Whichever one the projection can actually
    apply is the one it must use: adding an incumbency term while keeping the
    no-incumbency slope double-counts, and taking the with-incumbency sigma
    without applying the term claims an accuracy the model does not have.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import house_calibration as hc
        rows = hc.load_house(hc.DERIVED / "returns.csv")
        base = (hc.load_baseline_cook(cycle) if baseline == "cook"
                else hc.load_baseline_dra())
        inc = hc.incumbency(rows)
        obs = []
        for r in rows:
            if r["cycle"] not in (2022, 2024) or r["runoff"]:
                continue
            if (r["party"] != "DEMOCRAT" or r["uncontested"]
                    or r["votes_unreliable"]):
                continue
            bl = hc.baseline_for(base, r["state"], r["district"], r["cycle"])
            if bl is None or r["margin_D"] is None:
                continue
            obs.append({"cycle": r["cycle"], "state": r["state"],
                        "baseline": bl, "actual": r["margin_D"],
                        "inc": inc.get((r["cycle"], r["state"],
                                        r["district"]), 0)})
        if len(obs) < 100:
            return {"ok": False, "why": f"only {len(obs)} House observations",
                    **HOUSE_SIGMA_FALLBACK}
        out = {"ok": True, "baseline": baseline, "n": None, "cycles": None}
        for use, name in ((True, "with_incumbency"), (False, "baseline_only")):
            f = hc.fit(obs, use_inc=use)
            c = f["components"]
            if not c.get("ok"):
                return {"ok": False, "why": c.get("why", "no components"),
                        **HOUSE_SIGMA_FALLBACK}
            out["n"], out["cycles"] = f["n"], f["cycles"]
            out[name] = {
                "slope": f["slope_baseline"],
                "incumbency_pts": f["incumbency_pts"],
                "sigma_state": c["sigma_state"],
                "sigma_district": c["sigma_district"],
                "sigma_total": round(math.sqrt(
                    SIGMA_NATIONAL ** 2 + c["sigma_state"] ** 2
                    + c["sigma_district"] ** 2), 2)}
        return out
    except Exception as e:                       # never break the daily run
        return {"ok": False, "why": f"{type(e).__name__}: {e}",
                **HOUSE_SIGMA_FALLBACK}


# Which district index to use when the archive holds more than one. Cook's own
# 2026 lines first, then Grant Williams' republication of them, then whatever
# Wikipedia's ratings table carries. Previously this was "whichever row the
# parser happened to emit first", which made the House forecast depend on file
# ordering — the same model could change answer because a source was renamed.
# WHICH DISTRICT BASELINE THE HOUSE PROJECTION USES, in order.
#
# `dra` leads. Cook's index is the better-centred one and by the pooled
# 2022+2024 calibration it is also the more accurate -- sigma_state 3.15 vs
# 4.19, sigma_district 8.05 vs 8.82 -- but the whole difference is worth about
# two points on P(D majority) and 0.7 seats of width, and Cook's index can
# never be published. The redistricting page needs a district partisanship
# number a reader can check. That trade was made deliberately: a small,
# measured accuracy cost in exchange for a page that can exist at all.
#
# Cook stays second, so removing DRA from the archive falls back rather than
# failing, and the private class model can still be run against it.
PVI_PREFERENCE = ("dra", "cook_pvi", "grant_williams", "wikipedia")

# DRA computes no district export for a single-district state, because there
# the district IS the state and the app has nothing to divide. These six are
# filled from our own composite of MEDSL returns; see model/state_composite.py.
DRA_AT_LARGE = ("AK", "DE", "ND", "SD", "VT", "WY")

# The quantity pair DRA rows are filed under. See parsers/dra.py for why it is
# not `pvi`.
DRA_QUANTITIES = ("composite_share", "composite_share_prior")

# Below this, the DRA rows for a snapshot are treated as absent rather
# than as a baseline. 400 of 435 leaves room for a state missing from an
# export without accepting a table that is obviously a fragment.
DRA_MIN_DISTRICTS = 400

# Which calibration each baseline source needs. The slope, the incumbency
# coefficient and the three sigmas are all properties of the index they were
# fitted against, so this map is not a convenience -- reading it wrong means
# projecting one index with another's slope, which is the same class of error
# as borrowing a richer model's residual. grant_williams and wikipedia both
# republish Cook's index, so they calibrate as Cook.
BASELINE_CALIBRATION = {"dra": "dra", "cook_pvi": "cook",
                        "grant_williams": "cook", "wikipedia": "cook"}


def _at_large_shares(cycle: int = 2026) -> dict[str, float]:
    """{state: two-party D share} for the six single-district states."""
    f = DATA / str(cycle) / "derived" / "state_composite.csv"
    if not f.exists():
        return {}
    import csv as _csv
    out = {}
    with f.open() as fh:
        for r in _csv.DictReader(fh):
            if r["state"] in DRA_AT_LARGE:
                try:
                    out[r["state"]] = float(r["statewide_two_party_D"])
                except (TypeError, ValueError):
                    continue
    return out


def dra_baseline(rows: list[dict], cycle: int = 2026
                 ) -> tuple[dict[str, float], dict[str, float], dict]:
    """DRA's composite as a PVI-equivalent: (current, prior, detail).

    THE CENTRING IS THE WHOLE FUNCTION, so it is worth being explicit about
    what it does and what it cannot do.

    Cook's PVI is defined as a deviation from the national presidential vote,
    so `tide + 2 x PVI` is coherent with `tide` meaning "the national margin"
    without anyone having to do anything. DRA's composite is an absolute
    two-party share inside the district. Feed the generic ballot into that raw
    and every district comes out about two and a half points too Republican,
    because the composite's own national level is sitting inside the number.

    So each district is expressed as its distance from the MEAN DISTRICT:

        pvi_equivalent_i = share_i - mean(share over all 435 current districts)

    That is the same shape as Cook's definition with "the mean district"
    standing in for "the nation". It is not identical to the nation: districts
    are equal in population and unequal in turnout, and turnout is lower in the
    safest Democratic seats, so a population-weighted mean sits a little to the
    Democratic side of the actual national vote.

    WHAT THAT COSTS, MEASURED RATHER THAN ASSUMED. Refitting the calibration
    against the centred baseline and comparing each cycle's fitted intercept to
    the actual national House margin from returns.csv:

        centred DRA   2022  -1.04    2024  +0.72     mean -0.16
        Cook, as used 2022  -0.90    2024  +0.82     mean -0.04

    So centring makes DRA behave like Cook to within a sixth of a point on
    average, with the same +/- 0.9 of per-cycle wobble. That wobble is the
    error in translating a national margin onto a district index at all, it is
    a property of both baselines rather than of this choice, and against
    SIGMA_NATIONAL of 3.6 it raises the total by 3%. Named here so nobody
    rediscovers it as a bug.

    ONE CONSTANT, BOTH MAPS. The prior-map districts are centred on the SAME
    number as the current ones, computed from the current 435. Redistricting
    moves voters between districts; it does not change how the country votes.
    Recentring each vintage on its own mean would silently make a redraw look
    like a national swing, which is precisely the artefact the redistricting
    page exists to measure rather than manufacture.
    """
    cur, pri = maps.split_rows(rows, "dra", DRA_QUANTITIES)

    # The six single-district states, in the same units (a two-party share).
    #
    # DISTRICT "01", NOT "00". An at-large seat has no natural number and the
    # archive had to pick one; cook_pvi and the incumbency roster both write
    # 01, so that is the convention and this has to match it. Writing 00 here
    # cost nothing visible -- the baseline still had 435 districts and the run
    # still succeeded -- while quietly dropping the incumbency term for all six
    # states, because house_incumbency() is keyed on race_id and none of the
    # six keys matched. Four incumbents disappeared and the seat count moved
    # with no error anywhere. Hence the assertion below.
    filled = []
    for st, share in _at_large_shares(cycle).items():
        rid = f"HOU_{st}_01_{cycle}"
        if rid not in cur:
            cur[rid] = share
            filled.append(st)

    if not cur:
        return {}, {}, {"ok": False, "why": "no DRA composite rows"}

    # THE KEYS HAVE TO LINE UP WITH EVERY OTHER DISTRICT TABLE. A baseline
    # whose race_ids do not match the incumbency roster loses incumbency for
    # those districts and says nothing about it -- see the note on district
    # numbering above. This is cheap and it catches the whole class.
    roster = house_incumbency(cycle)
    if roster:
        orphans = sorted(rid for rid in cur if rid not in roster)
        if orphans:
            raise ValueError(
                f"DRA baseline has {len(orphans)} race_id(s) the incumbency "
                f"roster does not know: {orphans[:6]}. These would be "
                f"projected with no incumbency term and no warning. Check the "
                f"at-large district number and the cycle suffix.")

    centre = statistics.fmean(cur.values())
    detail = {"ok": True, "n_current": len(cur), "n_prior": len(pri),
              "centre_share": round(centre, 4),
              "at_large_filled": sorted(filled),
              "at_large_source": "derived/state_composite.csv (MEDSL)"}
    return ({rid: v - centre for rid, v in cur.items()},
            {rid: v - centre for rid, v in pri.items()},
            detail)



def house_forecast(tide: float, rows: list[dict], sigma_total: float,
                   asof: str | None = None, house_sigma: dict | None = None) -> dict:
    """
    District-level run.

    ON PUBLICATION. District margins here are OUR forecast, computed from a
    district partisan index we do not redistribute. The index itself — the
    `pvi` quantity — stays out of every published file and remains in
    aggregate.py's NEVER_PUBLISH.

    Be clear-eyed about what that does and does not protect. Publishing a
    district margin alongside the national tide gives the index back by exact
    arithmetic, PVI = (margin - tide) / 2, so this is a licensing judgment
    about republishing a derived forecast rather than a mathematical barrier.
    That judgment was made deliberately on 2026-08-21; anything written here
    that implied the arithmetic was a safeguard has been corrected, because a
    comment claiming a protection the code does not provide is worse than no
    comment at all.
    """
    # TWO SHAPES OF BASELINE, ONE SCALE OUT.
    #
    # Cook files a deviation under `pvi`; DRA files an absolute share under
    # `composite_share` and has to be centred before it means the same thing.
    # Everything downstream of here works in Cook's units -- a share deviation,
    # doubled into a margin by _pvi_to_margin -- so the difference is resolved
    # once, here, and never again.
    by_source: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["quantity"] == "pvi" and r["chamber"] == "house" and r["race_id"]:
            by_source.setdefault(r["source_id"], {}).setdefault(
                r["race_id"], float(r["value"]))
    dra_cur, dra_pri, dra_detail = dra_baseline(rows)
    # A HANDFUL OF DISTRICTS IS NOT A BASELINE, and accepting one produced the
    # most dangerous kind of failure: a plausible-looking run.
    #
    # Rebuilding 2026-08-21 read that date's parsed rows, which predate the DRA
    # snapshot, so `composite_share` was absent and dra_baseline returned only
    # the six at-large states it fills from the MEDSL composite. PVI_PREFERENCE
    # picked `dra` because it was non-empty, the House was projected over SIX
    # districts, and the answer — 2.73 expected Democratic seats — was written
    # to the archive without a single warning.
    #
    # The floor is deliberately low. It is not trying to judge whether a
    # baseline is good; it is refusing one that cannot possibly be complete, so
    # the preference falls through to Cook instead of silently succeeding.
    if dra_cur and len(dra_cur) >= DRA_MIN_DISTRICTS:
        by_source["dra"] = dra_cur
    elif dra_cur:
        print(f"  dra: only {len(dra_cur)} district(s) in this snapshot — not a "
              f"usable baseline, falling through to the next source in "
              f"PVI_PREFERENCE")
        dra_cur, dra_pri = {}, {}

    source = next((s for s in PVI_PREFERENCE if by_source.get(s)),
                  next(iter(by_source), None))
    pvi = by_source.get(source or "", {})
    if not pvi:
        return {"ok": False, "why": "no district baseline in the archive"}
    baseline_detail = dra_detail if source == "dra" else {"ok": True}

    # THE MAP IS A FUNCTION OF THE DATE.
    #
    # Ten states redrew during this cycle and 123 of 435 districts moved, so
    # projecting a March 2025 tide onto today's lines produces a seat count
    # for districts that did not exist. `asof` selects per state: current
    # index where that state's map was already in effect, previous index
    # where it was not. Passing nothing keeps the old behaviour exactly.
    vintage = "current map (no date supplied)"
    if asof:
        if source == "dra":
            cur, prior = dra_cur, dra_pri
        else:
            cur, prior = maps.split_rows(rows, source or "")
        if prior:
            pvi, detail = maps.baseline_asof(cur or pvi, prior, asof)
            vintage = detail["vintage"]
        else:
            vintage = (f"current map as of {asof} — NO pvi_prior rows in this "
                       f"snapshot, so no dated baseline was possible")

    # THREE LEVELS, not two. See calibrate_sigma_house: a state-shared error
    # is what a two-level structure cannot express, and it is the one that
    # decides the tails, because it moves a whole delegation at once instead of
    # averaging away across 435 independent draws.
    # THE SIGMA MUST BE THE ONE FITTED AGAINST THE INDEX ACTUALLY SELECTED.
    # `source` was chosen a few lines above by PVI_PREFERENCE, and it can
    # change without anyone editing this file -- a missing DRA snapshot falls
    # back to Cook silently and correctly. What must not happen silently is
    # projecting the fallback index with the preferred index's slope.
    hs = (house_sigma if house_sigma is not None
          else calibrate_sigma_house(
              baseline=BASELINE_CALIBRATION.get(source or "", "cook")))
    if hs.get("ok"):
        pass                       # components chosen below, beside the slope
    else:
        # The old two-level split, kept as the fallback so a missing returns
        # file degrades the model rather than stopping it. Named as such in the
        # output, because a silent fallback is how a model quietly stops being
        # the model that was documented.
        sigma_st = 0.0
        sigma_d = math.sqrt(max(sigma_total ** 2 - _SIGMA_NAT ** 2,
                                SIGMA_STATE_FLOOR ** 2))
        sigma_used = sigma_total
        sigma_src = f"FALLBACK, senate-calibrated ({hs.get('why', '?')})"

    # THE POINT ESTIMATE AND THE SPREAD NOW COME FROM ONE MODEL.
    #
    # Until now the margin was tide + 2 x PVI with no incumbency term, while
    # sigma was taken from a fit that HAD one. That borrows the tighter
    # residual of a richer model to describe a poorer prediction, and it is
    # wrong in the direction that flatters: it claims an accuracy the
    # projection has not earned.
    #
    # So the slope, the incumbency coefficient and the three sigmas are drawn
    # from the SAME fit, and if the roster is unavailable the model falls back
    # to the no-incumbency slope AND the no-incumbency sigma together, never
    # one without the other.
    inc = house_incumbency() if hs.get("ok") else {}
    spec_name = "with_incumbency" if inc else "baseline_only"
    spec = hs.get(spec_name) if hs.get("ok") else None
    if spec:
        sigma_st, sigma_d = spec["sigma_state"], spec["sigma_district"]
        sigma_used = spec["sigma_total"]
        slope, inc_pts = spec["slope"], (spec.get("incumbency_pts") or 0.0)
        sigma_src = (f"house-calibrated on {hs['n']} district-cycles "
                     f"{hs.get('cycles')}, baseline {hs.get('baseline')}, "
                     f"{spec_name}")
    else:
        slope, inc_pts = 1.0, 0.0
    districts = {rid: round(tide + slope * _pvi_to_margin(v)
                            + inc_pts * inc.get(rid, 0), 2)
                 for rid, v in pvi.items()}
    n_inc = sum(1 for rid in districts if inc.get(rid, 0) != 0)
    st_of = {rid: (rid.split("_")[1] if "_" in rid else "") for rid in districts}
    states = sorted(set(st_of.values()))

    rng = random.Random(SEED)
    order = sorted(districts)
    wins = []
    for _ in range(N_SIMS):
        nat = rng.gauss(0.0, _SIGMA_NAT)
        eff = ({s: rng.gauss(0.0, sigma_st) for s in states} if sigma_st > 0
               else {s: 0.0 for s in states})
        w = 0
        for rid in order:
            if (districts[rid] + nat + eff[st_of[rid]]
                    + rng.gauss(0.0, sigma_d) > 0):
                w += 1
        wins.append(w)
    wins.sort()
    return {
        "ok": True,
        "pvi_source": source,
        "baseline_detail": baseline_detail,
        "map_vintage": vintage,
        "n_districts": len(districts),
        "sigma_source": sigma_src,
        "sigma_national": _SIGMA_NAT,
        "sigma_state": round(sigma_st, 2),
        "sigma_district": round(sigma_d, 2),
        # THE THREE TERMS THAT WERE ACTUALLY DRAWN, not the calibration input.
        # sigma_used is the total the calibration was fitted at; once the
        # national term widens with the horizon it is no longer the total the
        # simulation ran on, and reporting it alongside sigma_national = 6.0
        # published a triple that does not satisfy its own Pythagoras.
        "sigma_total": round(math.sqrt(_SIGMA_NAT ** 2 + sigma_st ** 2
                                       + sigma_d ** 2), 2),
        "sigma_total_calibrated": round(sigma_used, 2),
        "baseline_slope": round(slope, 4),
        "incumbency_pts": round(inc_pts, 2),
        "n_incumbents": n_inc,
        "expected_D_seats": round(statistics.fmean(wins), 2),
        "D_seats_80pct": [wins[int(0.10 * N_SIMS)], wins[int(0.90 * N_SIMS)]],
        "prob_D_218_plus": round(sum(1 for w in wins if w >= 218) / N_SIMS, 4),
        # Per district: our expected margin and the win probability that
        # follows from it. The index it was built from is NOT here and never is.
        "districts": [
            {"race_id": rid,
             "state": rid.split("_")[1] if "_" in rid else "",
             "district": rid.split("_")[2] if rid.count("_") > 2 else "",
             "expected_margin_D": m,
             "win_prob_D": round(_norm_cdf(m / sigma_used), 4)}
            for rid, m in sorted(districts.items(), key=lambda kv: -kv[1])],
    }


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
# The polling line on this site began the day capture began, which is a fact
# about our infrastructure and not about the polls. Silver's captured file is
# not an average — it is several hundred INDIVIDUAL polls, each with its own
# enddate, running back to December 2024. Every one of them existed on its
# enddate. So the average as of any past date is a computation over exactly the
# polls published by then, not an estimate of what they might have said.
#
# The reconstruction machinery lives in model/academic.py, because BEW needed
# it first, and it is imported rather than copied. Two implementations of "the
# generic ballot as of date D" would drift, and the day they drifted the
# academic and polling lines would disagree for a reason no reader could see.
#
# RAW `net`, NOT `adjusted_net` — the adjusted columns are Silver's model
# output and get revised retroactively, so using them would leak hindsight into
# a number presented as contemporaneous. The live daily path keeps preferring
# adjusted, because for today there is no hindsight to leak. academic.py's
# comment has the full argument and the measured size of the seam.
#
# WHAT THIS BACKFILLS: our own class_polling tide, and only that. The four
# outside aggregators are other people's numbers on other people's dates and we
# hold no history for them; inventing one would be fabrication. So a backfilled
# polling date has ONE member where a live date has five — which is precisely
# the composition change chain_index() in aggregate.py exists to absorb.
def newest_parsed_date_for(cycle: int) -> str | None:
    files = sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")))
    return Path(files[-1]).stem if files else None


def history_entry(margin: float, n_polls: int, day: dt.date,
                  provenance: str) -> dict:
    """One row of polling_model_history.json.

    Factored out so the backfill and the daily extension below cannot drift
    into writing two different shapes into the same file.
    """
    days = (dt.date.fromisoformat(ELECTION_DAY) - day).days
    return {
        "snapshot_date": day.isoformat(),
        "generic_ballot": {"raw": round(margin, 3), "adjusted": None,
                           "used": "reconstructed_raw_mean",
                           "value": round(margin, 3)},
        "n_polls_in_window": n_polls,
        "shrink_lambda": round(shrink_lambda(days), 4),
        # The NOWCAST, matching what the live path feeds seats.py: the generic
        # ballot as it stood, not shrunk toward November. Shrinking here would
        # make the backfilled line a series of forecasts of election day while
        # the live end of the same line is a nowcast, and the join would be a
        # step with no cause.
        "nowcast_tide_D": round(margin, 3),
        "election_day_tide_D": round(margin * shrink_lambda(days), 3),
        "provenance": provenance,
    }


def extend_history(cycle: int, date: str) -> tuple[int, float] | None:
    """Append TODAY's reconstruction to polling_model_history.json.

    WHY THIS EXISTS, AND WHAT BROKE WITHOUT IT

        polling_model_history.json used to be written ONLY under --backfill,
        which is a flag nobody passes on an ordinary day. So the file stopped
        at whatever date somebody last ran the backfill by hand.

        That was harmless while the file was only an input to
        seats.py --backfill-history. It stopped being harmless on 2026-08-27,
        when seats.py started reading it on the LIVE path to build
        polling_reconstructed — the source that carries the class polling
        line's national margin, because class_polling itself is
        margin_published_elsewhere and contributes none.

        The consequence showed up the very next morning: the 2026-08-28 run
        found no entry for that date, skipped polling_reconstructed, and the
        class polling margin went missing again — the exact gap the live-path
        change had been written to close.

        Recomputing the whole grid daily would cost minutes for one new value.
        This appends one date instead.

    PROVENANCE IS `computed`, NOT `backfilled`, and the distinction is not
    cosmetic: aggregate.py maps `backfilled` to `retrospective`, meaning our
    arithmetic on a poll record as it stands now, chosen with the cycle
    visible, which must never be scored as a real-time forecast. A value
    computed on its own day is a real-time forecast and is scored as one.
    """
    priv = DATA / str(cycle) / "model_private"
    p = priv / "polling_model_history.json"
    try:
        hist = json.loads(p.read_text()) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        return None

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import academic  # noqa: E402  — the reconstruction lives there
    polls = academic.load_poll_history(cycle)
    if not polls:
        return None
    day = dt.date.fromisoformat(date)
    got = academic.reconstruct_generic_ballot(polls, day)
    if got is None:
        return None
    margin, n_polls = got

    # Never overwrite a date that was already computed live. Re-running today
    # is fine — it lands the same value — but a LATER --backfill sweeping
    # across this date must not relabel a real-time reading as retrospective.
    old = hist.get(date)
    if old and old.get("provenance") == "computed":
        return len(hist), old.get("nowcast_tide_D", margin)

    hist[date] = history_entry(margin, n_polls, day, "computed")
    priv.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: hist[k] for k in sorted(hist)}, indent=2))
    return len(hist), round(margin, 3)


def backfill_history(cycle: int, step_days: int = 7) -> dict:
    """{date: {nowcast_tide_D, generic_ballot, ...}} from the poll record."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import academic  # noqa: E402  — the reconstruction lives there

    polls = academic.load_poll_history(cycle)
    if not polls:
        print("  no Silver Bulletin poll list in raw/ — nothing to reconstruct")
        return {}

    first, last = polls[0][0], polls[-1][0]
    print(f"  poll record: {len(polls)} polls, {first} to {last}")
    # THROUGH TODAY, not only to the newest poll's end date. The reconstruction
    # has to exist on both sides of the join or the chained index has no member
    # in common there and breaks — see the note in aggregate.chain_index(). A
    # 21-day trailing window on a date a few days past the last poll is still a
    # real average over real polls; it is simply an average that has stopped
    # moving, which is the truth about a week with no new polling.
    end_date = dt.date.fromisoformat(
        (newest_parsed_date_for(cycle) or dt.date.today().isoformat()))
    # A full window in. The grid is anchored on the poll record, NOT on
    # academic.SERIES_START — see the long note in academic.backfill() for why
    # moving the anchor churns every historical date for a two-day gain. Points
    # before the start of the term are skipped in the loop below instead.
    cur = first + dt.timedelta(days=academic.RECONSTRUCT_WINDOW_DAYS)
    floor = dt.date.fromisoformat(academic.SERIES_START)
    out: dict[str, dict] = {}
    empty = 0
    while cur <= end_date:
        if cur < floor:                   # before the term: outside the frame
            cur += dt.timedelta(days=step_days)
            continue
        got = academic.reconstruct_generic_ballot(polls, cur)
        if got is None:
            empty += 1
            cur += dt.timedelta(days=step_days)
            continue
        margin, n_polls = got
        out[cur.isoformat()] = history_entry(margin, n_polls, cur, "backfilled")
        cur += dt.timedelta(days=step_days)

    print(f"  reconstructed {len(out)} date(s)")
    if empty:
        print(f"  {empty} date(s) had no poll in the trailing "
              f"{academic.RECONSTRUCT_WINDOW_DAYS} days and were left out "
              f"rather than carried forward")
    if out:
        ks = sorted(out)
        print(f"  polling {ks[0]} D{out[ks[0]]['nowcast_tide_D']:+.2f}"
              f"  ->  {ks[-1]} D{out[ks[-1]]['nowcast_tide_D']:+.2f}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Polling-based forecast.")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--calibrate", action="store_true", help="show the sigma fit and exit")
    ap.add_argument("--house", action="store_true", help="also run the private House model")
    ap.add_argument("--backfill", action="store_true",
                    help="also reconstruct this model's tide for every date "
                         "the poll record supports, into model_private/"
                         "polling_model_history.json. Rebuilt from the archive "
                         "each time, so it is safe to repeat.")
    ap.add_argument("--step-days", type=int, default=7,
                    help="spacing of backfilled points (default 7). The window "
                         "is a 21-day trailing average, so daily points mostly "
                         "repeat each other.")
    ap.add_argument("--holdover-d", type=int, default=HOLDOVER_D_DEFAULT,
                    help=f"D seats NOT up this cycle (default {HOLDOVER_D_DEFAULT}; "
                         f"see HOLDOVER_D_DEFAULT — one seat is worth ~20 points)")
    ap.add_argument("--sigma", type=float, default=None, help="override the calibrated sigma")
    a = ap.parse_args(argv)

    cal = calibrate_sigma(a.cycle)
    if a.calibrate:
        print(json.dumps(cal, indent=2))
        return 0

    date, rows = latest_parsed(a.cycle)
    gb = generic_ballot(rows)
    days = _days_to_election(date)
    lam = shrink_lambda(days)

    # THE PUBLISHED TIDE IS A NOWCAST: the generic ballot as it stands, not
    # shrunk toward even.
    #
    # This model used to publish gb * lambda — its estimate of where the tide
    # will END UP in November. That is a defensible forecast, but it made the
    # polling category incoherent as a category. The other members of it are
    # poll aggregators publishing what the polls say TODAY, and averaging
    # today's reading with a projection of November's is averaging two
    # different questions and calling the gap disagreement.
    #
    # It also destroyed the thing the site exists to show. If polling is
    # already shrunk toward the eventual answer, it starts near fundamentals
    # and stays there; the convergence between methods over the autumn — which
    # is the phenomenon this archive is built to watch — is assumed rather
    # than observed. A nowcast can move. Whether it drifts toward the
    # fundamentals line between now and November is then a fact about the
    # world instead of a consequence of the arithmetic.
    #
    # The shrinkage is not thrown away: lambda and the election-day projection
    # are still computed and published beside the nowcast, so the difference
    # between "today" and "November" stays visible and stays teachable.
    tide = gb["value"]
    election_day_tide = gb["value"] * lam

    if a.sigma is not None:
        sigma, sigma_src = a.sigma, "command line"
    elif cal.get("ok"):
        sigma, sigma_src = cal["sigma_total"], f"calibrated, n={cal['n']}"
    else:
        sigma, sigma_src = 9.0, f"FALLBACK PRIOR ({cal.get('why')})"

    pvi = reconstructed_state_pvi(a.cycle)
    states = senate_states_up(rows)
    sen = senate_forecast(tide, pvi, states, sigma, a.holdover_d)

    print("=" * 68)
    print(f"polling model · snapshot {date} · {days} days to {ELECTION_DAY}")
    print("=" * 68)
    print(f"  generic ballot   D{gb['value']:+.2f}  ({gb['used']}"
          + (f", raw D{gb['raw']:+.2f}" if gb.get("raw") is not None else "") + ")")
    print(f"  nowcast tide     D{tide:+.2f}  (the generic ballot as it stands)")
    print(f"  shrinkage        lambda = {lam:.3f}   -> election-day projection "
          f"D{election_day_tide:+.2f}  (diagnostic; not used downstream)")
    print(f"  sigma            {sigma:.2f} pts  ({sigma_src})")
    if cal.get("ok"):
        print(f"      fit: slope {cal['slope']:.2f} on 2xPVI, intercept "
              f"{cal['intercept']:+.2f}, years {cal['years']}"
              + ("   [THIN: one cycle only]" if cal.get("thin") else ""))
    print(f"\n  SENATE · {sen['n_races']} races, sigma_natl {sen['sigma_national']}, "
          f"sigma_state {sen['sigma_state']}")
    print(f"      expected D seats among those up: {sen['expected_D_seats_up']:.1f}  "
          f"(80% {sen['D_seats_up_80pct'][0]}-{sen['D_seats_up_80pct'][1]})")
    if "prob_D_50_plus" in sen:
        print(f"      P(D reach 50+ | {sen['holdover_D_assumed']} holdovers): "
              f"{sen['prob_D_50_plus']:.3f}")
        print(f"      P(D reach 51+, an outright majority):      "
              f"{sen['prob_D_51_plus']:.3f}   <- compare markets against THIS")
        sens = sen["prob_D_50_plus_sensitivity"]
        print("      one-seat sensitivity:  "
              + "   ".join(f"{h} -> {p:.3f}" for h, p in sorted(sens.items())))
        print("        (the baseline is bookkeeping, not a forecast — but it "
              "swings the headline\n         more than any modelling choice "
              "in this file. Label it on the site.)")

    comp = sorted(sen["races"].items(), key=lambda kv: abs(kv[1]["win_prob_D"] - 0.5))[:8]
    print("\n      closest races")
    for st, v in comp:
        print(f"        {st}  margin D{v['expected_margin_D']:+6.1f}   "
              f"P(D) {v['win_prob_D']:.2f}   PVI {v['pvi']:+.1f}")

    out = {
        "snapshot_date": date, "days_to_election": days,
        "generic_ballot": gb, "shrink_lambda": round(lam, 4),
        # The headline: what the polls say now. Everything downstream — the
        # seat projection, the category average, the tracker line — uses this.
        "nowcast_tide_D": round(tide, 3),
        # The same number shrunk toward even, i.e. where this model thinks the
        # tide lands in November. Published as a diagnostic and NOT fed to the
        # seat machinery, so the polling line on the site is a nowcast
        # throughout.
        "election_day_tide_D": round(election_day_tide, 3),
        # The tide carries an interval of its own, and it was missing — the
        # strip chart showed fundamentals with an 80% band and polling as a
        # bare dot, which reads as "the polling model is certain" when in fact
        # its uncertainty simply had not been written down. SIGMA_NATIONAL is
        # exactly this quantity: how wrong the national number can be after
        # shrinkage, hitting every race at once.
        "tide_D_80_low": round(tide - 1.2816 * _SIGMA_NAT, 3),
        "tide_D_80_high": round(tide + 1.2816 * _SIGMA_NAT, 3),
        "sigma_source": sigma_src, "calibration": cal,
        "constants": {"SHRINK_ASYMPTOTE": SHRINK_ASYMPTOTE,
                      "SHRINK_TAU_DAYS": SHRINK_TAU_DAYS,
                      "SIGMA_NATIONAL": SIGMA_NATIONAL,
                      "N_SIMS": N_SIMS, "SEED": SEED},
        "senate": sen,
        "publication": "individual",
        "note": ("Senate run uses PVI reconstructed from MEDSL CC0 returns by a "
                 "documented method, so it carries no third-party index."),
    }
    p = DATA / str(a.cycle) / "derived" / "polling_model.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))
    print(f"\n  wrote {p.relative_to(REPO)}   PUBLISHABLE")

    if a.house:
        h = house_forecast(tide, rows, sigma)
        if not h.get("ok"):
            print(f"  HOUSE: skipped — {h['why']}")
        else:
            hp = DATA / str(a.cycle) / "model_private" / "polling_model_house.json"
            hp.parent.mkdir(parents=True, exist_ok=True)
            hp.write_text(json.dumps({**h, "snapshot_date": date,
                                      "nowcast_tide_D": round(tide, 3),
                                      "election_day_tide_D": round(election_day_tide, 3)}, indent=2))
            print(f"\n  HOUSE · {h['n_districts']} districts")
            print(f"      expected D seats {h['expected_D_seats']:.1f}  "
                  f"(80% {h['D_seats_80pct'][0]}-{h['D_seats_80pct'][1]})")
            print(f"      P(D >= 218) {h['prob_D_218_plus']:.3f}")
            print(f"  wrote {hp.relative_to(REPO)}   "
                  f"(district index: {h.get('pvi_source')})")

    if a.backfill:
        hist = backfill_history(a.cycle, max(1, a.step_days))
        if hist:
            # TODAY IS NOT OVERWRITTEN WITH THE LIVE MODEL ANY MORE, and that
            # reversal is the point of this change.
            #
            # The reconstruction used to be treated as a stand-in for
            # class_polling on dates we had not captured, so today's real value
            # replaced it. That made one series with two recipes and a seam in
            # the middle. It is cleaner to call it what it is: a separate
            # aggregate, computed the same way on every date including today,
            # standing beside class_polling rather than pretending to be it.
            #
            # The practical payoff is a chained index with a member on both
            # sides of the join, so the polling line splices instead of
            # breaking.
            priv = DATA / str(a.cycle) / "model_private"
            priv.mkdir(parents=True, exist_ok=True)
            # MERGE, DO NOT REPLACE. Dates already written live carry
            # provenance `computed` and must keep it: a backfill sweeping over
            # them would relabel a real-time reading as retrospective, and
            # aggregate.py scores those two differently. See extend_history.
            p_hist = priv / "polling_model_history.json"
            kept = 0
            if p_hist.exists():
                try:
                    prior = json.loads(p_hist.read_text())
                except (json.JSONDecodeError, OSError):
                    prior = {}
                for k, v in prior.items():
                    if v.get("provenance") == "computed":
                        hist[k] = v
                        kept += 1
            p_hist.write_text(json.dumps({k: hist[k] for k in sorted(hist)},
                                         indent=2))
            print(f"  wrote model_private/polling_model_history.json"
                  f"   PRIVATE — {len(hist)} date(s)"
                  + (f", {kept} kept as live `computed`" if kept else ""))
            recon = hist.get(date, {}).get("nowcast_tide_D")
            live = out.get("nowcast_tide_D")
            print(f"  next: python3 forecast/model/seats.py --cycle {a.cycle} "
                  f"--backfill-academic   (projects these too)")
    else:
        # THE ORDINARY DAY, and the branch whose absence broke 2026-08-28.
        # seats.py reads this file on the live path to build
        # polling_reconstructed, which is what carries the class polling
        # line's national margin. Without this the file stops growing on the
        # day of the last hand-run backfill and the margin silently vanishes.
        got = extend_history(a.cycle, date)
        if got:
            n, v = got
            print(f"  wrote model_private/polling_model_history.json"
                  f"   PRIVATE — {n} date(s), {date} reconstructed D{v:+.2f}")
        else:
            # Loud. seats.py will decline to build polling_reconstructed and
            # say so too, but the cause is here.
            print(f"  WARNING: could not reconstruct the generic ballot for "
                  f"{date} — polling_model_history.json was NOT extended, and "
                  f"seats.py will have no polling_reconstructed today. Check "
                  f"that the Silver Bulletin poll list captured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
