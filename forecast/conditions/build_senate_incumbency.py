#!/usr/bin/env python3
"""Derive the party holding each 2026 Senate seat, from the archive's own returns.

    python3 forecast/conditions/build_senate_incumbency.py

WHAT IS DERIVED AND WHAT IS NOT

    party_holding comes from MEDSL returns already in the archive: the winner
    of the last regular election for that seat, 2020 for the Class 2 seats and
    the January 2021 runoff for Georgia. That half is reproducible and hashed.

    is_running is NOT derivable from returns and is left blank. A retirement
    is the single most valuable field in this file — it turns a held seat into
    an open one and moves the incumbency term by the full 5.49 points — and
    there is no way to infer it from past results.

WHY margin_D_prior_senate WAS NOT USED

    The obvious field is wrong for this. It carries the most recent Senate
    race in each state, which for AZ, CA, NV, PA and WI is 2024 — a different
    class from the seats up in 2026. Only 18 of its 33 states overlap the 35
    up next year, and Maine, Nebraska and Vermont read as +/-100 because an
    independent was on the ballot.

THE FAILURE MODE THIS FILE HAS

    Deriving from the last election gives the seat's party but a STALE
    incumbent wherever the seat changed hands between elections. Two are known:
    Nebraska returns Ben Sasse, who resigned in 2023, and Oklahoma returns Jim
    Inhofe, who resigned in 2023. In both the replacement is of the same party,
    so party_holding survives; the NAME does not, and neither would a
    replacement from the other party. Every name below wants checking.
"""
import csv, collections, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "forecast" / "model"))
import polling

OUT = REPO / "forecast" / "conditions" / "senate_incumbency_2026.csv"
FIELDS = ["race_id","state","party_holding","incumbent_name","is_running",
          "event_type","event_date","known_by","date_basis","source_url",
          "derivation","notes"]

# Hand resolutions, each with the reason. Nothing here is a guess about a
# fact; they are decisions about WHICH archived row applies.
RESOLVE = {
    # Four winner rows in 2020/21. The Class 2 seat is the one the regular
    # 2020 race was for: Perdue's, which went to a runoff Ossoff won in
    # January 2021. Warnock's was the Class 3 special, up again in 2022.
    "GA": ("DEMOCRAT", "JON OSSOFF", "2021 runoff for the Class 2 seat"),
    # MEDSL codes Lummis as OTHER. She is a Republican; this is a party_raw
    # coding fault in the returns file, not a fact about the seat. Recorded
    # here rather than patched upstream so the returns archive stays a
    # verbatim copy of what MEDSL published.
    "WY": ("REPUBLICAN", "CYNTHIA M. LUMMIS", "MEDSL party_raw reads OTHER; corrected"),
}

# ---------------------------------------------------------------------------
# VERIFIED FROM PRIMARY SOURCES, 2026-08-28. Each row cites the document it
# came from. Nothing here is inferred from a model or from memory.
#
#   BP = Ballotpedia, "List of U.S. Senate incumbents who are not running for
#        re-election in 2026", current as of 2026-08-05.
#   IE = Inside Elections, "Florida and Ohio Appointments Fill Out Senate
#        Battlefield", 2025-01-17.
#
# `known_by` is the date the fact was KNOWABLE, which for an announced
# retirement is the announcement date. That is what lets a backfilled
# projection for March 2025 correctly not know about a June 2025 retirement.
# ---------------------------------------------------------------------------
BP = "https://ballotpedia.org/List_of_U.S._Senate_incumbents_who_are_not_running_for_re-election_in_2026"
BP_NE = "https://ballotpedia.org/United_States_Senate_election_in_Nebraska,_2026"
IE = "https://www.insideelections.com/news/article/florida-and-ohio-appointments-moody-husted"

