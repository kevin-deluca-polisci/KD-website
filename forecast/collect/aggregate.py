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
import datetime as dt
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "forecast" / "data"

MIN_N = 3           # disclosure floor for any average containing a gated source
# How much of a date's published rows may disappear in a re-run before
# aggregate.py refuses to write. Generous on purpose: a parser correction
# removes a handful of cells, a lost day removes almost all of them, and the
# gap between those two is enormous. See would_shrink().
DROP_TOLERANCE = 0.10
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


# Sources whose national margin already reaches its category by another route,
# and which must therefore contribute SEATS here and not a margin.
#
#   class_polling  its tide is the generic ballot unchanged — which is to say
#                  it is Silver Bulletin's average read straight through.
#                  Emitting it would put that aggregator into the polling mean
#                  twice and quietly double its weight.
#   fair           his published vote share is captured and parsed like any
#                  other source, so the margin is already a row. seats.py adds
#                  the seat count that share implies; the margin would be the
#                  same number a second time.
#
# What each genuinely contributes is the seat projection: carrying a national
# margin through partisan lean is work neither an aggregator nor Fair does.
MARGIN_FROM_ELSEWHERE = {"class_polling", "fair"}


# --------------------------------------------------------------------------
# Sources that do not publish every day.
#
# THE BUG THIS FIXES. Ray Fair publishes a mid-term prediction a few times a
# year. The parser back-dates each one to the day he published it, which is the
# right thing to do — an archive that stamped his December forecast with
# today's date would be lying about when it was made. But every step
# downstream then grouped by snapshot_date and asked "who is in the
# fundamentals category TODAY?", and on any day Fair had not published, the
# answer was nobody but us. On 2026-08-21 the fundamentals average read
# D+10.5, n=1, sole source our own model — while Fair's published forecast of
# D+1.8 sat in the same archive, three weeks old and completely invisible.
# The two-member fundamentals category the site was built around had never
# once existed.
#
# WHY CARRYING FORWARD IS HONEST HERE, AND WHERE IT WOULD NOT BE.
# "Fair's forecast on 2026-08-21" is a well-formed question with the answer
# D+1.8: that is his current published prediction, and there is no newer one to
# prefer. Carrying it is a true statement about the world. Contrast a polling
# average that stops updating — there the underlying thing kept moving and the
# source stopped following it, so repeating yesterday's number asserts
# something false. The difference is exactly whether the source was EXPECTED to
# update, which is what `cadence` in the registry records. So cadence decides
# it, and a source that claims `daily` and goes quiet is left visibly missing,
# which is what the staleness detector is for.
#
# Fair's cadence was registered as `daily`, which is how this went unnoticed.
EPISODIC_CADENCES = {"sporadic", "weekly", "monthly"}

# Past this, a source is not episodic, it is abandoned, and repeating its last
# word would be asserting that a forecaster who has said nothing for most of a
# year still stands behind a number. model/seats.py enforces the same cap on
# the same sources for the tide it reads; the two must agree, and neither can
# import the other across the collect/model split.
CARRY_FORWARD_MAX_DAYS = 200


