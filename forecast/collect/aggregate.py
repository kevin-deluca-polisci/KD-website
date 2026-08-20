#!/usr/bin/env python3
"""
Phase 4 — aggregate per-forecaster rows into publishable category averages.

THIS FILE IS THE PRIVACY BOUNDARY. Everything upstream of it (raw/, parsed/) is
private. Everything it writes to derived/ is published. The rule is enforced
here in code rather than trusted to discipline, for the same reason the licence
gate lives in capture.py: a rule you have to remember is a rule you will
eventually forget at 11pm in late October.

THE TWO RULES

1. Publication tier, per source, taken from the registry and carried on every
   parsed row:
       individual      may be published per-forecaster (permissive licence)
       aggregate_only  only the category mean may leave
       private         never published in any form during the cycle

2. Minimum N for aggregate_only. If a category has two contributing sources and
   you publish the mean, anyone who knows one value recovers the other by
   subtraction. So an average that contains ANY aggregate_only or private source
   is published only when it has at least MIN_N contributors; otherwise the cell
   is suppressed and says so.

   Categories made up entirely of `individual` sources are exempt, because
   there is nothing to protect — Kalshi and Polymarket prices are a public order
   book in real time.

  python3 forecast/collect/aggregate.py             # every parsed date
  python3 forecast/collect/aggregate.py --check     # audit only, write nothing
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "forecast" / "data"

MIN_N = 3           # disclosure floor for any average containing a gated source
CYCLE_DEFAULT = 2026

# Honesty floor, separate from the disclosure floor above and applying to EVERY
# category regardless of licence. The scope doc's rule: "a category average of
# one source is republication with extra steps, both analytically and legally."
# A row labelled "Professional: D+5.7" reads as a consensus of professional
# forecasters; if it is one person's model, saying so is not optional.
#
# The row is still WRITTEN — it belongs in the archive — but it carries a
# `display` flag the site must respect, and `sole_source` names the single
# contributor when licence permits naming it.
MIN_DISPLAY_N = 3
# Categories allowed to display at n=2, by explicit exception. Markets are
# methodologically distinct, continuously priced, and there are only two venues;
# merging them into anything else would destroy real information.
THIN_OK = {"market"}

# Quantities that must never be averaged. Ordinal ratings do not combine with
# vote shares, and building a crosswalk is a judgment call better spent as a
# class discussion than buried in a script.
NO_AVERAGE = {"rating_ordinal", "rating_numeric"}

# Quantities that never leave the private tier regardless of which source
# carried them. PVI is Cook's proprietary index; we hold it for class use only.
NEVER_PUBLISH = {"pvi", "pvi_prior"}

# Reference baselines, not forecasts. Averaging a 2024 RESULT into a 2026
# forecast category would be a category error in the literal sense.
NOT_A_FORECAST = {"margin_D_pres_2024", "margin_D_prior_senate"}


def read_parsed(cycle: int) -> list[dict]:
    d = DATA_DIR / str(cycle) / "parsed"
    if not d.is_dir():
        return []
    rows = []
    for p in sorted(d.glob("*.csv")):
        with p.open(encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def aggregate(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (public_averages, public_by_source, suppressed).

    public_by_source contains ONLY rows whose source is publication=individual.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if (r["quantity"] in NO_AVERAGE or r["quantity"] in NEVER_PUBLISH
                or r["quantity"] in NOT_A_FORECAST):
            continue
        key = (r["snapshot_date"], r["category"], r["race_id"],
               r["chamber"], r["state"], r["district"], r["quantity"], r["unit"])
        groups[key].append(r)

    averages, suppressed = [], []
    for key, members in sorted(groups.items()):
        date, cat, rid, ch, st, dist, q, unit = key
        # One value per source: a source contributing several markets for the
        # same race must not count as several forecasters.
        per_source: dict[str, list[float]] = defaultdict(list)
        tiers: dict[str, str] = {}
        for m in members:
            try:
                per_source[m["source_id"]].append(float(m["value"]))
            except (TypeError, ValueError):
                continue
            tiers[m["source_id"]] = m["publication"]

        if any(t == "private" for t in tiers.values()):
            per_source = {s: v for s, v in per_source.items()
                          if tiers.get(s) != "private"}
        if not per_source:
            continue

        vals = [statistics.fmean(v) for v in per_source.values()]
        n = len(vals)
        gated = any(tiers.get(s) != "individual" for s in per_source)

        # How the site is allowed to render this cell.
        if n >= MIN_DISPLAY_N:
            display = "ok"
        elif n == 2 and cat in THIN_OK:
            display = "thin"
        else:
            display = "single" if n == 1 else "thin"
        # Name the lone contributor only where its licence permits naming.
        sole = ""
        if n == 1:
            only = next(iter(per_source))
            sole = only if tiers.get(only) == "individual" else ""

        rec = {
            "snapshot_date": date, "category": cat, "race_id": rid,
            "chamber": ch, "state": st, "district": dist,
            "quantity": q, "unit": unit, "n_sources": n,
            "mean": round(statistics.fmean(vals), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "sd": round(statistics.stdev(vals), 4) if n > 1 else "",
            "tier": "gated" if gated else "open",
            "display": display,
            "sole_source": sole,
        }
        if gated and n < MIN_N:
            suppressed.append({**rec, "mean": "", "min": "", "max": "", "sd": "",
                               "reason": f"only {n} contributing source(s); "
                                         f"MIN_N={MIN_N} for gated categories"})
        else:
            averages.append(rec)

    by_source = [
        {k: r[k] for k in ("snapshot_date", "source_id", "category", "race_id",
                           "chamber", "state", "district", "quantity", "value", "unit")}
        for r in rows
        if r["publication"] == "individual" and r["quantity"] not in NO_AVERAGE
        and r["quantity"] not in NEVER_PUBLISH
    ]
    return averages, by_source, suppressed


def ratings_panel(rows: list[dict]) -> list[dict]:
    """Ordinal ratings, kept whole and kept out of the dispersion figure."""
    return [
        {k: r[k] for k in ("snapshot_date", "source_id", "race_id", "chamber",
                           "state", "district", "value")}
        for r in rows
        if r["quantity"] == "rating_ordinal" and r["publication"] == "individual"
    ]


def audit(rows: list[dict], averages, by_source, suppressed) -> list[str]:
    """
    Belt and braces. Re-derive the guarantee from the OUTPUT rather than
    trusting the code path that produced it, so a future refactor that breaks
    the tier logic fails here instead of leaking.
    """
    problems = []

    # Per ROW, not per source. A source can legitimately carry rows at different
    # tiers — Grant Williams publishes his own forecast under MIT but also
    # republishes Cook PVI, which is gated. A source-level tier map would
    # collapse those to whichever row happened to be read last, and quietly
    # wave the gated one through.
    gated_keys = {
        (r["snapshot_date"], r["source_id"], r["race_id"], r["quantity"])
        for r in rows if r["publication"] != "individual"
    }
    for r in by_source:
        key = (r["snapshot_date"], r["source_id"], r["race_id"], r["quantity"])
        if key in gated_keys:
            problems.append(
                f"LEAK: per-source row published for {r['source_id']}/"
                f"{r['quantity']} which is gated at row level")

    # Nothing marked private may appear anywhere in the published tier.
    private_q = {(r["source_id"], r["quantity"])
                 for r in rows if r["publication"] == "private"}
    for a in averages:
        for sid, q in private_q:
            if a["quantity"] == q and a["category"] == next(
                    (r["category"] for r in rows if r["source_id"] == sid), None):
                problems.append(
                    f"LEAK: private quantity {q!r} reached the published averages")
                break
    for a in averages:
        if a["tier"] == "gated" and a["n_sources"] < MIN_N:
            problems.append(
                f"LEAK: gated average published with n={a['n_sources']} "
                f"< MIN_N for {a['race_id']}/{a['quantity']}")
    for a in averages:
        if a["quantity"] in NO_AVERAGE:
            problems.append(f"LEAK: averaged a non-averageable quantity {a['quantity']}")
    return problems


def write(cycle: int, averages, by_source, suppressed, ratings) -> list[Path]:
    d = DATA_DIR / str(cycle) / "derived"
    d.mkdir(parents=True, exist_ok=True)
    written = []

    def dump(name, recs, fields):
        p = d / name
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(recs)
        written.append(p)

    if averages:
        dump("category_averages.csv", averages, list(averages[0].keys()))
    if by_source:
        dump("by_source_open.csv", by_source, list(by_source[0].keys()))
    if suppressed:
        dump("suppressed.csv", suppressed, list(suppressed[0].keys()))
    if ratings:
        dump("expert_ratings.csv", ratings, list(ratings[0].keys()))
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate parsed rows for publication.")
    ap.add_argument("--cycle", type=int, default=CYCLE_DEFAULT)
    ap.add_argument("--check", action="store_true", help="audit only, write nothing")
    a = ap.parse_args(argv)

    rows = read_parsed(a.cycle)
    if not rows:
        print("No parsed rows. Run parse.py first.")
        return 0

    averages, by_source, suppressed = aggregate(rows)
    ratings = ratings_panel(rows)
    problems = audit(rows, averages, by_source, suppressed)

    print("=" * 70)
    print(f"aggregate · cycle {a.cycle}")
    print("=" * 70)
    print(f"  {len(rows):6d} parsed rows in   (private)")
    print(f"  {len(averages):6d} category averages out   (PUBLIC)")
    print(f"  {len(by_source):6d} per-source rows out     (PUBLIC — individual tier only)")
    print(f"  {len(ratings):6d} expert rating rows out   (PUBLIC, separate panel)")
    print(f"  {len(suppressed):6d} cells SUPPRESSED below MIN_N={MIN_N}")
    disp = defaultdict(int)
    for a_ in averages:
        disp[a_["display"]] += 1
    if disp:
        print(f"\n  display flags: " + ", ".join(f"{k}={v}" for k, v in sorted(disp.items())))
    # Per-category health. The old version of this block reported only the
    # cells with a single contributor, phrased as though it described the whole
    # category — so the run after Race to the WH started producing data still
    # said "'professional' has one contributor", while 507 cells had in fact
    # just gained a second. Progress toward MIN_N is the thing worth watching
    # here, and it was the one thing the summary could not show.
    cat_sources: dict[str, set] = defaultdict(set)
    for r in rows:
        cat_sources[r["category"]].add(r["source_id"])
    cells: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for a_ in averages + suppressed:
        cells[a_["category"]][int(a_["n_sources"])] += 1

    print()
    for cat in sorted(cat_sources):
        by_n = cells.get(cat, {})
        if not by_n:
            continue
        spread = ", ".join(f"n={k}: {v}" for k, v in sorted(by_n.items()))
        print(f"  {cat:14s} {len(cat_sources[cat])} source(s)  [{spread}]")
        if max(by_n) < MIN_N:
            need = MIN_N - max(by_n)
            print(f"      no cell reaches MIN_N={MIN_N}: needs {need} more "
                  f"contributing source(s) before any average may be published.")

    singles = {(a_["category"], a_["sole_source"]) for a_ in averages
               if a_["display"] == "single"}
    for cat, sole in sorted(singles):
        who = sole or "an unnameable source"
        print(f"    single-source cells in {cat!r} ({who}) must be LABELLED, "
              f"not averaged.")

    tiers: dict[str, set] = defaultdict(set)
    for r in rows:
        tiers[r["publication"]].add(r["source_id"])
    print()
    for t in ("individual", "aggregate_only", "private"):
        if tiers[t]:
            print(f"  tier {t:15s} {', '.join(sorted(tiers[t]))}")

    if problems:
        print("\n  *** PUBLICATION AUDIT FAILED ***")
        for p in problems[:20]:
            print(f"    {p}")
        print("  Nothing written.")
        return 1
    print("\n  publication audit: PASS")

    if a.check:
        print("  --check: nothing written")
        return 0
    for p in write(a.cycle, averages, by_source, suppressed, ratings):
        print(f"  wrote {p.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
