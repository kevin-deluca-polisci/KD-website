#!/usr/bin/env python3
"""
Seats as a function of the national tide, on two maps at once.

    python3 forecast/model/tide_curve.py --self-test
    python3 forecast/model/tide_curve.py --cycle 2026
    python3 forecast/model/tide_curve.py --cycle 2026 \
        --json forecast/data/2026/derived/tide_curve.json

-----------------------------------------------------------------------------
WHY A CURVE AND NOT A NUMBER

"Redistricting cost Democrats N seats" is not a fact, it is a fact evaluated at
one national tide. Gerrymanders work by spreading a party's voters thinly
across many districts, so the same map can be worth +5 seats in a neutral year
and -2 in a wave. Quoting the median hides exactly the behaviour that makes
mid-decade redistricting interesting.

So the object is E[D seats](tide), computed on the current lines and on the
lines that would have governed without the 2025-26 redraws, and the three
things worth saying are all read off the same picture:

  VERTICAL GAP at a given tide   how many seats the new lines are worth there
  HORIZONTAL GAP at the 218 line how much MORE tide Democrats now need for a
                                 majority. This is the headline number, and it
                                 is the honest answer to "what would it take".
  THE CROSSING, if there is one  the tide above which the new map returns FEWER
                                 seats to the party that drew it than the old
                                 one would have. A dummymander is not a
                                 separate analysis; it is a feature of this
                                 curve.

TWO NUMBERS THAT SOUND DIFFERENT AND ARE THE SAME NUMBER

The vertical and horizontal gaps are related by the slope of the seat-vote
curve, which in the modern House runs somewhere near 6-10 seats per point of
national margin. A seven-seat gerrymander is therefore worth roughly one point
of tide. "Seven seats" sounds larger and "one point" is more decision-relevant,
and a page that reports one without the other is picking the framing it likes.
Both get printed.

THE MARGIN FUNCTION IS A PARAMETER, DELIBERATELY

    margin_i = tide + slope * (baseline_i - center)

With Cook's PVI, slope is 2 and center is 0, because PVI is a share deviation
from the national presidential two-party share and a share gap is half a margin
gap. That identity is a property of COOK'S index and of nothing else. Dave's
Redistricting composite has no national counterpart to deviate from, so its
slope and center have to be estimated against real returns rather than
inherited by habit -- see ROADMAP section 3a. Passing them in keeps this module
honest about which it is using and lets the same code serve both.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import map_correction as mc                        # noqa: E402
import polling                                     # noqa: E402

REPO = HERE.parents[1]
MAJORITY = 218
# The ten states that redrew for 2026, and who drew each map. "court" and
# "voters" are not parties and are excluded from the dummymander test, which
# only makes sense for a map somebody drew to help themselves.
DRAWN_BY = {"TX": "R", "MO": "R", "NC": "R", "OH": "R", "FL": "R",
            "TN": "R", "LA": "R", "AL": "court", "UT": "court",
            "CA": "D"}


def margins(tide: float, baseline: dict[str, float],
            slope: float, center: float) -> list[float]:
    return [tide + slope * (v - center) for v in baseline.values()]


def curve(baseline: dict[str, float], sigma: float, slope: float,
          center: float, lo: float = -12.0, hi: float = 12.0,
          step: float = 0.25) -> list[dict]:
    """E[D seats] and P(D majority) across a sweep of national tides."""
    out = []
    t = lo
    while t <= hi + 1e-9:
        r = mc.house_analytic(t, {k: (v - center) * (slope / 2.0)
                                  for k, v in baseline.items()}, sigma)
        out.append({"tide": round(t, 3),
                    "e_seats": r["expected_D_seats"],
                    "p_majority": r["prob_D_majority"]})
        t += step
    return out


def _cross(pts: list[dict], key: str, target: float) -> float | None:
    """Where a monotone series crosses a target, linearly interpolated."""
    for a, b in zip(pts, pts[1:]):
        ya, yb = a[key], b[key]
        if (ya - target) * (yb - target) <= 0 and ya != yb:
            f = (target - ya) / (yb - ya)
            return round(a["tide"] + f * (b["tide"] - a["tide"]), 3)
    return None


def state_of(race_id: str) -> str:
    p = race_id.split("_")
    return p[1] if len(p) > 2 else "??"


def state_seats(baseline: dict[str, float], sigma: float, slope: float,
                center: float, tide: float) -> dict[str, float]:
    """E[D seats] contributed by each state at one tide.

    Summing Phi district by district is exact for an expectation, so a state's
    share of the national expectation is just its own districts' terms. No
    simulation and no apportioning required.
    """
    sig = max(sigma, 1e-6)
    out: dict[str, float] = defaultdict(float)
    for rid, v in baseline.items():
        out[state_of(rid)] += mc._phi((tide + slope * (v - center)) / sig)
    return dict(out)


def analyse(cur: dict[str, float], pri: dict[str, float], sigma: float,
            slope: float = 2.0, center: float = 0.0,
            at_tide: float | None = None) -> dict:
    """Everything the page and the note both need."""
    # ONLY DISTRICTS PRESENT ON BOTH MAPS. A state missing from one side would
    # otherwise show up as a redistricting effect the size of its delegation,
    # which is the single easiest way to publish a spectacular wrong number.
    common = set(cur) & set(pri)
    cur = {k: cur[k] for k in common}
    pri = {k: pri[k] for k in common}

    c_curve = curve(cur, sigma, slope, center)
    p_curve = curve(pri, sigma, slope, center)

    thr_c = _cross(c_curve, "p_majority", 0.5)
    thr_p = _cross(p_curve, "p_majority", 0.5)
    seat_c = _cross(c_curve, "e_seats", MAJORITY)
    seat_p = _cross(p_curve, "e_seats", MAJORITY)

    # THE SLOPE OF THE SEAT-VOTE CURVE, measured rather than assumed, so the
    # translation between "seats" and "points" is this map's and not a
    # textbook's.
    near = [x for x in c_curve if abs(x["tide"] - (thr_c or 0)) <= 2.0]
    swing_ratio = None
    if len(near) > 1:
        swing_ratio = round((near[-1]["e_seats"] - near[0]["e_seats"])
                            / (near[-1]["tide"] - near[0]["tide"]), 2)

    rows = []
    for a, b in zip(c_curve, p_curve):
        rows.append({"tide": a["tide"],
                     "e_seats_current": a["e_seats"],
                     "e_seats_prior": b["e_seats"],
                     "gap": round(a["e_seats"] - b["e_seats"], 2),
                     "p_majority_current": a["p_majority"],
                     "p_majority_prior": b["p_majority"]})

    # PER STATE, at whichever tide the caller cares about.
    t0 = at_tide if at_tide is not None else (thr_c or 0.0)
    sc = state_seats(cur, sigma, slope, center, t0)
    sp = state_seats(pri, sigma, slope, center, t0)
    per_state = []
    for st in sorted(set(sc) | set(sp)):
        d = sc.get(st, 0.0) - sp.get(st, 0.0)
        if abs(d) < 0.01 and st not in DRAWN_BY:
            continue
        per_state.append({"state": st, "drawn_by": DRAWN_BY.get(st),
                          "e_seats_current": round(sc.get(st, 0.0), 2),
                          "e_seats_prior": round(sp.get(st, 0.0), 2),
                          "gap_D": round(d, 2)})
    per_state.sort(key=lambda r: r["gap_D"])

    # THE DUMMYMANDER TEST. For each state a party drew, the tide at which
    # their new lines stop out-performing the old ones FOR THEM. Read on the
    # drawing party's own seat count, which for a Republican map means the
    # Democratic gap turning positive.
    dummy = []
    for st, party in DRAWN_BY.items():
        if party not in ("D", "R"):
            continue
        cs = {k: v for k, v in cur.items() if state_of(k) == st}
        ps = {k: v for k, v in pri.items() if state_of(k) == st}
        if not cs or not ps:
            continue
        pts, t = [], -12.0
        while t <= 12.0 + 1e-9:
            gd = (sum(mc._phi((t + slope * (v - center)) / sigma)
                      for v in cs.values())
                  - sum(mc._phi((t + slope * (v - center)) / sigma)
                        for v in ps.values()))
            # For an R-drawn map, the map helps its drawer while the D gap is
            # negative; the crossing is where that gap reaches zero.
            pts.append({"tide": round(t, 3),
                        "own": round(gd if party == "D" else -gd, 3)})
            t += 0.25
        cross = _cross(pts, "own", 0.0)
        at0 = next((x["own"] for x in pts if abs(x["tide"]) < 1e-9), None)
        dummy.append({"state": st, "drawn_by": party,
                      "own_gain_at_even": at0,
                      "backfires_above_tide": cross,
                      "n_districts": len(cs)})
    dummy.sort(key=lambda r: (r["backfires_above_tide"] is None,
                              r["backfires_above_tide"] or 0))

    return {
        "n_districts": len(common),
        "sigma": sigma, "slope": slope, "center": center,
        "majority_threshold_current": thr_c,
        "majority_threshold_prior": thr_p,
        "extra_tide_needed": (round(thr_c - thr_p, 2)
                              if thr_c is not None and thr_p is not None
                              else None),
        "seat218_tide_current": seat_c,
        "seat218_tide_prior": seat_p,
        "swing_ratio_seats_per_point": swing_ratio,
        "gap_at_even": next((r["gap"] for r in rows
                             if abs(r["tide"]) < 1e-9), None),
        "curve": rows, "per_state": per_state, "dummymander": dummy,
    }


def report(a: dict) -> None:
    print("=" * 74)
    print(f"  {a['n_districts']} district(s) on both maps   sigma "
          f"{a['sigma']:.2f}   margin = tide + {a['slope']:g} x "
          f"(baseline - {a['center']:g})")
    print("=" * 74)
    tc, tp, ex = (a["majority_threshold_current"], a["majority_threshold_prior"],
                  a["extra_tide_needed"])
    if ex is None:
        print("  the majority threshold is off the swept range on one map")
    else:
        print(f"\n  THE HEADLINE")
        print(f"    old lines: Democrats reach an even-money House majority at "
              f"a national D+{tp:.1f}")
        print(f"    new lines: they need D+{tc:.1f}")
        print(f"    -> the 2025-26 redraws moved the majority threshold by "
              f"{ex:+.1f} point(s).")
        if a["swing_ratio_seats_per_point"]:
            print(f"    At {a['swing_ratio_seats_per_point']:.1f} seats per "
                  f"point near the threshold, that is about "
                  f"{abs(ex) * a['swing_ratio_seats_per_point']:.1f} seats -- "
                  f"the same fact\n       stated the other way round.")
    if a["gap_at_even"] is not None:
        print(f"\n  At an EVEN national vote the new lines are worth "
              f"{a['gap_at_even']:+.2f} seats to Democrats.")

    print("\n  THE CURVE (expected D seats)")
    print(f"    {'tide':>7}{'new':>9}{'old':>9}{'gap':>8}   P(D maj) new / old")
    for r in a["curve"]:
        if abs(r["tide"] * 2 - round(r["tide"] * 2)) > 1e-6 or \
                r["tide"] not in [x / 1 for x in range(-10, 11, 2)]:
            continue
        print(f"    {r['tide']:>+7.1f}{r['e_seats_current']:>9.1f}"
              f"{r['e_seats_prior']:>9.1f}{r['gap']:>+8.2f}   "
              f"{r['p_majority_current']:.3f} / {r['p_majority_prior']:.3f}")

    print("\n  BY STATE (at the current-map majority threshold)")
    print(f"    {'st':<4}{'drew':<7}{'new':>8}{'old':>8}{'gap to D':>10}")
    for s in a["per_state"]:
        if s["drawn_by"] is None and abs(s["gap_D"]) < 0.05:
            continue
        print(f"    {s['state']:<4}{str(s['drawn_by'] or ''):<7}"
              f"{s['e_seats_current']:>8.2f}{s['e_seats_prior']:>8.2f}"
              f"{s['gap_D']:>+10.2f}")

    print("\n  DUMMYMANDER TEST -- where does a map stop helping the party "
          "that drew it?")
    for d in a["dummymander"]:
        c = d["backfires_above_tide"]
        where = (f"backfires above a national D+{c:.1f}" if c is not None
                 else "never backfires inside +/-12 points")
        print(f"    {d['state']:<4}({d['drawn_by']})  gains "
              f"{d['own_gain_at_even']:+.2f} seat(s) at an even vote, {where}")
    print("\n    A map that backfires inside the plausible range for this cycle"
          "\n    is a dummymander. One that backfires at D+11 is not; it is a"
          "\n    map that works, described pessimistically.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--baseline", metavar="CSV",
                    help="district_baseline.csv from dra_import.py. Without "
                         "it, falls back to the Cook index already held, which "
                         "is PRIVATE and only for checking the machinery.")
    ap.add_argument("--slope", type=float, default=2.0)
    ap.add_argument("--center", type=float, default=0.0)
    ap.add_argument("--sigma", type=float, default=None)
    ap.add_argument("--at-tide", type=float, default=None)
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    if a.baseline:
        import csv as _csv
        cur, pri = {}, {}
        for r in _csv.DictReader(Path(a.baseline).expanduser()
                                 .open(encoding="utf-8")):
            if (r.get("election") or "composite") != "composite":
                continue
            rid = f"HOU_{r['state']}_{r['district']}_{a.cycle}"
            try:
                v = float(r["two_party_D"])
            except (TypeError, ValueError):
                continue
            (cur if r["map_version"] == "current" else pri)[rid] = v
        # A state that did not redraw has one map, and it is both of them.
        for k, v in cur.items():
            pri.setdefault(k, v)
        where = a.baseline
    else:
        cur, pri, where = mc.load_pvi(a.cycle, None)
        for k, v in cur.items():
            pri.setdefault(k, v)
    if not cur:
        raise SystemExit(f"no district baseline found ({where})")
    print(f"  baseline: {where}   {len(cur)} current, {len(pri)} prior")

    sigma = a.sigma
    if sigma is None:
        hp = (REPO / "forecast" / "data" / str(a.cycle) / "model_private"
              / "seat_projections_history.json")
        if hp.exists():
            hist = json.loads(hp.read_text(encoding="utf-8"))
            sigma = next((hist[d].get("sigma") for d in sorted(hist, reverse=True)
                          if (hist[d] or {}).get("sigma")), None)
        sigma = sigma or 6.55
    res = analyse(cur, pri, sigma, a.slope, a.center, a.at_tide)
    report(res)
    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"\n  wrote {p}")
    return 0


def _self_test() -> int:
    fails = 0

    def check(c, m):
        nonlocal fails
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails += 1

    # THE DUMMYMANDER SHAPE, built deliberately.
    #
    # An aggressive Republican gerrymander packs Democrats into a few
    # overwhelming seats and spreads Republicans across many THIN ones. At an
    # even national vote that wins more seats than a modest map would. In a
    # wave it loses more, because every thin seat is exposed at once. The
    # first fixture I wrote had the property backwards, which is worth
    # recording: a test that passes on a map you built to pass it proves
    # nothing about maps you did not build.
    #
    # current: 5 D vote-sinks at D+50 margin, 15 R seats at only R+10
    # prior:   7 D seats at D+30,             13 R seats at a safer R+16
    cur = {f"HOU_XX_{i:02d}_2026": v for i, v in enumerate(
        [25.0] * 5 + [-5.0] * 15, start=1)}
    pri = {f"HOU_XX_{i:02d}_2026": v for i, v in enumerate(
        [15.0] * 7 + [-8.0] * 13, start=1)}
    r = analyse(cur, pri, 6.0, 2.0, 0.0)
    check(r["n_districts"] == 20, "only districts on both maps are compared")
    even = next(x for x in r["curve"] if abs(x["tide"]) < 1e-9)
    wave = next(x for x in r["curve"] if abs(x["tide"] - 12.0) < 1e-9)
    check(even["gap"] < 0,
          f"at an even vote the gerrymander costs D seats ({even['gap']:+.2f})")
    check(wave["gap"] > 0,
          f"in a big wave it BACKFIRES and gains D seats ({wave['gap']:+.2f})")
    check(wave["gap"] > even["gap"],
          "the gap moves toward the wronged party as the tide rises")
    check(all(b2["e_seats_current"] >= a2["e_seats_current"] - 1e-9
              for a2, b2 in zip(r["curve"], r["curve"][1:])),
          "expected seats rise monotonically with the tide")

    # The crossing is what makes it a dummymander, and it must be found.
    xs = [x["tide"] for x, y in zip(r["curve"], r["curve"][1:])
          if x["gap"] <= 0 <= y["gap"]]
    check(bool(xs), f"a crossing exists and is located (around D+{xs[0]:.1f})"
          if xs else "a crossing exists and is located")

    mism = analyse({"HOU_XX_01_2026": 1.0, "HOU_XX_02_2026": 2.0},
                   {"HOU_XX_01_2026": 1.0}, 6.0)
    check(mism["n_districts"] == 1,
          "a district missing from one map is dropped, not counted as an effect")

    # The two framings must agree: gap in seats = shift in points x slope.
    if r["extra_tide_needed"] and r["swing_ratio_seats_per_point"]:
        implied = abs(r["extra_tide_needed"] * r["swing_ratio_seats_per_point"])
        near = abs(next(x["gap"] for x in r["curve"]
                        if abs(x["tide"] - (r["majority_threshold_current"] or 0))
                        < 0.13))
        check(abs(implied - near) < max(1.5, 0.4 * near),
              f"seats and points tell the same story near the threshold "
              f"({implied:.1f} vs {near:.1f})")

    print("\n  " + ("PASS" if not fails else f"{fails} FAILURE(S)"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
