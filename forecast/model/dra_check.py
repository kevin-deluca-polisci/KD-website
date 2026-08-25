#!/usr/bin/env python3
"""
Is a Dave's Redistricting export a usable substitute for Cook's PVI?

    # the real check: rebuild Cook's formula from two presidential exports
    python3 forecast/model/dra_check.py --state TX --map prior \
        --pres2020 ~/dl/TX_2022map_pres2020.csv \
        --pres2024 ~/dl/TX_2022map_pres2024.csv

    # the quick diagnostic: how does ANY single DRA index relate to Cook?
    python3 forecast/model/dra_check.py --state TX --map prior \
        --single ~/dl/TX2022Congressionaldistrictstatistics.csv

-----------------------------------------------------------------------------
WHY THIS FILE EXISTS

The seat model needs a district baseline. The one we hold is Cook's, and Cook's
cannot be published, so nothing computed from it can be either -- publishing a
district margin beside the national tide gives the index back exactly, since
PVI = (margin - tide) / 2. A public-domain baseline is therefore not a nicety;
it is the only way the House side of this archive ever gets released.

Dave's Redistricting is the obvious candidate. But "correlates with Cook" is
not the standard. The seat model consumes the index as

    district margin = national tide + 2 x PVI

so an index that is systematically WIDER than Cook's does not merely disagree
at the edges: it multiplies straight through into every seat count, making safe
seats safer and understating how many districts are actually in play. Slope is
the number that matters, not r.

WHAT THIS REPORTS, AND WHAT EACH NUMBER DISQUALIFIES

  slope        1.00 is the target. Anything else is a scale error and biases
               seats even when r is near 1.
  r, R2        agreement of ORDER. Necessary, nowhere near sufficient.
  residual sd  stated in points of MARGIN, which is 2x the share it is
               computed in, so it can be read against the model's own sigma.
               If it approaches sigma, the measurement error is the size of the
               entire forecast uncertainty and the index is unusable.
  worst rows   named, because the residuals are usually not noise. When they
               cluster somewhere real -- one region, one kind of district --
               that pattern is the finding, and it belongs on the methods page
               rather than in a footnote about data quality.

Cook's integer rounding contributes about 0.6 points of margin to the residual
sd on its own. Subtract that before deciding anything is alarming.

NOTHING HERE IS PUBLISHED. Cook's numbers are an input to this check and never
an output of it; what may be published is the agreement statistics, which is
the whole point -- a public audit of a proprietary index that reproduces none
of it.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# National two-party Democratic share, in percent. These set the LEVEL of the
# whole index -- Cook's PVI is a deviation from exactly these numbers -- so
# they are written down rather than approximated, and they should be re-pinned
# against the FEC's official tally before anything built on them is published.
NATIONAL_2P_D = {
    2016: 100 * 65853514 / (65853514 + 62984828),   # Clinton / Trump
    2020: 100 * 81283501 / (81283501 + 74223975),   # Biden   / Trump
    2024: 100 * 75017613 / (75017613 + 77303568),   # Harris  / Trump  (VERIFY)
}
# Which pair of presidentials each Cook vintage averages.
COOK_VINTAGE = {"pvi": (2020, 2024), "pvi_prior": (2016, 2020)}


def read_dra(path: Path) -> tuple[dict[int, float], float]:
    """{district: two-party D share}, plus the statewide row.

    Three shapes in this file will bite anyone who writes the naive loop: a
    trailing comma on every line gives csv a phantom None column, the row with
    ID "Un" is unassigned territory and is all zeros, and the row with a BLANK
    id is the statewide total rather than a district.
    """
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if not rows:
        raise SystemExit(f"{path}: empty")
    for need in ("ID", "Dem", "Rep"):
        if need not in rows[0]:
            raise SystemExit(f"{path}: no '{need}' column -- is this a DRA "
                             f"district-statistics export?")
    out: dict[int, float] = {}
    state = None
    for r in rows:
        i = (r.get("ID") or "").strip().strip('"')
        try:
            d, rp = float(r["Dem"]), float(r["Rep"])
        except (TypeError, ValueError):
            continue
        if d + rp <= 0:
            continue                          # "Un", and any empty district
        share = 100 * d / (d + rp)
        if i == "":
            state = share
        elif i.isdigit():
            out[int(i)] = share
    if not out:
        raise SystemExit(f"{path}: understood 0 districts")
    return out, state


def cook(state: str, cycle: int = 2026) -> dict[int, dict]:
    """Cook's hand-entered table, newest capture. PRIVATE -- input only."""
    base = DATA / str(cycle) / "raw" / "cook_pvi"
    caps = sorted(p for p in base.glob("*/manual.json")) if base.exists() else []
    if not caps:
        raise SystemExit(f"no cook_pvi capture under {base}")
    doc = json.loads(caps[-1].read_text(encoding="utf-8"))
    return {int(r["district"]): r
            for r in (doc.get("rows") or [])
            if str(r.get("state", "")).upper() == state.upper()}


