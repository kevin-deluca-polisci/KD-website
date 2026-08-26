#!/usr/bin/env python3
"""
Stage 3b — the approval panel: three numbers that disagree, kept apart.

    python3 forecast/model/approval.py --cycle 2026

WHY THIS FILE EXISTS. Approval is now the largest live input to two models, and
the number those models are fed sits several points below every published
approval tracker. That gap is real, it is explainable, and left unexplained it
reads as an arithmetic error on our part. So rather than publish one number and
argue about it, this writes all three with a label on each.

    MODEL INPUT     the mean of ADULTS-SAMPLE polls.
                    Gallup interviewed adults for all twenty midterms the
                    coefficients were fit on, so this is the instrument, not a
                    preference. Reconstructible back to January 2025 from the
                    poll-level file, which is why the backfill can use it.

    WHOLE FIELD     the mean of every poll regardless of population.
                    The same polls, none excluded. Sits 3 to 5 points higher
                    because likely-voter screens currently run about eight
                    points better for this president than adult samples.

    AGGREGATE       the mean of the published approval aggregators.
                    A family average in the same sense as every other line on
                    this site: not our arithmetic, not a pick, the consensus of
                    the people who do this for a living.

THE ONE TEST THAT DECIDES WHICH ONE THE MODELS EAT. Gallup published twelve
readings before it stopped on 2025-12-15, so each construction can be scored on
how well it reproduces an actual Gallup number:

    adults average      MAE 2.07   bias -1.82
    Silver-adjusted     MAE 4.02   bias -4.02
    whole field, raw    MAE 5.22   bias -5.22

Their standard deviations are all about 1.9; the difference is almost entirely
BIAS. So feeding a model fit on Gallup a whole-field number is not a defensible
alternative choice, it is a known 5-point offset in a known direction, worth
about 1.4 points of D margin every day. That is the whole argument, and the
numbers above are re-derived by collect/sb_approval.py's self-test rather than
being taken on trust here.

WHY THE AGGREGATE CANNOT BE THE MODEL INPUT EITHER, even though it is the
number most readers will recognise: every aggregator publishes a whole-field
figure, so it carries the same offset. And it has no history. An aggregator's
average is overwritten in place, so the only date we can honestly attach to one
is the day we read it; this series starts at the first capture and grows
forward, while the model needs a value for every date back to January 2025.

Publication: the aggregate is `individual` per source (CC BY-SA, attributed);
the two poll-derived series are aggregate_only arithmetic over a licensed poll
list and are published as means, never as rows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"
sys.path.insert(0, str(REPO / "forecast" / "collect"))

import sb_approval as sb                                    # noqa: E402
import wiki_approval as wa                                  # noqa: E402

# One point a week on the poll-derived series. The underlying windows are 14
# days wide and move about 0.19 points a day, so a daily series would be 540
# points describing the same curve a weekly one draws in 78.
STEP_DAYS = 7
FIRST = "2025-03-01"     # the first date the adults window clears MIN_N


def series(cycle: int, basis: str, first: str = FIRST,
           last: str | None = None) -> list[dict]:
    polls = sb.load_history(cycle)
    if not polls:
        return []
    end = dt.date.fromisoformat(last or dt.date.today().isoformat())
    cur = dt.date.fromisoformat(first)
    out = []
    while cur <= end:
        got = sb.approval_on(polls, cur.isoformat(), basis=basis)
        if got:
            out.append({"date": cur.isoformat(), "approve": got[0], "n": got[2]})
        cur += dt.timedelta(days=STEP_DAYS)
    if out and out[-1]["date"] != end.isoformat():
        got = sb.approval_on(polls, end.isoformat(), basis=basis)
        if got:
            out.append({"date": end.isoformat(), "approve": got[0], "n": got[2]})
    return out


def aggregate_series(cycle: int) -> list[dict]:
    """One point per capture, with the spread across members.

    THE SPREAD IS THE POINT, not decoration. On the first capture the ten
    members ran from 35.0 to 39.5 — a four-and-a-half point spread among people
    all trying to measure the same thing on the same day. A reader who knows
    that will not read a one-point move on any single tracker as news.
    """
    out = []
    for day, rows in sorted(wa.aggregator_history(cycle).items()):
        vals = sorted(rows.values())
        out.append({
            "date": day,
            "approve": round(statistics.fmean(vals), 2),
            "n": len(vals),
            "low": vals[0], "high": vals[-1],
            "members": {k: rows[k] for k in sorted(rows)},
        })
    return out


# ---------------------------------------------------------------------------
# A chart payload, built here rather than in the template
# ---------------------------------------------------------------------------
# collect/charts.py already owns the timeline panels, and this is deliberately
# NOT one of them: those are forecasts, scored and averaged and governed by the
# disclosure floor, and approval is a right-hand-side variable. Putting it
# through that machinery would make it look like a forecast on every page that
# iterates the panel list.
#
# So this emits its own small payload in the same SHAPE charts.py uses — a
# polyline string and tick positions — because the template already knows how
# to draw that, and a second drawing convention on one site is a second thing
# to keep in sync.
CHART_W, CHART_H = 640.0, 190.0
PAD_L, PAD_R, PAD_T, PAD_B = 34.0, 8.0, 10.0, 20.0


def _chart(sers: dict) -> dict | None:
    pts = [p for v in sers.values() for p in v["points"]]
    if len(pts) < 2:
        return None
    ds = sorted({p["date"] for p in pts})
    d0 = dt.date.fromisoformat(ds[0])
    span = max((dt.date.fromisoformat(ds[-1]) - d0).days, 1)
    lo = min(p["approve"] for p in pts) - 1
    hi = max(p["approve"] for p in pts) + 1

    def X(iso):
        f = (dt.date.fromisoformat(iso) - d0).days / span
        return round(PAD_L + f * (CHART_W - PAD_L - PAD_R), 1)

    def Y(v):
        f = (v - lo) / (hi - lo)
        return round(CHART_H - PAD_B - f * (CHART_H - PAD_T - PAD_B), 1)

    lines = {}
    for k, v in sers.items():
        # Series that are a scatter rather than a trend. Gallup published
        # monthly, so its twelve readings are twelve points and joining them
        # would draw a trend it never claimed.
        if v.get("marks"):
            lines[k] = {"marks": [{"x": X(p["date"]), "y": Y(p["approve"]),
                                   "v": p["approve"]} for p in v["points"]],
                        "label": v["label"]}
            continue
        if len(v["points"]) < 2:
            # A one-point series is a dot, not a line, and a polyline of one
            # point renders as nothing at all rather than as an error.
            if v["points"]:
                p0 = v["points"][0]
                lines[k] = {"dot": {"x": X(p0["date"]), "y": Y(p0["approve"]),
                                    "v": p0["approve"]}, "label": v["label"]}
            continue
        lines[k] = {
            "polyline": " ".join(f'{X(p["date"])},{Y(p["approve"])}'
                                 for p in v["points"]),
            "label": v["label"],
            "end": {"x": X(v["points"][-1]["date"]),
                    "y": Y(v["points"][-1]["approve"]),
                    "v": v["points"][-1]["approve"]},
        }

    yticks = []
    t = int(lo) + (5 - int(lo) % 5)
    while t < hi:
        yticks.append({"v": t, "y": Y(t)})
        t += 5
    xticks = []
    y0 = d0.year
    for yr in range(y0, dt.date.fromisoformat(ds[-1]).year + 1):
        for m in (1, 4, 7, 10):
            iso = f"{yr}-{m:02d}-01"
            if ds[0] <= iso <= ds[-1]:
                xticks.append({"label": dt.date.fromisoformat(iso)
                               .strftime("%b %y"), "x": X(iso)})
    return {"w": CHART_W, "h": CHART_H, "lines": lines,
            "yticks": yticks, "xticks": xticks,
            "y_lo": round(lo, 1), "y_hi": round(hi, 1)}


def populations(cycle: int, asof: str | None = None) -> list[dict]:
    """[{pop, label, approve, n}] over the current window, in screen order.

    THE PAGE NEEDS THESE LIVE. The sentence that explains the whole panel names
    all three figures, and three numbers typed into a template are three
    numbers that go stale without anyone noticing — which is the failure this
    site's own layouts carry a comment warning about.
    """
    polls = sb.load_history(cycle)
    end = asof or dt.date.today().isoformat()
    lo = (dt.date.fromisoformat(end)
          - dt.timedelta(days=sb.WINDOW_DAYS)).isoformat()
    # A LIST, NOT A MAP, because the order is part of the point: adults,
    # registered, likely, which is the order of increasing screen and
    # increasing approval. A Go template ranges a map in key order, so a map
    # would print them A, LV, RV and lose the progression.
    out = []
    for pop, label in (("A", "all adults"), ("RV", "registered voters"),
                       ("LV", "likely voters")):
        v = [p["approve"] for p in polls
             if p["population"] == pop and lo < p["date"] <= end
             and "gallup" not in p["pollster"].lower()]
        if v:
            out.append({"pop": pop, "label": label,
                        "approve": round(statistics.fmean(v), 1), "n": len(v)})
    return out


def build(cycle: int) -> dict:
    model = series(cycle, "adults")
    field = series(cycle, "all_field")
    agg = aggregate_series(cycle)

    # GALLUP IS TWELVE READINGS, NOT A LINE. An earlier version ran it through
    # the same windowed sampler as the others, which held each monthly reading
    # flat for four weeks and then stepped. Every step was the sampler, not
    # Gallup, and on the chart it read as a rendering fault. A monthly poll
    # plots as the points it published.
    gallup = [{"date": q["date"], "approve": q["approve"], "n": 1}
              for q in sorted(
                  (p for p in sb.load_history(cycle)
                   if "gallup" in p["pollster"].lower()),
                  key=lambda p: p["date"])]

    out = {
        "cycle": cycle,
        "generated": dt.date.today().isoformat(),
        "note": "Three constructions of the same week's polls. The models are "
                "fed `model_input` because the coefficients were fitted on a "
                "Gallup series and Gallup interviewed all adults; the other "
                "two are published for comparison and are not inputs.",
        "series": {
            "model_input": {
                "label": "Adults-sample polls (what the models use)",
                "publication": "aggregate_only",
                "source": "Silver Bulletin poll list — our unweighted mean of "
                          "raw published figures, never his adjusted columns",
                "points": model,
            },
            "field": {
                "label": "All polls, every population",
                "publication": "aggregate_only",
                "source": "the same poll list with no population filter",
                "points": field,
            },
            "aggregate": {
                "label": "Average of the published aggregators",
                "publication": "individual",
                "source": "Wikipedia's approval aggregator table (CC BY-SA), "
                          "excluding the row Wikipedia computes itself. Starts "
                          "at the first capture: an aggregator's average is "
                          "overwritten in place, so a past value is only "
                          "recoverable if we were there to read it.",
                "points": agg,
            },
            "gallup": {
                "marks": True,
                "label": "Gallup itself (ended 2025-12-15)",
                "publication": "aggregate_only",
                "source": "the exact instrument the coefficients were fitted "
                          "on, for as long as it existed",
                "points": gallup,
            },
        },
    }
    for k, v in out["series"].items():
        pts = v["points"]
        v["latest"] = pts[-1] if pts else None
    out["populations"] = populations(cycle)
    out["chart"] = _chart(out["series"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args()

    out = build(a.cycle)
    d = DATA / str(a.cycle) / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / "approval.json").write_text(json.dumps(out, indent=2))

    print(f"  wrote forecast/data/{a.cycle}/derived/approval.json")
    for k, v in out["series"].items():
        pts = v["points"]
        if not pts:
            print(f"      {k:12} (no points)")
            continue
        lo = min(p["approve"] for p in pts)
        hi = max(p["approve"] for p in pts)
        print(f"      {k:12} {len(pts):>3} point(s)  {pts[0]['date']} .. "
              f"{pts[-1]['date']}  [{lo:.1f} .. {hi:.1f}]  latest "
              f"{pts[-1]['approve']:.2f}")
    agg = out["series"]["aggregate"]["latest"]
    mi = out["series"]["model_input"]["latest"]
    if agg and mi:
        print(f"\n  today: aggregators {agg['approve']:.1f} across {agg['n']} "
              f"({agg['low']:.1f} to {agg['high']:.1f}), model input "
              f"{mi['approve']:.1f}. The {agg['approve'] - mi['approve']:.1f}"
              f"-point gap is population, not disagreement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
