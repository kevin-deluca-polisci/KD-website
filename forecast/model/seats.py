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

The House run publishes district MARGINS and never the district INDEX. Those
are different objects and only the second is someone else's dataset — but be
honest about the gap between them: given the national tide, PVI =
(margin - tide) / 2 exactly, so anyone who wants the index can divide. This is
therefore a licensing judgment about republishing a derived forecast, taken
deliberately on 2026-08-21, and not a mathematical safeguard. An earlier
version of this file claimed the second; it was wrong to.

What the whitelist below still does is real, and it is why it is a whitelist
rather than a blacklist: a field added to house_forecast() later cannot reach
the published tier until someone puts it on the list, which forces the question
to be asked once per field rather than never.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
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
                       "prob_D_218_plus", "pvi_source", "districts")
# Sources that publish a national House margin and leave the seat count to
# whoever wants one. Mapped to the category their forecast belongs to.
# Outside sources that publish a NATIONAL MARGIN and no seat count. Each one's
# margin is pushed through the same partisan lean our own models use, and the
# resulting seat counts are averaged in aggregate.py alongside everyone else's.
#
# WHY THIS IS A SET AND NOT A MAPPING. It used to map source -> category, which
# meant the category was asserted twice: once by the parser that read the row
# and once here. Those two can disagree, and the row is the one that knows —
# Wikipedia's aggregator table stamps `category="polling"` on The Economist's
# generic-ballot average even though the Economist is registered as a
# professional forecaster, because a generic-ballot number IS polling whoever
# publishes it. So the category, the licence tier and the date all travel with
# the row and nothing is restated here.
#
# WHY SILVER BULLETIN IS NOT ON THIS LIST. class_polling's tide is the generic
# ballot carried through, which is to say it is Silver Bulletin's average.
# Adding Silver Bulletin here would push that same number through the seat
# machinery a second time and put one aggregator into the polling seat average
# twice. aggregate.py already refuses the equivalent double-count on the margin
# side; this is the same trap one layer down.
EXTERNAL_TIDE_SOURCES = {
    "fair",
    # Generic-ballot aggregators, read off Wikipedia's aggregator table and
    # attributed to their owners. Each publishes a national margin and stops
    # there, which is exactly Fair's situation.
    "ddhq", "rcp", "race_to_the_wh", "economist",
    "votehub", "fiftyplusone", "split_ticket",
}

# Spelled the same way the parsers spell it.
NATL_HOUSE = "NATL_HOUSE_2026"

HOUSE_MAJORITY = 218

# Must match CARRY_FORWARD_MAX_DAYS in collect/aggregate.py. The two cannot
# import each other across the collect/model split, so the number is written
# twice and cross-referenced in both places. Past this a source is not
# episodic, it is abandoned.
TIDE_MAX_AGE_DAYS = 200


