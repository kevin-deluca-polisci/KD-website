#!/usr/bin/env python3
"""Race-level evidence: invert it to a national tide, and keep what is left.

WHAT THIS IS FOR

    Every source on this site currently contributes exactly one number to the
    seat machinery: a national margin. That margin is pushed through the same
    partisan lean, the same district baseline and the same uncertainty as every
    other source, which is what makes the lines comparable — a difference
    between two of them is a difference in the tide and in nothing else.

    It is also, for any source that publishes race-level numbers of its own, a
    deliberate destruction of most of what that source knows. Measured against
    Race to the WH's own 35 Senate margins on 2026-08-28, with its own generic
    ballot of D+6.00 as the tide:

        ME   uniform swing D+13.6   its own race number D+2.4    (-11.3)
        TX   uniform swing R+5.8    its own race number D+3.0     (+8.8)

        n=35   mean |deviation| 5.57 pts   sd 5.90 pts

    A national tide plus twice the state lean says Maine is a comfortable hold
    and Texas is out of reach. The people actually looking at those two races
    say both are close. The standard deviation of that disagreement, 5.90
    points, is larger than this model's entire per-race uncertainty of 4.19 —
    so the discarded signal is bigger than the retained noise.

THE DECOMPOSITION, AND WHY IT IS TWO STEPS AND NOT ONE

    The obvious move is to use a source's race number where it has one and fall
    back to uniform swing where it does not. That is right in spirit and wrong
    as stated, because the deviation is not purely race-level. On the same day
    the MEAN deviation was +2.44 points, not zero: Race to the WH's races are
    systematically more Democratic than its own generic ballot implies under
    our lean mapping. Splice that blend into the covered races only and the
    chamber moves for a reason that has nothing to do with those races, and a
    partially covered source ends up internally inconsistent about which
    national environment it is in.

    So the level and the race-specific part are separated:

        1. INVERT the observed races to an implied tide T.
        2. Use T as the national environment for ALL of that source's races,
           covered or not.
        3. delta[i] = observed[i] - (T + lean[i]), which now has mean zero by
           construction and carries only race-specific information.
        4. Apply delta where observed, zero elsewhere.

    Step 1 is also worth having for its own sake. The gap between the implied
    tide and the source's published one is the top-down/bottom-up disagreement,
    measured rather than asserted, and it is the same quantity the AI panel's
    pre-registration specifies in §8.

WHAT MUST NOT BREAK

    Race outcomes are correlated through the national environment, and that
    correlation is the one thing the shared machinery supplies that no source
    publishes. Thirty-five races at p=0.5 summed as independent coin flips give
    a seat count with sd of about 3.0; the true figure is far larger. So
    race-level evidence enters as an OFFSET ON A SIMULATED TIDE and never as a
    finished probability:

        m = tide + lean[i] + delta[i] + nat + eps[i]      nat drawn ONCE

    polling.senate_forecast already draws `nat` once per simulation, so adding
    delta to the per-race mean preserves the correlation automatically. Nothing
    about the error structure changes.

WHAT THIS MODULE DOES NOT DO

    It does not decide how much an observed race should shrink the residual
    variance of that race. Observing a race ought to make it less uncertain,
    not equally uncertain, and that is most of the value of race-level data.
    How much is a source-reliability question that cannot be answered before
    November, so it is left to a pre-registered constant rather than derived
    here and tuned.

REPORT

    python3 forecast/model/race_level.py --cycle 2026

    Reports, per source holding race-level rows: coverage, the implied tide
    against the published one, and the spread of what is left. Writes nothing.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import math
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# A market with almost nothing in it is not evidence. Below this it counts as
# unobserved rather than as a delta of whatever the last trade happened to be.
# A FILTER, deliberately, and not a weight: a threshold can be stated in
# advance and audited, where a shrinkage weight is a parameter nobody can
# validate before the election.
MIN_MARKET_VOLUME = 500.0

# Probabilities are clipped before inversion. A market quoting 0.99 exerts
# unbounded leverage on a least-squares fit through the normal CDF, and "this
# race is certain" is exactly the claim a thin market is least entitled to.
P_CLIP = 0.02


# ---------------------------------------------------------------------------
# Inversion
# ---------------------------------------------------------------------------

def invert_from_margins(observed: dict[str, float],
                        lean: dict[str, float],
                        robust: bool = False) -> float | None:
    """The tide implied by observed race MARGINS.

    Least squares on a constant, which is the mean of (observed - lean). The
    robust variant is the median, and the two are reported side by side: a
    large gap between them means one or two races are carrying the tide, which
    is worth seeing rather than smoothing away.
    """
    r = [observed[k] - lean[k] for k in observed if k in lean]
    if not r:
        return None
    return statistics.median(r) if robust else statistics.fmean(r)


def invert_from_probs(observed_p: dict[str, float],
                      lean: dict[str, float],
                      sigma: float) -> float | None:
    """The tide implied by observed race PROBABILITIES.

    Minimises sum over races of (Phi((T + lean_i) / sigma) - p_i)^2. Not
    closed-form, but the objective is smooth and unimodal in T, so a ternary
    search on a bracket wide enough to contain any real national environment
    is enough and is deterministic — no seed, no starting point, same answer
    every time it is computed.
    """
    ks = [k for k in observed_p if k in lean]
    if not ks or sigma <= 0:
        return None
    ps = {k: min(1.0 - P_CLIP, max(P_CLIP, observed_p[k])) for k in ks}

    def loss(t: float) -> float:
        s = 0.0
        for k in ks:
            phi = 0.5 * (1.0 + math.erf((t + lean[k]) / (sigma * math.sqrt(2))))
            s += (phi - ps[k]) ** 2
        return s

    lo, hi = -60.0, 60.0
    for _ in range(200):
        a = lo + (hi - lo) / 3.0
        b = hi - (hi - lo) / 3.0
        if loss(a) < loss(b):
            hi = b
        else:
            lo = a
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2.0


def deltas(observed: dict[str, float], lean: dict[str, float],
           tide: float) -> dict[str, float]:
    """What each race says beyond the national environment. Mean ~0 by
    construction when `tide` came from invert_from_margins on the same set."""
    return {k: observed[k] - (tide + lean[k]) for k in observed if k in lean}


def summarise(observed: dict[str, float], lean: dict[str, float],
              published_tide: float | None = None) -> dict:
    """Everything the report and the caller need, in one pass."""
    t = invert_from_margins(observed, lean)
    if t is None:
        return {}
    t_rob = invert_from_margins(observed, lean, robust=True)
    d = deltas(observed, lean, t)
    v = list(d.values())
    out = {
        "n": len(v),
        "tide_implied": round(t, 3),
        "tide_implied_robust": round(t_rob, 3),
        "delta_sd": round(statistics.pstdev(v), 3) if len(v) > 1 else 0.0,
        "delta_max_abs": round(max(abs(x) for x in v), 3),
        "deltas": {k: round(x, 3) for k, x in d.items()},
    }
    if published_tide is not None:
        out["tide_published"] = round(published_tide, 3)
        out["tide_gap"] = round(t - published_tide, 3)
    return out


# ---------------------------------------------------------------------------
# Reading what is on disk
# ---------------------------------------------------------------------------

def newest_parsed(cycle: int) -> str | None:
    fs = sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")))
    return Path(fs[-1]).stem if fs else None


def observed_races(cycle: int, date: str) -> dict:
    """{source_id: {"margin_D": {ST: v}, "win_prob_D": {ST: p}, "volume": {ST: v}}}

    Senate only for now. The House needs the district index and the map
    vintage for the date, which lives in seats.py; the functions above are
    written over a plain lean mapping so that path is plumbing rather than new
    arithmetic.
    """
    p = DATA / str(cycle) / "parsed" / f"{date}.csv"
    if not p.exists():
        return {}
    out: dict = collections.defaultdict(lambda: collections.defaultdict(dict))
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rid = r.get("race_id") or ""
            if not rid.startswith("SEN_"):
                continue
            st = rid[4:6]
            q = r.get("quantity")
            if q not in ("margin_D", "win_prob_D",
                         "market_volume_D", "market_liquidity_D"):
                continue
            try:
                v = float(r["value"])
            except (TypeError, ValueError):
                continue
            key = "volume" if q.startswith("market_") else q
            # Volume and liquidity both land in `volume`; the larger wins,
            # because either one being healthy is enough evidence.
            if key == "volume":
                out[r["source_id"]][key][st] = max(
                    out[r["source_id"]][key].get(st, 0.0), v)
            else:
                out[r["source_id"]][key][st] = v
    return {k: dict(v) for k, v in out.items()}


def state_lean(cycle: int) -> dict[str, float]:
    """State lean as a MARGIN, matching polling._pvi_to_margin (2 x PVI)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import polling  # noqa: E402
    # The same index seats.py projects from, built by the same call, so the
    # inversion is the exact inverse of the forward model rather than a second
    # implementation of the lean that could drift from it.
    pvi = polling.reconstructed_state_pvi(cycle) or {}
    return {st: polling._pvi_to_margin(v) for st, v in pvi.items()}


