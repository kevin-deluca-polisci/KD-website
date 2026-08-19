#!/usr/bin/env python3
"""
Statewide PVI, reconstructed from MEDSL official returns.

WHY THIS EXISTS

Cook publishes a state PVI, but it is subscriber-only, cookpolitical.com gives
ClaudeBot `Disallow: /`, and their terms forbid use "as a stand-alone product
apart from your own work" — which a redistributable index plainly is. Taking it
via Wikipedia does not relicense it either: Wikipedia's CC BY-SA covers
Wikipedia's own text, not Cook's compilation.

So we compute our own, from MEDSL's official returns, which are **CC0** — public
domain dedication, no attribution obligation, no share-alike. The method is
Cook's own published one, which is not itself proprietary:

    state PVI = weighted average of the state's presidential two-party margin
                relative to the national margin, across the last two cycles,
                with the more recent cycle weighted more heavily.

Cook's 2022 methodology update introduced the recency weighting. They have not
published the exact weights, so ours are explicit and documented: 0.75 on the
most recent cycle, 0.25 on the prior. Anyone can disagree with that choice and
recompute — which is the whole point of publishing a method rather than a number.

WHAT THIS BUYS
    A partisan baseline for all 50 states that we can actually SHOW, put in the
    data release, and let students use without a licence question. Cook's version
    stays private as a validation benchmark: if ours tracks theirs closely, that
    is evidence the reconstruction is sound, and the comparison itself is a good
    methods-page figure.

    python3 forecast/model/state_pvi.py                 # compute and write
    python3 forecast/model/state_pvi.py --compare       # check against Cook's
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# Recency weights, most recent cycle first. Cook weights the recent election more
# heavily but has not published the ratio; these are ours and are documented as
# such on the methods page.
WEIGHTS = (0.75, 0.25)


def two_party_by_state(rows: list[dict], year: int) -> dict[str, tuple[float, float]]:
    """state_po -> (dem, rep) votes for one presidential year."""
    tally: dict[str, dict[str, float]] = defaultdict(lambda: {"D": 0.0, "R": 0.0})
    for r in rows:
        try:
            if int(float(r.get("year") or 0)) != year:
                continue
        except (TypeError, ValueError):
            continue
        office = (r.get("office") or "").strip().upper()
        if office and "PRESIDENT" not in office:
            continue
        po = (r.get("state_po") or "").strip().upper()
        if len(po) != 2:
            continue
        party = (r.get("party_simplified") or r.get("party_detailed")
                 or r.get("party") or "").strip().upper()
        try:
            v = float(r.get("candidatevotes") or r.get("votes") or 0)
        except (TypeError, ValueError):
            continue
        if party.startswith("DEMOCRAT"):
            tally[po]["D"] += v
        elif party.startswith("REPUBLICAN"):
            tally[po]["R"] += v
    return {k: (v["D"], v["R"]) for k, v in tally.items()
            if (v["D"] + v["R"]) > 0}


def margins(tp: dict[str, tuple[float, float]]) -> tuple[dict[str, float], float]:
    """Per-state D-R two-party margin (pct), and the national margin."""
    per = {po: (d - r) / (d + r) * 100 for po, (d, r) in tp.items()}
    nd = sum(d for d, _ in tp.values())
    nr = sum(r for _, r in tp.values())
    natl = (nd - nr) / (nd + nr) * 100 if (nd + nr) else 0.0
    return per, natl


def compute(rows: list[dict], years: tuple[int, int]) -> dict:
    out: dict[str, dict] = {}
    lean: dict[int, dict[str, float]] = {}
    natl: dict[int, float] = {}
    for y in years:
        tp = two_party_by_state(rows, y)
        if not tp:
            raise SystemExit(
                f"no {y} presidential returns found. The MEDSL bundle with the "
                f"full 1976-2024 series may not have downloaded — check "
                f"forecast/data/2026/raw/medsl/")
        per, n = margins(tp)
        natl[y] = n
        # PVI is the state's lean RELATIVE to the nation, which is what makes it
        # comparable across cycles of different national environments.
        lean[y] = {po: m - n for po, m in per.items()}

    recent, prior = years
    for po in sorted(set(lean[recent]) & set(lean[prior])):
        pvi = WEIGHTS[0] * lean[recent][po] + WEIGHTS[1] * lean[prior][po]
        out[po] = {
            "pvi": round(pvi, 2),
            f"lean_{recent}": round(lean[recent][po], 2),
            f"lean_{prior}": round(lean[prior][po], 2),
        }
    return {"weights": {str(recent): WEIGHTS[0], str(prior): WEIGHTS[1]},
            "national_margin": {str(y): round(natl[y], 3) for y in years},
            "n_states": len(out), "states": out}


def label(v: float) -> str:
    if abs(v) < 0.5:
        return "EVEN"
    return f"{'D' if v > 0 else 'R'}+{abs(v):.0f}"


def load_medsl(cycle: int) -> list[dict]:
    root = DATA / str(cycle) / "raw" / "medsl"
    if not root.is_dir():
        raise SystemExit("no MEDSL capture found — run ./forecast/run.sh first")
    rows: list[dict] = []
    for day in sorted(root.iterdir(), reverse=True):
        if not day.is_dir():
            continue
        for f in sorted(day.glob("*.csv")):
            with f.open(encoding="utf-8-sig", newline="") as fh:
                rows.extend(csv.DictReader(fh))
        if rows:
            break
    if not rows:
        raise SystemExit("MEDSL capture contains no CSV rows")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reconstruct statewide PVI from MEDSL.")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--years", default="2024,2020",
                    help="recent,prior presidential years")
    ap.add_argument("--compare", action="store_true",
                    help="compare against the hand-entered Cook state PVI")
    a = ap.parse_args(argv)

    recent, prior = (int(x) for x in a.years.split(","))
    res = compute(load_medsl(a.cycle), (recent, prior))

    print("=" * 66)
    print(f"state PVI reconstruction · {recent} weighted {WEIGHTS[0]}, "
          f"{prior} weighted {WEIGHTS[1]}")
    print("=" * 66)
    print(f"  national margin: {recent} {res['national_margin'][str(recent)]:+.2f}, "
          f"{prior} {res['national_margin'][str(prior)]:+.2f}")
    print(f"  {res['n_states']} states\n")
    items = sorted(res["states"].items(), key=lambda kv: -kv[1]["pvi"])
    for po, v in items[:5]:
        print(f"    {po}  {label(v['pvi']):>7s}")
    print("    ...")
    for po, v in items[-5:]:
        print(f"    {po}  {label(v['pvi']):>7s}")

    out = DATA / str(a.cycle) / "derived" / "state_pvi_reconstructed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    print(f"\n  wrote {out.relative_to(REPO)}")
    print("  PUBLISHABLE: computed from CC0 data by a documented method.")

    if a.compare:
        # Validation against Cook's own numbers, which stay private. This
        # compares; it does not republish.
        pf = DATA / str(a.cycle) / "parsed"
        cook = {}
        for f in sorted(pf.glob("*.csv")) if pf.is_dir() else []:
            for r in csv.DictReader(f.open()):
                if r["source_id"] == "cook_state_pvi" and r["quantity"] == "pvi":
                    cook[r["state"]] = float(r["value"])
        if not cook:
            print("\n  --compare: no Cook state PVI parsed yet, nothing to check against")
            return 0
        diffs = [res["states"][po]["pvi"] - cook[po]
                 for po in cook if po in res["states"]]
        if diffs:
            print(f"\n  vs Cook (n={len(diffs)}):  mean diff "
                  f"{statistics.fmean(diffs):+.2f}, "
                  f"max abs {max(abs(d) for d in diffs):.2f}")
            worst = sorted(((abs(res['states'][po]['pvi'] - cook[po]), po)
                            for po in cook if po in res["states"]), reverse=True)[:5]
            print("  largest disagreements:")
            for d, po in worst:
                print(f"    {po}  ours {label(res['states'][po]['pvi']):>7s}  "
                      f"Cook {label(cook[po]):>7s}   diff {d:.1f}")
            print("  (Cook's numbers stay private; this compares, it does not publish.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
