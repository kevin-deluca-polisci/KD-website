#!/usr/bin/env python3
"""Which line on the site each forecast belongs to.

Every forecast row is assigned to one SOURCE line:

    polling       poll averages, from any publisher (Silver Bulletin, RCP,
                  DDHQ, FiftyPlusOne, Race to the WH's poll average, ...),
                  and our translation of those averages into seats
    market        prediction markets
    professional  forecasters' own models (Race to the WH's seat model, ...)
    academic      published academic models, computed from their equations
    class         the PLSC 2219 class model. Shown on the site only; it never
                  passes through this file, because it is not part of the
                  data archive (see forecast/class/).

    reference     inputs rather than forecasts (Cook PVI, FRED, MEDSL, DRA,
                  approval). Excluded from every average.

Until 2026-09-29 rows were also grouped by TYPE (polling, fundamentals,
composite, market, expert) and the site had a toggle between the two views.
The type view was removed; the first element of each assignment is kept only
so that `reference` rows and ordinal ratings (`expert`) can still be told
apart from forecasts.

DROPPED sources are not published anywhere:
    class_fundamentals  the site's earlier class model, replaced by the PLSC
                        2219 model. Its code and history are kept privately.
    grant_williams      an individual's model, not a professional forecaster.
    wiki_endorsements   endorsements are no longer part of the site or archive.

AUDIT

    python3 forecast/collect/facets.py --cycle 2026

Reports any (source_id, category) pair with no assignment.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "forecast" / "data"

TYPES = ("polling", "fundamentals", "composite", "market", "expert", "reference")
SOURCES = ("polling", "market", "professional", "academic", "class", "reference")

TYPE_LABEL = {
    "polling": "Polling", "fundamentals": "Fundamentals",
    "composite": "Composite models", "market": "Markets",
    "expert": "Expert ratings",
}
SOURCE_LABEL = {
    "polling": "Polling", "market": "Markets", "professional": "Professional",
    "academic": "Academic", "class": "Class model",
}

# Least modelled to most modelled, as elsewhere on the site.
TYPE_ORDER = ["polling", "market", "fundamentals", "composite", "expert"]
SOURCE_ORDER = ["polling", "market", "professional", "academic", "class"]

# --------------------------------------------------------------------------
# The assignments.
#
# Keyed (source_id, existing category). A source_id on its own is the fallback
# when the pair is unknown, which is how a new poll aggregator picked up by the
# Wikipedia parser lands somewhere sensible on its first day rather than
# failing the run.
# --------------------------------------------------------------------------
BY_PAIR: dict[tuple[str, str], tuple[str, str]] = {
    # Race to the WH publishes two different things: a generic-ballot poll
    # average (category `polling`) and a seat forecast (category
    # `professional`). They go on different lines.
    ("race_to_the_wh", "polling"): ("polling", "polling"),
    ("race_to_the_wh", "professional"): ("composite", "professional"),
}

BY_SOURCE: dict[str, tuple[str, str]] = {
    # -- poll averages: the Polling line, whoever publishes them -------------
    "silver_bulletin": ("polling", "polling"),
    "ddhq": ("polling", "polling"),
    "rcp": ("polling", "polling"),
    "votehub": ("polling", "polling"),
    "fiftyplusone": ("polling", "polling"),
    "twoseventy": ("polling", "polling"),
    # our translation of those poll averages into seats and probabilities
    "class_polling": ("polling", "polling"),
    "polling_reconstructed": ("polling", "polling"),

    # -- forecasters' own models: the Professional line ---------------------
    "economist": ("composite", "professional"),
    "split_ticket": ("composite", "professional"),

    # -- academic models ----------------------------------------------------
    "academic_bew": ("polling", "academic"),
    "academic_economic_pessimism": ("fundamentals", "academic"),
    "academic_political_history": ("fundamentals", "academic"),
    "academic_referendum": ("fundamentals", "academic"),
    "academic_state_approval_economy": ("fundamentals", "academic"),
    "fair": ("fundamentals", "academic"),

    # -- prediction markets -------------------------------------------------
    "kalshi": ("market", "market"),
    "polymarket": ("market", "market"),
    "predictit": ("market", "market"),

    # -- ordinal race ratings: collected, not shown on the site -------------
    "wikipedia": ("expert", "professional"),
    "cook": ("expert", "professional"),
    "sabato": ("expert", "professional"),
    "inside_elections": ("expert", "professional"),
    "fox_power_rankings": ("expert", "professional"),
    "twoseventy_ratings": ("expert", "professional"),

    # -- inputs, not forecasts ----------------------------------------------
    "cook_pvi": ("reference", "reference"),
    "cook_state_pvi": ("reference", "reference"),
    "dra": ("reference", "reference"),
    "fred": ("reference", "reference"),
    "medsl": ("reference", "reference"),
    "wiki_approval": ("reference", "reference"),
}

# Last resort, by the row's own category. There is deliberately no default
# for `professional` or `academic`: a row with an unknown source_id in those
# categories is reported by the audit rather than guessed.
BY_CATEGORY: dict[str, tuple[str, str]] = {
    "polling": ("polling", "polling"),
    "market": ("market", "market"),
    "expert_ordinal": ("expert", "professional"),
}

# The retired site class models. class_polling still supplies the polling
# line's national seat counts, but neither model's own rows go in the
# published by-source file (they are not part of the data archive).
NOT_IN_ARCHIVE = {"class_polling"}


def in_archive(source_id: str) -> bool:
    return not is_dropped(source_id) and source_id not in NOT_IN_ARCHIVE


# Not published anywhere. facets() returns None for these, which excludes
# them from every average. The prefix rule catches versioned ids such as
# `class_fundamentals_v2`; the PLSC 2219 class model is not affected because
# it never enters the parsed rows.
DROPPED_SOURCES = {"class_fundamentals", "grant_williams", "wiki_endorsements"}

# Sources on the polling line whose race-level numbers are the NATIONAL
# polling average carried to each race by partisan lean. They count on the
# polling line only for national quantities (House margin, seat totals,
# chances of control). A race's polling number comes only from polling
# averages of that race (the aggregators listed in its Wikipedia article).
POLLING_NATIONAL_ONLY = {"class_polling", "polling_reconstructed"}
NATIONAL_RACES = ("NATL_",)


# Row provenance for values this archive computed (seats.py pushing a
# source's national margin through the seat model), as opposed to values
# read from the source itself.
COMPUTED = ("computed", "retrospective")


def on_line(source_id: str, category: str, race_id: str,
            provenance: str = "") -> tuple[str, str] | None:
    """facets(), plus the race-level rule for the polling line: a race's
    polling number must be a polling average OF THAT RACE. Rows that carry a
    national polling average to the race (the class polling model, the
    reconstructed average, or any aggregator's national number run through
    the seat model) count only for national quantities."""
    got = facets(source_id, category)
    if got and got[1] == "polling" \
            and not (race_id or "").startswith(NATIONAL_RACES) \
            and (source_id in POLLING_NATIONAL_ONLY or provenance in COMPUTED):
        return None
    return got


DROPPED_PREFIXES = ("class_fundamentals",)


def is_dropped(source_id: str) -> bool:
    return (source_id in DROPPED_SOURCES
            or any(source_id.startswith(p) for p in DROPPED_PREFIXES))


def facets(source_id: str, category: str) -> tuple[str, str] | None:
    """(type, source) for a row, or None if it is dropped or unknown."""
    if is_dropped(source_id):
        return None
    got = BY_PAIR.get((source_id, category)) or BY_SOURCE.get(source_id)
    if got:
        return got
    return BY_CATEGORY.get(category)


def is_forecast(source_id: str, category: str) -> bool:
    got = facets(source_id, category)
    return bool(got) and got[0] != "reference"


# --------------------------------------------------------------------------

def _seen(cycle: int) -> collections.Counter:
    seen: collections.Counter = collections.Counter()
    for p in sorted(glob.glob(str(DATA_DIR / str(cycle) / "parsed" / "*.csv"))):
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                seen[(r["source_id"], r["category"])] += 1
    sp = DATA_DIR / str(cycle) / "model_private" / "seat_projections.json"
    if sp.exists():
        d = json.loads(sp.read_text())
        for sid, m in (d.get("projections") or d).items():
            if isinstance(m, dict) and m.get("category"):
                seen[(sid, m["category"])] += 1
    return seen


def audit(cycle: int) -> int:
    seen = _seen(cycle)
    print("=" * 74)
    print(f"facets · cycle {cycle} · {len(seen)} (source, category) pair(s)")
    print("=" * 74)
    missing, dropped = [], []
    by_source = collections.defaultdict(set)
    print(f"  {'source_id':30s} {'category':16s} line")
    for (sid, cat), n in sorted(seen.items()):
        if is_dropped(sid):
            dropped.append(sid)
            print(f"  {sid:30s} {cat:16s} (dropped, not published)")
            continue
        got = facets(sid, cat)
        if got is None:
            missing.append((sid, cat))
            print(f"  {sid:30s} {cat:16s} UNASSIGNED")
            continue
        t, s = got
        print(f"  {sid:30s} {cat:16s} {s}{'  (rating, not shown)' if t == 'expert' else ''}")
        if t not in ("reference", "expert"):
            by_source[s].add(sid)

    print("\n  lines on the site")
    for s in SOURCE_ORDER:
        if by_source.get(s):
            print(f"    {SOURCE_LABEL[s]:14s} {', '.join(sorted(by_source[s]))}")

    # Within the source lines no source may appear twice, except Race to the
    # WH, whose poll average and seat model are different objects.
    counts: collections.Counter = collections.Counter()
    for g in by_source.values():
        counts.update(g)
    dupes = sorted(s for s, k in counts.items() if k > 1 and s != "race_to_the_wh")
    print()
    if missing:
        print(f"  FAIL: {len(missing)} unassigned pair(s); add them to "
              f"BY_SOURCE or BY_PAIR:")
        for sid, cat in missing:
            print(f"    ({sid!r}, {cat!r})")
    if dupes:
        print(f"  FAIL: source(s) on two lines: {dupes}")
    if not missing and not dupes:
        print("  PASS: every pair assigned, and no source is on two lines.")
    return 1 if (missing or dupes) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit the forecast taxonomy.")
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)
    return audit(a.cycle)


if __name__ == "__main__":
    sys.exit(main())
