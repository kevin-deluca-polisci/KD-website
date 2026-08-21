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


# ---------------------------------------------------------------------------
# The four-category spread.
#
# Per race, what each FAMILY of forecast says — not one named forecaster
# against another. That framing was wrong twice over. It implied a benchmark
# where we have no evidence of one, and it only worked at all because exactly
# one outside per-race forecast happens to carry a permissive licence, which is
# a fact about paperwork rather than about quality.
#
# Categories also make the disclosure question disappear. A category average is
# already the published tier: aggregate.py decides what may appear in one and
# refuses to write if the answer is nothing. Nothing here re-derives that
# judgment, and nothing here can name a forecaster the aggregator withheld.
# ---------------------------------------------------------------------------

CATEGORY_ORDER = ["fundamentals", "polling", "professional", "market"]
CATEGORY_LABEL = {"fundamentals": "Fundamentals", "polling": "Polling",
                  "professional": "Professional", "market": "Markets"}


def build_model_index(d: Path, latest: str) -> dict:
    """Today's national numbers, keyed by source, for the methods page.

    Prose and links are editorial and live in the template; numbers are data
    and live here. A template that hardcodes "X says D+5.7" is a number that
    goes stale silently.

    Built from by_source_open.csv, which by construction holds only sources
    whose terms permit being quoted by name. A gated forecaster cannot appear
    here even if someone adds them to the template — the lookup simply misses.
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


def _cat_cells(avgs: list[dict], latest: str) -> dict:
    """(race_id, quantity, category) -> the published average, or its absence.

    Carries the suppressed cells too. A withheld number is not the same as a
    missing one, and a page that renders both as an empty cell is telling the
    reader that nobody has an opinion when in fact we have one and may not
    show it.
    """
    out: dict[tuple, dict] = {}
    for r in avgs:
        if r["snapshot_date"] != latest:
            continue
        try:
            v = float(r["mean"])
        except (TypeError, ValueError):
            continue
        out[(r["race_id"], r["quantity"], r["category"])] = {
            "value": v, "n": int(r["n_sources"]),
            "n_gated": int(r.get("n_gated") or 0),
            "display": r.get("display", "ok"),
            "sole_source": r.get("sole_source", ""),
            "withheld": False,
        }
    return out


def _withheld_cells(supp: list[dict], latest: str) -> set:
    return {(r["race_id"], r["quantity"], r["category"]) for r in supp
            if r["snapshot_date"] == latest}


def _seat_markers(chamber: str, proj: dict | None, avgs: list[dict],
                  latest: str) -> dict:
    """Every method's seat count, for the ladder's tick marks.

    Our two models answer from the projection; the professionals and the
    markets answer through the published category average, which means a
    category that is withheld today simply has no marker — correct, and
    visibly different from a category that has no opinion.
    """
    out: dict[str, float | None] = {}
    p = (proj or {}).get("projections") or {}
    for key in ("fundamentals", "polling"):
        got = p.get(key) or {}
        if chamber == "senate":
            out[key] = (got.get("senate") or {}).get("expected_D_total")
        else:
            out[key] = (got.get("house") or {}).get("expected_D_seats")
    rid = NATL_SENATE if chamber == "senate" else NATL_HOUSE
    for r in avgs:
        if (r["snapshot_date"] == latest and r["race_id"] == rid
                and r["quantity"] == "seats_D" and r["category"] in ("professional", "market")):
            try:
                out[r["category"]] = float(r["mean"])
            except (TypeError, ValueError):
                pass
    return {k: v for k, v in out.items() if v is not None}


def build_ladders(senate: dict | None, proj: dict | None,
                  avgs: list[dict] | None = None, latest: str = "") -> dict:
    """One ladder per chamber, both off the polling projection.

    The Senate ladder counts from the seats not on the ballot; the House has no
    such block, so its caps are the safe seats either side of the drawn window.
    Same function, different bookkeeping — which is the reason build_ladder
    stopped taking a polling-model dict and started taking a plain list.
    """
    out: dict[str, dict | None] = {}
    if senate and senate.get("races") and senate.get("holdover_D") is not None:
        hold_D = int(senate["holdover_D"])
        n_up = len(senate["races"])
        out["senate"] = charts.build_ladder(
            senate["races"], chamber="senate",
            fixed_left=hold_D, fixed_right=100 - hold_D - n_up, total=100,
            thresholds=((50, "a tie, broken by the vice-president"),
                        (51, "an outright majority")),
            expected=senate.get("expected_D_total"),
            markers=_seat_markers("senate", proj, avgs or [], latest),
            max_drawn=n_up,
            left_label=f"{hold_D} D seats not on the ballot",
            right_label=f"{100 - hold_D - n_up} R seats not on the ballot")

    p = ((proj or {}).get("projections") or {}).get("polling") or {}
    districts = p.get("districts") or []
    house = p.get("house") or {}
    if districts and house:
        rows = [{**d,
                 "label": f"{d['state']}-{d['district']}" if d.get("district") else d["state"],
                 "competitive": COMPETITIVE_LO < d["win_prob_D"] < COMPETITIVE_HI}
                for d in districts]
        out["house"] = charts.build_ladder(
            rows, chamber="house", fixed_left=0, fixed_right=0,
            total=len(rows),
            thresholds=((house.get("majority_at", 218), "a majority"),),
            expected=house.get("expected_D_seats"),
            markers=_seat_markers("house", proj, avgs or [], latest),
            max_drawn=45,
            left_label="safe Democratic seats",
            right_label="safe Republican seats")
    return out


def build_governors(latest: str, avgs: list[dict], supp: list[dict],
                    ratings: list[dict]) -> dict | None:
    """Governors: collected and shown, but not modelled and not on the timeline.

    We run no gubernatorial model, and that is a decision rather than a gap.
    The machinery this site has translates a NATIONAL tide through partisan
    lean, and governors are the office where that translation is weakest —
    Kansas, Kentucky, Vermont and Massachusetts have all recently elected
    governors of the party that loses the state by twenty points. Publishing a
    tide-plus-lean number for those races would produce confident nonsense.

    So the governors panel carries only what someone else measured: the market
    price where a market exists, the raters' spread, and the state's 2024
    presidential margin as a baseline. No line on the tracker, because there is
    no series of ours to draw.
    """
    withheld = _withheld_cells(supp, latest)
    cells: dict[tuple, dict] = {}
    for r in avgs:
        if r["snapshot_date"] != latest or r["chamber"] != "governor":
            continue
        try:
            cells[(r["race_id"], r["quantity"], r["category"])] = {
                "value": float(r["mean"]), "n": int(r["n_sources"]),
                "sole_source": r.get("sole_source", "")}
        except (TypeError, ValueError):
            continue

    # Ratings, one row per race, reduced to a spread.
    NUM = {"Safe D": 0.0, "Solid D": 0.0, "Likely D": 1.5, "Lean D": 3.0,
           "Tilt D": 4.0, "Toss-up": 5.0, "Tossup": 5.0,
           "Tilt R": 6.0, "Lean R": 7.0, "Likely R": 8.5,
           "Safe R": 10.0, "Solid R": 10.0}
    rated: dict[str, dict[str, str]] = defaultdict(dict)
    state_of: dict[str, str] = {}
    for r in ratings:
        if r["snapshot_date"] != latest or r["chamber"] != "governor":
            continue
        v = r.get("value", "")
        if ":" not in v:
            continue
        who, label = v.split(":", 1)
        rated[r["race_id"]][who] = label
        state_of[r["race_id"]] = r["state"]

    ids = sorted(set(rated) | {rid for rid, _, _ in cells})
    if not ids:
        return None
    races = []
    for rid in ids:
        who = rated.get(rid, {})
        vals = [NUM[v] for v in who.values() if v in NUM]
        market = cells.get((rid, "win_prob_D", "market"))
        races.append({
            "race_id": rid,
            "state": state_of.get(rid) or (rid.split("_")[1] if "_" in rid else rid),
            "n_raters": len(vals),
            "mean_rating": round(sum(vals) / len(vals), 2) if vals else None,
            "spread": round(max(vals) - min(vals), 1) if len(vals) > 1 else None,
            "labels": [{"forecaster": charts.rater_name(k), "label": v}
                       for k, v in sorted(who.items())],
            "market_prob_D": (market or {}).get("value"),
            "market_withheld": (rid, "win_prob_D", "market") in withheld,
        })
    # Closest first, on whichever signal exists. Ratings if we have them,
    # otherwise the market price, otherwise alphabetical — sorting on the mean
    # rating alone put every unrated race at the top as if it were a toss-up.
    def closeness(r):
        if r["mean_rating"] is not None:
            return (0, abs(r["mean_rating"] - 5.0), -(r["spread"] or 0))
        if r["market_prob_D"] is not None:
            return (0, abs(r["market_prob_D"] - 0.5) * 10.0, 0)
        return (1, 0, 0)
    races.sort(key=lambda r: (*closeness(r), r["state"]))
    # Only the close ones reach the page. Governors are not the focus of the
    # course — the framing is congressional — but a handful of these races are
    # genuinely competitive and dropping them entirely would lose the one part
    # a reader would look for. "Close" is a rating between Lean D and Lean R,
    # or a market price between 20% and 80%; everything else is collected,
    # archived, and not drawn.
    def close(r):
        if r["mean_rating"] is not None and 3.5 <= r["mean_rating"] <= 6.5:
            return True
        p = r["market_prob_D"]
        return p is not None and 0.20 <= p <= 0.80
    for r in races:
        r["close"] = close(r)
    shown = [r for r in races if r["close"]]
    return {
        "n_races": len(races),
        "n_rated": sum(1 for r in races if r["n_raters"]),
        "n_market": sum(1 for r in races if r["market_prob_D"] is not None),
        "n_close": len(shown),
        "races": shown,
        "note": ("No class model for governors. A national tide carried through "
                 "partisan lean is a poor description of gubernatorial races, so "
                 "this panel shows only what others measure."),
    }


def build_spread(d: Path, latest: str, proj: dict | None, avgs: list[dict],
                 supp: list[dict], senate: dict | None) -> dict | None:
    """National and per-race, four categories each."""
    cells = _cat_cells(avgs, latest)
    withheld = _withheld_cells(supp, latest)
    projections = (proj or {}).get("projections") or {}

    def cat_cell(race, qty, cat):
        got = cells.get((race, qty, cat))
        if got:
            return got
        if (race, qty, cat) in withheld:
            return {"value": None, "withheld": True}
        return None

    # ---- national: four categories, two chambers, three quantities ----
    national = []
    for cat in CATEGORY_ORDER:
        p = projections.get(cat) or {}
        s, h = p.get("senate") or {}, p.get("house") or {}
        margin = cat_cell(NATL_HOUSE, "margin_D", cat)
        row = {
            "category": cat, "label": CATEGORY_LABEL[cat],
            "ours": cat in projections,
            # Our own models answer from the projection; everyone else answers
            # through the published category average.
            "house_margin": (p.get("tide_D") if cat in projections
                             else (margin or {}).get("value")),
            "house_margin_withheld": bool((margin or {}).get("withheld")),
            "house_seats": h.get("expected_D_seats"),
            "house_seats_80": h.get("D_seats_80pct"),
            "house_prob": h.get("prob_D_majority"),
            "senate_seats": s.get("expected_D_total"),
            "senate_seats_80": s.get("D_total_80pct"),
            "senate_prob": s.get("prob_D_51_plus"),
            "n_sources": (margin or {}).get("n"),
            "sole_source": (margin or {}).get("sole_source", ""),
        }
        if cat not in projections:
            for key, (race, qty) in (
                ("house_seats", (NATL_HOUSE, "seats_D")),
                ("house_prob", (NATL_HOUSE, "win_prob_D")),
                ("senate_seats", (NATL_SENATE, "seats_D")),
                ("senate_prob", (NATL_SENATE, "win_prob_D")),
            ):
                got = cat_cell(race, qty, cat)
                row[key] = (got or {}).get("value")
                row[key + "_withheld"] = bool((got or {}).get("withheld"))
                if got and got.get("sole_source"):
                    row["sole_source"] = got["sole_source"]
                if got and got.get("n"):
                    row["n_sources"] = row["n_sources"] or got["n"]
        if any(row.get(k) is not None for k in
               ("house_margin", "house_seats", "house_prob",
                "senate_seats", "senate_prob")):
            national.append(row)

    # ---- per race: the Senate, one row per seat ----
    races = []
    for r in (senate or {}).get("races", []):
        st, rid = r["state"], f"SEN_{r['state']}_2026"
        entry = {"state": st, "race_id": rid,
                 "competitive": r.get("competitive", False), "cats": {}}
        for cat in CATEGORY_ORDER:
            p = projections.get(cat)
            if p is not None:
                got = (p.get("races") or {}).get(st)
                if got:
                    entry["cats"][cat] = {"margin": got["expected_margin_D"],
                                          "prob": got["win_prob_D"],
                                          "withheld": False, "n": 1}
                continue
            m = cat_cell(rid, "margin_D", cat)
            w = cat_cell(rid, "win_prob_D", cat)
            if m or w:
                entry["cats"][cat] = {
                    "margin": (m or {}).get("value"),
                    "prob": (w or {}).get("value"),
                    "withheld": bool((m or {}).get("withheld")
                                     or (w or {}).get("withheld")),
                    "n": (m or w or {}).get("n"),
                }
        have = [c for c, v in entry["cats"].items() if v.get("prob") is not None]
        entry["n_cats"] = len(have)
        if len(have) >= 2:
            ps = [entry["cats"][c]["prob"] for c in have]
            entry["prob_spread"] = round(max(ps) - min(ps), 4)
        else:
            entry["prob_spread"] = None
        races.append(entry)

    covered = sorted({c for r in races for c in r["cats"]},
                     key=CATEGORY_ORDER.index)
    missing = [c for c in CATEGORY_ORDER if c not in covered]
    return {
        "national": national,
        "races": races,
        "categories": CATEGORY_ORDER,
        "labels": CATEGORY_LABEL,
        "categories_per_race": covered,
        "categories_missing_per_race": missing,
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
    proj = json.loads((d / "seat_projections.json").read_text()) \
           if (d / "seat_projections.json").exists() else None

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

    # Accumulate the timeline and lay out every panel. This is the only part of
    # publish.py that WRITES to derived/ rather than only reading it, because
    # the history has to survive tomorrow's overwrite of the model files.
    #
    # The old expert_ratings_panel is gone. It pivoted 1,836 rows into a
    # thirty-row table of comma-separated labels, and the ratings are now drawn
    # as a spread instead — charts.build_ratings_spread. Same data, and the
    # count has since grown to 4,206 rows across twelve raters, which is well
    # past what any table was going to carry.
    chart_data = charts.build(d, latest)
    chart_data["ladders"] = build_ladders(senate, proj, avgs, latest)
    spread = build_spread(d, latest, proj, avgs, supp, senate)
    governors = build_governors(latest, avgs, supp, ratings)
    model_index = build_model_index(d, latest)

    out = {
        "cycle": a.cycle,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "latest_snapshot": latest,
        "snapshot_count": len(dates),
        "headline": sorted(headline, key=lambda x: x["category"]),
        "series": {k: sorted(v) for k, v in series.items()},
        "fundamentals_model": model,
        "polling_model": senate,
        # Without the district arrays. All 435 of them tripled the size of a
        # file every visitor downloads, to feed a chart that draws 45 — and the
        # full set is already published in derived/seat_projections.json for
        # anyone who wants it.
        "seat_projections": (None if not proj else {
            **proj,
            "projections": {k: {kk: vv for kk, vv in v.items() if kk != "districts"}
                            for k, v in (proj.get("projections") or {}).items()},
        }),
        "charts": chart_data,
        "spread": spread,
        "governors": governors,
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
