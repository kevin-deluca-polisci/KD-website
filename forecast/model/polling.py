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
# dominates the seat distribution's tails. 4.0 points of margin at this
# horizon is a deliberately generous reading of recent final-poll misses.
SIGMA_NATIONAL = 4.0
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
        if r["quantity"] in ("margin_D", "margin_D_adjusted"):
            got[r["quantity"]] = float(r["value"])
    if not got:
        raise SystemExit(
            "no generic ballot rows in the latest parsed file.\n"
            "  silver_bulletin is the polling category — check it captured.")
    used = "margin_D_adjusted" if "margin_D_adjusted" in got else "margin_D"
    return {"raw": got.get("margin_D"), "adjusted": got.get("margin_D_adjusted"),
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
    sigma_state = math.sqrt(max(sigma_total ** 2 - SIGMA_NATIONAL ** 2,
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
        nat = rng.gauss(0.0, SIGMA_NATIONAL)
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
        "sigma_national": SIGMA_NATIONAL,
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


# Which district index to use when the archive holds more than one. Cook's own
# 2026 lines first, then Grant Williams' republication of them, then whatever
# Wikipedia's ratings table carries. Previously this was "whichever row the
# parser happened to emit first", which made the House forecast depend on file
# ordering — the same model could change answer because a source was renamed.
PVI_PREFERENCE = ("cook_pvi", "grant_williams", "wikipedia")


def house_forecast(tide: float, rows: list[dict], sigma_total: float) -> dict:
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
    by_source: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["quantity"] == "pvi" and r["chamber"] == "house" and r["race_id"]:
            by_source.setdefault(r["source_id"], {}).setdefault(
                r["race_id"], float(r["value"]))
    source = next((s for s in PVI_PREFERENCE if by_source.get(s)),
                  next(iter(by_source), None))
    pvi = by_source.get(source or "", {})
    if not pvi:
        return {"ok": False, "why": "no district PVI in the archive"}

    sigma_state = math.sqrt(max(sigma_total ** 2 - SIGMA_NATIONAL ** 2,
                                SIGMA_STATE_FLOOR ** 2))
    districts = {rid: round(tide + _pvi_to_margin(v), 2) for rid, v in pvi.items()}

    rng = random.Random(SEED)
    order = sorted(districts)
    wins = []
    for _ in range(N_SIMS):
        nat = rng.gauss(0.0, SIGMA_NATIONAL)
        w = 0
        for rid in order:
            if districts[rid] + nat + rng.gauss(0.0, sigma_state) > 0:
                w += 1
        wins.append(w)
    wins.sort()
    return {
        "ok": True,
        "pvi_source": source,
        "n_districts": len(districts),
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
             "win_prob_D": round(_norm_cdf(m / sigma_total), 4)}
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
    cur = first + dt.timedelta(days=academic.RECONSTRUCT_WINDOW_DAYS)
    out: dict[str, dict] = {}
    empty = 0
    while cur <= end_date:
        got = academic.reconstruct_generic_ballot(polls, cur)
        if got is None:
            empty += 1
            cur += dt.timedelta(days=step_days)
            continue
        margin, n_polls = got
        days = (dt.date.fromisoformat(ELECTION_DAY) - cur).days
        out[cur.isoformat()] = {
            "snapshot_date": cur.isoformat(),
            "generic_ballot": {"raw": round(margin, 3), "adjusted": None,
                               "used": "reconstructed_raw_mean",
                               "value": round(margin, 3)},
            "n_polls_in_window": n_polls,
            "shrink_lambda": round(shrink_lambda(days), 4),
            # The NOWCAST, matching what the live path feeds seats.py: the
            # generic ballot as it stood, not shrunk toward November. Shrinking
            # here would make the backfilled line a series of forecasts of
            # election day while the live end of the same line is a nowcast,
            # and the join would be a step with no cause.
            "nowcast_tide_D": round(margin, 3),
            "election_day_tide_D": round(margin * shrink_lambda(days), 3),
            "provenance": "backfilled",
        }
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
        "tide_D_80_low": round(tide - 1.2816 * SIGMA_NATIONAL, 3),
        "tide_D_80_high": round(tide + 1.2816 * SIGMA_NATIONAL, 3),
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
            (priv / "polling_model_history.json").write_text(
                json.dumps(hist, indent=2))
            print(f"  wrote model_private/polling_model_history.json"
                  f"   PRIVATE — {len(hist)} date(s)")
            recon = hist.get(date, {}).get("nowcast_tide_D")
            live = out.get("nowcast_tide_D")
            print(f"  next: python3 forecast/model/seats.py --cycle {a.cycle} "
                  f"--backfill-academic   (projects these too)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
