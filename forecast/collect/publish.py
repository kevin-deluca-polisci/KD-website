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
NATL_SENATE = "NATL_SENATE_2026"

# A race table of 35 rows in which 17 say ">99%" or "<1%" is mostly filler, and
# filler is not neutral: it trains a reader to skim, and the rows worth reading
# are in the middle. The cut is on probability rather than margin because
# probability is what the reader is being asked to weigh.
#
# One consequence is worth stating out loud rather than tuning around. Nebraska
# sits at P(D)=0.012 and survives this filter by a whisker — and it is the one
# race where our model is answering a question nobody asked, because there is
# no Democrat on the ballot. Dan Osborn is running as an independent. A
# two-party model cannot represent that at all, so Nebraska's number should be
# read as "what a generic Democrat would do here", which is not the race.
COMPETITIVE_LO, COMPETITIVE_HI = 0.01, 0.99
COMPETITIVE_NOTE = (
    "Races where the model puts the Democratic win probability between 1% and "
    "99%. The rest are not close under any assumption this model makes.")

def rd(p):
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []


# Every model that may be shown BY NAME on the comparison page, with the two
# things a reader needs in order to argue with it: where to find it, and what
# it is actually made of. Anything not in here is either ours or gated.
#
# The gate is not editorial. by_source_open.csv contains exactly the sources
# whose terms permit per-forecaster republication during the cycle, and
# aggregate.py re-derives that guarantee from its own output and refuses to
# write if it is violated. So the comparison page can only ever name a source
# that is already in that file — which is why the code below joins on it rather
# than on a list of forecasters someone typed.
NAMED = {
    "grant_williams": {
        "label": "Grant Williams",
        "url": "https://grantwilliamsforecast.com/",
        "kind": "professional",
        "what": "A published statistical forecast combining district-level "
                "partisan lean, polling, incumbency and candidate quality. "
                "Released under an MIT licence, which is why it can be shown "
                "here race by race while most of its competitors cannot.",
    },
    "polymarket": {
        "label": "Polymarket",
        "url": "https://polymarket.com/",
        "kind": "market",
        "what": "Real-money prediction markets. The price of a contract that "
                "pays $1 if an event happens is read as a probability, which "
                "is a reasonable approximation and not an identity — fees, "
                "capital costs and the favourite-longshot bias all push it "
                "around at the extremes.",
    },
    "medsl": {
        "label": "MIT Election Lab",
        "url": "https://electionlab.mit.edu/data",
        "kind": "returns",
        "what": "Certified official returns, not a forecast. Used here as the "
                "baseline a forecast has to beat: what the state actually did "
                "last time. Public domain (CC0).",
    },
}


def build_model_index(d: Path, latest: str) -> dict:
    """Today's national numbers, keyed by source, for the methods page.

    The methods page has to name a model and then show what it currently says,
    and the two halves live in different places on purpose: the prose and the
    links are editorial and belong in the template, the numbers are data and
    belong here. A template that hardcodes "Grant Williams says D+5.7" is a
    number that goes stale silently.

    Built from by_source_open.csv, which by construction holds only sources
    whose terms permit being quoted by name. A gated forecaster cannot appear
    here even if someone adds them to the template — the lookup simply misses
    and the page says so.
    """
    idx: dict[str, dict] = defaultdict(dict)
    for r in rd(d / "by_source_open.csv"):
        if r["snapshot_date"] != latest or r["chamber"] != "national":
            continue
        if r["race_id"] not in (NATL_HOUSE, NATL_SENATE):
            continue
        try:
            v = float(r["value"])
        except (TypeError, ValueError):
            continue
        key = ("house" if r["race_id"] == NATL_HOUSE else "senate") + "_" + r["quantity"]
        idx[r["source_id"]][key] = v
    return dict(idx)