def carry_forward(rows: list[dict], registry: dict) -> list[str]:
    """Fill an episodic source's most recent value onto later snapshot dates.

    Mutates `rows` in place — every row gains `as_of`, the date the value was
    actually published, which equals snapshot_date for everything observed
    normally and is older for anything carried. Returns human-readable notes.

    WHICH DATES GET FILLED. Only dates where the source's own category already
    has a row from somebody else. The archive holds roughly 190 snapshot dates,
    most of them Wikipedia rating revisions back-filled from page history, and
    those are not days on which anyone measured the fundamentals — filling them
    would invent about 180 snapshots that never happened and draw a fundamentals
    line stretching back to January beside a polling line that starts in August.
    A carried value completes an average on a day we actually took one. It does
    not manufacture a day.
    """
    # ENABLED episodic sources only. A source we have switched off has been
    # taken out of the picture deliberately, and repeating its last forecast
    # every day afterwards would quietly undo that decision — the first run
    # carried fiftyplusone forward, which is disabled precisely because we are
    # not collecting it.
    eligible = {s["id"] for s in registry.get("sources", [])
                if s.get("enabled")
                and (s.get("cadence") or "daily") in EPISODIC_CADENCES}

    for r in rows:
        r.setdefault("as_of", r["snapshot_date"])
    if not eligible or not rows:
        return []

    # Dates on which each category was genuinely measured, and by whom.
    cat_dates: dict[str, set] = defaultdict(set)
    for r in rows:
        cat_dates[r["category"]].add((r["snapshot_date"], r["source_id"]))

    # Only quantities that would survive aggregation anyway. Cook's PVI, MEDSL's
    # past results and FRED's income series are reference data the model is fed,
    # not forecasts anyone makes — they are filtered out downstream regardless,
    # and carrying them added a thousand rows that could not reach a single
    # published number. Carrying a value forward is a claim that a forecaster
    # still stands behind it; there is nobody standing behind a past election
    # result.
    skip = NO_AVERAGE | NEVER_PUBLISH | NOT_A_FORECAST

    series: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        if r["source_id"] in eligible and r["quantity"] not in skip:
            key = (r["source_id"], r["category"], r["race_id"], r["chamber"],
                   r["state"], r["district"], r["quantity"], r["unit"])
            series[key][r["snapshot_date"]] = r

    added: list[dict] = []
    stats: dict[str, dict] = defaultdict(lambda: {"rows": 0, "max_age": 0,
                                                  "as_of": ""})
    for key, observed in series.items():
        sid, cat = key[0], key[1]
        # Days this category was measured by somebody other than this source.
        targets = sorted({d for d, s in cat_dates[cat] if s != sid})
        seen = sorted(observed)
        for d in targets:
            if d in observed or d < seen[0]:
                continue
            prior = [x for x in seen if x <= d]
            if not prior:
                continue
            src = observed[prior[-1]]
            age = (dt.date.fromisoformat(d)
                   - dt.date.fromisoformat(src["as_of"])).days
            if age > CARRY_FORWARD_MAX_DAYS:
                continue
            added.append({**src, "snapshot_date": d, "as_of": src["as_of"]})
            st = stats[sid]
            st["rows"] += 1
            if age >= st["max_age"]:
                st["max_age"], st["as_of"] = age, src["as_of"]

    rows.extend(added)
    return [f"{sid}: carried {v['rows']} row(s) forward, oldest "
            f"{v['max_age']} day(s) (published {v['as_of']})"
            for sid, v in sorted(stats.items())]


