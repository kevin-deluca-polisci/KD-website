#!/usr/bin/env python3
"""
Audit a generated conditions draft: flag the rows a human has to look at, and
say what is probably wrong with each.

    python3 forecast/collect/conditions_audit.py
    python3 forecast/collect/conditions_audit.py --in forecast/conditions/drafts

Reads `candidacy_events_draft.csv`, writes `candidacy_events_audited.csv`
beside it with two columns added, and prints a triage summary.

-----------------------------------------------------------------------------
WHY THIS IS A FILE AND NOT A ONE-OFF READ-THROUGH

The draft is regenerated every time the extractor improves or the archive
grows another month. A judgement made by reading 136 rows once is lost the
moment that happens, and re-reading them is how a review turns into a chore
nobody does. Rules survive regeneration; opinions do not.

Everything here is a FLAG, never a deletion. The auditor's job is to sort the
pile, not to decide what is true — the extractor already produced rows that
look right and are wrong, and a second automatic layer that quietly threw them
away would compound that rather than catch it.

-----------------------------------------------------------------------------
THE ONE FINDING THAT SHAPED THIS

The two pages describe the same fact differently, and the difference is a
CATEGORY, not a wording:

    elections page:  "#KY-6: Andy Barr is retiring to run for the U.S. Senate."
    ratings page:    "! KY-6 | Andy | Barr (retiring) | 63.0% R"

The first is `seeking_other_office`. The second is `retiring`. Both are true,
because the ratings page's "(retiring)" annotation means "not on the ballot"
and is a superset of the specific reason. So twenty members carry two rows
whose event types genuinely disagree, and the generic one is always the later
sighting, because the ratings page adds the annotation after the news breaks.

That is not an extraction bug and it should not be fixed in the extractor:
both rows are faithful readings of what their page said. It is a MERGE rule,
and merge rules belong here, where they can be seen and argued with.
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DRAFTS = REPO / "forecast" / "conditions" / "drafts"

# Ordered by how much each says. `retiring` is the weakest claim: it means the
# member is not on the ballot and gives no reason. Anything else is a reason,
# so anything else beats it.
SPECIFICITY = {
    "retiring": 0,
    "seeking_other_office": 1,
    "withdrew": 1,
    "resigned": 2,
    "died": 2,
    "lost_primary": 2,
}

OPEN_SEAT = re.compile(r"\bopen seat\b", re.I)
ENTRY = re.compile(r"^\s*[#*!|]")


def person_key(name: str) -> str:
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", n.lower()).strip()


def audit(rows: list[dict]) -> list[dict]:
    by_entity: dict[tuple, list[dict]] = collections.defaultdict(list)
    by_race: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if r.get("person") and r.get("race_id"):
            by_entity[(r["race_id"], person_key(r["person"]))].append(r)
            by_race[r["race_id"]].append(r)

    for r in rows:
        flags: list[str] = []
        action = ""
        ev = r.get("_evidence", "")
        words = len(ev.split())

        # --- the merge rule, stated above ------------------------------------
        if r.get("person") and r.get("race_id"):
            siblings = by_entity[(r["race_id"], person_key(r["person"]))]
            if len(siblings) > 1:
                best = max(SPECIFICITY.get(s["event_type"], 0)
                           for s in siblings)
                mine = SPECIFICITY.get(r["event_type"], 0)
                if mine < best:
                    other = next(s for s in siblings
                                 if SPECIFICITY.get(s["event_type"], 0) == best)
                    if other["known_by"] <= r["known_by"]:
                        flags.append("generic_duplicate")
                        action = (f"drop — {other['event_type']} on "
                                  f"{other['known_by']} says the same thing "
                                  f"earlier and with the reason")
                    else:
                        # The generic row came FIRST. That is a real sequence:
                        # the page said "retiring", and only later said why.
                        # The earlier date is the better known_by, so the rows
                        # merge rather than one being dropped.
                        flags.append("earlier_generic")
                        action = (f"merge — keep this date, take the "
                                  f"{other['event_type']} type from "
                                  f"{other['known_by']}")
                elif mine == best and len(siblings) > 2:
                    flags.append("multi_event_person")
                    action = "check the sequence is real, not a page rewrite"
                elif mine == best:
                    lower = [s for s in siblings
                             if SPECIFICITY.get(s["event_type"], 0) < mine]
                    if lower and all(s["known_by"] >= r["known_by"]
                                     for s in lower):
                        flags.append("has_generic_twin")
                        action = "keep this row; its twin is the drop"

        # --- seat-level rows, which are consequences and not events ----------
        if not r.get("person"):
            if OPEN_SEAT.search(ev):
                flags.append("seat_level_confirmation")
                action = ("not an event — the ratings page marking the seat "
                          "open AFTER the incumbent announced. Useful only as "
                          "a cross-check on incumbent_on_ballot_after")
            else:
                flags.append("person_not_extracted")
                action = ("read the evidence and fill `person` in, or drop it "
                          "as a duplicate of the row that has the name")

        # --- prose that got into a table cell --------------------------------
        # Thirty words, not eighteen. The governor and Senate incumbent rows
        # carry a candidate list in their last cell and run to about twenty,
        # so the tighter threshold flagged a pile of perfectly good rows and
        # buried the one that was actually wrong.
        if words > 30 and not OPEN_SEAT.search(ev):
            flags.append("long_prose")
            action = action or ("a sentence, not an entry — check the person "
                                "and the race actually belong to each other")

        # --- two names for one seat ------------------------------------------
        # The sharper version of the check above. A table row names the
        # incumbent in bare cells and a challenger in a wikilink, and when the
        # cell pattern shifts the reader can take the wrong one: GOV_MN got
        # both "Tim Walz" and "Peggy Bennett" for the same retirement. Two
        # different people retiring from one seat is not impossible, but it is
        # rare enough to be worth a look every time.
        if r.get("race_id") and r.get("person"):
            others = {s["person"] for s in by_race[r["race_id"]]
                      if s["event_type"] == r["event_type"]}
            if len(others) > 1:
                flags.append("person_conflict")
                action = (f"two names for this seat and event type: "
                          f"{', '.join(sorted(others))} — one is a "
                          f"misread table cell")

        # --- dates that are bounds rather than estimates ---------------------
        if r.get("_left_censored") == "yes":
            flags.append("date_is_an_upper_bound")
            action = action or ("already on the page when we started watching "
                                "— the real date is EARLIER. Needs a primary "
                                "source or the row goes")

        if r.get("_confidence") == "low":
            flags.append("one_day_only")
            action = action or ("visible for a single day. Usually a reworded "
                                "entry that left a stale twin behind")

        if r.get("_race_why") == "non-voting delegate":
            flags.append("delegate")
            action = action or ("not one of the 435 — record it or drop it, "
                                "but it must not get a race_id")

        r["_flag"] = "; ".join(flags)
        r["_suggested_action"] = action
        # `has_generic_twin` is a KEEP marker, not a problem. Counting it as
        # work to do would put twenty rows that are already correct into the
        # review pile and make the pile look twice as big as it is.
        real = [f for f in flags if f != "has_generic_twin"]
        r["_needs_human"] = "yes" if real else ""
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--in", dest="indir", default=str(DRAFTS))
    a = ap.parse_args(argv)

    src = Path(a.indir) / "candidacy_events_draft.csv"
    if not src.exists():
        print(f"no draft at {src} — run wiki_firstseen.py --extract first")
        return 2
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    rows = audit(rows)

    cols = list(rows[0].keys())
    out = Path(a.indir) / "candidacy_events_audited.csv"
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    counts = collections.Counter(
        f for r in rows for f in (r["_flag"].split("; ") if r["_flag"] else []))
    clean = sum(1 for r in rows if not r["_needs_human"])

    print("=" * 72)
    print(f"AUDIT · {len(rows)} rows · {clean} need no attention · "
          f"{len(rows) - clean} to review")
    print("=" * 72)
    for f, n in counts.most_common():
        print(f"  {n:>4}  {f}")
    print(f"\n  -> {out}")
    print("""
  Work through it in this order, because the first group is bulk and the
  last group is the one that needs judgement:

    generic_duplicate        delete. A better row already says it, earlier.
    seat_level_confirmation  delete as events; they are the ratings page
                             noticing a seat went open, not the announcement.
    person_not_extracted     one line each: read it, name the person or bin it.
    long_prose               a sentence that landed in a table cell. These are
                             where a person gets attached to the wrong race.
    date_is_an_upper_bound   the only group where the DATE is wrong rather
                             than the row. Needs a primary source.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
