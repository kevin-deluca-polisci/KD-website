#!/usr/bin/env python3
"""
Seat projections: run the same seat machinery off each national tide we have.

    python3 forecast/model/seats.py

WHY THIS EXISTS

The site shows four ways of forecasting, and until now two of them could only
answer one question. The professionals and the markets publish seat counts and
chamber probabilities; our two class models published a national margin and
nothing else, so half the comparison table was empty. That is not a limitation
of the models — a national tide plus the seat geography gives a seat count for
free. It was a limitation of what we bothered to compute.

So: take each tide we have, push it through the same Senate and House
machinery, and record what falls out.

    fundamentals tide   from fundamentals_model.json   (approval, income, seats)
    polling tide        from polling_model.json        (generic ballot, shrunk)

Everything downstream of the tide is IDENTICAL between the two runs — same
state lean, same sigma, same simulation, same seed. That is deliberate. Any
difference between the fundamentals and polling seat counts is a difference in
the tide and nothing else, which is exactly the comparison the site is for. If
the two runs used different error assumptions the reader would have no way to
tell a disagreement about the country from a disagreement about the machinery.

WHAT IS PUBLISHABLE HERE, AND THE ONE PLACE IT GETS SUBTLE

The Senate run is publishable in full: our state lean is reconstructed from
MEDSL's CC0 returns, so it encodes nobody's proprietary index.

The House run is built from Cook's district PVI, and a published district
margin gives that index back exactly as PVI = (margin - tide) / 2. So district
rows stay private, and this module never returns them.

The AGGREGATES are a different question and the answer is that they are safe.
An expected seat count is a sum over 435 districts; a chamber probability is a
tail of that sum. Neither is invertible — 435 unknowns, one number, published
at one tide per day. Nothing about any individual district's lean can be
recovered from "the model expects 243 seats". The rule that matters is the one
enforced in code below: this module returns counts and probabilities from the
House run and NEVER a per-district anything, so there is no path from what it
writes to what Cook sells.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polling  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# Fields of the House run that may cross into the published tier. Anything not
# on this list stays in model_private/, and the filter is a whitelist rather
# than a blacklist so that a field added to house_forecast() later has to be
# considered before it can be published rather than after.
HOUSE_PUBLIC_FIELDS = ("n_districts", "expected_D_seats", "D_seats_80pct",
                       "prob_D_218_plus")
HOUSE_MAJORITY = 218


def public_house(h: dict | None) -> dict | None:
    if not h or not h.get("ok"):
        return None
    return {k: h[k] for k in HOUSE_PUBLIC_FIELDS if k in h}


def project(tide: float, pvi: dict, states: list, rows: list,
            sigma: float, holdover_D: int) -> dict:
    """One tide in, one full set of seat answers out."""
    sen = polling.senate_forecast(tide, pvi, states, sigma, holdover_D)
    house = public_house(polling.house_forecast(tide, rows, sigma))
    out = {
        "tide_D": round(tide, 3),
        "senate": {
            "n_races": sen["n_races"],
            "expected_D_seats_up": sen["expected_D_seats_up"],
            "D_seats_up_80pct": sen["D_seats_up_80pct"],
            # Total chamber, which is the number a reader actually wants:
            # "49 of 100" rather than "15 of 35".
            "expected_D_total": round(sen["expected_D_seats_up"] + holdover_D, 2),
            "D_total_80pct": [sen["D_seats_up_80pct"][0] + holdover_D,
                              sen["D_seats_up_80pct"][1] + holdover_D],
            "prob_D_50_plus": sen.get("prob_D_50_plus"),
            "prob_D_51_plus": sen.get("prob_D_51_plus"),
        },
        "races": sen["races"],
    }
    if house:
        out["house"] = {
            "n_districts": house["n_districts"],
            "expected_D_seats": house["expected_D_seats"],
            "D_seats_80pct": house["D_seats_80pct"],
            "prob_D_majority": house["prob_D_218_plus"],
            "majority_at": HOUSE_MAJORITY,
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seat projections from each tide.")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--holdover-d", type=int, default=polling.HOLDOVER_D_DEFAULT)
    a = ap.parse_args(argv)

    d = DATA / str(a.cycle) / "derived"
    date, rows = polling.latest_parsed(a.cycle)
    pvi = polling.reconstructed_state_pvi(a.cycle)
    states = polling.senate_states_up(rows)

    cal = polling.calibrate_sigma(a.cycle)
    sigma = cal["sigma_total"] if cal.get("ok") else 9.0

    # Tides. Both are read from files another step already wrote, so this
    # module can never disagree with what the site says those models predict —
    # a second implementation of the fundamentals equation here would be a
    # second thing to keep in sync, and it would drift.
    tides = {}
    fm = d / "fundamentals_model.json"
    if fm.exists():
        m = json.loads(fm.read_text())
        if m.get("margin_D") is not None:
            tides["fundamentals"] = float(m["margin_D"])
    pm = d / "polling_model.json"
    if pm.exists():
        m = json.loads(pm.read_text())
        if m.get("election_day_tide_D") is not None:
            tides["polling"] = float(m["election_day_tide_D"])
    if not tides:
        print("  no tides available — run fundamentals.py and polling.py first")
        return 1

    print("=" * 68)
    print(f"seat projections · snapshot {date} · sigma {sigma:.2f} · "
          f"holdover D {a.holdover_d}")
    print("=" * 68)

    projections = {}
    for name, tide in sorted(tides.items()):
        p = project(tide, pvi, states, rows, sigma, a.holdover_d)
        projections[name] = p
        s, h = p["senate"], p.get("house")
        print(f"\n  {name.upper()}  tide D{tide:+.2f}")
        print(f"      SENATE  {s['expected_D_total']:.1f} of 100 "
              f"(80% {s['D_total_80pct'][0]}-{s['D_total_80pct'][1]})   "
              f"P(50+) {s['prob_D_50_plus']:.3f}   P(51+) {s['prob_D_51_plus']:.3f}")
        if h:
            print(f"      HOUSE   {h['expected_D_seats']:.1f} of {h['n_districts']} "
                  f"(80% {h['D_seats_80pct'][0]}-{h['D_seats_80pct'][1]})   "
                  f"P(majority) {h['prob_D_majority']:.3f}")
        else:
            print("      HOUSE   skipped — no district PVI in the archive")

    out = {
        "snapshot_date": date,
        "sigma": round(sigma, 2),
        "holdover_D": a.holdover_d,
        "majority_at": {"house": HOUSE_MAJORITY, "senate_tie": 50, "senate_majority": 51},
        "projections": projections,
        "publication": "individual",
        "note": ("House figures are chamber aggregates only. District margins "
                 "are built from a licensed index and never leave "
                 "model_private/; a seat count is a sum over 435 districts and "
                 "cannot be inverted back to any of them."),
    }
    p = d / "seat_projections.json"
    p.write_text(json.dumps(out, indent=2))
    print(f"\n  wrote {p.relative_to(REPO)}   PUBLISHABLE (aggregates only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