# ---------------------------------------------------------------------------

def report(cycle: int, date: str | None = None) -> int:
    date = date or newest_parsed(cycle)
    if not date:
        print("  no parsed files")
        return 2
    lean = state_lean(cycle)
    if not lean:
        print("  could not build the state lean index — is derived/ built?")
        return 2

    sp = DATA / str(cycle) / "model_private" / "seat_projections.json"
    published = {}
    if sp.exists():
        d = json.loads(sp.read_text())
        for sid, m in (d.get("projections") or d).items():
            if isinstance(m, dict) and m.get("tide_D") is not None:
                published[sid] = float(m["tide_D"])

    obs = observed_races(cycle, date)
    print("=" * 74)
    print(f"race-level evidence · cycle {cycle} · {date} · Senate")
    print("=" * 74)
    if not obs:
        print("  no race-level rows on this date")
        return 0

    for sid in sorted(obs):
        m = obs[sid].get("margin_D") or {}
        p = obs[sid].get("win_prob_D") or {}
        vol = obs[sid].get("volume") or {}
        if vol:
            thin = [st for st in p if vol.get(st, 0.0) < MIN_MARKET_VOLUME]
            for st in thin:
                p.pop(st, None)
        print(f"\n  {sid}")
        print(f"      coverage: {len(m)} margin, {len(p)} probability"
              + (f", {len(thin)} dropped below volume {MIN_MARKET_VOLUME:.0f}"
                 if vol and thin else ""))
        if m:
            s = summarise(m, lean, published.get(sid))
            if s:
                pub = (f"published D{s['tide_published']:+.2f}, "
                       f"gap {s['tide_gap']:+.2f}"
                       if "tide_published" in s else "no published tide")
                print(f"      implied tide  D{s['tide_implied']:+.2f}   "
                      f"(robust D{s['tide_implied_robust']:+.2f})   {pub}")
                print(f"      delta sd {s['delta_sd']:.2f} pts, "
                      f"max |delta| {s['delta_max_abs']:.2f}")
                big = sorted(s["deltas"].items(), key=lambda kv: -abs(kv[1]))[:5]
                print("      largest: " + ", ".join(
                    f"{k} {v:+.1f}" for k, v in big))
        if p and not m:
            print("      (probabilities only — inversion needs the sigma the "
                  "caller is using; wired in when this is used for real)")
    print("\n  Report only. Nothing written, no projection changed.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    return report(a.cycle, a.date)


if __name__ == "__main__":
    sys.exit(main())
