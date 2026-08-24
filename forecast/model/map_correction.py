#!/usr/bin/env python3
"""
How wrong were the backfilled seat counts, and by how much?

    python3 forecast/model/map_correction.py
    python3 forecast/model/map_correction.py --csv /tmp/map_correction.csv

READS ONLY. Writes nothing under data/ and does not touch the history file.
Run it before `seats.py --backfill-history` to see what that run will change.

-----------------------------------------------------------------------------
WHAT IT COMPARES

`seat_projections_history.json` holds 89 dates of seat counts, every one of
them computed on TODAY's district lines. This recomputes each of them on the
lines that were in force on its own date and reports the difference.

-----------------------------------------------------------------------------
WHY IT DOES NOT SIMULATE

house_forecast runs 20,000 Monte Carlo draws over 435 districts. Doing that
twice for 89 dates times eight models is a couple of hours, which is too slow
to be a check anybody actually runs. The same numbers are available in closed
form:

    margin_i = tide + 2 * pvi_i
    a district is won when   margin_i + nat + eps_i > 0
        nat ~ N(0, SIGMA_NATIONAL)     shared by every district
        eps_i ~ N(0, sigma_state)      independent per district

    E[seats]     = SUM_i  PHI( margin_i / sigma_total )        -- exact

    P(D >= 218)  = INTEGRAL over nat of
                     PHI( (mu(nat) - 217.5) / sqrt(v(nat)) ) * phi(nat)
        where, given nat,   p_i = PHI( (margin_i + nat) / sigma_state )
                            mu  = SUM p_i        v = SUM p_i (1 - p_i)

The expectation is exact. The majority probability uses one approximation —
that the count of wins, CONDITIONAL on the national error, is close to normal
— which is the textbook Poisson-binomial approximation and is very good at
435 districts. The national error, which is the part that actually correlates
the districts and drives the tails, is integrated exactly by quadrature and is
not approximated at all.

-----------------------------------------------------------------------------
AND WHY YOU CAN TRUST IT

Every run validates itself. It recomputes each stored projection under TODAY's
map, which is what the stored number used, and compares. If the analytic
engine and the simulator disagree by more than Monte Carlo noise, the run says
so and the correction figures should not be believed. If they agree, the same
engine's corrected numbers are as good as the originals.

That check is worth having for its own sake: it is an independent
implementation of the seat model, and the two agreeing is evidence about both.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import maps                                        # noqa: E402
import polling                                     # noqa: E402

REPO = HERE.parents[1]
DATA = REPO / "forecast" / "data"
MAJORITY = 218


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _gauss_legendre(n: int) -> tuple[list[float], list[float]]:
    """Nodes and weights on [-1, 1], by Newton iteration on the Legendre
    polynomial. Standard, and it keeps this file free of numpy."""
    xs, ws = [], []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, 0.0
            for j in range(1, n + 1):
                p0, p1 = ((2 * j - 1) * x * p0 - (j - 1) * p1) / j, p0
            dp = n * (x * p0 - p1) / (x * x - 1.0)
            dx = -p0 / dp
            x += dx
            if abs(dx) < 1e-14:
                break
        xs.append(x)
        ws.append(2.0 / ((1.0 - x * x) * dp * dp))
    return xs, ws


_NODES, _WEIGHTS = _gauss_legendre(96)


def house_analytic(tide: float, pvi: dict[str, float], sigma_total: float,
                   majority: int = MAJORITY) -> dict:
    """Expected seats and P(majority) in closed form. See the module docstring."""
    sigma_nat = polling.SIGMA_NATIONAL
    sigma_state = math.sqrt(max(sigma_total ** 2 - sigma_nat ** 2,
                                polling.SIGMA_STATE_FLOOR ** 2))
    margins = [tide + 2.0 * v for v in pvi.values()]

    e_seats = sum(_phi(m / sigma_total) for m in margins)

    lo, hi = -5.0 * sigma_nat, 5.0 * sigma_nat
    half, mid = (hi - lo) / 2.0, (hi + lo) / 2.0
    total = 0.0
    for x, w in zip(_NODES, _WEIGHTS):
        nat = mid + half * x
        dens = math.exp(-0.5 * (nat / sigma_nat) ** 2) / (
            sigma_nat * math.sqrt(2.0 * math.pi))
        mu = vv = 0.0
        for m in margins:
            p = _phi((m + nat) / sigma_state)
            mu += p
            vv += p * (1.0 - p)
        if vv <= 0.0:
            cond = 1.0 if mu >= majority else 0.0
        else:
            cond = 1.0 - _phi((majority - 0.5 - mu) / math.sqrt(vv))
        total += w * half * dens * cond
    return {"expected_D_seats": round(e_seats, 2),
            "prob_D_majority": round(total, 4),
            "n_districts": len(margins)}


# ---------------------------------------------------------------------------
def load_pvi(cycle: int, explicit: str | None) -> tuple[dict, dict, str]:
    """(current, prior, where_from)."""
    if explicit:
        p = Path(explicit)
        obj = json.loads(p.read_text(encoding="utf-8"))
        cur, pri = {}, {}
        for r in obj.get("rows", []):
            st, d = r.get("state"), r.get("district")
            if not st or d is None:
                continue
            rid = f"HOU_{st}_{int(d):02d}_{cycle}"
            if r.get("pvi") is not None:
                cur[rid] = float(r["pvi"])
            if r.get("pvi_prior") is not None:
                pri[rid] = float(r["pvi_prior"])
        return cur, pri, str(p)

    _date, rows = polling.latest_parsed(cycle)
    for src in polling.PVI_PREFERENCE:
        cur, pri = maps.split_rows(rows, src)
        if cur:
            return cur, pri, f"parsed rows · {src}"
    return {}, {}, "nothing found"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--history", help="path to seat_projections_history.json")
    ap.add_argument("--pvi-from", metavar="JSON",
                    help="read the district index straight from a cook_pvi "
                         "manual.json instead of from parsed rows")
    ap.add_argument("--csv", metavar="PATH", help="write the full table here")
    ap.add_argument("--tolerance", type=float, default=0.25,
                    help="seats of disagreement allowed between the analytic "
                         "engine and the stored simulation before the run "
                         "declares itself untrustworthy")
    a = ap.parse_args(argv)

    hp = Path(a.history) if a.history else (
        DATA / str(a.cycle) / "model_private" / "seat_projections_history.json")
    if not hp.exists():
        print(f"no history at {hp}")
        return 2
    hist = json.loads(hp.read_text(encoding="utf-8"))

    cur, pri, where = load_pvi(a.cycle, a.pvi_from)
    print("=" * 78)
    print(f"map correction · {len(hist)} dates in history")
    print("=" * 78)
    print(f"  district index: {where}")
    print(f"  {len(cur)} current, {len(pri)} prior")
    if not cur:
        print("  no district index available — cannot compare")
        return 2
    if not pri:
        print("  NO pvi_prior values. Nothing to correct with; re-run parse.py "
              "so the cook_pvi parser emits pvi_prior rows.")
        return 2

    eff = maps.effective_dates()
    print(f"  {len(eff)} states with a dated map change, "
          f"{min(v['date'] for v in eff.values())} .. "
          f"{max(v['date'] for v in eff.values())}")

    # SIGMA IS NOT ON EVERY DAY, AND GUESSING IT WRECKS THE COMPARISON.
    #
    # seats.py calibrates sigma ONCE per run and uses that one value for every
    # date it backfills, so a backfilled day inherits the sigma of the run
    # that wrote it — and older days do not record it at all. The first
    # version of this file fell back to 9.0 when the key was missing while the
    # real value was 6.55, which inflated every expected-seat figure by about
    # a seat and a half and made the engine check fail. The check was right
    # and the fallback was wrong.
    #
    # The correct reconstruction is the sigma of the newest day that has one,
    # because that is the run that produced the backfill.
    dated_sigma = {d: (hist[d] or {}).get("sigma") for d in hist}
    fallback = next((dated_sigma[d] for d in sorted(hist, reverse=True)
                     if dated_sigma[d]), 9.0)
    n_missing = sum(1 for v in dated_sigma.values() if not v)
    print(f"  sigma: {len(hist) - n_missing} date(s) record it; "
          f"{n_missing} do not and inherit {fallback} from the newest run")

    out, checks = [], []
    for date in sorted(hist):
        day = hist[date] or {}
        sigma = day.get("sigma") or fallback
        base, detail = maps.baseline_asof(cur, pri, date, eff)
        for key, proj in (day.get("projections") or {}).items():
            h = proj.get("house") or {}
            tide = proj.get("tide_D")
            if tide is None or h.get("expected_D_seats") is None:
                continue
            # 1. the same map the stored number used — this is the CHECK
            same = house_analytic(float(tide), cur, float(sigma))
            # 2. the map that was actually in force on that date
            dated = house_analytic(float(tide), base, float(sigma))
            checks.append(same["expected_D_seats"] - h["expected_D_seats"])
            out.append({
                "date": date, "model": key, "tide_D": tide,
                "stored_seats": h["expected_D_seats"],
                "analytic_same_map": same["expected_D_seats"],
                "corrected_seats": dated["expected_D_seats"],
                "delta_seats": round(dated["expected_D_seats"]
                                     - h["expected_D_seats"], 2),
                "stored_prob_majority": h.get("prob_D_majority"),
                "corrected_prob_majority": dated["prob_D_majority"],
                "states_on_previous_lines":
                    " ".join(detail["states_on_previous_lines"]),
                "provenance": proj.get("provenance", ""),
            })

    if not out:
        print("  no comparable projections found")
        return 1

    # ---------------- the self-check, before any conclusions ---------------
    worst = max(abs(c) for c in checks)
    mean = statistics.fmean(checks)
    print("\n-- engine check (analytic vs the stored Monte Carlo, same map) --")
    print(f"  {len(checks)} projections · mean difference {mean:+.3f} seats · "
          f"worst {worst:.3f}")
    ok = worst <= a.tolerance
    print("  " + ("AGREE — the corrected figures below are trustworthy"
                  if ok else
                  "DISAGREE by more than the tolerance. Something differs "
                  "between this engine and polling.house_forecast; do NOT "
                  "trust the corrections until that is explained."))

    # ---------------- the correction ---------------------------------------
    moved = [r for r in out if abs(r["delta_seats"]) >= 0.005]
    print(f"\n-- correction · {len(moved)} of {len(out)} projections move --")
    if moved:
        ds = [r["delta_seats"] for r in moved]
        print(f"  mean {statistics.fmean(ds):+.2f} seats · "
              f"median {statistics.median(ds):+.2f} · "
              f"range {min(ds):+.2f} to {max(ds):+.2f}")

    by_date: dict[str, list[float]] = {}
    for r in out:
        by_date.setdefault(r["date"], []).append(r["delta_seats"])
    print(f"\n  {'date':<12}{'n':>3}{'mean delta':>12}   states still on previous lines")
    shown = 0
    prev_states = None
    for date in sorted(by_date):
        states = next(r["states_on_previous_lines"] for r in out
                      if r["date"] == date)
        # One line per REGIME rather than per date: the baseline only changes
        # on the ten effective dates, so printing all 89 would be the same
        # eleven rows repeated.
        if states == prev_states:
            continue
        prev_states = states
        m = statistics.fmean(by_date[date])
        print(f"  {date:<12}{len(by_date[date]):>3}{m:>+12.2f}   "
              f"{states or '(none — current map)'}")
        shown += 1
    print(f"  {shown} distinct map regimes across the history")

    big = sorted(out, key=lambda r: -abs(r["delta_seats"]))[:8]
    print("\n  largest single corrections")
    for r in big:
        print(f"    {r['date']}  {r['model']:<28} "
              f"{r['stored_seats']:>7.2f} -> {r['corrected_seats']:>7.2f}  "
              f"({r['delta_seats']:+.2f})  "
              f"P(maj) {r['stored_prob_majority']} -> "
              f"{r['corrected_prob_majority']}")

    if a.csv:
        p = Path(a.csv)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        print(f"\n  wrote {p}")

    print("\n  Nothing was written to data/. To apply the correction:")
    print("    python3 forecast/model/seats.py --backfill-history")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
