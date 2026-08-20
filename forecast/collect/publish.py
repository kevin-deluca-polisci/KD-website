#!/usr/bin/env python3
"""
Stage 5 — assemble the single JSON the site reads.

Reads ONLY forecast/data/<cycle>/derived/, which is the published tier. It has
no access to parsed/ or raw/ by construction, so it cannot leak even if
someone later edits it carelessly.
"""
from __future__ import annotations
import argparse, csv, json, sys, datetime as dt
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts   # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"
NATL_HOUSE = "NATL_HOUSE_2026"

def rd(p): 
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)
    d = DATA / str(a.cycle) / "derived"

    avgs = rd(d / "category_averages.csv")
    supp = rd(d / "suppressed.csv")
    ratings = rd(d / "expert_ratings.csv")
    model = json.loads((d / "fundamentals_model.json").read_text()) \
            if (d / "fundamentals_model.json").exists() else None
    polling = json.loads((d / "polling_model.json").read_text()) \
              if (d / "polling_model.json").exists() else None

    dates = sorted({r["snapshot_date"] for r in avgs}) or [
        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")]
    latest = dates[-1]

    # The headline panel: one row per method category, national House margin.
    headline = []
    for r in avgs:
        if r["snapshot_date"] == latest and r["race_id"] == NATL_HOUSE \
                and r["quantity"] == "margin_D":
            headline.append({"category": r["category"], "margin_D": float(r["mean"]),
                             "n_sources": int(r["n_sources"]),
                             "low": float(r["min"]), "high": float(r["max"]),
                             "tier": r["tier"],
                             # "ok" = a real category average; "thin" = n=2 by
                             # exception; "single" = ONE contributor, and the
                             # page must say whose rather than calling it an
                             # average of anything.
                             "display": r.get("display", "ok"),
                             "sole_source": r.get("sole_source", "")})
    if model:
        # Our own model, so n=1 is not a defect — but it is labelled as ours
        # rather than presented as a category average of anything.
        headline.append({"category": "fundamentals", "margin_D": model["margin_D"],
                         "n_sources": 1, "low": model["margin_D_80_low"],
                         "high": model["margin_D_80_high"], "tier": "open",
                         "display": "single", "sole_source": "class model",
                         "note": "class model"})

    series = defaultdict(list)
    for r in avgs:
        if r["race_id"] == NATL_HOUSE and r["quantity"] == "margin_D":
            series[r["category"]].append([r["snapshot_date"], float(r["mean"])])

    # The polling model's Senate table, trimmed to what the page renders.
    # Deliberately NOT the whole file: site.json is public, and shipping every
    # intermediate invites someone to read a diagnostic as a forecast.
    senate = None
    if polling and polling.get("senate", {}).get("races"):
        s_ = polling["senate"]
        senate = {
            "tide_D": polling["election_day_tide_D"],
            "generic_ballot": polling["generic_ballot"]["value"],
            "shrink_lambda": polling["shrink_lambda"],
            "sigma": s_["sigma_total"],
            "expected_D_seats_up": s_["expected_D_seats_up"],
            "D_seats_up_80pct": s_["D_seats_up_80pct"],
            "holdover_D": s_.get("holdover_D_assumed"),
            # Total chamber seats, not just the ones on the ballot. "15 of 35"
            # is the modelling quantity; "49 of 100" is the thing a reader
            # actually wants, because 50 is the number that decides control.
            # Computed here rather than in the template so the arithmetic is
            # testable and the page only ever displays.
            "expected_D_total": (round(s_["expected_D_seats_up"] + s_["holdover_D_assumed"], 2)
                                 if s_.get("holdover_D_assumed") is not None else None),
            "D_total_80pct": ([s_["D_seats_up_80pct"][0] + s_["holdover_D_assumed"],
                               s_["D_seats_up_80pct"][1] + s_["holdover_D_assumed"]]
                              if s_.get("holdover_D_assumed") is not None else None),
            "prob_D_50_plus": s_.get("prob_D_50_plus"),
            # Carried so the page can show it. A one-seat change in the
            # baseline moves this by ~20 points, which is more than any
            # modelling choice in the model — publishing the headline without
            # it would be publishing false precision.
            "sensitivity": s_.get("prob_D_50_plus_sensitivity"),
            "races": [{"state": k, **v} for k, v in
                      sorted(s_["races"].items(),
                             key=lambda kv: -kv[1]["expected_margin_D"])],
        }

    # Expert ratings, reshaped for rendering. The CSV is one row per
    # (date, race, forecaster) — 1,836 of them — and a template is the wrong
    # place to pivot that. Templates render; Python computes.
    #
    # Only the latest snapshot, and only races where at least one forecaster
    # sees a contest: a table of 435 rows that says "Safe R" 300 times teaches
    # nothing. `disagreement` is the spread between the most D and most R
    # rating on the same seat, which is the column worth sorting by.
    panel = defaultdict(dict)
    meta_ = {}
    for r in ratings:
        if r["snapshot_date"] != latest or ":" not in r.get("value", ""):
            continue
        who, label = r["value"].split(":", 1)
        panel[r["race_id"]][who] = label
        meta_[r["race_id"]] = {"chamber": r["chamber"], "state": r["state"],
                               "district": r.get("district", "")}
    ORD = {"Safe D": 0, "Solid D": 0, "Likely D": 1, "Lean D": 2, "Tilt D": 3,
           "Toss-up": 4, "Tossup": 4,
           "Tilt R": 5, "Lean R": 6, "Likely R": 7, "Safe R": 8, "Solid R": 8}
    ratings_panel = []
    for rid, who in panel.items():
        vals = [ORD[v] for v in who.values() if v in ORD]
        if not vals or (min(vals) in (0, 8) and max(vals) in (0, 8)
                        and min(vals) == max(vals)):
            continue                     # unanimous Safe: nothing to show
        ratings_panel.append({
            "race_id": rid, **meta_[rid],
            "n_forecasters": len(who),
            "disagreement": max(vals) - min(vals),
            "mean_rating": round(sum(vals) / len(vals), 2),
            "ratings": [{"forecaster": k, "label": v} for k, v in sorted(who.items())],
        })
    ratings_panel.sort(key=lambda x: (-x["disagreement"], x["race_id"]))

    # Accumulate the timeline and lay out both panels. This is the only part of
    # publish.py that WRITES to derived/ rather than only reading it, because
    # the history has to survive tomorrow's overwrite of the model files.
    chart_data = charts.build(d, latest)

    out = {
        "cycle": a.cycle,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "latest_snapshot": latest,
        "snapshot_count": len(dates),
        "headline": sorted(headline, key=lambda x: x["category"]),
        "series": {k: sorted(v) for k, v in series.items()},
        "expert_ratings_panel": ratings_panel,
        "fundamentals_model": model,
        "polling_model": senate,
        "charts": chart_data,
        "suppressed_cells": len(supp),
        "display_note": (
            "A row with display='single' has ONE contributing source and is not "
            "a category average. Render it named, never as a consensus."),
        "disclosure_note": (
            "Category averages only for sources whose terms do not permit "
            "per-forecaster republication during the cycle. Averages containing "
            "any such source are shown only with at least 3 contributors. "
            "Full per-forecaster data will be released as a documented archive "
            "after the election."),
    }
    p = DATA / str(a.cycle) / "site.json"
    p.write_text(json.dumps(out, indent=2))
    print(f"  wrote {p.relative_to(REPO)}  "
          f"({len(headline)} categories, {len(dates)} snapshots)")

    # Hugo reads this at build time from assets/, NOT from data/.
    #
    # Two reasons, and the second one is load-bearing:
    #   1. never point Hugo at forecast/data/ — that is the whole archive
    #      including the private tiers, and Hugo would ingest all of it.
    #   2. a template that reads .Site.Data forces Hugo to assemble the entire
    #      data map, and this site's data/*.csv load as [][]string, which Hugo
    #      0.123 cannot merge. One .Site.Data reference from the forecast page
    #      broke the whole build with "unexpected data type [][]string in file
    #      media.csv". Loading from assets/ via resources.Get avoids the data
    #      map entirely.
    hugo = REPO / "assets" / f"forecast_{a.cycle}.json"
    hugo.parent.mkdir(parents=True, exist_ok=True)
    hugo.write_text(json.dumps(out, indent=2))
    print(f"  wrote {hugo.relative_to(REPO)}  (Hugo build-time data)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