def external_tides(cycle: int, today: str) -> dict[str, dict]:
    """source_id -> {category, margin, as_of, publication} for outside tides.

    THE MOST RECENT PUBLISHED VALUE ANYWHERE IN THE ARCHIVE, not the value in
    today's parsed file.

    This used to read only the rows for the current snapshot, which is correct
    for a source that publishes daily and silently wrong for one that does not.
    Fair posts a mid-term prediction a few times a year and the parser
    back-dates each to its publication day, so his rows live in
    parsed/2026-07-31.csv and nowhere near today's file.

    THE TIER TRAVELS WITH THE TIDE, AND IT HAS TO. Given the national tide a
    seat count inverts straight back to the margin that produced it. Publishing
    a seat projection by name for a source whose licence permits only category
    averages would therefore hand back that source's gated number and walk
    around the disclosure floor entirely. Fair is `individual` and may be
    named; Decision Desk HQ is `aggregate_only` and may not. Carrying the row's
    own publication field is what keeps the floor meaningful.

    Sorted filenames mean a later date overwrites an earlier one, so what
    survives per source is its newest prediction.
    """
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv"))):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if (r.get("source_id") in EXTERNAL_TIDE_SOURCES
                        and r.get("race_id") == NATL_HOUSE
                        and r.get("quantity") == "margin_D"):
                    try:
                        v = float(r["value"])
                    except (TypeError, ValueError):
                        continue
                    out[r["source_id"]] = {
                        "category": r.get("category") or "",
                        "margin": v,
                        "as_of": r["snapshot_date"],
                        "publication": r.get("publication") or "private",
                    }

    fresh = {}
    for sid, t in out.items():
        if not t["category"]:
            print(f"  {sid}: margin row carries no category — skipping rather "
                  f"than guessing which panel it belongs in")
            continue
        age = (dt.date.fromisoformat(today)
               - dt.date.fromisoformat(t["as_of"])).days
        if age > TIDE_MAX_AGE_DAYS:
            print(f"  {sid}: last published {t['as_of']} ({age} days ago) — past "
                  f"{TIDE_MAX_AGE_DAYS}, not projecting a seat count from it")
            continue
        fresh[sid] = t
    return fresh


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
            "pvi_source": house.get("pvi_source"),
        }
        out["districts"] = house.get("districts") or []
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

    # Tides, ONE PER MODEL rather than one per category.
    #
    # Each model's own national margin is pushed through partisan lean
    # separately and the resulting seat counts are averaged afterwards, in
    # aggregate.py, alongside every other forecast in the category. The
    # alternative — average the tides first, then push the average through
    # once — gives a different answer, because the map from national margin to
    # seats is not linear near the majority line: a seat that flips at D+2 and
    # one that flips at D+10 do not average into a seat that flips at D+6.
    # Projecting each model and averaging the projections keeps each model's
    # own answer intact and visible.
    #
    # Every tide is read from a file some earlier step wrote, so this module
    # cannot disagree with what the site says a model predicts. A second
    # implementation of the fundamentals equation here would be a second thing
    # to keep in sync, and it would drift.
    tides: dict[str, tuple[str, float]] = {}   # source_id -> (category, tide)

    fm = d / "fundamentals_model.json"
    if fm.exists():
        m = json.loads(fm.read_text())
        if m.get("margin_D") is not None:
            # Ours. Its tide exists nowhere else as a row, so it is the one
            # projection that also contributes the margin.
            tides["class_fundamentals"] = ("fundamentals", float(m["margin_D"]),
                                           "individual", False)

    pm = d / "polling_model.json"
    if pm.exists():
        m = json.loads(pm.read_text())
        # The NOWCAST, not the election-day projection. The polling line on
        # this site is "what the polls say today, carried through partisan
        # lean"; feeding the shrunk tide here would have made the seat
        # projection a November forecast while the margin beside it was a
        # nowcast, and the two would have disagreed by construction.
        tide_key = ("nowcast_tide_D" if m.get("nowcast_tide_D") is not None
                    else "election_day_tide_D")
        if m.get(tide_key) is not None:
            # Ours, but its tide IS the generic ballot, which reaches the
            # polling margin through the aggregators' own rows. It projects
            # seats and contributes no margin.
            tides["class_polling"] = ("polling", float(m[tide_key]),
                                      "individual", True)

    # Outside models that publish a national margin and nothing else. Fair
    # gives a two-party House vote share and stops there; the seat count that
    # share implies is ours to compute, and computing it the same way we
    # compute our own is the only thing that makes the two comparable. Read
    # from the parsed rows for the snapshot rather than from derived/, because
    # these arrive through capture and parse like any other source.
    tide_as_of: dict[str, str] = {}
    for sid, t in external_tides(a.cycle, date).items():
        # An outside tide's margin is ALREADY a parsed row in its category, so
        # its projection must contribute seats only. Emitting the margin again
        # here would put the same aggregator into the polling mean twice.
        tides[sid] = (t["category"], t["margin"], t["publication"], True)
        tide_as_of[sid] = t["as_of"]
        if t["as_of"] != date:
            print(f"  {sid}: using the margin published {t['as_of']} — its "
                  f"most recent, and there is no newer")

    if not tides:
        print("  no tides available — run fundamentals.py and polling.py first")
        return 1

    print("=" * 68)
    print(f"seat projections · snapshot {date} · sigma {sigma:.2f} · "
          f"holdover D {a.holdover_d}")
    print("=" * 68)

    projections = {}
    for name, (category, tide, tier, margin_elsewhere) in sorted(tides.items()):
        p = project(tide, pvi, states, rows, sigma, a.holdover_d)
        # The category travels WITH the projection. aggregate.py files each
        # one under it, so adding a model is a registry entry plus a line in
        # EXTERNAL_TIDE_SOURCES and nothing downstream has to learn its name.
        p["category"] = category
        # The LICENCE travels with it too. A seat count inverts back to the
        # tide that made it, so a projection built from a gated source's margin
        # is that source's number in another coat and carries its tier.
        p["publication"] = tier
        # Whether this source's margin is already a row in its own right. If it
        # is, this projection contributes seats and no margin, or the same
        # forecaster lands in the category mean twice.
        p["margin_published_elsewhere"] = bool(margin_elsewhere)
        # When this model last spoke. Ours speak today by construction; an
        # external tide may be weeks old, and aggregate.py copies this onto the
        # rows it emits so the seat count carries the same date stamp as the
        # margin it came from.
        p["as_of"] = tide_as_of.get(name, date)
        projections[name] = p
        s, h = p["senate"], p.get("house")
        print(f"\n  {name.upper()}  [{category}]  tide D{tide:+.2f}")
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
        "note": ("District margins are our own forecast; the district index "
                 "they are built from is never published. Given the national "
                 "tide the index is recoverable by division, so that is a "
                 "licensing position rather than a technical one."),
    }
    # TWO FILES, AND THE SPLIT IS THE PRIVACY BOUNDARY FOR THIS STEP.
    #
    # derived/ is committed and published. model_private/ is neither — the
    # daily workflow adds derived/ by an explicit allowlist and never touches
    # model_private/, which is the protection that actually holds.
    #
    # Until the generic-ballot aggregators were projected, every projection
    # came from a source we may name — our two models and Ray Fair — so writing
    # them all to derived/ was correct. It stopped being correct the moment
    # Decision Desk HQ and VoteHub arrived: given the national tide a seat
    # count inverts straight back to the margin that produced it, so publishing
    # DDHQ's projection by name republishes DDHQ's gated margin in another
    # unit, and publishing VoteHub's republishes a source we may not quote at
    # all. aggregate.py drops those rows from the CSVs, which is why this was
    # easy to miss: the CSVs were clean and this file was not.
    #
    # So the published copy carries only `individual` projections, and the full
    # set — which aggregate.py needs, because a category AVERAGE may legally
    # contain gated contributors subject to MIN_N — goes to model_private/.
    priv = DATA / str(a.cycle) / "model_private"
    priv.mkdir(parents=True, exist_ok=True)
    (priv / "seat_projections.json").write_text(json.dumps(out, indent=2))

    named = {k: v for k, v in projections.items()
             if (v.get("publication") or "individual") == "individual"}
    withheld = sorted(set(projections) - set(named))
    p = d / "seat_projections.json"
    p.write_text(json.dumps({**out, "projections": named}, indent=2))
    print(f"\n  wrote {(priv / 'seat_projections.json').relative_to(REPO)}   "
          f"PRIVATE — all {len(projections)} projections, for aggregate.py")
    print(f"  wrote {p.relative_to(REPO)}   PUBLISHABLE — "
          f"{len(named)} nameable projection(s)")
    if withheld:
        print(f"    withheld from the published copy (gated sources, whose "
              f"margin a seat count would give back): {withheld}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