def class_model_rows(cycle: int) -> list[dict]:
    """Seat projections as ordinary contributors to their own category.

    WHY THIS EXISTS. The class models used to bypass this file entirely:
    publish.py read them straight out of their JSON and pasted them onto the
    page, so "Fundamentals" was our model and nothing else, and a second
    fundamentals model arriving would have sat in a category average NEXT TO
    ours rather than being averaged with it. That is the wrong shape for a page
    whose whole claim is that a category is a way of knowing rather than a
    person. Emitting these as rows makes them contributors like any other: the
    mean, the min/max, the spread and the n all pick them up for free.

    Read from seat_projections.json, which holds every model in one shape — the
    same machinery applied to each tide — so they cannot drift apart here
    through a copy-paste. Each projection carries the CATEGORY it belongs to,
    so this function never has to learn a model's name: seats.py adds a model
    and it appears in the right average.

    Tier is `individual`. Our own models are ours and there is no licence to
    gate; Fair's seat projection is OUR arithmetic on his published share, so
    it is likewise ours to show. That also keeps them out of the MIN_N count,
    which is correct — the floor exists to stop a reader recovering a gated
    forecast by subtraction, and a number published in full subtracts out to
    nothing.

    NOT emitted: `pvi`. It rides along inside each race entry and it is Cook's
    proprietary index. NEVER_PUBLISH would catch it downstream anyway; not
    writing it is the belt to that braces.
    """
    # PREFER THE PRIVATE COPY. seats.py writes the full set to model_private/
    # and a name-safe subset to derived/, because a seat count gives back the
    # margin that made it and not every contributing source may be named. The
    # averages computed here MAY legally contain gated contributors — that is
    # what MIN_N is for — so this step needs all of them, and takes the
    # published copy only as a fallback for a tree where the model step has not
    # run.
    priv = DATA_DIR / str(cycle) / "model_private" / "seat_projections.json"
    pub = DATA_DIR / str(cycle) / "derived" / "seat_projections.json"
    p = priv if priv.exists() else pub
    if not p.exists():
        return []
    proj = json.loads(p.read_text())
    date = proj.get("snapshot_date")
    if not date:
        return []

    rows: list[dict] = []

    # Set per projection, read by emit(). These rows are dated TODAY because
    # the projection was computed today, but the tide behind an external model
    # may be weeks old. seats.py records which, and it travels with the number
    # rather than being re-derived here from the source's name.
    as_of = {"d": date}
    # THE LICENCE COMES FROM THE PROJECTION, NOT FROM THIS FILE.
    #
    # Every projection used to be stamped `individual`, which was true while
    # the only tides were our own two models and Ray Fair, all three of them
    # publishable by name. It stopped being true the moment the generic-ballot
    # aggregators were projected: Decision Desk HQ and RealClearPolling permit
    # the category average and not per-forecaster republication, and a seat
    # count inverts straight back to the margin that produced it. Stamping
    # those `individual` would have published a gated forecaster's number in
    # another unit and left MIN_N counting zero gated contributors on a cell
    # that was made entirely of them.
    tier = {"p": "individual"}

    def emit(source_id, cat, race_id, chamber, state, district, quantity,
             value, unit):
        if value is None:
            return
        rows.append({
            "snapshot_date": date, "source_id": source_id,
            "category": cat,
            "race_id": race_id, "chamber": chamber, "state": state,
            "district": district, "quantity": quantity,
            "value": float(value), "unit": unit,
            "publication": tier["p"],
            "as_of": as_of["d"],
            "captured_at": "", "raw_sha256": "", "raw_path": "",
        })

    for source_id, model in (proj.get("projections") or {}).items():
        cat = model.get("category")
        if not cat:
            continue
        as_of["d"] = model.get("as_of") or date
        tier["p"] = model.get("publication") or "individual"
        senate, house = model.get("senate") or {}, model.get("house") or {}
        # Whether this model's margin is already a row in its own right. The
        # projection says so itself now; MARGIN_FROM_ELSEWHERE remains as the
        # answer for payloads written before seats.py carried the flag.
        elsewhere = model.get("margin_published_elsewhere")
        if elsewhere is None:
            elsewhere = source_id in MARGIN_FROM_ELSEWHERE
        if not elsewhere:
            emit(source_id, cat, NATL_HOUSE, "national", "", "", "margin_D",
                 model.get("tide_D"), "margin")
        emit(source_id, cat, NATL_HOUSE, "national", "", "", "seats_D",
             house.get("expected_D_seats"), "seats")
        emit(source_id, cat, NATL_HOUSE, "national", "", "", "win_prob_D",
             house.get("prob_D_majority"), "prob")
        emit(source_id, cat, NATL_SENATE, "national", "", "", "seats_D",
             senate.get("expected_D_total"), "seats")
        # 51+ is a majority. 50+ is a tie the vice-president breaks, and every
        # outside forecast and market this is averaged against prices the
        # majority, so averaging our 50+ against their 51+ would compare two
        # different events and call the difference disagreement.
        emit(source_id, cat, NATL_SENATE, "national", "", "", "win_prob_D",
             senate.get("prob_D_51_plus"), "prob")
        for st, r in (model.get("races") or {}).items():
            rid = f"SEN_{st}_2026"
            emit(source_id, cat, rid, "senate", st, "", "margin_D",
                 r.get("expected_margin_D"), "margin")
            emit(source_id, cat, rid, "senate", st, "", "win_prob_D",
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
        as_ofs: dict[str, str] = {}
        for m in members:
            try:
                per_source[m["source_id"]].append(float(m["value"]))
            except (TypeError, ValueError):
                continue
            tiers[m["source_id"]] = m["publication"]
            as_ofs[m["source_id"]] = m.get("as_of") or date

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
            # Provenance in time. `oldest_as_of` is the publication date of the
            # stalest thing in this mean, and `n_carried` counts how many
            # contributors are being quoted from an earlier day. Together they
            # let the page say "fundamentals, 2 sources — Fair last published
            # Jul 31" instead of implying both forecasters spoke this morning.
            # A category average that silently mixes today and three weeks ago
            # is the kind of small dishonesty this whole archive exists to
            # avoid.
            "oldest_as_of": min((as_ofs[s] for s in per_source), default=date),
            "n_carried": sum(1 for s in per_source if as_ofs.get(s, date) != date),
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
                    # Recomputed over the open subset, not inherited: the
                    # withheld gated source may well have been the stale one,
                    # and reporting its as-of date on a mean it is not in would
                    # be wrong in the direction that matters.
                    "oldest_as_of": min((as_ofs[s] for s in open_srcs),
                                        default=date),
                    "n_carried": sum(1 for s in open_srcs
                                     if as_ofs.get(s, date) != date),
                })
        else:
            averages.append(rec)

    # `as_of` is on the per-source table too. These rows are the ones the site
    # publishes BY NAME, so this is where a reader can check for themselves
    # that the Fair number beside our model is three weeks old rather than
    # taking the category note's word for it.
    by_source = [
        {**{k: r[k] for k in ("snapshot_date", "source_id", "category", "race_id",
                              "chamber", "state", "district", "quantity", "value",
                              "unit")},
         "as_of": r.get("as_of") or r["snapshot_date"]}
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


def _known_cells(cycle: int, averages: list[dict],
                 suppressed: list[dict] | None = None):
    """What the archive KNOWS about a date, published or withheld.

    Counting only published rows made the guard blind to the difference
    between losing a day and covering one up. A cell that moves behind the
    disclosure floor has not been lost — we still hold it, aggregate.py simply
    may not show it — and MIN_N moves cells across that line for reasons that
    have nothing to do with data going missing. Adding one gated forecaster to
    a category full of open single-source cells re-tiers every one of them at a
    stroke.

    That is exactly what happened on 2026-08-21. The published baseline for
    that date, 1,379 rows, was written by a run in which the wikipedia and
    race_to_the_wh parsers were both crashing. Repairing them ADDED two
    sources, one of them gated, which pushed a large block of professional
    cells below the floor — so the repaired run published 11% fewer cells while
    holding strictly more data, and the guard read a fix as a loss and refused.

    So both sides of the comparison count published + suppressed. A genuine
    lost day still trips it, because a day whose sources did not arrive has
    neither.
    """
    d = DATA_DIR / str(cycle) / "derived"
    have: dict[str, int] = defaultdict(int)
    have_cats: dict[str, set] = defaultdict(set)
    for name in ("category_averages.csv", "suppressed.csv"):
        p = d / name
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                have[r["snapshot_date"]] += 1
                have_cats[r["snapshot_date"]].add(r["category"])

    now: dict[str, int] = defaultdict(int)
    now_cats: dict[str, set] = defaultdict(set)
    for a in list(averages) + list(suppressed or ()):
        now[a["snapshot_date"]] += 1
        now_cats[a["snapshot_date"]].add(a["category"])
    return have, have_cats, now, now_cats


def would_shrink(cycle: int, averages: list[dict],
                 suppressed: list[dict] | None = None) -> list[str]:
    """Snapshot dates this run would publish materially LESS of.

    THE HAZARD. raw/ is pushed to a separate private archive and parsed/ is
    never committed, so a clone of this repo carries the DERIVED data for every
    day but the inputs for none of them. Run aggregate.py in such a clone and
    it rebuilds category_averages.csv from whatever parsed/ happens to hold
    locally and writes it over a file covering weeks. The write succeeds, the
    audit passes, every number that survives is correct, and the archive is
    quietly shorter. Nothing downstream notices: publish.py reads the newest
    date and the site looks entirely normal.

    WHAT COUNTS AS SHRINKING, AND WHY NOT "ANY DECREASE".
    The first version refused on a decrease of even one row, and the first
    thing it blocked was a fix rather than a fault: correcting the Polymarket
    parser dropped one junk cell — an untagged candidate market that should
    never have been a row — and two dates went 1000 -> 999. A guard that
    cannot tell a repaired parser from a lost day will be answered with
    --force, routinely, and then it is not a guard at all.

    A lost day does not look like that. It loses most of its rows, or it loses
    a whole CATEGORY, because the missing inputs are entire sources. So:

      - any category that vanishes from a date is a refusal, whatever the
        count. Categories only disappear when their sources do.
      - a row count that falls by more than DROP_TOLERANCE is a refusal.
      - anything smaller is reported and allowed, because that is what a
        parser getting more accurate looks like.

    A day cannot be reconstructed without its bytes, so refusing is the only
    safe move. Recover by cloning the raw archive and re-running parse.py
    --all, or by letting the daily Action do it where the whole store lives.
    """
    have, have_cats, now, now_cats = _known_cells(
        cycle, averages, suppressed)
    if not have:
        return []

    out = []
    for date in sorted(have):
        before, after = have[date], now.get(date, 0)
        lost_cats = sorted(have_cats[date] - now_cats.get(date, set()))
        if lost_cats:
            out.append(f"{date}: loses categor{'y' if len(lost_cats) == 1 else 'ies'} "
                       f"{', '.join(lost_cats)} ({before} rows -> {after})")
            continue
        if before and (before - after) / before > DROP_TOLERANCE:
            pct = 100.0 * (before - after) / before
            out.append(f"{date}: {before} rows published, this run has {after} "
                       f"({pct:.0f}% fewer)")
    return out


def small_drops(cycle: int, averages: list[dict],
                suppressed: list[dict] | None = None) -> list[str]:
    """Dates that lose a few rows — allowed, but said out loud.

    This is what a parser correction looks like from here, and it should be
    visible in the run log rather than silent. If it appears on a day nobody
    changed a parser, that is worth a look.
    """
    have, have_cats, now, now_cats = _known_cells(
        cycle, averages, suppressed)
    if not have:
        return []
    out = []
    for date in sorted(have):
        before, after = have[date], now.get(date, 0)
        # A date that would_shrink() refuses must not ALSO be reported here as
        # an allowed drop. Losing a category is a refusal whatever the row
        # count, and a date can lose one while shedding only a handful of rows —
        # which is exactly how the same day ended up in both lists, printed as
        # tolerable immediately above the paragraph refusing to write it.
        if have_cats[date] - now_cats.get(date, set()):
            continue
        if after < before and (before - after) / before <= DROP_TOLERANCE:
            out.append(f"{date}: {before} -> {after} ({before - after} fewer)")
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

    try:
        from parse import load_registry
        carried = carry_forward(rows, load_registry(a.cycle))
    except Exception as e:
        # Do not aggregate without knowing each source's cadence. Silently
        # skipping the carry-forward would republish the exact bug this was
        # written to fix, and it would look like a normal run.
        print(f"could not read the registry for source cadences: "
              f"{type(e).__name__}: {e}")
        return 2

    averages, by_source, suppressed = aggregate(rows)
    ratings = ratings_panel(rows)
    problems = audit(rows, averages, by_source, suppressed)
    shrunk = would_shrink(a.cycle, averages, suppressed)
    nibbles = small_drops(a.cycle, averages, suppressed)

    print("=" * 70)
    print(f"aggregate · cycle {a.cycle}")
    print("=" * 70)
    print(f"  {len(rows):6d} parsed rows in   (private)")
    if carried:
        for line in carried:
            print(f"    carried forward  {line}")
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

    if nibbles:
        # Allowed, but never silent. A handful of rows leaving a published date
        # is what a parser correction looks like; on a day nobody touched a
        # parser it is worth a second look.
        print(f"\n  {len(nibbles)} published date(s) lose a few rows "
              f"(within the {DROP_TOLERANCE:.0%} tolerance — allowed):")
        for line in nibbles[:8]:
            print(f"      {line}")
        if len(nibbles) > 8:
            print(f"      … and {len(nibbles) - 8} more")
        print("    Expected after a parser fix that stops emitting a bad cell. "
              "Unexpected otherwise.")

    if shrunk and not a.force:
        print("\n  *** REFUSING TO WRITE: this run would shorten the archive ***")
        print(f"    {len(shrunk)} snapshot date(s) lose a whole category, or "
              f"more than {DROP_TOLERANCE:.0%} of their rows:")
        for line in shrunk[:8]:
            print(f"      {line}")
        if len(shrunk) > 8:
            print(f"      … and {len(shrunk) - 8} more")
        print("    That is the shape of MISSING INPUTS, not of a corrected "
              "parser: categories only vanish when their sources do.")
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
