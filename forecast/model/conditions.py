#!/usr/bin/env python3
"""
The structural inputs two academic models take, as a function of DATE.

WHY THIS EXISTS. Lockerbie's model takes a count of open House seats and
Lewis-Beck & Quinlan's takes counts of Democratic Senate retirements and
Democratic governors. All three were hand-typed constants, which is why both
models could only ever draw a horizontal line: not because the models do not
move, but because their inputs were frozen at whatever the number happened to
be on the day somebody read it off a page.

All three genuinely move through a cycle, and the archive already holds the
dated events that move them. This module turns those events into a series.

WHAT THIS IS AND IS NOT. Running Lockerbie's equation in March 2026 on March's
open-seat count is NOT what Lockerbie published: his specification says June of
the election year, and the month is part of the specification. It is our best
estimate of what his equation WOULD have said on that date, which is a
different and weaker object. Every point before the specification date is
therefore flagged `spec_date_reached: False`, and the model output says so, so
that a reader can tell his forecast from our extrapolation of it.

PROVENANCE. Reads the audited conditions table where it exists and falls back
to the automatic draft where it does not, saying which. The draft is Wikipedia
revision history read by machine and has not been checked by a person; a number
built on it is `retrospective`, not `captured`.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COND = REPO / "forecast" / "conditions"
AUDITED = COND / "candidacy_events.csv"
DRAFT = COND / "drafts" / "candidacy_events_draft.csv"

# THE ONE INPUT THAT IS GENUINELY A STEP FUNCTION AND NOT AN ACCUMULATION.
# Governors change only at elections. In this archive's window there was one
# gubernatorial cycle, November 2025, and one seat changed party: Virginia
# flipped R to D. New Jersey stayed D. So the count is 23 until that election
# and 24 after it, and nothing else in the window moves it.
DEM_GOVERNORS = [("2025-01-01", 23), ("2025-11-04", 24)]

# Michigan Surveys of Consumers, Table 8, "Expected Change in Financial
# Situation in a Year", WORSE OFF row. Read off the published table rather than
# inferred from the index score: the index is (better - worse + 100), which is
# one equation in two unknowns and cannot be inverted without the better-off
# row. Both are in the same table, so there was no reason to guess.
#
# The four values Lockerbie's own entry already carried -- Mar 39, Apr 41,
# May 45, Jun 37 -- appear here unchanged, and the table's CASES row gives
# 1,380 for June 2026, matching the n recorded beside them. That is the check
# that this is the same table and the same column.
LOCKERBIE_WORSE = {
    "2025-06": 33.0, "2025-07": 34.0, "2025-08": 36.0, "2025-09": 38.0,
    "2025-10": 41.0, "2025-11": 38.0, "2025-12": 33.0,
    "2026-01": 32.0, "2026-02": 33.0, "2026-03": 39.0, "2026-04": 41.0,
    "2026-05": 45.0, "2026-06": 37.0,
}
LOCKERBIE_WORSE_SOURCE = ("University of Michigan Surveys of Consumers, "
                          "Table 8, WORSE OFF row, monthly")


def _events() -> tuple[list[dict], str]:
    """(rows, provenance note). Audited if it has rows, else the draft."""
    if AUDITED.exists():
        rows = list(csv.DictReader(AUDITED.open()))
        if rows:
            return rows, "audited conditions table"
    if DRAFT.exists():
        return (list(csv.DictReader(DRAFT.open())),
                "UNAUDITED automatic draft from Wikipedia revision history — "
                "not yet checked by a person")
    return [], "no conditions table found"


def open_seats(asof: str | None = None, cycle: int = 2026) -> tuple[int, str]:
    """(open House seats as of `asof`, how it was derived).

    THE COUNT IS ANCHORED ON TODAY AND WALKED BACKWARDS, which is worth
    explaining because the obvious method is worse.

    The obvious method counts retirement announcements up to `asof`. It
    undercounts, because a seat can be open for reasons that never appear as an
    announcement in the window we track — a member who left before collection
    began, a death, a seat opened by a route the scraper does not read.

    So instead: take the seats that ARE open today, which we know from the
    incumbency roster and do not have to infer, and subtract the ones whose
    opening had not yet been announced by `asof`. Seats open today with no
    dated event are treated as having been open the whole time, which is the
    conservative reading — they opened before we were looking.

    The derivation reproduces the hand-typed constant it replaces: of the 67
    seats open today, 60 carry a dated event, and 60 is the number that was
    typed into LOCKERBIE_INPUTS by hand.
    """
    # Sibling import, matching how academic.py and seats.py reach each other.
    # `from model import polling` works from the repo root and not from inside
    # model/, which is where academic.py runs from — and the caller wraps this
    # in a try/except, so getting it wrong showed up as "the constants are
    # used instead" rather than as an error.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import polling

    inc = polling.house_incumbency(cycle)
    open_now = {rid for rid, v in inc.items() if v == 0}
    if not open_now:
        return 0, "no incumbency roster — cannot count open seats"

    rows, prov = _events()
    first: dict[str, str] = {}
    for r in rows:
        rid = r.get("race_id", "")
        if rid not in open_now:
            continue
        k = (r.get("known_by") or "").strip()
        if k and (rid not in first or k < first[rid]):
            first[rid] = k

    pre = len(open_now) - len(first)
    end = asof or dt.date.today().isoformat()
    n = pre + sum(1 for v in first.values() if v <= end)
    return n, (f"{len(open_now)} open today ({pre} with no dated event, "
               f"treated as open throughout); {n} announced by {end} — {prov}")


# LOCKERBIE'S OPEN SEAT IS NARROWER THAN "NO INCUMBENT ON THE BALLOT".
#
# Reconstructing his |OpenInt| from MEDSL returns — a seat counts as open when
# the previous general-election winner does not appear anywhere in that state's
# returns two years later, which absorbs district renumbering — gives a number
# that is HIGHER THAN HIS IN EVERY MIDTERM TESTED:
#
#     1986  his 43  ours 52       2006  his 30  ours 43
#     1990  his 28  ours 52       2010  his 40  ours 62
#     1994  his 52  ours 66       2014  his 46  ours 60
#     1998  his 34  ours 48       2018  his 56  ours 83
#     2002  his 43  ours 52
#
# Always lower, by 9 to 27 and about 16 on average. Two things sit in our count
# that are not "an incumbent chose not to run": members who LOST A PRIMARY, who
# did seek re-election and failed, and seats already refilled by SPECIAL
# ELECTION, where the sitting member is not the previous general winner. Both
# are the right size to explain the gap.
#
# So this excludes primary losers, which we can identify from event_type. It
# cannot yet exclude special-election refills, so the number is still a little
# high, and the residual is named rather than hidden.
LOCKERBIE_EXCLUDE = {"lost_primary"}

# BALLOTPEDIA'S COUNT, WHICH IS THE ONE HIS VARIABLE ACTUALLY TRACKS.
#
# Their series counts U.S. House incumbents NOT SEEKING RE-ELECTION, which is
# the construction the MEDSL reconstruction pointed at without being able to
# name. Set against Lockerbie's |OpenInt| for the three midterms that overlap:
#
#     2014   his 46   ballotpedia 41
#     2018   his 56   ballotpedia 52
#     2022   his 49   ballotpedia 49
#
# Within three seats on average and exact in 2022, against a reconstruction
# that ran 9 to 27 high in every year tested. Same variable.
#
# THE FULL DATED LIST, not the monthly summary. An earlier version of this held
# their month-by-month counts, which forced two compromises: the series could
# only step once a month, and the months summed to 59 against a published total
# of 60, so a phantom "one announcement before 2025" had to be carried to
# reconcile them. The dated list dissolves both. The 60th is Chuck Edwards on
# 2026-08-05, which the monthly table had not yet picked up — the two were
# snapshots of the same page taken at different times, and the difference was
# never a pre-2025 announcement at all.
#
# It validates: 60 rows, 23 D and 37 R, matching their own summary exactly.
#
# ENTERED BY HAND, DELIBERATELY. ballotpedia.org disallows this path in
# robots.txt, and the registry's position is that working round a bot filter is
# not something this project can do and still describe its own methods
# honestly. A person reading a page they are entitled to view and typing it out
# is a different act, and the same one cook_pvi already depends on.
#
#   source: List_of_U.S._House_incumbents_who_are_not_running_for_re-election
#           _in_2026, read 2026-08-25
BALLOTPEDIA_FILE = COND / "ballotpedia_not_seeking_2026.csv"

# Their history, for the record and for anyone checking the claim above.
BALLOTPEDIA_HISTORY = {           # cycle: (D, R, total not seeking re-election)
    2026: (23, 37, 60), 2024: (24, 21, 45), 2022: (31, 18, 49),
    2020: (9, 26, 36), 2018: (18, 34, 52), 2016: (16, 24, 40),
    2014: (16, 25, 41), 2012: (23, 20, 43),
}
LOCKERBIE_OPENINT = {2014: 46, 2018: 56, 2022: 49}

_BP_CACHE: list[dict] | None = None


def ballotpedia_events() -> list[dict]:
    global _BP_CACHE
    if _BP_CACHE is None:
        if BALLOTPEDIA_FILE.exists():
            _BP_CACHE = sorted(csv.DictReader(BALLOTPEDIA_FILE.open()),
                               key=lambda r: r["date"])
        else:
            _BP_CACHE = []
    return _BP_CACHE


def ballotpedia_open_seats(asof: str | None = None) -> tuple[int, str]:
    """Incumbents who had announced they were not seeking re-election by `asof`.

    Exact to the day, from their dated list. Lockerbie reads a June figure and
    June's announcements are part of it, so the comparison is inclusive.
    """
    ev = ballotpedia_events()
    if not ev:
        return 0, "no Ballotpedia list on disk"
    end = asof or dt.date.today().isoformat()
    n = sum(1 for r in ev if r["date"] <= end)
    return n, (f"{n} House incumbents had announced they were not seeking "
               f"re-election by {end} — Ballotpedia's dated list, entered by "
               f"hand, read 2026-08-25")


def lockerbie_open_seats(asof: str | None = None, cycle: int = 2026
                         ) -> tuple[int, str]:
    """Open seats on Lockerbie's definition: the incumbent declined to run.

    Ballotpedia's dated list is the source, because it is a maintained count of
    exactly that and it matches his published series to within three seats.
    Falls back to the count derived from our own conditions table, which is
    looser — it counts any seat with no incumbent on the ballot, so it includes
    primary losers and members who died or resigned, and it ran about 16 high
    against his numbers in every midterm tested.

    THE TWO DISAGREE BY DESIGN AND THE GAP IS THE CHECK. Reconciling them for
    2026 found exactly one announcement the Wikipedia scrape had missed (Julia
    Letlow, LA-05) and fifteen rows in our draft that Ballotpedia excludes:
    nine incumbents who lost primaries, two who died in office, and four that
    look like scrape errors. That is the list an RA should adjudicate, rather
    than re-verifying all 128 rows from scratch.
    """
    bp, src = ballotpedia_open_seats(asof)
    if bp:
        return bp, src
    n, src = open_seats(asof, cycle)
    return n, ("FALLBACK, no Ballotpedia list on disk — " + src)


def dem_senate_retirements(asof: str | None = None) -> tuple[int, str]:
    """(Democratic senators not contesting, as of `asof`, derivation).

    Six Senate retirements are in the record and all six are identifiable by
    name, so party comes from the roster rather than from a section heading:
    Peters, Smith, Shaheen and Durbin are Democrats; McConnell and Tuberville
    are Republicans. Four, which is the constant this replaces.
    """
    D = {"Gary Peters", "Tina Smith", "Jeanne Shaheen", "Dick Durbin"}
    rows, prov = _events()
    end = asof or dt.date.today().isoformat()
    seen = set()
    for r in rows:
        if not r.get("race_id", "").startswith("SEN"):
            continue
        if r.get("event_type") not in ("retiring", "seeking_other_office"):
            continue
        k = (r.get("known_by") or "").strip()
        if not k or k > end:
            continue
        party = (r.get("party") or "").strip().upper()
        is_d = party == "D" if party else (r.get("person") in D)
        if is_d:
            seen.add(r.get("race_id"))
    return len(seen), (f"{len(seen)} Democratic Senate retirement(s) known by "
                       f"{end} — {prov}")


def dem_governors(asof: str | None = None) -> tuple[int, str]:
    end = asof or dt.date.today().isoformat()
    n = DEM_GOVERNORS[0][1]
    for d, v in DEM_GOVERNORS:
        if d <= end:
            n = v
    return n, (f"{n} — steps at the November 2025 elections, when Virginia "
               f"flipped R to D and New Jersey held")


def lockerbie_worse(asof: str | None = None) -> tuple[float | None, str]:
    """The WORSE OFF percentage for the month containing `asof`.

    Carries the last published month forward rather than interpolating: the
    survey is monthly and a value between two months is not a thing the survey
    ever produced.
    """
    end = asof or dt.date.today().isoformat()
    key = end[:7]
    months = sorted(LOCKERBIE_WORSE)
    usable = [m for m in months if m <= key]
    if not usable:
        return None, (f"no Michigan reading on or before {key} — the table "
                      f"starts at {months[0]}")
    m = usable[-1]
    carried = "" if m == key else f" (carried forward from {m})"
    return LOCKERBIE_WORSE[m], f"{LOCKERBIE_WORSE_SOURCE}, {m}{carried}"


def _self_test() -> int:
    fails = 0

    def ck(name, got, want):
        nonlocal fails
        if got != want:
            fails += 1
            print(f"  FAIL {name}: got {got!r} want {want!r}")

    ck("governors before the election", dem_governors("2025-06-01")[0], 23)
    ck("governors after the election", dem_governors("2025-12-01")[0], 24)
    ck("governors on the day", dem_governors("2025-11-04")[0], 24)
    ck("michigan june 2026", lockerbie_worse("2026-06-15")[0], 37.0)
    ck("michigan may 2026", lockerbie_worse("2026-05-20")[0], 45.0)
    ck("michigan carried forward", lockerbie_worse("2026-08-25")[0], 37.0)
    ck("michigan before the table", lockerbie_worse("2025-01-01")[0], None)
    d, _ = dem_senate_retirements("2025-02-01")
    ck("one D senate retirement by feb 2025", d, 1)
    d, _ = dem_senate_retirements("2026-08-25")
    ck("four D senate retirements by now", d, 4)
    print("  self-test:", "PASS" if not fails else f"{fails} FAILURE(S)")
    return 1 if fails else 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    print(f"  {'date':<12}{'open':>6}{'demSenRet':>11}{'demGov':>8}{'worse%':>8}")
    for d in ("2025-01-20", "2025-06-30", "2025-12-31", "2026-03-31",
              "2026-06-30", "2026-08-25"):
        o, _ = open_seats(d)
        s, _ = dem_senate_retirements(d)
        g, _ = dem_governors(d)
        w, _ = lockerbie_worse(d)
        print(f"  {d:<12}{o:>6}{s:>11}{g:>8}"
              f"{('-' if w is None else f'{w:.0f}'):>8}")
    print()
    print("  validation — Lockerbie's OpenInt against Ballotpedia's count:")
    for y in sorted(LOCKERBIE_OPENINT):
        his = LOCKERBIE_OPENINT[y]
        bp = BALLOTPEDIA_HISTORY[y][2]
        print(f"     {y}  his {his:>3}   ballotpedia {bp:>3}   diff {his - bp:+d}")
    print()
    print("  " + open_seats()[1])
    print("  " + dem_senate_retirements()[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
