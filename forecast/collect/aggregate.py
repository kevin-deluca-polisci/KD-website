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

# The two chamber-wide race ids, spelled the same way the parsers spell them.
NATL_HOUSE = "NATL_HOUSE_2026"
NATL_SENATE = "NATL_SENATE_2026"

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
NOT_A_FORECAST = {
    "margin_D_pres_2024", "margin_D_prior_senate",
    # Economic inputs, not predictions. Averaging "real income growth" across
    # forecasters would be meaningless — there is one true value and FRED
    # publishes it. They live in the archive because the fundamentals model
    # consumes them and the archive should record what it was fed.
    "income_growth_last_full_year", "income_growth_ytd",
    "income_growth_yoy_latest_month", "income_ytd_months",
}


def read_parsed(cycle: int) -> list[dict]:
    d = DATA_DIR / str(cycle) / "parsed"
    if not d.is_dir():
        return []
    rows = []
    for p in sorted(d.glob("*.csv")):
        with p.open(encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


# The class models, named as sources. They are not captured from anywhere, so
# they never appear in parsed/ — but they ARE forecasts of their category, and
# the category number should be the average of every forecast of that kind
# including ours.
CLASS_MODELS = {
    "fundamentals": "class_fundamentals",
    "polling": "class_polling",
}


def class_model_rows(cycle: int) -> list[dict]:
    """The class models as ordinary contributors to their own category.

    WHY THIS EXISTS. Until now the two class models bypassed this file
    entirely: publish.py read them straight out of their JSON and pasted them
    onto the page as their own rows, so "Fundamentals" was our model and
    nothing else, and a second fundamentals model arriving tomorrow would have
    sat in a category average NEXT TO ours rather than being averaged with it.
    That is the wrong shape for a page whose whole claim is that a category is
    a way of knowing rather than a person. Emitting them here as rows makes
    them contributors like any other: the mean, the min/max, the spread and
    the n all pick them up for free, and adding Ray Fair later is a registry
    entry and a parser rather than a change to how the page thinks.

    Read from seat_projections.json because it holds BOTH models in one shape
    already — the same tide pushed through the same seat machinery — so the
    two cannot drift apart here through a copy-paste.

    Tier is `individual`: these are ours, we publish the code, and there is no
    licence to gate. That also means they never count toward MIN_N, which is
    correct — the floor exists to stop a reader recovering a gated forecast by
    subtraction, and a number we publish in full subtracts out to nothing.

    NOT emitted: `pvi`. It rides along inside each race entry and it is Cook's
    proprietary index. NEVER_PUBLISH would catch it downstream anyway; not
    writing it is the belt to that braces.
    """
    p = DATA_DIR / str(cycle) / "derived" / "seat_projections.json"
    if not p.exists():
        return []
    proj = json.loads(p.read_text())
    date = proj.get("snapshot_date")
    if not date:
        return []

    rows: list[dict] = []

    def emit(cat, race_id, chamber, state, district, quantity, value, unit):
        if value is None:
            return
        rows.append({
            "snapshot_date": date, "source_id": CLASS_MODELS[cat],
            "category": cat, "publication": "individual",
            "race_id": race_id, "chamber": chamber, "state": state,
            "district": district, "quantity": quantity,
            "value": float(value), "unit": unit,
            "captured_at": "", "raw_sha256": "", "raw_path": "",
        })

    for cat, model in (proj.get("projections") or {}).items():
        if cat not in CLASS_MODELS:
            continue
        senate, house = model.get("senate") or {}, model.get("house") or {}
        # The national MARGIN, but only from fundamentals.
        #
        # Since the polling model became a nowcast its tide is the generic
        # ballot unchanged — which is to say it is Silver Bulletin's average,
        # read straight through. Emitting it here would put that one
        # aggregator into the polling mean twice, once under its own name and
        # once under ours, and quietly give it double weight. The polling
        # model's own contribution is the SEAT projection below: carrying a
        # tide through partisan lean is work no aggregator does.
        #
        # Fundamentals is different. Its margin is estimated from approval,
        # income and seats defended, and shares no input with anything else in
        # its category, so it belongs in the mean.
        if cat != "polling":
            emit(cat, NATL_HOUSE, "national", "", "", "margin_D",
                 model.get("tide_D"), "margin")
        emit(cat, NATL_HOUSE, "national", "", "", "seats_D",
             house.get("expected_D_seats"), "seats")
        emit(cat, NATL_HOUSE, "national", "", "", "win_prob_D",
             house.get("prob_D_majority"), "prob")
        emit(cat, NATL_SENATE, "national", "", "", "seats_D",
             senate.get("expected_D_total"), "seats")
        # 51+ is a majority. 50+ is a tie the vice-president breaks, and every
        # outside forecast and market this is averaged against prices the
        # majority, so averaging our 50+ against their 51+ would compare two
        # different events and call the difference disagreement.
        emit(cat, NATL_SENATE, "national", "", "", "win_prob_D",
             senate.get("prob_D_51_plus"), "prob")
        for st, r in (model.get("races") or {}).items():
            rid = f"SEN_{st}_2026"
            emit(cat, rid, "senate", st, "", "margin_D",
                 r.get("expected_margin_D"), "margin")
            emit(cat, rid, "senate", st, "", "win_prob_D",
                 r.get("win_prob_D"), "prob")
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
        # The floor has to count GATED contributors, not all contributors.
        #
        # An individual-tier source is published by name elsewhere on the site,
        # so its value is known. If the floor counted it, an average of one open
        # and two gated forecasts would clear MIN_N=3 while handing a reader the
        # mean of the two gated ones by subtraction — and one open, one gated,
        # one open would hand over the gated value exactly. The protection was
        # never about how many numbers went in; it is about how many UNKNOWN
        # numbers a reader is left with after subtracting the ones we published.
        n_gated = sum(1 for s in per_source if tiers.get(s) != "individual")

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
            "quantity": q, "unit": unit, "n_sources": n, "n_gated": n_gated,
            # 0 unless this row is the open-only subset of a gated cell.
            "partial": 0, "n_withheld": 0,
            "mean": round(statistics.fmean(vals), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "sd": round(statistics.stdev(vals), 4) if n > 1 else "",
            "tier": "gated" if gated else "open",
            "display": display,
            "sole_source": sole,
            # Who actually went into the mean. Leading underscore: audit()
            # reads it, write() strips it. It must NOT reach the CSV — naming
            # the members of a gated average is a disclosure in its own right,
            # and `sole_source` already handles the one case where naming is
            # both necessary and permitted.
            "_contributors": sorted(per_source),
        }
        if gated and n_gated < MIN_N:
            suppressed.append({**rec, "mean": "", "min": "", "max": "", "sd": "",
                               "reason": f"only {n_gated} gated source(s) of "
                                         f"{n} contributing; MIN_N={MIN_N} counts "
                                         f"gated sources only, because the open "
                                         f"ones are published by name and can be "
                                         f"subtracted back out"})
            # ...but do not let the whole category go dark.
            #
            # If any contributor is open-tier, publish the average of just
            # those. It reveals nothing: an open source's value is already
            # published under its own name in by_source_open.csv, so a mean
            # over open sources is a rearrangement of numbers a reader can
            # already read. What it avoids is a category vanishing from the
            # site the day a second forecaster arrives — which is what
            # happened on 2026-08-20, when the professional line stopped
            # because Race to the WH came online beside Grant Williams and one
            # open plus one gated is below the floor.
            #
            # Labelled `partial`, with the number withheld, so the page can say
            # "1 of 2 shown" rather than presenting it as the whole category.
            open_srcs = [s for s in per_source if tiers.get(s) == "individual"]
            if open_srcs:
                ov = [statistics.fmean(per_source[s]) for s in open_srcs]
                averages.append({
                    **rec, "tier": "open", "n_sources": len(ov), "n_gated": 0,
                    "_contributors": sorted(open_srcs),
                    "mean": round(statistics.fmean(ov), 4),
                    "min": round(min(ov), 4), "max": round(max(ov), 4),
                    "sd": round(statistics.stdev(ov), 4) if len(ov) > 1 else "",
                    "display": "single" if len(ov) == 1 else "ok",
                    "sole_source": open_srcs[0] if len(ov) == 1 else "",
                    "partial": 1, "n_withheld": n_gated,
                })
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

    # Nothing marked private may CONTRIBUTE to a published average.
    #
    # Two earlier versions of this check were both too coarse, and each was
    # caught only because it blocked a clean publication:
    #
    #   - by quantity NAME: did any source anywhere carry this quantity at
    #     private tier in this category? Fired the day a private aggregator
    #     appeared alongside four public ones — correctly excluded from the
    #     mean, but the proxy could not tell membership from existence.
    #   - by SOURCE: is any contributor private anywhere? Fired on Grant
    #     Williams, who publishes his own forecast openly and republishes Cook
    #     PVI privately. Same trap as the row-level check above, which already
    #     learned this lesson and was not consulted.
    #
    # Tier is a property of a ROW, not of a source and not of a quantity name.
    # So the membership test has to be keyed the way aggregate() groups: date,
    # category, race, quantity, source. Anything coarser answers a question
    # nobody asked and blocks publications that are entirely clean — and a
    # check that cries wolf gets switched off by whoever is on deadline, which
    # is worse than not having it.
    private_keys = {
        (r["snapshot_date"], r["category"], r["race_id"], r["quantity"],
         r["source_id"])
        for r in rows if r["publication"] == "private"
    }
    for a in averages:
        bad = sorted(
            s for s in (a.get("_contributors") or ())
            if (a["snapshot_date"], a["category"], a["race_id"],
                a["quantity"], s) in private_keys)
        if bad:
            problems.append(
                f"LEAK: private source(s) {bad} contributed to the "
                f"published average for {a['race_id']}/{a['quantity']}")
    for a in averages:
        if a["tier"] == "gated" and int(a.get("n_gated", a["n_sources"])) < MIN_N:
            problems.append(
                f"LEAK: gated average published with n_gated="
                f"{a.get('n_gated')} of n={a['n_sources']} "
                f"< MIN_N for {a['race_id']}/{a['quantity']}")
    for a in averages:
        if a["quantity"] in NO_AVERAGE:
            problems.append(f"LEAK: averaged a non-averageable quantity {a['quantity']}")
    return problems


def would_shrink(cycle: int, averages: list[dict]) -> list[str]:
    """Snapshot dates this run would publish LESS of than is already published.

    THE HAZARD. raw/ is pushed to a separate private archive and parsed/ is
    never committed, so a clone of this repo carries the DERIVED data for every
    day but the inputs for none of them. Run aggregate.py in such a clone and
    it rebuilds category_averages.csv from whatever parsed/ happens to hold
    locally — typically the day or two you captured yourself — and writes it
    over a file covering weeks. The write succeeds, the audit passes, every
    number that survives is correct, and the archive is quietly shorter than
    it was. Nothing downstream notices: publish.py reads the newest date and
    the site looks entirely normal.

    Counting DATES is not enough, and the first version of this check made
    exactly that mistake. The class models are emitted from
    seat_projections.json, which carries the newest date whether or not the
    parsed store does — so a local run still produces a row for today, the
    date set matches, and the check passes while today quietly loses every
    contributor except ours. Compare the row count per date instead: that is
    the thing actually being destroyed.

    This is not a merge. A day cannot be reconstructed without its bytes, so
    the only safe move is to refuse. Recover by cloning the raw archive and
    re-running parse.py --all, or by letting the daily Action do it where the
    whole store lives.
    """
    p = DATA_DIR / str(cycle) / "derived" / "category_averages.csv"
    if not p.exists():
        return []
    have: dict[str, int] = defaultdict(int)
    with p.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            have[r["snapshot_date"]] += 1
    now: dict[str, int] = defaultdict(int)
    for a in averages:
        now[a["snapshot_date"]] += 1
    out = []
    for date in sorted(have):
        before, after = have[date], now.get(date, 0)
        if after < before:
            out.append(f"{date}: {before} rows published, this run has {after}")
    return out


def write(cycle: int, averages, by_source, suppressed, ratings) -> list[Path]:
    d = DATA_DIR / str(cycle) / "derived"
    d.mkdir(parents=True, exist_ok=True)
    written = []

    def dump(name, recs, fields):
        # Internal keys never reach disk. `_contributors` in particular names
        # the members of every average, gated ones included.
        fields = [f for f in fields if not f.startswith("_")]
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
    ap.add_argument("--force", action="store_true",
                    help="write even if it drops previously published snapshot "
                         "dates (see would_shrink)")
    a = ap.parse_args(argv)

    rows = read_parsed(a.cycle) + class_model_rows(a.cycle)
    if not rows:
        print("No parsed rows. Run parse.py first.")
        return 0

    averages, by_source, suppressed = aggregate(rows)
    ratings = ratings_panel(rows)
    problems = audit(rows, averages, by_source, suppressed)
    shrunk = would_shrink(a.cycle, averages)

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
    # Counted by GATED contributors, which is what the floor actually tests.
    # Counting all contributors made this read "n=3, publishable" for a cell
    # that was in fact one open source and two gated ones.
    cells: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for a_ in averages + suppressed:
        cells[a_["category"]][int(a_.get("n_gated", a_["n_sources"]))] += 1

    print()
    for cat in sorted(cat_sources):
        by_n = cells.get(cat, {})
        if not by_n:
            continue
        spread = ", ".join(f"n_gated={k}: {v}" for k, v in sorted(by_n.items()))
        print(f"  {cat:14s} {len(cat_sources[cat])} source(s)  [{spread}]")
        if max(by_n) < MIN_N and any(k > 0 for k in by_n):
            need = MIN_N - max(by_n)
            print(f"      no cell reaches MIN_N={MIN_N}: needs {need} more "
                  f"GATED source(s) before any gated average may be published. "
                  f"Adding an open source does not help — it is published by "
                  f"name and subtracts straight back out.")

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

    if shrunk and not a.force:
        print("\n  *** REFUSING TO WRITE: this run would shorten the archive ***")
        print(f"    {len(shrunk)} snapshot date(s) would lose rows:")
        for line in shrunk[:8]:
            print(f"      {line}")
        if len(shrunk) > 8:
            print(f"      … and {len(shrunk) - 8} more")
        print("    parsed/ is not committed and raw/ lives in the private "
              "archive, so a fresh clone can rebuild the newest day but not the")
        print("    older ones. Writing now would drop them from derived/ with "
              "no way to get them back except a re-parse.")
        print("    Fix: clone the raw archive and re-run parse.py --cycle "
              f"{a.cycle} --all, or let the daily Action do it where the whole")
        print("    store lives. Use --force only if shortening the archive is "
              "what you actually mean.")
        return 1
    if shrunk and a.force:
        print(f"\n  --force: shortening {len(shrunk)} previously published "
              f"snapshot date(s) in derived/.")

    for p in write(a.cycle, averages, by_source, suppressed, ratings):
        print(f"  wrote {p.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
