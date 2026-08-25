"""What SIGMA_NATIONAL actually buys, and what the market thinks it should be.

SIGMA_NATIONAL is the one parameter in polling.py that was never estimated from
anything. It is also, by the seat-total variance decomposition, about 85% of the
uncertainty in the headline number. This module exists so that claim is a table
rather than an assertion.

Three things are printed.

1. A SWEEP. The House projection re-run across a range of national sigmas with
   the point estimate, the baseline, the slope, the incumbency term and the
   state/district sigmas all held fixed. Everything that moves between rows is
   the one number.

2. THE MARKET'S NATIONAL MARGIN. Kalshi lists a ladder on the national House
   popular vote margin (KXHOUSEPOPVOTEMARGIN). Fitting a normal to its implied
   CDF gives a market read on exactly the quantity SIGMA_NATIONAL describes.
   The fit is reported over several sets of strikes because the answer is
   sensitive to the illiquid ones: the open lower bucket trades at a two-cent
   spread while the 0-2% bucket is 1.4c bid / 3.1c ask, and a midpoint from a
   1.7c-wide market is not a probability.

3. THE MARKET'S SEAT DISTRIBUTION, from the two seat ladders, as an independent
   second read. If our seat SD is far from theirs at a given SIGMA_NATIONAL,
   that is informative regardless of who is right.

NOTHING HERE FEEDS THE FORECAST. Backing our own national sigma out of a market
price would make the site's model a market derivative wearing a model's clothes.
This is a diagnostic that gets read by a person.

Run from the repo root:  python3 forecast/model/sigma_sweep.py
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from model import maps, polling                     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DERIVED = ROOT / "data" / "2026" / "derived"
RAW_KALSHI = ROOT / "data" / "2026" / "raw" / "kalshi"

SWEEP = (0.0, 1.0, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0)


# --------------------------------------------------------------------------
# the projection, unpacked far enough to swap one distribution

def setup(cycle: int = 2026):
    """Everything house_forecast() builds, returned instead of simulated.

    This duplicates a dozen lines of house_forecast rather than calling it,
    for one reason: the sweep has to vary a module-level constant, and a
    function that reads a global at call time cannot be swept without either
    monkeypatching it (fragile, and it leaks if the run throws) or rebuilding
    the district table 10 times (slow, and it re-reads the archive each time).
    The duplicated lines are the cheap arithmetic; if they drift from
    house_forecast the assert at the end of main() catches it.
    """
    date, rows = polling.latest_parsed(cycle)
    # The baseline PVI_PREFERENCE would actually pick, not the default: this
    # module exists to describe the model the site runs, and a sweep carrying
    # the wrong index's slope and sigmas describes a different one.

    pm = json.loads((DERIVED / "polling_model.json").read_text())
    tide = float(pm.get("nowcast_tide_D")
                 if pm.get("nowcast_tide_D") is not None
                 else pm["election_day_tide_D"])

    # BOTH BASELINE SHAPES, exactly as house_forecast resolves them. The first
    # version of this read only the `pvi` quantity, which meant that the day
    # DRA became the preferred index this module silently kept projecting
    # Cook's -- and said nothing, because 435 districts came back either way.
    # The guard in main() caught it on the first run after the switch, which is
    # the only reason this comment exists rather than a wrong table.
    by: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["quantity"] == "pvi" and r["chamber"] == "house" and r["race_id"]:
            by.setdefault(r["source_id"], {}).setdefault(
                r["race_id"], float(r["value"]))
    dra_cur, dra_pri, _ = polling.dra_baseline(rows, cycle)
    if dra_cur:
        by["dra"] = dra_cur
    src = next((s for s in polling.PVI_PREFERENCE if by.get(s)),
               next(iter(by), None))
    pvi = by[src]
    # The calibration has to follow the index that was actually selected, for
    # the same reason house_forecast does it that way: a slope and a sigma
    # belong to the index they were fitted against.
    hs = polling.calibrate_sigma_house(
        cycle, baseline=polling.BASELINE_CALIBRATION.get(src, "dra"))
    if not hs.get("ok"):
        raise SystemExit(f"no house calibration: {hs.get('why')}")
    cur, prior = ((dra_cur, dra_pri) if src == "dra"
                  else maps.split_rows(rows, src))
    if prior:
        pvi, _ = maps.baseline_asof(cur or pvi, prior, date)

    inc = polling.house_incumbency(cycle)
    spec = hs["with_incumbency"] if inc else hs["baseline_only"]
    slope = spec["slope"]
    inc_pts = spec.get("incumbency_pts") or 0.0

    margin = {rid: tide + slope * polling._pvi_to_margin(v)
              + inc_pts * inc.get(rid, 0)
              for rid, v in pvi.items()}
    state_of = {rid: (rid.split("_")[1] if "_" in rid else "") for rid in margin}
    return {"date": date, "rows": rows, "tide": tide, "spec": spec,
            "margin": margin, "state_of": state_of,
            "states": sorted(set(state_of.values())),
            "order": sorted(margin), "baseline": hs.get("baseline"),
            "house_sigma": hs}


def simulate(env: dict, draw_nat, n: int = polling.N_SIMS) -> dict:
    sst = env["spec"]["sigma_state"]
    sd = env["spec"]["sigma_district"]
    margin, state_of = env["margin"], env["state_of"]
    rng = random.Random(polling.SEED)
    wins = []
    for _ in range(n):
        nat = draw_nat(rng)
        eff = {s: rng.gauss(0.0, sst) for s in env["states"]}
        wins.append(sum(1 for rid in env["order"]
                        if margin[rid] + nat + eff[state_of[rid]]
                        + rng.gauss(0.0, sd) > 0))
    w = sorted(wins)
    return {"mean": statistics.fmean(wins), "sd": statistics.stdev(wins),
            "lo": w[int(0.10 * n)], "hi": w[int(0.90 * n)],
            "p_maj": sum(1 for x in wins if x >= 218) / n}


# --------------------------------------------------------------------------
# the market side

def _mid(m: dict) -> float:
    return (float(m.get("yes_bid_dollars") or 0)
            + float(m.get("yes_ask_dollars") or 0)) / 2


def _spread(m: dict) -> float:
    return (float(m.get("yes_ask_dollars") or 0)
            - float(m.get("yes_bid_dollars") or 0))


def _probit(p: float) -> float:
    """Acklam's inverse normal CDF. Central branch only; callers stay inside."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    q = p - 0.5
    r = q * q
    return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r
             + a[5]) * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))