def build_comparison(d: Path, latest: str, senate: dict | None,
                     model: dict | None, avgs: list[dict]) -> dict | None:
    """Model against model, race by race, using only nameable sources.

    The pedagogy is the point. Two forecasts of the same seat that disagree by
    ten points are not both noise: one of them knows something the other does
    not, and which one is a question a student can actually answer by reading.
    Maine is this cycle's worked example — the structural model has it dark
    blue because Maine votes Democratic for president, and the forecast that
    knows Susan Collins is on the ballot has it nearly tied.

    Returns None rather than an empty scaffold when there is nothing to compare,
    so the template can drop the whole section instead of rendering a table of
    dashes.
    """
    rows = [r for r in rd(d / "by_source_open.csv") if r["snapshot_date"] == latest]
    if not rows:
        return None

    def pick(src, chamber, qty, key="state"):
        return {r[key]: float(r["value"]) for r in rows
                if r["source_id"] == src and r["chamber"] == chamber
                and r["quantity"] == qty and r["value"] not in ("", None)}

    def one(src, race, qty):
        for r in rows:
            if r["source_id"] == src and r["race_id"] == race and r["quantity"] == qty:
                return float(r["value"])
        return None

    # ---- national: the same two questions, asked of every method we have ----
    natl = []
    if model:
        natl.append({"key": "fundamentals", "label": "Fundamentals",
                     "who": "class model", "house_margin": model["margin_D"],
                     "house_prob": None, "house_seats": None,
                     "senate_prob": None, "senate_prob_basis": ""})
    if senate:
        natl.append({"key": "polling", "label": "Polling", "who": "class model",
                     "house_margin": senate["tide_D"], "house_prob": None,
                     "house_seats": None,
                     "senate_prob": senate.get("prob_D_51_plus"),
                     "senate_prob_basis": "51+"})
    for src in ("grant_williams", "polymarket"):
        meta = NAMED[src]
        hm, hp = one(src, NATL_HOUSE, "margin_D"), one(src, NATL_HOUSE, "win_prob_D")
        hs, sp = one(src, NATL_HOUSE, "seats_D"), one(src, NATL_SENATE, "win_prob_D")
        if hm is hp is hs is sp is None:
            continue
        natl.append({"key": src, "label": meta["label"], "who": meta["kind"],
                     "url": meta["url"], "house_margin": hm, "house_prob": hp,
                     "house_seats": hs, "senate_prob": sp,
                     # Outside forecasts and markets resolve on CONTROL, which
                     # needs 51 given a Republican vice-president. Ours is
                     # stated at 51+ above for exactly this reason; without the
                     # basis label the two columns would silently compare
                     # different events.
                     "senate_prob_basis": "51+" if sp is not None else ""})

    # ---- senate, race by race ----
    gw_m, gw_p = pick("grant_williams", "senate", "margin_D"), \
                 pick("grant_williams", "senate", "win_prob_D")
    pres = pick("medsl", "national", "margin_D_pres_2024")
    races = []
    for r in (senate or {}).get("races", []):
        st, ours = r["state"], r["expected_margin_D"]
        gw = gw_m.get(st)
        races.append({
            "state": st, "ours": ours, "ours_prob": r["win_prob_D"],
            "gw": gw, "gw_prob": gw_p.get(st),
            "pres_2024": pres.get(st),
            "gap": round(ours - gw, 2) if gw is not None else None,
            # Sign disagreement is the loud case: the two models do not merely
            # differ on how much, they differ on who wins.
            "opposed": gw is not None and (ours > 0) != (gw > 0),
            "competitive": r.get("competitive", False),
        })
    # Competitive races first, then by size of disagreement.
    #
    # Sorting purely by gap puts Kentucky, Oklahoma, Alabama and Idaho at the
    # top, where the two models are arguing about whether a Republican wins by
    # ten or by twenty-four. That disagreement is real and it is diagnostic —
    # it is the linear PVI mapping overshooting at the tails — but it is not
    # the thing a reader came for, and burying Maine below four safe seats
    # teaches the wrong lesson about what to look at.
    races.sort(key=lambda x: (not x["competitive"],
                              -(abs(x["gap"]) if x["gap"] is not None else -1),
                              x["state"]))
    gaps = [abs(x["gap"]) for x in races if x["gap"] is not None]
    comp_gaps = [abs(x["gap"]) for x in races
                 if x["gap"] is not None and x["competitive"]]
    safe_gaps = [x["gap"] for x in races
                 if x["gap"] is not None and not x["competitive"]]

    # Geometry for the dumbbell chart: one row per competitive race, two dots
    # joined by a rule. Scaled over the competitive races ONLY. Letting Wyoming
    # into the range calculation would compress every row that matters into the
    # middle sixth of the plot, which is the same mistake the timeline panel
    # made before its axis was fixed.
    comp = [x for x in races if x["competitive"] and x["gap"] is not None]
    span = [v for x in comp for v in (x["ours"], x["gw"]) if v is not None]
    dumbbell = None
    if span:
        lo, hi = min(span), max(span)
        pad = max((hi - lo) * 0.08, 1.0)
        lo, hi = lo - pad, hi + pad
        def X(v):
            return round((v - lo) / (hi - lo) * 100, 2)
        dumbbell = {
            "lo": round(lo, 2), "hi": round(hi, 2),
            "zero_x": X(0.0) if lo < 0 < hi else None,
            "ticks": [{"v": t, "x": X(t),
                       "label": "EVEN" if t == 0 else
                                (f"D+{t}" if t > 0 else f"R+{abs(t)}")}
                      for t in (-20, -10, 0, 10, 20) if lo <= t <= hi],
            "rows": [{"state": x["state"], "gap": x["gap"], "opposed": x["opposed"],
                      "ours": x["ours"], "gw": x["gw"],
                      "x_ours": X(x["ours"]), "x_gw": X(x["gw"]),
                      "x_pres": X(x["pres_2024"]) if x["pres_2024"] is not None
                                and lo <= x["pres_2024"] <= hi else None,
                      "pres_2024": x["pres_2024"]}
                     for x in comp],
        }

    # How much of the professional category we are allowed to name. Derived
    # from the published files rather than asserted, so it cannot drift out of
    # date when a source's terms change.
    prof_n = next((int(a["n_sources"]) for a in avgs
                   if a["snapshot_date"] == latest and a["race_id"] == NATL_HOUSE
                   and a["quantity"] == "margin_D" and a["category"] == "professional"), 0)
    named_prof = len({r["source_id"] for r in rows
                      if NAMED.get(r["source_id"], {}).get("kind") == "professional"})

    return {
        "national": natl,
        "senate": races,
        "dumbbell": dumbbell,
        "sources": NAMED,
        "stats": {
            "n": len(gaps),
            "mean_abs_gap": round(sum(gaps) / len(gaps), 2) if gaps else None,
            "max_gap": round(max(gaps), 2) if gaps else None,
            "n_opposed": sum(1 for x in races if x["opposed"]),
            "n_competitive": len(comp_gaps),
            "mean_abs_gap_competitive": (round(sum(comp_gaps) / len(comp_gaps), 2)
                                         if comp_gaps else None),
            # SIGNED, not absolute, and that is the whole point of carrying it:
            # a mean near zero would say the two models scatter around each
            # other, and a mean well below zero says ours is systematically
            # more Republican in seats that are not close. This cycle it is the
            # second, which is the linear PVI-to-margin mapping running out of
            # road in the deepest-red states rather than a disagreement about
            # any particular race.
            "mean_signed_gap_safe": (round(sum(safe_gaps) / len(safe_gaps), 2)
                                     if safe_gaps else None),
        },
        "professional_named": named_prof,
        "professional_total": prof_n,
        "naming_note": (
            ((f"The only professional forecast in today's average may be shown "
              f"by name." if prof_n == 1 else
              f"All {prof_n} professional forecasts in today's average may be "
              f"shown by name.")
             if named_prof >= prof_n else
             f"{named_prof} of the {prof_n} professional forecasts in today's "
             f"average may be shown by name. The rest permit collection but not "
             f"per-forecaster republication during the cycle, so they appear only "
             f"inside the category average on the tracker.")
            + " The full archive is released after the election."),
    }

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
            # 50+ is a tie the vice-president breaks; 51+ is a majority. Both
            # ship, because every outside forecast and market this page is
            # compared against prices the 51+ event.
            "prob_D_51_plus": s_.get("prob_D_51_plus"),
            # Carried so the page can show it. A one-seat change in the
            # baseline moves this by ~20 points, which is more than any
            # modelling choice in the model — publishing the headline without
            # it would be publishing false precision.
            "sensitivity": s_.get("prob_D_50_plus_sensitivity"),
            "races": [{"state": k, **v, "competitive": COMPETITIVE_LO < v["win_prob_D"] < COMPETITIVE_HI}
                      for k, v in sorted(s_["races"].items(),
                                         key=lambda kv: -kv[1]["expected_margin_D"])],
        }
        senate["n_competitive"] = sum(1 for r in senate["races"] if r["competitive"])
        senate["competitive_note"] = COMPETITIVE_NOTE

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
    chart_data["ladder"] = charts.build_ladder(senate)
    comparison = build_comparison(d, latest, senate, model, avgs)
    model_index = build_model_index(d, latest)

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
        "comparison": comparison,
        "model_index": model_index,
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
