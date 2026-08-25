#!/usr/bin/env python3
"""
A statewide partisan composite for all fifty states, calibrated against DRA.

    python3 forecast/model/state_composite.py --dra forecast/data/DRA
    python3 forecast/model/state_composite.py --dra forecast/data/DRA \
        --out forecast/data/2026/derived/state_composite.csv

-----------------------------------------------------------------------------
THE PROBLEM THIS SOLVES, AND WHY IT IS SMALLER THAN IT LOOKS

Dave's Redistricting publishes district statistics for congressional PLANS, and
a state with one at-large seat has no plan to publish -- there is nothing to
draw. Alaska, Delaware, North Dakota, South Dakota, Vermont and Wyoming come
back empty. Six districts missing out of 435 does not sound like much until you
remember that the headline number is a MAJORITY THRESHOLD counted out of 435.

But the district-level gap is an illusion. The seat model decomposes as

    margin_i = state term  +  slope x (district's deviation from its state)

and in a one-district state the district IS the state, so that deviation is
exactly zero. There is nothing to estimate at the district level. What those
six states need is the STATE term, which every other state needs too.

WHY NOT JUST AVERAGE SOME ELECTIONS AND CALL IT A COMPOSITE

Because "some elections" is a choice, and a choice made to fill six holes is a
choice nobody can check. DRA has already published its own statewide composite
for the 44 states that do have plans. So the honest construction is to build a
composite from contests we hold, FIT it against those 44 known values, and
report how well it reproduces them. If the fit is good, the six predictions
inherit that accuracy and it is a stated number rather than a hope. If the fit
is poor, we have learned that our construct is not DRA's and should say so
instead of quietly splicing two different measures into one column.

Leave-one-out is reported for the same reason: a fit evaluated on the points it
was fitted to is a description of the fit, not of the prediction.

EVERYTHING HERE IS PUBLIC DOMAIN. MEDSL's returns are CC0 via Harvard
Dataverse, so unlike the Cook baseline this can be published outright.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[0] / "collect"))

REPO = HERE.parents[1]
AT_LARGE = ["AK", "DE", "ND", "SD", "VT", "WY"]


def two_party(rows: list[dict]) -> tuple[float | None, float]:
    """(D share of the two-party vote, third-party share) for one contest."""
    d = sum(float(r["candidatevotes"] or 0) for r in rows
            if r.get("party_simplified") == "DEMOCRAT")
    rp = sum(float(r["candidatevotes"] or 0) for r in rows
             if r.get("party_simplified") == "REPUBLICAN")
    tot = max((float(r["totalvotes"] or 0) for r in rows), default=0.0)
    if d <= 0 or rp <= 0:
        return None, 0.0            # uncontested: DRA excludes these too
    third = (100 * (tot - d - rp) / tot) if tot else 0.0
    return 100 * d / (d + rp), third


def contests(path: Path, office_years: list[int]) -> dict[tuple, float]:
    """{(state, year): two-party D} for the contests worth using.

    DRA's stated rules for its own composite, applied to ours: uncontested
    races are excluded, and so are those with a large third-party share
    (they use roughly 10%), because a strong independent distorts the
    two-party reading of a race rather than informing it.
    """
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in csv.DictReader(path.open(encoding="utf-8", errors="replace")):
        try:
            y = int(r["year"])
        except (TypeError, ValueError):
            continue
        if y not in office_years:
            continue
        by[(r.get("state_po") or "", y)].append(r)
    out = {}
    for k, rows in by.items():
        share, third = two_party(rows)
        if share is None or third > 10.0:
            continue
        out[k] = share
    return out


def ols(X: list[list[float]], y: list[float]) -> list[float]:
    """Least squares by normal equations. Handful of predictors, no numpy."""
    n, k = len(X), len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)]
         + [sum(X[i][a] * y[i] for i in range(n))] for a in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(A[r][c]))
        A[c], A[piv] = A[piv], A[c]
        if abs(A[c][c]) < 1e-12:
            raise ValueError("singular design")
        for r in range(k):
            if r == c:
                continue
            f = A[r][c] / A[c][c]
            for j in range(c, k + 1):
                A[r][j] -= f * A[c][j]
    return [A[i][k] / A[i][i] for i in range(k)]


def load_dra_statewide(folder: Path) -> dict[str, float]:
    """DRA's own statewide composite, from the CURRENT-map exports only.

    Current maps only, deliberately. The prior-map exports failed the
    self-consistency check in dra_import -- nine of ten states disagree with
    their own current export by up to 5 points, because a superseded plan can
    carry an older election dataset. Fitting against a mixture of two
    instruments would produce a calibration that matches neither.
    """
    import dra_import as di
    out = {}
    for f in sorted(folder.rglob("*.csv")):
        if "current" not in f.parent.name.lower():
            continue
        st = di.infer(f.name)["state"]
        if not st:
            continue
        try:
            _rows, state_row = di.read_export(f)
        except Exception:
            continue
        if state_row:
            out[st] = state_row["two_party_D"]
    return out


def state_average(pres: dict, sen: dict, states: list[str]) -> dict[str, dict]:
    """The composite as an unweighted mean of usable statewide contests.

    THIS IS DRA'S OWN CONSTRUCTION, not an approximation of it. Their Election
    Composite averages a set of contests and excludes the uncontested ones and
    those with a large third-party share. Ours does the same with the contests
    we hold. The earlier version regressed DRA's number on ours and predicted
    the gaps; that fitted a different functional form than the thing it was
    fitting to, and inherited a 2.4-point leave-one-out error for no reason.

    STATEWIDE CONTESTS ONLY -- NO HOUSE RACES, and Alaska is why.

    A House result is a statewide result in a one-district state, so it is
    tempting. But a House race is dominated by the two people in it. Mary
    Peltola ran roughly nine points ahead of the presidential baseline in
    Alaska; folding her races into a PARTISANSHIP measure would bake her
    personal vote into the district's lean and then the candidate-quality
    model would find nothing left to explain, because the thing it is meant to
    measure would already be inside its own control variable. President and
    Senate races are the least candidate-driven statewide contests available,
    which is why DRA leans on them too.
    """
    out = {}
    for st in states:
        used = []
        for (s, y), v in sorted(pres.items()):
            if s == st:
                used.append((f"pres{y}", v))
        for (s, y), v in sorted(sen.items()):
            if s == st:
                used.append((f"sen{y}", v))
        if not used:
            continue
        out[st] = {"value": sum(v for _n, v in used) / len(used),
                   "n_contests": len(used),
                   "contests": [n for n, _v in used]}
    return out


def validate(ours: dict[str, dict], dra: dict[str, float]) -> None:
    """How close is our contest set to theirs, on the 44 we can check?"""
    both = sorted(set(ours) & set(dra))
    if not both:
        print("\n  nothing to validate against")
        return
    diffs = [(ours[s]["value"] - dra[s], s) for s in both]
    vals = [d for d, _s in diffs]
    mean = sum(vals) / len(vals)
    rmse = math.sqrt(sum(d * d for d in vals) / len(vals))
    sd = math.sqrt(sum((d - mean) ** 2 for d in vals) / len(vals))
    print("=" * 74)
    print(f"  VALIDATION against DRA's own statewide composite, {len(both)} states")
    print("=" * 74)
    print(f"    mean difference (ours - DRA): {mean:+.2f} pts")
    print(f"    RMSE {rmse:.2f}   SD about the mean {sd:.2f}")
    print("    A mean offset is a different CONTEST SET; the spread about it is")
    print("    how state-specific their pick is. The spread is what a seam")
    print("    between the two sources would cost.")
    print("    furthest apart:")
    for d, s in sorted(diffs, key=lambda t: -abs(t[0]))[:6]:
        print(f"      {s}  DRA {dra[s]:6.2f}   ours {ours[s]['value']:6.2f}   "
              f"{d:+6.2f}   ({ours[s]['n_contests']} contests)")
    print("    closest:")
    for d, s in sorted(diffs, key=lambda t: abs(t[0]))[:3]:
        print(f"      {s}  DRA {dra[s]:6.2f}   ours {ours[s]['value']:6.2f}   "
              f"{d:+6.2f}")


def find(cycle: int, needle: str) -> Path | None:
    base = REPO / "forecast" / "data" / str(cycle) / "raw"
    best = None
    for p in base.rglob("*"):
        if needle in p.name and p.suffix in (".csv", ".txt", ".tab"):
            if best is None or p.stat().st_mtime > best.stat().st_mtime:
                best = p
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dra", default="forecast/data/DRA")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--pres-years", default="2016,2020,2024")
    ap.add_argument("--sen-years", default="2018,2020,2022,2024")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    py = [int(x) for x in a.pres_years.split(",")]
    sy = [int(x) for x in a.sen_years.split(",")]

    pp = find(a.cycle, "president_state_1976")
    sp = find(a.cycle, "senate-state") or find(a.cycle, "senate_state")
    if pp is None:
        raise SystemExit("no MEDSL president-by-state file in the archive")
    print(f"  president: {pp.name}")
    print(f"  senate:    {sp.name if sp else '(none found)'}")
    pres = contests(pp, py)
    sen = contests(sp, sy) if sp else {}
    print(f"  {len(pres)} usable presidential state-years, "
          f"{len(sen)} senate")

    dra = load_dra_statewide(Path(a.dra).expanduser())
    print(f"  {len(dra)} DRA statewide composite(s) available")

    allst = sorted(set(list(dra) + AT_LARGE))
    ours = state_average(pres, sen, allst)
    validate(ours, dra)

    print("\n  THE SIX WITH NO DRA PLAN")
    est = {}
    for st in AT_LARGE:
        o = ours.get(st)
        if not o:
            print(f"    {st}: no usable contests")
            continue
        est[st] = o["value"]
        print(f"    {st}: {o['value']:5.2f}% two-party D "
              f"-> margin {2*o['value']-100:+6.1f}   "
              f"({o['n_contests']} contests: {', '.join(o['contests'])})")

    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["state", "statewide_two_party_D", "source"])
            for st in sorted(set(list(dra) + list(est))):
                if st in dra:
                    w.writerow([st, round(dra[st], 4), "dra_export"])
                else:
                    w.writerow([st, round(est[st], 4), "medsl_contest_average"])
        print(f"\n  wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
