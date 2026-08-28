#!/usr/bin/env python3
"""The taxonomy: what a forecast is made of, and who made it.

WHY THIS FILE EXISTS

    Until 2026-08-27 the archive had one `category` field holding five values —
    polling, fundamentals, market, professional, academic — and those five are
    answers to TWO different questions:

        polling · fundamentals · market      what evidence is it built from
        professional · academic              who built it

    Mixing them forced every academic model to be cross-listed into a second
    category so it would appear on both readings, which is why three of the
    four academic models were also three of the five fundamentals members, and
    why those two lines tracked each other almost exactly. It was not agreement
    between two methods. It was one set of models drawn twice.

    So: two facets, and a source belongs to exactly one group in each.

    TYPE    polling       a poll aggregate and nothing more
            fundamentals  structural — approval, economy, exposure, no polls
            composite     a full forecast model: polls AND structure AND
                          race-level judgment, blended
            market        a traded price
            expert        an ordinal race rating, which is a judgment rather
                          than a number and lives on its own panel

    SOURCE  academic      published academic models and their authors
            professional  forecasters and outlets doing this commercially
            class         this project's own models
            market        the exchanges
            ai            reserved; the AI panel is pre-registered and not yet
                          running (see forecast/ai/PREREGISTRATION.md)

    reference is a sixth TYPE and a fifth SOURCE, for the things that are
    inputs rather than forecasts — Cook's PVI, FRED income, MEDSL results,
    DRA composites. They are already excluded from every average by
    NOT_A_FORECAST and NEVER_PUBLISH; naming them here keeps the audit honest
    instead of letting them fall through a default.

WHY THE KEY IS (source_id, category) AND NOT source_id ALONE

    One forecaster can publish two different KINDS of thing. Race to the WH
    publishes a generic-ballot average — a poll aggregate — and a seat
    forecast built on top of it. The archive already separates those: the
    aggregate arrives under category `polling`, the forecast under
    `professional`. That existing split is exactly the type/source distinction
    showing through, so it is what this maps from rather than something to be
    reconstructed.

AUDIT

    python3 forecast/collect/facets.py --cycle 2026

    Reads the parsed rows and the seat projections, and reports any
    (source_id, category) pair with no assignment. A missing pair is a source
    that would silently vanish from one of the two views, so it fails loudly.
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
SOURCES = ("academic", "professional", "class", "market", "ai", "reference")

TYPE_LABEL = {
    "polling": "Polling", "fundamentals": "Fundamentals",
    "composite": "Composite models", "market": "Markets",
    "expert": "Expert ratings",
}
SOURCE_LABEL = {
    "academic": "Academic", "professional": "Professional",
    "class": "This class", "market": "Markets", "ai": "AI",
}

# Least modelled to most modelled, as elsewhere on the site.
TYPE_ORDER = ["polling", "market", "fundamentals", "composite", "expert"]
SOURCE_ORDER = ["market", "professional", "academic", "class", "ai"]

# --------------------------------------------------------------------------
# The assignments.
#
# Keyed (source_id, existing category). A source_id on its own is the fallback
# when the pair is unknown, which is how a new poll aggregator picked up by the
# Wikipedia parser lands somewhere sensible on its first day rather than
# failing the run.
# --------------------------------------------------------------------------
BY_PAIR: dict[tuple[str, str], tuple[str, str]] = {
    # Race to the WH is both things, and this is the pair that proves the key
    # has to be a pair: its generic ballot is a poll average, its seat model
    # is not.
    ("race_to_the_wh", "polling"): ("polling", "professional"),
    ("race_to_the_wh", "professional"): ("composite", "professional"),
}

BY_SOURCE: dict[str, tuple[str, str]] = {
    # -- poll aggregators, published commercially ---------------------------
    "silver_bulletin": ("polling", "professional"),
    "ddhq": ("polling", "professional"),
    "rcp": ("polling", "professional"),
    "votehub": ("polling", "professional"),
    "fiftyplusone": ("polling", "professional"),
    "twoseventy": ("polling", "professional"),
    "economist": ("composite", "professional"),
    "split_ticket": ("composite", "professional"),
    "grant_williams": ("composite", "professional"),

    # -- academic -----------------------------------------------------------
    # BEW is the judgment call worth recording. It regresses the November vote
    # on the generic ballot, so the generic ballot IS its input and it belongs
    # with the aggregators; the midterm-penalty term is the discount it applies
    # to that input rather than a second source of evidence. It sits about 1.8
    # points below the professional aggregators, which is the model saying
    # what it exists to say.
    "academic_bew": ("polling", "academic"),
    "academic_economic_pessimism": ("fundamentals", "academic"),
    "academic_political_history": ("fundamentals", "academic"),
    "academic_referendum": ("fundamentals", "academic"),
    "academic_state_approval_economy": ("fundamentals", "academic"),
    # Ray Fair publishes a named equation under his own name. Source describes
    # who made the forecast, and he is an economist publishing academic work,
    # so he belongs in that line even though we take his number as published
    # rather than re-estimating it.
    "fair": ("fundamentals", "academic"),

    # -- ours ---------------------------------------------------------------
    "class_fundamentals": ("fundamentals", "class"),
    "class_polling": ("polling", "class"),
    "polling_reconstructed": ("polling", "class"),

    # -- exchanges ----------------------------------------------------------
    "kalshi": ("market", "market"),
    "polymarket": ("market", "market"),
    "predictit": ("market", "market"),

    # -- ordinal race ratings, on their own panel ---------------------------
    "wikipedia": ("expert", "professional"),
    "cook": ("expert", "professional"),
    "sabato": ("expert", "professional"),
    "inside_elections": ("expert", "professional"),
    "fox_power_rankings": ("expert", "professional"),
    "twoseventy_ratings": ("expert", "professional"),

    # -- inputs, not forecasts ---------------------------------------------
    "cook_pvi": ("reference", "reference"),
    "cook_state_pvi": ("reference", "reference"),
    "dra": ("reference", "reference"),
    "fred": ("reference", "reference"),
    "medsl": ("reference", "reference"),
    "wiki_approval": ("reference", "reference"),
    "wiki_endorsements": ("reference", "reference"),
}

# Last resort, by the category the row already carries. Deliberately NOT a
# silent default for the two who-made-it categories: a row arriving as
# `professional` or `academic` with an unknown source_id has no type we can
# infer, and guessing one would put a number on a line it may not belong to.
BY_CATEGORY: dict[str, tuple[str, str]] = {
    "polling": ("polling", "professional"),
    "market": ("market", "market"),
    "expert_ordinal": ("expert", "professional"),
}


# Versioned class models. Each homework changes the specification, and a
# change of specification is a change of identity — see MODEL_ID in
# model/fundamentals.py for why. That produces ids like
# `class_fundamentals_v2`, and they must not fall through to a default that
# would file the class's own model under `professional`.
#
# The prefix rule means a new version lands correctly without anyone
# remembering to edit this file. The audit below still prints where it landed,
# so a wrong guess is visible on the first run rather than in November.
BY_PREFIX: tuple[tuple[str, tuple[str, str]], ...] = (
    ("class_fundamentals", ("fundamentals", "class")),
    ("class_polling", ("polling", "class")),
    ("class_", ("fundamentals", "class")),
)


def facets(source_id: str, category: str) -> tuple[str, str] | None:
    """(type, source) for a row, or None if we cannot say."""
    got = BY_PAIR.get((source_id, category)) or BY_SOURCE.get(source_id)
    if got:
        return got
    for pre, val in BY_PREFIX:
        if source_id.startswith(pre):
            return val
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
    missing, by_type, by_source = [], collections.defaultdict(set), collections.defaultdict(set)
    print(f"  {'source_id':30s} {'category':16s} {'type':13s} source")
    for (sid, cat), n in sorted(seen.items()):
        got = facets(sid, cat)
        if got is None:
            missing.append((sid, cat))
            print(f"  {sid:30s} {cat:16s} {'—':13s} —   UNASSIGNED")
            continue
        t, s = got
        print(f"  {sid:30s} {cat:16s} {t:13s} {s}")
        if t != "reference":
            by_type[t].add(sid)
            by_source[s].add(sid)

    print("\n  grouped by TYPE")
    for t in TYPE_ORDER:
        if by_type.get(t):
            print(f"    {TYPE_LABEL[t]:18s} {', '.join(sorted(by_type[t]))}")
    print("\n  grouped by SOURCE")
    for s in SOURCE_ORDER:
        if by_source.get(s):
            print(f"    {SOURCE_LABEL[s]:18s} {', '.join(sorted(by_source[s]))}")

    # THE POINT OF THE WHOLE CHANGE, ASSERTED. Within one facet no source may
    # appear twice, or the old overlap has been rebuilt under new names.
    bad = []
    for name, groups in (("type", by_type), ("source", by_source)):
        counts: collections.Counter = collections.Counter()
        for g in groups.values():
            counts.update(g)
        dupes = [s for s, k in counts.items() if k > 1]
        # race_to_the_wh is the one legitimate exception: its poll average and
        # its seat model are different objects and belong on different lines.
        dupes = [s for s in dupes if s != "race_to_the_wh"]
        if dupes:
            bad.append(f"{name}: {sorted(dupes)}")
    print()
    if missing:
        print(f"  FAIL: {len(missing)} unassigned pair(s) — add them to "
              f"BY_SOURCE or BY_PAIR:")
        for sid, cat in missing:
            print(f"    ({sid!r}, {cat!r})")
    if bad:
        print(f"  FAIL: a source appears in two groups of one facet — "
              f"{'; '.join(bad)}")
    if not missing and not bad:
        print("  PASS: every pair assigned, and no source is in two groups "
              "of the same facet.")
    return 1 if (missing or bad) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit the forecast taxonomy.")
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)
    return audit(a.cycle)


if __name__ == "__main__":
    sys.exit(main())