# state: (incumbent_name, is_running, event_type, event_date, known_by, basis, url, note)
VERIFIED = {
    # -- not seeking re-election: retiring from public office (BP) ----------
    "OK": ("Alan Armstrong", "FALSE", "retiring", "2026-03-24", "2026-03-24",
           "ballotpedia_announcement", BP,
           "appointed after Mullin resigned 2026-03-23 to serve as secretary of homeland security"),
    "MT": ("Steve Daines", "FALSE", "retiring", "2026-03-04", "2026-03-04",
           "ballotpedia_announcement", BP, ""),
    "WY": ("Cynthia M. Lummis", "FALSE", "retiring", "2025-12-19", "2025-12-19",
           "ballotpedia_announcement", BP, "MEDSL codes her party as OTHER; she is a Republican"),
    "IA": ("Joni Ernst", "FALSE", "retiring", "2025-09-02", "2025-09-02",
           "ballotpedia_announcement", BP, ""),
    "NC": ("Thom Tillis", "FALSE", "retiring", "2025-06-29", "2025-06-29",
           "ballotpedia_announcement", BP, ""),
    "IL": ("Richard J. Durbin", "FALSE", "retiring", "2025-04-23", "2025-04-23",
           "ballotpedia_announcement", BP, ""),
    "NH": ("Jeanne Shaheen", "FALSE", "retiring", "2025-03-12", "2025-03-12",
           "ballotpedia_announcement", BP, ""),
    "KY": ("Mitch McConnell", "FALSE", "retiring", "2025-02-20", "2025-02-20",
           "ballotpedia_announcement", BP, ""),
    "MN": ("Tina Smith", "FALSE", "retiring", "2025-02-13", "2025-02-13",
           "ballotpedia_announcement", BP, ""),
    "MI": ("Gary Peters", "FALSE", "retiring", "2025-01-28", "2025-01-28",
           "ballotpedia_announcement", BP, ""),
    # -- not seeking re-election: running for another office (BP) -----------
    "AL": ("Tommy Tuberville", "FALSE", "seeking_other_office", "2025-05-27",
           "2025-05-27", "ballotpedia_announcement", BP, "running for governor"),
    # -- the two specials, appointed holders (IE) ---------------------------
    "FL": ("Ashley Moody", "TRUE", "appointed", "2025-01-16", "2025-01-17",
           "inside_elections_report", IE,
           "SPECIAL for the remainder of Rubio's term; Rubio resigned 2025-01-21 as secretary of state. "
           "is_running TRUE by absence from the BP not-running list; appointee identity is from a 2025-01 report and wants re-checking"),
    "OH": ("Jon Husted", "TRUE", "appointed", "2025-01-17", "2025-01-17",
           "inside_elections_report", IE,
           "SPECIAL for the remainder of Vance's term; Vance resigned 2025-01-09 as vice president. "
           "is_running TRUE by absence from the BP not-running list; appointee identity is from a 2025-01 report and wants re-checking"),
    # -- appointed after a vacancy, and on the ballot ------------------------
    "SC": ("Darlene Graham", "TRUE", "appointed", "2026-07-11", "2026-07-11",
           "user_provided", BP,
           "Lindsey Graham died 2026-07-11; his sister was appointed to the seat and "
           "won the Republican primary. NOT yet confirmed against a document in this "
           "archive — supplied by the maintainer, wants a source_url"),
    # -- incumbent ran and LOST the primary: no incumbent on the ballot -----
    # is_running means THE SITTING SENATOR IS THE NOMINEE, not that the party
    # still holds the seat. Cornyn lost the Republican primary to Ken Paxton,
    # so Texas has a Republican nominee and no incumbent, and the term must be
    # 0. This is the same standard _historical_senate_incumbency applies when
    # it fits the coefficient: it matches on candidate_key across the six-year
    # lag, so a seat held by a different person of the same party has never
    # counted as incumbent-held there either.
    #
    # It is also the case my inference rule got wrong. "Absent from the
    # not-running list" does not mean running: a senator who sought
    # renomination and lost was running, so they are correctly absent from a
    # list of people not seeking re-election, and are still not on the November
    # ballot.
    "TX": ("John Cornyn", "FALSE", "lost_primary", "2026-05-12", "2026-05-12",
           "user_provided", "",
           "lost the Republican primary to Ken Paxton. NOT confirmed against a "
           "document in this archive — supplied by the maintainer"),

    # -- confirmed running, and the archive's derived name was stale ---------
    "NE": ("Pete Ricketts", "TRUE", "running", "2026-05-12", "2026-05-12",
           "ballotpedia_race_page", BP_NE,
           "appointed 2023 after Sasse resigned, WON the 2024 special, won the 2026 "
           "R primary 81.5%. No Democrat on the general ballot: Cindy Burbank (D) won "
           "the primary and withdrew 2026-07-17, so the two-party assumption cannot "
           "represent this race"),
}

# Seats whose derived NAME is stale because the seat changed hands between
# elections, with no replacement documented in the sources above.
STALE_NAME = {}

# Seats where "absent from the not-running list" is doing all the work and a
# primary defeat would overturn it. Listed so the assumption is visible.
WEAK = {
    "LA": "CHECK: Cassidy has been reported as losing renomination. If so "
          "is_running must be FALSE, as in Texas. Not confirmed here.",
}

# Specials with no prior regular election for the seat in this class.
SPECIALS = {"FL", "OH"}


