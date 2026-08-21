#!/usr/bin/env python3
"""
Timeline accumulation and chart geometry for the public page.

TWO JOBS, AND THE FIRST ONE IS THE IMPORTANT ONE.

1. ACCUMULATE. The point of this archive is watching methods move: do
   fundamentals, polling, the professionals and the markets converge as the
   election approaches, or does one of them sit systematically high all year?
   You cannot answer that from a snapshot, and until now nothing kept the
   history.

   category_averages.csv already accumulates by date, so the professional and
   market series have a past. Our OWN models did not: fundamentals_model.json
   and polling_model.json are overwritten on every run, so each day destroyed
   the previous day's answer. Every day that went by without this file was a
   day of the series permanently lost. That is why this exists.

   timeline.csv is therefore append-only and idempotent: one row per
   (date, series), rewritten if the same date is recomputed, never duplicated.

2. LAY OUT. Turn that series into plot coordinates here, in Python, rather
   than doing arithmetic in Go templates. The template should place elements,
   not compute scales.

COLOUR IS NOT DECORATION HERE. The four hues are assigned per ENTITY and fixed
for the life of the project, so "professional" is the same amber in the margin
panel and the probability panel. Rotating colour by position in the chart would
mean a series changed colour when another was added, which is the fastest way
to make a time series lie. The set was validated for colour-vision deficiency
(worst adjacent pair ΔE 16.2 protan) rather than chosen by eye. Amber and
magenta fall below 3:1 against the light surface, which is why every series is
directly labelled and a table view ships alongside.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

TIMELINE_FIELDS = ["snapshot_date", "series", "panel", "unit",
                   "value", "low", "high", "n_sources", "label"]

# entity -> (light, dark). Fixed. Never reassign by position.
COLORS = {
    "fundamentals": ("#4a3aa7", "#9085e9"),
    "polling":      ("#008300", "#199e70"),
    "professional": ("#eda100", "#c98500"),
    "market":       ("#e87ba4", "#d55181"),
}
# Labels name the METHOD, not who built it. A chart comparing four ways of
# forecasting the same number should put them on equal footing; tagging two of
# them "(class model)" in the legend made the axis look like it was about
# provenance rather than method.
LABELS = {
    "fundamentals": "Fundamentals",
    "polling":      "Polling",
    "professional": "Professional",
    "market":       "Markets",
}
ORDER = ["fundamentals", "polling", "professional", "market"]


def _rd(p: Path) -> list[dict]:
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def collect_today(derived: Path, snapshot: str) -> list[dict]:
    """Everything we can say about today, one row per series."""
    out = []

    fm = derived / "fundamentals_model.json"
    if fm.exists():
        m = json.loads(fm.read_text())
        if m.get("margin_D") is not None:
            out.append(dict(snapshot_date=snapshot, series="fundamentals",
                            panel="margin", unit="pct", value=m["margin_D"],
                            low=m.get("margin_D_80_low"), high=m.get("margin_D_80_high"),
                            n_sources=1, label=LABELS["fundamentals"]))

    pm = derived / "polling_model.json"
    if pm.exists():
        m = json.loads(pm.read_text())
        if m.get("election_day_tide_D") is not None:
            out.append(dict(snapshot_date=snapshot, series="polling",
                            panel="margin", unit="pct", value=m["election_day_tide_D"],
                            low="", high="", n_sources=1, label=LABELS["polling"]))

    # Professional and market come from the published averages, which already
    # carry history — so these rows can be rebuilt for past dates too.
    for r in _rd(derived / "category_averages.csv"):
        if r["race_id"] != "NATL_HOUSE_2026":
            continue
        cat, q, v = r["category"], r["quantity"], _f(r["mean"])
        if v is None:
            continue
        if cat == "professional" and q == "margin_D":
            out.append(dict(snapshot_date=r["snapshot_date"], series="professional",
                            panel="margin", unit="pct", value=round(v, 3),
                            low=_f(r.get("min")) or "", high=_f(r.get("max")) or "",
                            n_sources=r.get("n_sources", 1), label=LABELS["professional"]))
        elif q == "win_prob_D" and cat in ("professional", "market"):
            out.append(dict(snapshot_date=r["snapshot_date"], series=cat,
                            panel="prob", unit="prob", value=round(v, 4),
                            low="", high="", n_sources=r.get("n_sources", 1),
                            label=LABELS[cat]))
    return out


def update_timeline(derived: Path, snapshot: str) -> list[dict]:
    """Merge today's rows into timeline.csv. Idempotent on (date, series, panel)."""
    path = derived / "timeline.csv"
    rows = {(r["snapshot_date"], r["series"], r["panel"]): r for r in _rd(path)}
    for r in collect_today(derived, snapshot):
        rows[(r["snapshot_date"], r["series"], r["panel"])] = r
    ordered = sorted(rows.values(), key=lambda r: (r["panel"], r["snapshot_date"],
                                                   ORDER.index(r["series"])
                                                   if r["series"] in ORDER else 9))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TIMELINE_FIELDS)
        w.writeheader()
        for r in ordered:
            w.writerow({k: r.get(k, "") for k in TIMELINE_FIELDS})
    return ordered


