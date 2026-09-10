#!/usr/bin/env python3
"""predict_2026.py — apply the fitted model to the 2026 Senate map.

    python3 forecast/endorse_quality/predict_2026.py

NOT A FORECAST, AND NOT PUBLISHABLE. Three reasons, all of which the output
repeats so a table copied out of it cannot lose them.

1. THE CYCLE TERM DOES NOT EXIST FOR 2026. The model carries a fixed effect
   with two levels, 2022 and 2024, worth 4.1 points between them. 2026 is
   neither. Setting it to the 2022 level assumes 2026 behaves like 2022;
   setting it to 2024 assumes the opposite. Both are printed, as a bracket
   rather than a point, because a single number here would be a choice
   disguised as a result.

2. THE ENDORSEMENT TERM APPLIES TO SEVEN RACES. Only 7 of the 35 have
   general-phase endorsements for BOTH nominees. For the other 28 the share is
   UNDEFINED, not zero -- coding it zero would assert an even split nobody
   observed. Those races get the baseline only, and are marked.

3. THE COEFFICIENT IT RESTS ON IS FRAGILE. 8.41 with an HC1 standard error of
   4.09, and it falls to 4.64 when incumbency is dropped. See
   PREREGISTRATION 2b.

DRA's license permits republication and its CSVs are in the public repo; the
`publication: private` tier means no per-district figure is SHOWN on a forecast
page or written into derived/. Nothing from THIS script is publishable either
way, for the three reasons above.
"""

from __future__ import annotations

import collections
import csv
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DERIVED = REPO_ROOT / "forecast" / "data" / "2026" / "derived"
CONDITIONS = REPO_ROOT / "forecast" / "conditions"

_s = importlib.util.spec_from_file_location("fm", HERE / "fit_model.py")
fm = importlib.util.module_from_spec(_s)
_s.loader.exec_module(fm)

# The primary specification, from fit_model.py on 75 races.
B = {"const": -82.965, "dra": 1.636, "c24": -4.133, "inc": 5.383, "share": 8.406}
NOPARTY = {"Organizations", "Labor unions", "Newspapers", "Political parties"}


def endorsement_share() -> dict:
    """state -> (share_D, n_D, n_R) for 2026 Senate, general phase only."""
    per = collections.defaultdict(collections.Counter)
    for r in json.loads((DERIVED / "endorsements_2026.json").read_text()):
        if r.get("phase") != "general" or r.get("duplicate_key"):
            continue
        if r.get("chamber") != "senate":
            continue
        if (r.get("category_label") or r.get("category") or "") in NOPARTY:
            continue
        p = r.get("candidate_party")
        if p in ("D", "R"):
            per[r.get("state")][p] += 1
    out = {}
    for st, c in per.items():
        if c["D"] and c["R"]:
            out[st] = ((c["D"] / (c["D"] + c["R"])) - 0.5, c["D"], c["R"])
    return out


def incumbency() -> dict:
    out = {}
    with (CONDITIONS / "senate_incumbency_2026.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            running = str(r.get("is_running", "")).strip().upper() == "TRUE"
            held = (r.get("party_holding") or "").upper()
            v = 0
            if running:
                v = 1 if held.startswith("DEMOCRAT") else -1 if held.startswith("REPUBLIC") else 0
            out[r["state"]] = (v, r.get("incumbent_name", ""), running, held)
    return out


def main() -> int:
    maps, kind = fm.load_dra()
    shares = endorsement_share()
    inc = incumbency()
    payload = json.loads((REPO_ROOT / "assets" / "forecast_2026.json").read_text())
    races = {r["state"]: r for r in payload.get("races", [])}

    rows = []
    for st, race in races.items():
        dra = fm.partisanship(maps, kind, "senate", st, None, 2026)
        if dra is None:
            continue
        iv, iname, running, held = inc.get(st, (0, "", False, ""))
        sh = shares.get(st)
        base = B["const"] + B["dra"] * dra + B["inc"] * iv
        pred22 = base
        pred24 = base + B["c24"]
        if sh:
            pred22 += B["share"] * sh[0]
            pred24 += B["share"] * sh[0]

        cur = race.get("current", {})
        def g(k):
            v = cur.get(k, {}).get("margin_D")
            return v["mean"] if v else None
        rows.append({
            "state": st, "name": race.get("name", st), "dra": dra, "inc": iv,
            "share": sh[0] if sh else None, "nD": sh[1] if sh else 0, "nR": sh[2] if sh else 0,
            "lo": min(pred22, pred24), "hi": max(pred22, pred24),
            "poll": g("polling"), "fund": g("fundamentals"), "prof": g("professional"),
        })

    # "Competitive" by the site's own polling average where it exists, so the
    # ranking is not chosen by the model being tested.
    def rank(r):
        v = r["poll"] if r["poll"] is not None else r["fund"]
        return abs(v) if v is not None else 999
    rows.sort(key=rank)

    print("=" * 88)
    print("2026 SENATE — the endorsement model applied, against the site's own averages")
    print("=" * 88)
    print("  NOT a forecast. The cycle term has no 2026 level, so the model is a")
    print("  RANGE: low end assumes 2026 behaves like 2022, high end like 2024.")
    print("  * = endorsement term applied (both nominees endorsed). Others are")
    print("  baseline only: DRA partisanship + incumbency.\n")
    print(f"  {'race':<17}{'model range':>16}{'polling':>10}{'fund':>9}{'prof':>9}"
          f"{'DRA':>7}{'inc':>5}  endorse")
    print("  " + "-" * 84)
    for r in rows[:10]:
        star = "*" if r["share"] is not None else " "
        rng = f"{r['lo']:+.1f} to {r['hi']:+.1f}"
        def f(v):
            return f"{v:+.1f}" if v is not None else "  --"
        e = f"{r['nD']}-{r['nR']}" if r["share"] is not None else "--"
        print(f" {star}{r['name'][:16]:<17}{rng:>16}{f(r['poll']):>10}{f(r['fund']):>9}"
              f"{f(r['prof']):>9}{r['dra']:>7.1f}{r['inc']:>5}  {e}")

    print("\n  Where the model has an endorsement term (all 7):")
    for r in [x for x in rows if x["share"] is not None]:
        print(f"    {r['name'][:16]:<17} model {r['lo']:+.1f} to {r['hi']:+.1f}"
              f"   polling {r['poll'] if r['poll'] is not None else float('nan'):+.1f}"
              f"   endorsements D{r['nD']}-R{r['nR']} (share {r['share']:+.2f})")

    withp = [r for r in rows if r["poll"] is not None]
    if withp:
        mid = [(r["lo"] + r["hi"]) / 2 for r in withp]
        err = [m - r["poll"] for m, r in zip(mid, withp)]
        print(f"\n  vs the polling average across {len(withp)} races:"
              f" mean gap {sum(err)/len(err):+.1f}, "
              f"mean |gap| {sum(abs(e) for e in err)/len(err):.1f} points")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
