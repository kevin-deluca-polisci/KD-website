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
    academic tides      from academic_models.json      (published specifications)
    outside tides       from the parsed rows           (Fair, the aggregators)

Everything downstream of the tide is IDENTICAL between the runs — same
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
import pathlib
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
# `map_vintage` is on this list on purpose. It names which district lines a
# seat count was computed on, and a seat count whose baseline is invisible is
# one nobody can reproduce or argue with. It reveals nothing about the index
# itself — only which of two published maps was in force on a date.
# The sigma fields were added on 2026-08-25 and the whitelist did its job:
# it forced the question rather than letting them through. They are parameters
# of OUR model, contain nothing from any third-party index, and a seat
# probability whose spread is invisible cannot be reproduced or argued with by
# anyone outside. `sigma_source` names its own provenance, including when it
# falls back, which is the field that stops a quiet change of method looking
# like a change of opinion.
HOUSE_PUBLIC_FIELDS = ("n_districts", "expected_D_seats", "D_seats_80pct",
                       "prob_D_218_plus", "pvi_source", "map_vintage",
                       "sigma_source", "sigma_national", "sigma_state",
                       "sigma_district", "sigma_total",
                       # HOW THE BASELINE WAS BUILT. For Cook this is empty --
                       # a PVI is a deviation already. For the DRA composite it
                       # carries the centring constant, and that number is
                       # load-bearing: every district margin is its composite
                       # share minus this, so a projection cannot be
                       # reproduced without it. It also names the six at-large
                       # districts whose value came from our own MEDSL
                       # composite rather than from DRA at all, which is a
                       # provenance fact a reader is entitled to.
                       "baseline_detail",
                       "districts")
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
    # NOTHING PUBLISHED AFTER THE DATE BEING PROJECTED, EVER.
    #
    # "The most recent value anywhere in the archive" was written for the live
    # run, where the newest row is always today's and the sentence is harmless.
    # It is not harmless when a PAST date is projected. Rebuilding 2026-08-21
    # pulled Decision Desk HQ's and RCP's margins from 2026-08-25 and stamped
    # the result 08-21 — four days of hindsight in a file whose entire purpose
    # is to record what was knowable at the time.
    #
    # The staleness check below did not catch it, and could not: it computes
    # `age = today - as_of`, which goes NEGATIVE for a future row, and a
    # negative age passes a "too old?" test comfortably.
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv"))):
        if pathlib.Path(f).stem > today:
            continue
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("snapshot_date", "") > today:
                    continue
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
            sigma: float, holdover_D: int, asof: str | None = None) -> dict:
    """One tide in, one full set of seat answers out.

    `asof` is the date being projected, and it selects the DISTRICT MAP: see
    model/maps.py. The Senate run ignores it because no Senate seat was
    redistricted; only the House baseline moves.
    """
    sen = polling.senate_forecast(tide, pvi, states, sigma, holdover_D)
    house = public_house(polling.house_forecast(tide, rows, sigma, asof=asof))
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
            "map_vintage": house.get("map_vintage"),
            "n_districts": house["n_districts"],
            "expected_D_seats": house["expected_D_seats"],
            "D_seats_80pct": house["D_seats_80pct"],
            "prob_D_majority": house["prob_D_218_plus"],
            "majority_at": HOUSE_MAJORITY,
            "pvi_source": house.get("pvi_source"),
            # THE SPREAD, ALONGSIDE THE POINT ESTIMATE. A seat count and a
            # probability are both functions of sigma, and one published
            # without it cannot be reproduced or argued with from outside.
            # `sigma_source` names its own provenance, including when it falls
            # back to the old Senate-calibrated value -- which is what stops a
            # quiet change of method from reading as a change of opinion.
            #
            # Note this dict is a SECOND narrowing after HOUSE_PUBLIC_FIELDS.
            # Adding a field to the whitelist alone is not enough; it has to be
            # named here too, which is why the sigma fields were missing from
            # the first run after they were whitelisted.
            "sigma_source": house.get("sigma_source"),
            "sigma_national": house.get("sigma_national"),
            "sigma_state": house.get("sigma_state"),
            "sigma_district": house.get("sigma_district"),
            "sigma_total": house.get("sigma_total"),
            "baseline_detail": house.get("baseline_detail") or {},
        }
        out["districts"] = house.get("districts") or []
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seat projections from each tide.")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--holdover-d", type=int, default=polling.HOLDOVER_D_DEFAULT)
    ap.add_argument("--date", default=None,
                    help="rebuild ONE past date from its own parsed rows, "
                         "instead of projecting today. For repairing a day "
                         "whose live run was incomplete. Everything written is "
                         "stamped `retrospective`, because it is today's model "
                         "on that date's evidence rather than what the model "
                         "said at the time.")
    # WINDOWING THE BACKFILL. It is roughly four seconds per model per date,
    # so a full archive is tens of minutes and will not survive a shell that
    # gets closed. These let it be run in slices and resumed: every slice
    # updates the same history file in place, so the pieces add up.
    ap.add_argument("--backfill-from", default=None,
                    help="earliest date to backfill (inclusive)")
    ap.add_argument("--backfill-to", default=None,
                    help="latest date to backfill (inclusive)")
    ap.add_argument("--backfill-limit", type=int, default=None,
                    help="stop after this many dates")
    ap.add_argument("--backfill-history", action="store_true",
                    help="project every date in the model history files "
                         "(academic and polling) rather than only today. The "
                         "real name for this; --backfill-academic is kept as "
                         "an alias so existing workflow files keep working.")
    ap.add_argument("--backfill-academic", action="store_true",
                    help="project every date in model_private/"
                         "academic_models_history.json, not just today. Slow "
                         "(seconds per date per model) and intended as a "
                         "one-off after academic.py --backfill. Only academic "
                         "keys are written; a date's captured projections are "
                         "left alone.")
    a = ap.parse_args(argv)

    d = DATA / str(a.cycle) / "derived"

    # REBUILDING ONE PAST DATE FROM ITS OWN PARSED ROWS.
    #
    # --backfill-history can only reach projections whose tide it can
    # reconstruct, which is the academic and polling-reconstructed families.
    # Everything else was captured live on the day, and if that day's run was
    # incomplete the hole is permanent: 2026-08-21 carries three projections
    # where 08-20 has eleven and 08-22 has twelve, because the code was being
    # changed underneath it.
    #
    # The inputs are not lost. parsed/2026-08-21.csv holds the same sources as
    # its neighbours, so the projections can be rebuilt exactly — same rows,
    # same map vintage for that date, today's model.
    #
    # WHAT THIS COSTS, and it is not nothing. A rebuilt projection is no longer
    # the number the model produced on that date; it is today's model run on
    # that date's evidence. Under score/RULES.md that is `retrospective`, not
    # `captured`, and it is stamped so. Use it to repair a run that failed, not
    # to restate history that merely looks wrong now.
    if a.date:
        # THE DATE GOVERNS THE TIDES. THE BASELINE COMES FROM THE ARCHIVE.
        #
        # The first version of this read the target date's parsed file for
        # everything, which is wrong in a way that is worth keeping written
        # down. A district partisan index is a STATIC ARTEFACT — it is imported
        # once and carries forward, and which lines were in force on a given
        # date is already handled by maps.baseline_asof through `asof`. So
        # 2026-08-21's file predates the DRA import and simply has no baseline
        # in it, and reading it produced a six-district House.
        #
        # What genuinely has to respect the date is the TIDES: a forecast
        # published on 08-25 must not appear in a projection stamped 08-21.
        # That is external_tides' job and it now refuses future rows.
        #
        # So: latest rows for the baseline, target date for everything else.
        # This is the same split --backfill-history has always used.
        date = a.date
        f = DATA / str(a.cycle) / "parsed" / f"{date}.csv"
        if not f.exists():
            raise SystemExit(f"no parsed rows for {date} — nothing to rebuild "
                             f"from. Looked for {f}")
        _latest, rows = polling.latest_parsed(a.cycle)
        print(f"  REBUILDING {date}. Tides as of that date; district baseline "
              f"from the archive ({_latest}), with the map vintage selected for "
              f"{date}. Everything written is stamped `retrospective`.")
    else:
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

    # ACADEMIC MODELS. Published specifications we reimplemented, run on our
    # own inputs — see model/academic.py for why these are a family of their
    # own rather than more fundamentals.
    #
    # They enter here rather than through external_tides() because they are not
    # captured: nobody fetched them, there is no raw byte behind them, and no
    # parsed row carries their margin. They are computed here on the day, the
    # same as our own two models, so they take the same route our own two
    # models take.
    #
    # margin_elsewhere is False. An academic model's tide exists nowhere else
    # as a row, so — unlike an aggregator read off Wikipedia — its margin has
    # to be contributed here or it never appears at all.
    #
    # Tier is read from the file rather than asserted, for the same reason the
    # category is: if a model is ever added whose licence is not ours to give,
    # this loop must not be the thing that quietly publishes it.
    acad_categories: dict[str, list] = {}
    am = d / "academic_models.json"
    if am.exists():
        acad = json.loads(am.read_text())
        for key, m in (acad.get("models") or {}).items():
            if m.get("margin_D") is None:
                continue
            cats = m.get("categories") or [m.get("category") or "academic"]
            # The tide carries the PRIMARY family, which is what the tuple has
            # always held; the full list travels separately and is stamped onto
            # the projection below. A model may belong to more than one family
            # — see the note at the top of academic.py — and the category
            # averages honour every membership while the across-family average
            # uses only the first.
            acad_categories[key] = cats
            tides[key] = (cats[0], float(m["margin_D"]),
                          m.get("publication") or "individual", False)
        skipped = acad.get("not_implemented") or {}
        if skipped:
            print(f"  academic: {len(skipped)} model(s) declared but not run "
                  f"({', '.join(sorted(skipped))}) — see academic_models.json "
                  f"for what each one is missing")

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
        p = project(tide, pvi, states, rows, sigma, a.holdover_d, asof=date)
        # The category travels WITH the projection. aggregate.py files each
        # one under it, so adding a model is a registry entry plus a line in
        # EXTERNAL_TIDE_SOURCES and nothing downstream has to learn its name.
        p["category"] = category
        # Every family this model belongs to, primary first. Defaults to the
        # single category so nothing outside academic/ has to change.
        p["categories"] = acad_categories.get(name, [category])
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
        # A rebuilt day is not a captured one. See the note in main().
        **({"provenance": "retrospective",
            "rebuilt_at": dt.date.today().isoformat()} if a.date else {}),
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

    # AND AN APPEND-ONLY HISTORY, FOR THE SAME REASON collect/charts.py KEEPS
    # ONE.
    #
    # This file holds today's projections and only today's. aggregate.py reads
    # it and stamps every row with the one snapshot_date inside — so on a full
    # rebuild, every class-model and external-tide row lands on the newest day
    # and vanishes from all the earlier ones. The archive could therefore never
    # accumulate a history of our own models' seat counts: each run quietly
    # took them off yesterday and put them on today. It is visible in the
    # guard's own output, which reported 2026-08-19 "losing categories
    # fundamentals, polling" on a day when nothing had gone missing at all —
    # those categories had no other contributor on that date, so moving the
    # projections forward emptied them.
    #
    # A projection cannot be recomputed for a past date: polling_model.json and
    # fundamentals_model.json are overwritten every run, so the inputs that
    # produced Tuesday's number are gone by Wednesday. The only way to keep it
    # is to write it down on the day, which is what this does. Idempotent on
    # the snapshot date, so re-running a day replaces it rather than duplicating.
    hist_p = priv / "seat_projections_history.json"
    try:
        hist = json.loads(hist_p.read_text()) if hist_p.exists() else {}
    except json.JSONDecodeError:
        hist = {}
    hist[date] = out

    # ---- academic backfill --------------------------------------------------
    #
    # THE ONE EXCEPTION to "a projection cannot be recomputed for a past date",
    # and it is worth being precise about why, because the paragraph above is
    # otherwise a rule this block appears to break.
    #
    # It cannot be recomputed for OUR models because their inputs are gone:
    # polling_model.json and fundamentals_model.json are overwritten every run.
    # The academic models have no such problem. BEW's only moving input is the
    # generic ballot, and every archived date's generic ballot is still sitting
    # in parsed/<date>.csv. academic.py --backfill reads those files and writes
    # the resulting tides to academic_models_history.json; this loop pushes each
    # one through the same seat machinery as everything else.
    #
    # WHAT IS ASSUMED, and it is not nothing: the district baselines and the
    # holdover Senate seats are TODAY'S, applied to a past tide. Those do not
    # move during a cycle — the baselines come from the 2024 returns and the
    # holdovers are fixed by which classes are up — so the assumption is sound
    # for 2026 and would NOT be sound across a redistricting. Each backfilled
    # projection is stamped so the page can say which is which.
    #
    # Deliberately behind a flag. It is roughly four seconds per date per model
    # at N_SIMS=20000, so a full archive is tens of minutes — fine as a one-off,
    # wrong as part of the daily run. Existing dates are updated in place rather
    # than duplicated, and a date's own captured projections are preserved:
    # only the academic keys are touched.
    if a.backfill_academic or a.backfill_history:
        # TWO HISTORIES, ONE LOOP. academic.py and polling.py each reconstruct
        # their own tides from the poll record and write a history file; both
        # are projected here through the same seat machinery as everything
        # else, so a backfilled date is built exactly like a live one.
        ah: dict = {}
        ah_p = priv / "academic_models_history.json"
        if ah_p.exists():
            for d0, day in json.loads(ah_p.read_text()).items():
                ah.setdefault(d0, {}).update(day.get("models") or {})

        ph_p = priv / "polling_model_history.json"
        if ph_p.exists():
            for d0, m in json.loads(ph_p.read_text()).items():
                t = m.get("nowcast_tide_D")
                if t is None:
                    continue
                # Same shape academic.py writes, so the loop below needs no
                # branch. class_polling contributes seats only: its tide IS the
                # generic ballot, which reaches the margin panel through the
                # aggregators' own rows, and emitting it again here would put
                # one average into the polling mean twice.
                # ITS OWN SOURCE ID, not class_polling.
                #
                # This is our generic-ballot average, reconstructed from
                # Silver's poll-level file by an unweighted 21-day mean of raw
                # poll margins. class_polling is a different number: it takes
                # Silver's own house-effect-adjusted average and carries it
                # through. Same polls, different recipes, so they are two
                # aggregates and filing them under one id was wrong.
                #
                # AND IT CONTRIBUTES ITS MARGIN. class_polling does not, because
                # its tide IS Silver's published average and Silver is already
                # one of the aggregators on the margin panel — emitting it would
                # count him twice. That objection does not apply here: this is
                # our arithmetic on the poll list, it differs from his average
                # by a measurable amount, and on every date before the capture
                # began it is the only polling evidence that exists at all.
                # Withholding it is what left the polling margin line with five
                # points against fifty-two on the seats panel.
                ah.setdefault(d0, {})["polling_reconstructed"] = {
                    "margin_D": t, "category": "polling",
                    "categories": ["polling"], "publication": "individual",
                    "provenance": m.get("provenance") or "backfilled",
                    "margin_published_elsewhere": False,
                }

        # OUR OWN FUNDAMENTALS MODEL, which the comment above named as one of
        # the things that could NOT be backfilled: "polling_model.json and
        # fundamentals_model.json are overwritten every run". That was a fact
        # about the files and never about the model. All three of its inputs
        # are datable now — approval from the poll list by each poll's own end
        # date, income from an ALFRED vintage or a released-months truncation,
        # and seats_before is 220 either way — so fundamentals.py --backfill
        # writes a history exactly like academic.py's and it lands here.
        #
        # IT CONTRIBUTES ITS MARGIN, unlike class_polling, for the same reason
        # the live path does: this tide exists nowhere else as a row, so
        # emitting it counts nothing twice.
        fh_p = priv / "fundamentals_model_history.json"
        if fh_p.exists():
            for d0, m in json.loads(fh_p.read_text()).items():
                if m.get("margin_D") is None:
                    continue
                ah.setdefault(d0, {})["class_fundamentals"] = {
                    "margin_D": float(m["margin_D"]),
                    "margin_D_80_low": m.get("margin_D_80_low"),
                    "margin_D_80_high": m.get("margin_D_80_high"),
                    "category": "fundamentals", "categories": ["fundamentals"],
                    "publication": "individual",
                    "provenance": m.get("provenance") or "backfilled",
                    "inputs": m.get("inputs") or {},
                    "margin_published_elsewhere": False,
                }

        if not ah:
            print("  --backfill-history: no history files — run "
                  "academic.py --backfill and/or polling.py --backfill first")
        else:
            ah = {d0: {"models": mm} for d0, mm in ah.items()}
            todo = sorted(d0 for d0 in ah if d0 != date)
            if a.backfill_from:
                todo = [d0 for d0 in todo if d0 >= a.backfill_from]
            if a.backfill_to:
                todo = [d0 for d0 in todo if d0 <= a.backfill_to]
            if a.backfill_limit:
                todo = todo[:a.backfill_limit]
            print(f"\n  backfilling academic projections for {len(todo)} "
                  f"date(s) — about {len(todo) * 4}s, one Monte Carlo per "
                  f"model per date")
            filled = 0
            for i, d0 in enumerate(todo, 1):
                day = hist.get(d0) or {"snapshot_date": d0, "projections": {}}
                day.setdefault("projections", {})
                for key, m in (ah[d0].get("models") or {}).items():
                    if m.get("margin_D") is None:
                        continue
                    # THE DATE GOES IN. This loop re-projected every past
                    # date onto today's lines, which is the whole bug: a
                    # March 2025 tide was being turned into seats using
                    # districts that did not exist until August.
                    p0 = project(float(m["margin_D"]), pvi, states, rows,
                                 sigma, a.holdover_d, asof=d0)
                    p0["category"] = m.get("category") or "academic"
                    p0["categories"] = (m.get("categories")
                                        or [p0["category"]])
                    p0["publication"] = m.get("publication") or "individual"
                    # Its margin is not a parsed row on that date — nobody
                    # captured it, we computed it — so this projection has to
                    # contribute the margin or the backfilled line has seats
                    # and no tide behind it.
                    p0["margin_published_elsewhere"] = bool(
                        m.get("margin_published_elsewhere", False))
                    p0["as_of"] = d0
                    p0["provenance"] = m.get("provenance") or "backfilled"
                    day["projections"][key] = p0
                    filled += 1
                day["snapshot_date"] = d0
                hist[d0] = day
                if i % 20 == 0 or i == len(todo):
                    print(f"    {i}/{len(todo)} dates")
            print(f"  backfilled {filled} academic projection(s) across "
                  f"{len(todo)} date(s)")

    hist_p.write_text(json.dumps(hist, indent=1, sort_keys=True))
    print(f"  wrote {hist_p.relative_to(REPO)}   PRIVATE — "
          f"{len(hist)} date(s) of projections retained")

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