# --------------------------------------------------------------------------
# Geometry. Output is in a 0-100 x 0-100 box; the template scales it.
# --------------------------------------------------------------------------

def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** (len(str(int(abs(raw)))) - 1) if abs(raw) >= 1 else 0.1
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    start = (int(lo / step)) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5:
        if v >= lo - step * 0.5:
            ticks.append(round(v, 4))
        v += step
    return ticks


def build_panel(rows: list[dict], panel: str) -> dict | None:
    rs = [r for r in rows if r["panel"] == panel]
    if not rs:
        return None
    dates = sorted({r["snapshot_date"] for r in rs})
    vals = [_f(r["value"]) for r in rs if _f(r["value"]) is not None]
    # Only the LAST point's interval widens the axis, because only the last
    # point's interval is drawn. Letting every historical band into the range
    # calculation blew the axis out to EVEN..D+20 for data that lived between
    # D+5 and D+13, squashing the entire story into the middle third of the
    # plot. Found by rendering 74 simulated days and looking at it.
    bands = []
    for key in {r["series"] for r in rs}:
        pts = sorted((r for r in rs if r["series"] == key), key=lambda r: r["snapshot_date"])
        for k in ("low", "high"):
            v = _f(pts[-1].get(k))
            if v is not None:
                bands.append(v)
    lo, hi = min(vals + bands), max(vals + bands)
    pad = max((hi - lo) * 0.18, 0.5 if panel == "margin" else 0.04)
    lo, hi = lo - pad, hi + pad
    ticks = _nice_ticks(lo, hi)
    if ticks:
        lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])

    def X(d):
        # A single date sits in the middle rather than at x=0, where it would
        # look like a truncated chart instead of a young one.
        return 50.0 if len(dates) == 1 else dates.index(d) / (len(dates) - 1) * 100
    def Y(v):
        return 100 - (v - lo) / (hi - lo) * 100

    series = []
    for key in ORDER:
        pts = sorted((r for r in rs if r["series"] == key),
                     key=lambda r: r["snapshot_date"])
        if not pts:
            continue
        coords = [{"x": round(X(r["snapshot_date"]), 2),
                   "y": round(Y(_f(r["value"])), 2),
                   "date": r["snapshot_date"], "v": _f(r["value"])}
                  for r in pts if _f(r["value"]) is not None]
        if not coords:
            continue
        last = pts[-1]
        band = None
        if _f(last.get("low")) is not None and _f(last.get("high")) is not None:
            band = {"y_low": round(Y(_f(last["low"])), 2),
                    "y_high": round(Y(_f(last["high"])), 2),
                    "low": _f(last["low"]), "high": _f(last["high"])}
        series.append({
            "key": key, "label": LABELS.get(key, key),
            "color": COLORS.get(key, ("#666", "#999"))[0],
            "color_dark": COLORS.get(key, ("#666", "#999"))[1],
            "points": coords,
            "polyline": " ".join(f"{c['x']},{c['y']}" for c in coords),
            "last": coords[-1], "band": band,
            "n_sources": last.get("n_sources", 1),
        })
    # Direct-label de-collision.
    #
    # Two series can sit on top of each other, and here they actually do: the
    # class polling model says D+5.67 and the professionals say D+5.73. That is
    # a real and interesting agreement, but drawn naively the second label lands
    # exactly on the first and one of the two numbers silently disappears — the
    # reader sees two series in the legend and one value on the chart.
    #
    # So push labels apart vertically until they clear each other, keeping their
    # order. The MARKERS stay where the data is; only the text moves.
    gap = 8.0 if panel == "margin" else 13.0
    for s_ in sorted(series, key=lambda x: x["last"]["y"]):
        s_["label_y"] = s_["last"]["y"]
    ordered_lbl = sorted(series, key=lambda x: x["label_y"])
    for i in range(1, len(ordered_lbl)):
        prev, cur = ordered_lbl[i - 1], ordered_lbl[i]
        if cur["label_y"] - prev["label_y"] < gap:
            cur["label_y"] = prev["label_y"] + gap
    for s_ in series:
        s_["label_nudged"] = abs(s_["label_y"] - s_["last"]["y"]) > 0.5

    # Above ~20 snapshots a marker per point stops being a marker and becomes a
    # dot cloud that hides the line it is supposed to annotate. Past that the
    # line carries the series and only the final point keeps a dot.
    dense = len(dates) > 20

    # Date ticks: ends always, plus a few interior ones once the axis is long
    # enough to need them.
    idxs = [0, len(dates) - 1] if len(dates) < 8 else \
           sorted({0, len(dates) // 3, 2 * len(dates) // 3, len(dates) - 1})
    date_ticks = [{"date": dates[i],
                   "x": round(50.0 if len(dates) == 1 else i / (len(dates) - 1) * 100, 2),
                   "anchor": "start" if i == 0 else ("end" if i == len(dates) - 1 else "middle")}
                  for i in idxs]

    # Horizontal strip layout: the same current values laid out along a shared
    # value axis instead of against time. This is the "where do the methods
    # stand today" view, and it is the one that reads at a glance — a reader
    # sees the spread between methods without having to trace three lines.
    for s_ in series:
        s_["sx"] = round((s_["last"]["v"] - lo) / (hi - lo) * 100, 2)
        if s_["band"]:
            s_["sx_low"] = round((s_["band"]["low"] - lo) / (hi - lo) * 100, 2)
            s_["sx_high"] = round((s_["band"]["high"] - lo) / (hi - lo) * 100, 2)

    return {
        "panel": panel,
        "unit": "pct" if panel == "margin" else "prob",
        "dates": dates, "n_dates": len(dates), "dense": dense,
        "date_ticks": date_ticks,
        "x_ticks": [{"v": t["v"], "x": round((t["v"] - lo) / (hi - lo) * 100, 2),
                     "label": t["label"]} for t in
                    [{"v": t, "label": (f"D+{t:.0f}" if t > 0 else
                       ("EVEN" if abs(t) < 1e-9 else f"R+{abs(t):.0f}"))
                       if panel == "margin" else f"{t*100:.0f}%"} for t in ticks]],
        "y_ticks": [{"v": t, "y": round(Y(t), 2),
                     "label": (f"D+{t:.0f}" if t > 0 else ("EVEN" if abs(t) < 1e-9
                               else f"R+{abs(t):.0f}")) if panel == "margin"
                              else f"{t*100:.0f}%"}
                    for t in ticks],
        "zero_y": round(Y(0.0), 2) if panel == "margin" and lo < 0 < hi else None,
        "half_y": round(Y(0.5), 2) if panel == "prob" and lo < 0.5 < hi else None,
        "series": series,
    }


# --------------------------------------------------------------------------
# The seat ladder: which race is the fiftieth Democratic seat.
# --------------------------------------------------------------------------

# Margins are clipped for display. The Senate's expected margins span D+34 to
# R+41, and on a scale that wide the entire competitive band — every race that
# actually decides control — collapses into a few pixels around zero. Clipping
# at ±20 keeps 22 of 35 races at true length and costs nothing, because the
# question "how safe is Wyoming" has no bearing on where the majority lands.
# Clipped bars are drawn with a notch so the chart never implies R+41 and R+23
# are the same number.
LADDER_CLIP = 20.0

# The x-axis is seat number, but it is NOT linear across all hundred seats.
#
# Drawn linearly, the sixty-five seats that are not on the ballot eat two thirds
# of the width and the eighteen races that decide anything are squeezed into a
# hundred pixels — too narrow to label, which is how the first version of this
# chart came out: a legible structure with an illegible roster underneath it.
# So the two blocks of holdover seats are compressed to a fixed cap at each end,
# marked with a break, and the thirty-five contested seats get the rest. The
# fifty- and fifty-one-seat lines are still drawn in the right place relative to
# the seats, which is the only thing the axis has to get right.
LADDER_CAP = 8.0        # display units given to each block of holdover seats


def build_ladder(polling: dict | None) -> dict | None:
    """Seats ordered from most-Democratic to most-Republican, with the seats
    that decide control marked.

    This is deliberately the DETERMINISTIC picture — every race to its expected
    winner — and not a simulation. The point it makes is structural: control
    does not turn on the average race, it turns on one specific race in the
    middle of the order, and naming that race is more useful to a reader than
    another probability. The probabilistic answer lives beside it as a number.

    Two thresholds, not one. Fifty seats is a tie, and a tie is broken by the
    vice-president, who is a Republican this cycle — so fifty is the line at
    which Democrats stop losing and fifty-one is the line at which they start
    winning. Charts that draw only the fifty-line quietly assume a friendly
    vice-president.
    """
    if not polling or not polling.get("races"):
        return None
    hold_D = polling.get("holdover_D")
    if hold_D is None:
        return None

    up = sorted(polling["races"], key=lambda r: -r["expected_margin_D"])
    n_up = len(up)
    hold_R = 100 - hold_D - n_up
    if hold_R < 0:
        return None

    inner = 100.0 - 2 * LADDER_CAP
    slot = inner / n_up

    def X(k: float) -> float:
        """k counts contested seats from the left, 0 = the block boundary."""
        return LADDER_CAP + k * slot

    seats, tipping = [], {}
    for k, r in enumerate(up, start=1):
        m = r["expected_margin_D"]
        seat_no = hold_D + k             # Democratic seat count if they win this one
        clipped = abs(m) > LADDER_CLIP
        row = {
            "state": r["state"], "margin": round(m, 2),
            "win_prob_D": r["win_prob_D"], "seat_no": seat_no,
            "x": round(X(k - 0.5), 2),   # centred in its own slot
            "y": round(_ladder_y(m), 2),
            "clipped": clipped,
            "competitive": bool(r.get("competitive")),
            "lead": "D" if m > 0 else "R",
        }
        if seat_no in (50, 51):
            row["threshold"] = seat_no
            tipping[seat_no] = {"state": r["state"], "margin": round(m, 2),
                                "win_prob_D": r["win_prob_D"],
                                # How far a uniform national move would have to
                                # carry this one race to put it on the other
                                # side. Zero if it is already there.
                                "swing_needed": round(max(0.0, -m), 2),
                                # The threshold line sits at the RIGHT edge of
                                # this seat's slot: winning it is what takes the
                                # count to fifty.
                                "x": round(X(k), 2)}
        seats.append(row)

    ticks = [t for t in (-20, -10, 0, 10, 20)]
    return {
        "clip": LADDER_CLIP,
        "holdover_D": hold_D, "holdover_R": hold_R, "n_up": n_up,
        "hold_D_x": [0, LADDER_CAP], "hold_R_x": [100 - LADDER_CAP, 100],
        "slot_w": round(slot, 3),
        # Seat-number ticks, so the compressed ends never leave a reader
        # guessing where they are on the chamber.
        "seat_ticks": [{"seat_no": s, "x": round(X(s - hold_D - 0.5), 2)}
                       for s in range(hold_D + 5, hold_D + n_up, 5)],
        "seats": seats,
        "y_zero": round(_ladder_y(0.0), 2),
        "y_ticks": [{"v": t, "y": round(_ladder_y(t), 2),
                     "label": ("EVEN" if t == 0 else
                               (f"D+{t}" if t > 0 else f"R+{abs(t)}")) +
                              ("+" if abs(t) == LADDER_CLIP else "")}
                    for t in ticks],
        "tipping_50": tipping.get(50), "tipping_51": tipping.get(51),
        "prob_D_50_plus": polling.get("prob_D_50_plus"),
    }


def _ladder_y(m: float) -> float:
    m = max(-LADDER_CLIP, min(LADDER_CLIP, m))
    return 50 - (m / LADDER_CLIP) * 50


def build(derived: Path, snapshot: str) -> dict:
    rows = update_timeline(derived, snapshot)
    return {p: build_panel(rows, p) for p in ("margin", "prob")}