def regress(x: list[float], y: list[float]) -> dict:
    n = len(x)
    mx, my = st.mean(x), st.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    slope = sxy / sxx
    return {"n": n, "slope": slope, "intercept": my - slope * mx,
            "r": sxy / (sxx * syy) ** 0.5,
            "sd_x": st.pstdev(x), "sd_y": st.pstdev(y),
            "mean_x": mx, "mean_y": my,
            "resid": [b - (slope * a + (my - slope * mx))
                      for a, b in zip(x, y)]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--state", required=True)
    ap.add_argument("--map", choices=["current", "prior"], default="current",
                    help="which Cook column to compare against")
    ap.add_argument("--pres2020"); ap.add_argument("--pres2024")
    ap.add_argument("--pres2016")
    ap.add_argument("--single", help="one DRA export, any index. Diagnostic "
                                     "only -- reports the relationship, does "
                                     "not claim to reproduce Cook.")
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)

    col = "pvi" if a.map == "current" else "pvi_prior"
    ck = cook(a.state, a.cycle)
    have = {d: r[col] for d, r in ck.items() if r.get(col) is not None}
    if not have:
        raise SystemExit(f"cook_pvi carries no '{col}' for {a.state}")
    y0, y1 = COOK_VINTAGE[col]
    nat_cook = (NATIONAL_2P_D[y0] + NATIONAL_2P_D[y1]) / 2

    print("=" * 72)
    print(f"{a.state} -- DRA vs Cook '{col}' ({y0}/{y1} presidential, "
          f"national two-party D {nat_cook:.2f}%)")
    print("=" * 72)

    if a.single:
        dra, state_row = read_dra(Path(a.single).expanduser())
        ks = sorted(set(dra) & set(have))
        if state_row is not None:
            print(f"\n  statewide row: two-party D {state_row:.2f}%")
            print("  CHECK THIS against the known statewide result for the "
                  "election you selected.\n  If it does not match, the export "
                  "is on a different dataset than you think --\n  DRA's "
                  "default is the multi-election Election Composite, which is "
                  "state-specific\n  and has no national counterpart to "
                  "subtract.")
        x = [have[k] + nat_cook for k in ks]     # Cook-implied district share
        yv = [dra[k] for k in ks]
        label = "DRA index"
    else:
        if not (a.pres2020 and a.pres2024) and not (a.pres2016 and a.pres2020):
            raise SystemExit("give --pres2020 with --pres2024 (current-map "
                             "vintage), or --pres2016 with --pres2020 (prior), "
                             "or use --single for the diagnostic")
        files = {y0: getattr(a, f"pres{y0}"), y1: getattr(a, f"pres{y1}")}
        if not all(files.values()):
            raise SystemExit(f"'{col}' is the {y0}/{y1} vintage; supply "
                             f"--pres{y0} and --pres{y1}")
        parts, states = {}, {}
        for yr, f in files.items():
            parts[yr], states[yr] = read_dra(Path(f).expanduser())
            print(f"\n  {yr}: {len(parts[yr])} districts, statewide "
                  f"two-party D {states[yr]:.2f}%")
        ks = sorted(set(parts[y0]) & set(parts[y1]) & set(have))
        # COOK'S FORMULA, EXACTLY: average the district's two-party share over
        # the two elections, subtract the national average over the same two.
        rebuilt = {k: (parts[y0][k] + parts[y1][k]) / 2 - nat_cook for k in ks}
        x = [have[k] for k in ks]
        yv = [rebuilt[k] for k in ks]
        label = "rebuilt PVI"

    if len(ks) < 3:
        raise SystemExit(f"only {len(ks)} districts in common -- nothing to say")
    g = regress(x, yv)
    rsd = st.pstdev(g["resid"])
    print(f"\n  {label} = {g['slope']:.4f} x Cook + {g['intercept']:+.3f}")
    print(f"  r = {g['r']:.4f}   R2 = {g['r']**2:.4f}   n = {g['n']} districts")
    print(f"  spread ratio (DRA sd / Cook sd) = {g['sd_y']/g['sd_x']:.3f}")
    print(f"  residual sd = {rsd:.2f} pts of share = {2*rsd:.2f} PTS OF MARGIN")
    print(f"  max |residual| = {max(abs(v) for v in g['resid']):.2f} pts of share")

    print("\n  verdict:")
    ok = True
    if abs(g["slope"] - 1) > 0.03:
        ok = False
        d = "wider" if g["slope"] > 1 else "narrower"
        print(f"    FAIL  slope {g['slope']:.3f} -- the index is {abs(g['slope']-1)*100:.0f}% "
              f"{d} than Cook's.\n          That is a scale error and it "
              f"multiplies through 'margin = tide + 2 x PVI'.")
    else:
        print(f"    ok    slope {g['slope']:.3f} -- same scale")
    if 2 * rsd > 2.0:
        ok = False
        print(f"    FAIL  {2*rsd:.2f} points of margin of measurement error. "
              f"Compare against the\n          model's own sigma (~6.5). "
              f"Cook's integer rounding explains ~0.6 of it.")
    else:
        print(f"    ok    {2*rsd:.2f} points of margin of measurement error")
    if g["r"] ** 2 < 0.99:
        print(f"    note  R2 {g['r']**2:.4f} -- order agrees, which is the "
              f"weakest of the three tests")

    print("\n  worst-fitting districts (the pattern here is usually the finding):")
    for k, rv in sorted(zip(ks, g["resid"]), key=lambda t: -abs(t[1]))[:8]:
        print(f"    {a.state}-{k:02d}   Cook {x[ks.index(k)]:6.1f}   "
              f"{label} {yv[ks.index(k)]:6.1f}   resid {rv:+5.1f}")

    print("\n  " + ("USABLE as a Cook substitute for this state."
                    if ok else
                    "NOT usable as a Cook substitute as exported."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
