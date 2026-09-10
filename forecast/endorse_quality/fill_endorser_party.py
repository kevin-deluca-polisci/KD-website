#!/usr/bin/env python3
"""fill_endorser_party.py — resolve the party of congressional endorsers.

    python3 forecast/endorse_quality/fill_endorser_party.py --describe
    python3 forecast/endorse_quality/fill_endorser_party.py --write

WHY THIS EXISTS. `endorser_party` is filled on 11% of the rows that could carry
one, and `cross_party` is never set without it -- so a race showing zero
cross-party endorsements is overwhelmingly saying "the endorsers' parties are
unknown", not "no defections happened". A measure built on that reads
missingness as evidence of absence.

WHAT IT DOES AND DOES NOT COVER. Only U.S. senators and representatives: 688
rows, 333 distinct people. Organizations, unions and newspapers are excluded on
purpose and permanently -- they have no party to resolve, and giving them a
lean is the group ideal-point problem, which is not identified at this n. State
and local officials are the larger remaining tranche and need their party off a
linked Wikipedia article; that is a separate job.

HOW IT MATCHES, AND WHY NOT BY NAME ALONE. Every congressional endorser carries
a `descriptor` -- "U.S. Senator from Tennessee (2019-present)", "U.S.
Representative from New York's 21st congressional district (2015-present)" --
filled on 686 of 688. Parsing the office, state and district out of it turns a
name lookup into a KEYED one: the roster is built from the archive's own MEDSL
returns, and a person is matched on (chamber, state, district) with an election
year inside their service span. Two Congressmen named Scott in different states
cannot collide, because the state is part of the key rather than a tiebreak.

The roster is the returns file already in the archive. No network, no external
dataset, CC0, and it is the same source the outcome comes from.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DERIVED = REPO_ROOT / "forecast" / "data" / "2026" / "derived"

CONGRESS_CATEGORIES = {
    "U.S. representatives", "U.S. senators",
    "U.S. Representatives", "U.S. Senators",
}

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

# INDEPENDENTS CANNOT COME FROM THIS ROUTE, and are deliberately left unfilled.
# MEDSL codes Sanders, King and 2022 Murkowski all as "OTHER", and party_raw
# does not separate them either -- it reads "OTHER" for Sanders and for
# Murkowski alike, though she is a Republican. Mapping OTHER to "I" would
# label her an independent. Better an honest gap: an Independent term, if it
# is built, needs the Wikipedia-infobox route, not this one.
PARTY_LETTER = {"DEMOCRAT": "D", "REPUBLICAN": "R", "LIBERTARIAN": "L"}

# "(2019-present)", "(2015-2023)", with an en dash or a hyphen.
_YEARS = re.compile(r"\((\d{4})\s*[-\u2013\u2014]\s*(\d{4}|present)\)", re.I)
_ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)\s+congressional district", re.I)

# "NY-21", "IN-3", "AZ-5". This shorthand is the MAJORITY of the file: spelling
# out "New York's 21st congressional district" was the form the first version
# of this parser expected, and it left 3,280 rows unparsed because most
# descriptors do not use it.
_ABBR_DIST = re.compile(r"\b([A-Z]{2})[-\u2013](\d{1,2})\b")
_ABBRS = set(STATES.values())

# STATE offices that Wikipedia sometimes files under a federal heading. These
# must be REJECTED rather than merely left unresolved: "member of the Arizona
# House of Representatives" reaching the statewide fallback could match a U.S.
# Representative from Arizona on surname alone and assert a party for the wrong
# person. A miss is recoverable; a wrong fill is not visible afterwards.
_STATE_OFFICE = re.compile(
    r"\b(state (senator|senate|representative|house|assembly|legislature)"
    r"|member of the [A-Z][a-z]+(?: [A-Z][a-z]+)* (senate|house|assembly|general assembly)"
    r"|state legislator|city council|mayor|county|school board|governor"
    r"|lieutenant governor|attorney general|secretary of state|treasurer)\b",
    re.I)


def parse_descriptor(desc: str, category: str | None = None) -> dict | None:
    """(chamber, state, district, from_year, to_year) out of the descriptor text."""
    if not desc:
        return None
    d = desc.strip()
    low = d.lower()
    if _STATE_OFFICE.search(d) and not re.search(r"\bu\.?s\.?\b|\bunited states\b|congressional district", low):
        return None

    # THE CATEGORY DECIDES THE CHAMBER, NOT THE PROSE. Requiring the word
    # "senator" or "representative" in the descriptor rejected 2,660 rows,
    # because the common form carries neither: "IN-3 (2017-2025)",
    # "Missouri (2019-present)". The row is already filed under
    # "U.S. representatives" or "U.S. senators"; that is the authority.
    cat = (category or "").lower()
    if "senator" in cat:
        chamber = "senate"
    elif "representative" in cat:
        chamber = "house"
    elif "senator" in low:
        chamber = "senate"
    elif "representative" in low or "congressional district" in low:
        chamber = "house"
    else:
        return None

    state = None
    dist_from_abbr = None
    m = _ABBR_DIST.search(d)
    if m and m.group(1) in _ABBRS:
        state = (m.group(1).lower(), m.group(1))
        dist_from_abbr = str(int(m.group(2)))
        chamber = "house"
    if state is None:
        for name, ab in STATES.items():
            if re.search(rf"\bfrom {re.escape(name)}\b", low):
                if state is None or len(name) > len(state[0]):
                    state = (name, ab)
    if state is None:
        # Bare, with no "from": descriptors like "Missouri (2019-present)".
        # Longest first so "west virginia" is not swallowed by "virginia".
        for name in sorted(STATES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", low):
                state = (name, STATES[name])
                break
    if state is None:
        for ab in _ABBRS:
            if re.search(rf"\b{ab}\b", d):
                state = (ab.lower(), ab)
                break
    if state is None:
        return None

    district = dist_from_abbr
    if chamber == "house" and district is None:
        m = _ORDINAL.search(d)
        if m:
            district = str(int(m.group(1)))

    # ALL the spans, not the first. "AZ-4 (2013-2023), U.S. representative
    # from AZ-9 (2011-2013)" is one person with two stints, and taking only
    # span one would reject an election that really was theirs.
    spans = _YEARS.findall(d)
    frm = min(int(a) for a, _ in spans) if spans else None
    to = (max(2100 if b.lower() == "present" else int(b) for _, b in spans)
          if spans else None)

    return {"chamber": chamber, "state": state[1], "district": district,
            "from": frm, "to": to}


def build_roster() -> dict:
    """(chamber, state, district) -> [(year, surname_set, party), ...] winners."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from endorse_quality import surnames_of, _is_pseudo

    roster = collections.defaultdict(list)
    with (DERIVED / "returns.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("won")).lower() != "true":
                continue
            try:
                y = int(r["year"])
            except (TypeError, ValueError, KeyError):
                continue
            nm = (r.get("candidate") or "").strip()
            if not nm or _is_pseudo(nm.lower()):
                continue
            party = PARTY_LETTER.get((r.get("party") or "").strip().upper())
            dist = (r.get("district") or "").strip().lstrip("0") or None
            roster[(r.get("chamber"), r.get("state"), dist)].append(
                (y, surnames_of(nm), party))
    return roster


def resolve(endorser: str, desc: dict, roster: dict,
            cycle: int | None = None) -> tuple[str | None, str]:
    """Party for one endorser. Returns (party, how)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from endorse_quality import surnames_of

    want = surnames_of(endorser)
    if not want:
        return None, "no surname"

    keys = [(desc["chamber"], desc["state"], desc["district"])]
    if desc["chamber"] == "house" and desc["district"] is None:
        keys = [k for k in roster if k[0] == "house" and k[1] == desc["state"]]

    hits = []
    for k in keys:
        for y, surs, party in roster.get(k, ()):
            if not (want & surs) or not party:
                continue
            # Served then, per the descriptor's own dates. Guards against a
            # different person of the same surname holding the seat decades
            # apart -- which happens, and which a name-only match cannot see.
            if desc["from"] and y < desc["from"] - 1:
                continue
            if desc["to"] and y > desc["to"]:
                continue
            hits.append((y, party))
    if not hits:
        # District numbers move under a sitting member when a state redraws, so
        # fall back to the whole state before giving up.
        for k in [k for k in roster if k[0] == desc["chamber"] and k[1] == desc["state"]]:
            for y, surs, party in roster.get(k, ()):
                if (want & surs) and party:
                    if desc["from"] and y < desc["from"] - 1:
                        continue
                    if desc["to"] and y > desc["to"]:
                        continue
                    hits.append((y, party))
        if hits:
            counts = collections.Counter(p for _, p in hits)
            return counts.most_common(1)[0][0], "statewide fallback"
        return None, "no roster match"

    counts = collections.Counter(p for _, p in hits)
    if len(counts) > 1:
        # A PARTY SWITCH, usually, not two people. Richard Shelby is D in 1986
        # and 1992 and R from 1994 on; a modal vote makes him a Republican in
        # 1990 and refusing makes him nothing at all. Take the party he held at
        # the most recent election AT OR BEFORE the endorsement.
        prior = [(y, p) for y, p in hits if cycle is None or y <= cycle]
        if prior:
            return max(prior)[1], "descriptor+roster (switch, dated)"
        return None, f"ambiguous ({dict(counts)})"
    return counts.most_common(1)[0][0], "descriptor+roster"


def run(cycles=(2022, 2024, 2026), write: bool = False) -> int:
    roster = build_roster()
    stats = collections.Counter()
    unresolved = []
    lookup: dict = {}

    for cy in cycles:
        f = DERIVED / f"endorsements_{cy}.json"
        if not f.exists():
            continue
        rows = json.loads(f.read_text())
        for r in rows:
            cat = r.get("category_label") or r.get("category")
            if cat not in CONGRESS_CATEGORIES:
                continue
            stats["congressional rows"] += 1
            if r.get("endorser_party"):
                stats["already filled"] += 1
                continue
            desc = parse_descriptor(r.get("descriptor") or "", cat)
            if not desc:
                stats["descriptor unparsed"] += 1
                unresolved.append((r.get("endorser"), r.get("descriptor"), "unparsed"))
                continue
            party, how = resolve(r.get("endorser") or "", desc, roster, cycle=cy)
            if party:
                stats[f"resolved: {how}"] += 1
                prior = lookup.get(r.get("endorser_key"))
                if prior and prior[0] != party:
                    stats["CONFLICT across cycles"] += 1
                lookup[r.get("endorser_key") or (r.get("endorser") or "").lower()] = (
                    party, how, r.get("endorser") or "")
            else:
                stats["unresolved"] += 1
                unresolved.append((r.get("endorser"), r.get("descriptor"), how))

    if write and lookup:
        # A SIDECAR, NOT AN EDIT TO derived/. endorsements_<cycle>.json is
        # pipeline output: aggregate and publish rebuild derived/ on every
        # daily run, so a party written into it survives until tomorrow
        # morning and then silently does not. This file is an input the model
        # joins on endorser_key, it lives with the model rather than with the
        # pipeline, and it is reproducible by re-running this script.
        out = Path(__file__).resolve().parent / "endorser_party.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["endorser_key", "endorser", "party", "method"])
            for k, (party, how, nm) in sorted(lookup.items()):
                w.writerow([k, nm, party, how])
        print(f"  wrote {len(lookup)} endorsers to {out.relative_to(REPO_ROOT)}\n")

    print("=" * 62)
    print("CONGRESSIONAL ENDORSER PARTY FILL")
    print("=" * 62)
    for k, v in stats.most_common():
        print(f"  {k:<34} {v:>5}")
    tot = stats["congressional rows"]
    got = sum(v for k, v in stats.items() if k.startswith("resolved")) + stats["already filled"]
    if tot:
        print(f"\n  coverage after fill: {got}/{tot} ({100*got/tot:.0f}%)")
    if unresolved:
        print(f"\n  unresolved ({len(unresolved)}), first 12:")
        seen = set()
        for nm, desc, why in unresolved:
            if nm in seen:
                continue
            seen.add(nm)
            print(f"    {str(nm)[:28]:<28} {why:<22} {str(desc)[:44]}")
            if len(seen) >= 12:
                break
    if not write:
        print("\n  dry run. Re-run with --write to fill the files.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--describe", action="store_true", help="dry run (default)")
    ap.add_argument("--write", action="store_true", help="fill endorser_party in place")
    a = ap.parse_args(argv)
    return run(write=a.write)


if __name__ == "__main__":
    sys.exit(main())