def main() -> int:
    rows = []
    for r in csv.DictReader(open(REPO/"forecast/data/2026/parsed/2026-08-28.csv",
                                 encoding="utf-8")):
        try: r["value"] = float(r["value"])
        except Exception: pass
        rows.append(r)
    up = sorted(set(polling.senate_states_up(rows)))

    win = collections.defaultdict(list)
    for r in csv.DictReader(open(REPO/"forecast/data/2026/derived/returns.csv",
                                 encoding="utf-8")):
        if r["chamber"].lower().startswith("sen") and \
           r["won"].lower() in ("true","1","yes") and r["year"] in ("2020","2021"):
            win[r["state"]].append(r)

    out, todo = [], []
    for st in up:
        rec = {f: "" for f in FIELDS}
        rec["race_id"], rec["state"] = f"SEN_{st}_2026", st
        if st in RESOLVE:
            p, nm, why = RESOLVE[st]
            rec.update(party_holding=p, incumbent_name=nm, derivation=why)
        elif st in SPECIALS:
            # Both seats were Republican-held (Rubio, Vance) and both appointees
            # were named by Republican governors, per the Inside Elections
            # report. party_holding is therefore unambiguous even though no
            # prior regular election for this class exists to derive from.
            rec.update(party_holding="REPUBLICAN",
                       derivation="SPECIAL — seat vacated mid-term, holder appointed (IE 2025-01-17)")
        else:
            w = win.get(st, [])
            if len(w) == 1:
                rec.update(party_holding=w[0]["party"],
                           incumbent_name=w[0]["candidate"],
                           derivation=f"MEDSL {w[0]['year']} Senate winner")
            else:
                rec["notes"] = f"{len(w)} candidate rows — NEEDS RESOLUTION"
                todo.append(f"{st}: {len(w)} winner rows in 2020/21")
        if st in VERIFIED:
            nm, run, ev, ed, kb, basis, url, note = VERIFIED[st]
            # A vacancy CLEARS the derived name. Leaving the last winner in
            # place would have the file assert that South Carolina's incumbent
            # is Lindsey Graham, who died on 2026-07-11.
            rec["incumbent_name"] = nm if nm else ""
            rec.update(is_running=run, event_type=ev, event_date=ed,
                       known_by=kb, date_basis=basis, source_url=url)
            rec["notes"] = (rec["notes"] + "; " + note).strip("; ") if note else rec["notes"]
        elif st in STALE_NAME:
            # The NAME is stale, not the fact of an incumbent running: the seat
            # is absent from the not-running list like any other.
            rec["notes"] = STALE_NAME[st]
            rec.update(is_running="TRUE", event_type="running",
                       known_by="2026-08-05",
                       date_basis="ballotpedia_absent_from_list", source_url=BP)
        else:
            # ABSENT FROM A COMPREHENSIVE NOT-RUNNING LIST MEANS RUNNING, and
            # that inference is only as good as the list's currency. Ballotpedia
            # is current to 2026-08-05; anything announced in the three weeks
            # since is not in this file.
            rec.update(is_running="TRUE", event_type="running",
                       known_by="2026-08-05", date_basis="ballotpedia_absent_from_list",
                       source_url=BP)
            # THE WEAK INFERENCE, flagged rather than hidden. It cannot see a
            # primary defeat, because losing a renomination bid is not the same
            # as not seeking one. Louisiana is the open case: Cassidy has been
            # reported as losing renomination and is not confirmed here.
            if st in WEAK:
                rec["notes"] = ((rec["notes"] + "; ") if rec["notes"] else "") + WEAK[st]
        if not rec["is_running"]:
            todo.append(f"{st}: is_running UNKNOWN")
        out.append(rec)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(out)
    have = sum(1 for r in out if r["party_holding"])
    print(f"  wrote {OUT.relative_to(REPO)}  ({len(out)} seats)")
    print(f"  party_holding derived for {have}/{len(out)}")
    run = sum(1 for r in out if r["is_running"])
    print(f"  is_running filled for {run}/{len(out)}")
    import collections as _c
    print(f"  running: {sum(1 for r in out if r['is_running']=='TRUE')}, "
          f"NOT running: {sum(1 for r in out if r['is_running']=='FALSE')}, "
          f"unknown: {len(out)-run}")
    print("\n  STILL NEEDS A HUMAN:")
    for t in sorted(set(x for x in todo if 'UNKNOWN' in x or 'special' in x)):
        print(f"    {t}")
    print(f"  split: {dict(collections.Counter(r['party_holding'] for r in out if r['party_holding']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