def latest_kalshi_day() -> pathlib.Path | None:
    days = sorted(p for p in RAW_KALSHI.glob("20*") if p.is_dir())
    return days[-1] if days else None


def margin_ladder(day: pathlib.Path) -> list[tuple]:
    f = day / "markets-KXHOUSEPOPVOTEMARGIN.json"
    if not f.exists():
        return []
    out = []
    for m in json.loads(f.read_text()).get("markets", []):
        if m.get("floor_strike") is None:
            continue
        out.append((m["floor_strike"], m.get("cap_strike"),
                    _mid(m), _spread(m)))
    return sorted(out)


def fit_normal(edges: list[tuple[float, float]]) -> tuple[float, float]:
    """OLS of strike on probit(CDF). Returns (mean, sd)."""
    pts = [(x, _probit(c)) for x, c in edges if 0.01 < c < 0.99]
    n = len(pts)
    if n < 3:
        return float("nan"), float("nan")
    sz = sum(z for _, z in pts)
    sx = sum(x for x, _ in pts)
    szx = sum(z * x for x, z in pts)
    szz = sum(z * z for _, z in pts)
    sd = (n * szx - sz * sx) / (n * szz - sz * sz)
    return (sx - sd * sz) / n, sd


def seat_ladder(day: pathlib.Path, fname: str, flip: bool) -> dict | None:
    f = day / fname
    if not f.exists():
        return None
    pts = []
    for m in json.loads(f.read_text()).get("markets", []):
        s = (m.get("custom_strike") or {}).get("Seats", "")
        t = (m.get("yes_sub_title") or "").lower()
        digits = "".join(ch for ch in t if ch.isdigit())
        if "-" in s:
            lo, hi = (int(x) for x in s.split("-"))
            c = (lo + hi) / 2
        elif t.startswith("above") and digits:
            c = int(digits) + 5.5          # open tail, placed half a bucket out
        elif t.startswith("below") and digits:
            c = int(digits) - 5.5
        else:
            continue
        pts.append((435 - c if flip else c, _mid(m)))
    if not pts:
        return None
    tot = sum(p for _, p in pts)
    mu = sum(c * p for c, p in pts) / tot
    var = sum(p / tot * (c - mu) ** 2 for c, p in pts)
    return {"mean": mu, "sd": math.sqrt(var), "sum_mids": tot,
            "p_maj": sum(p for c, p in pts if c >= 218) / tot}


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--sims", type=int, default=polling.N_SIMS)
    a = ap.parse_args(argv)

    env = setup(a.cycle)
    sp = env["spec"]

    # THE DUPLICATION GUARD. setup() rebuilds house_forecast's district table
    # by hand so the sweep can vary a module global cheaply. If the two ever
    # disagree the sweep is describing a model the site does not run, which is
    # worse than not having the sweep. So run the real thing once and check.
    ref = polling.house_forecast(env["tide"], env["rows"], 9.0,
                                 asof=env["date"],
                                 house_sigma=env["house_sigma"])
    mine = simulate(env, lambda rng: rng.gauss(0.0, polling.SIGMA_NATIONAL),
                    polling.N_SIMS)
    drift = abs(mine["mean"] - ref["expected_D_seats"])
    if drift > 0.05:
        raise SystemExit(
            f"sigma_sweep has drifted from house_forecast: this module says "
            f"{mine['mean']:.2f} expected D seats at the live "
            f"SIGMA_NATIONAL, house_forecast says {ref['expected_D_seats']}. "
            f"Fix setup() before trusting anything below it.")
    if ref.get("n_districts") != len(env["margin"]):
        raise SystemExit(
            f"district count differs: {len(env['margin'])} here vs "
            f"{ref.get('n_districts')} in house_forecast")

    print(f"archive {env['date']}   tide D+{env['tide']:.2f}   "
          f"baseline {env['baseline']}   slope {sp['slope']:.3f}   "
          f"sigma_state {sp['sigma_state']}   "
          f"sigma_district {sp['sigma_district']}")
    print()
    print("HOW MUCH THE HEADLINE DEPENDS ON A NUMBER NOBODY ESTIMATED")
    print(f"{'sig_nat':>8} {'E[D seats]':>11} {'SD':>6} "
          f"{'80% range':>13} {'P(D 218+)':>10}")
    for s in SWEEP:
        r = simulate(env, lambda rng, s=s: rng.gauss(0.0, s), a.sims)
        star = "  <-- current" if s == polling.SIGMA_NATIONAL else ""
        band = "{} - {}".format(r["lo"], r["hi"])
        print(f"{s:8.1f} {r['mean']:11.1f} {r['sd']:6.2f} "
              f"{band:>13} {r['p_maj']:10.3f}{star}")

    day = latest_kalshi_day()
    if not day:
        print("\nno kalshi captures — market comparison skipped")
        return 0
    print(f"\nMARKET READS, from {day.name}")

    lad = margin_ladder(day)
    if lad:
        tot = sum(p for _, _, p, _ in lad)
        cum, edges = 0.0, []
        for lo, hi, p, spd in lad:
            cum += p / tot
            if hi is not None and hi < 50:          # skip the open upper tail
                edges.append((hi, cum, spd))
        print(f"  national margin ladder, normal fit to the implied CDF "
              f"(mids sum to {tot:.3f}):")
        for label, sel in (
                ("every strike", edges),
                ("liquid core 4-14", [e for e in edges if 4 <= e[0] <= 14]),
                ("tightest core 6-12", [e for e in edges if 6 <= e[0] <= 12]),
                ("bid-ask <= 2c only", [e for e in edges if e[2] <= 0.02])):
            mu, sd = fit_normal([(x, c) for x, c, _ in sel])
            print(f"    {label:<22} mean D+{mu:5.2f}   sd {sd:4.2f}   "
                  f"(n={len(sel)})")
        print("    the spread across those rows IS the uncertainty in this "
              "read; the illiquid")
        print("    strikes widen it, and the lower tail is fatter than any "
              "normal fitted here.")

    print("  seat ladders (two independent listings of the same quantity):")
    for fname, flip, lab in (
            ("markets-KXDHOUSESEATS.json", False, "D-seats"),
            ("markets-KXRHOUSESEATS.json", True, "R-seats, reflected")):
        s = seat_ladder(day, fname, flip)
        if s:
            print(f"    {lab:<22} E[D] {s['mean']:6.1f}   SD {s['sd']:5.2f}   "
                  f"P(D 218+) {s['p_maj']:.3f}   (mids sum {s['sum_mids']:.3f})")
    print("    read the sweep against these: the SIGMA_NATIONAL whose seat SD "
          "matches theirs")
    print("    is what the market's own uncertainty implies, GIVEN our "
          "state and district sigmas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
