#!/usr/bin/env python3
"""
Presidential approval, read out of the Silver Bulletin poll list.

WHY THIS EXISTS. wiki_approval.py said it in its own docstring: Wikipedia's
nationwide monthly tables are dense through 2025 and nearly empty for 2026, so
they support a 2025 backfill and not a 2026 one — which is the half the
fundamentals models actually need. Every backfilled date from August 2025 to
May 2026 therefore fell through to the hand-set constant of 38.0, and both
approval-driven models drew a flat line across ten months for no reason except
a missing feed.

Silver's approval sheet closes that gap outright: 1,323 "All polls" rows from
2025-01-20 to 2026-08-21, 55 to 83 a month with no month below 38. The shape of
what comes out of here is deliberately identical to wiki_approval.load_history
so that fundamentals.py can pool the two rather than choose.

PUBLICATION: AGGREGATE_ONLY, for the same reason the generic-ballot parser is.
robots permits collection and there is no stated licence, so we may compute and
publish an average and may not republish his individual rows. Nothing in this
module returns a row to the site; it returns numbers to a model, and the model
publishes a mean.

RAW `approve`, NEVER `adjusted_approve`. The adjusted columns are his model's
output and are revised retroactively — today's file carries today's opinion of
what a poll from last November really said. Using them to reconstruct last
November leaks nine months of hindsight into a number presented as
contemporaneous. This is the identical decision academic.py already recorded
for `net` versus `adjusted_net`, and it goes the same way.

"All polls" ONLY. The file carries the same poll several times over: an overall
reading plus Economy, Immigration, Trade, Cost cuts, plus Adults/Voters and
Strong/Weak splits. 6,010 rows collapse to 1,323 headline ones. Averaging
across subgroups would count a poll up to five times and would also silently
mix job approval with issue approval.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

SUBGROUP = "All polls"


# ---------------------------------------------------------------------------
# THE GALLUP PROBLEM, AND WHY THE ANSWER IS TO STOP TRYING TO SOLVE IT
# ---------------------------------------------------------------------------
# Both models that take approval were fit on a Gallup-only column running back
# to 1946, so they want a number on the Gallup scale, and Gallup stopped
# polling presidential approval after 2025-12-15. There is no more Gallup to
# prefer. The question is what to feed them instead.
#
# THE FIRST ANSWER WAS TO MEASURE THE GAP AND SHIFT. fundamentals.py did that
# on the three overlapping readings Wikipedia carried and got -4.03. This file
# carries TWELVE Gallup readings and they say something the three could not:
#
#     field = every non-Gallup poll        gallup - field = -5.35  (sd 1.88)
#     field = ADULTS-population polls only gallup - field = -1.87  (sd 1.60)
#
# GALLUP POLLS ADULTS. All twelve readings are population "A". Against the
# field's 513 likely-voter, 398 registered-voter and 409 adult polls the gap is
# -7.79, -5.49 and -1.82. Three quarters of what the -4.03 was correcting for
# was not Gallup being Gallup; it was Gallup interviewing a different
# population from most of the field. The historical column is Gallup-of-adults
# for all twenty midterms, so the population is part of the instrument the
# coefficients were fit on, and the comparison set has to be adults.
#
# THE SECOND ANSWER WAS TO SHIFT BY THE REMAINING -1.87, AND THAT IS THE ONE
# THIS FILE NO LONGER DOES. Three things argued against it:
#
#   1. IT DRIFTS. Split the twelve at midyear and the first half averages
#      -1.30, the second -2.34; the fitted slope is -0.14 points a month. A
#      constant fitted on a drifting series and then carried eight months past
#      the last observation is an extrapolation, and by August 2026 the
#      extrapolated value would be near -4.
#   2. AN INDEPENDENT ESTIMATE IS A THIRD THE SIZE. Silver publishes his own
#      house-effect adjustments in the same file. They move Gallup +2.15 and
#      other adults polls +1.43, so his implied Gallup house effect WITHIN
#      adults is about -0.72. Ours is -1.87 on an unweighted fortnightly
#      window. When two estimates of the same quantity differ by more than
#      either one's standard error, neither is a constant worth carrying.
#   3. IT IS THE SMALLEST TERM IN THE PROBLEM. The population choice is worth
#      about 5.4 approval points today, or 1.4 points of D margin. The residual
#      house effect is worth 1.9 points of approval, half a point of margin. We
#      were spending the extrapolation risk on the small half.
#
# SO: THE INSTRUMENT IS "THE AVERAGE OF ADULTS-SAMPLE POLLS", unshifted, for
# the whole series. One instrument end to end, which is the same trade the
# chained-index decision made and it goes the same way. The residual Gallup
# house effect is somewhere between half a point and two points, it can never
# be re-measured because Gallup has left the field, and the honest treatment of
# a quantity like that is to name it and leave it in the error bar rather than
# to correct for a point estimate of it.
#
# WHAT THIS COSTS THE READER, and it is the thing to explain rather than hide:
# the number will sit several points below every published approval tracker,
# because those average the whole field and this averages one population of it.
# On 2026-08-25 the whole field read 40.4 raw and about 38.9 on Silver's
# adjusted scale, while adults alone read 35.0. Both numbers are right about
# different questions. `field_average()` exists so the page can show the
# familiar one beside the one the model eats.
#
# Measured 2026-08-25. The constants stay as documentation of a finding, and
# `basis="gallup_shifted"` still applies the -1.87 for anyone comparing.
GALLUP_VS_ADULTS = -1.87        # gallup minus adults-population field
GALLUP_VS_ADULTS_SD = 1.60
GALLUP_VS_FIELD = -5.35         # gallup minus the whole field
GALLUP_VS_FIELD_SD = 1.88
GALLUP_OVERLAP_N = 12
GALLUP_LAST = "2025-12-15"
SILVER_IMPLIED_GALLUP_WITHIN_ADULTS = -0.72   # from his own adjusted columns

# Populations Gallup's instrument matches. "V" appears three times and is
# unlabelled as to screen; it is left out rather than guessed at.
ADULT_POPULATIONS = ("A",)

# The ladder. Adults first at the tight window, adults again wider, then the
# whole field with the population shift and a loud note. MIN_N is a floor on a
# mean, not a disclosure floor: a single poll is not an approval reading.
WINDOW_DAYS = 14
WINDOW_WIDE = 28
MIN_N = 3


def _poll_file(cycle: int) -> Path | None:
    """The newest captured approval sheet.

    NEWEST IS RIGHT EVEN FOR OLD DATES. He adds polls rather than rotating them
    out, so each file is a superset of its predecessors and the newest one has
    the longest history. This is the same argument academic._silver_poll_file
    makes for the generic ballot, and it holds only because we read the RAW
    columns: a superset in the adjusted columns would be a superset of revised
    numbers.
    """
    base = DATA / str(cycle) / "raw" / "silver_bulletin"
    if not base.exists():
        return None
    for day in sorted((d for d in base.iterdir() if d.is_dir()), reverse=True):
        f = day / "approval_polls.csv"
        if f.exists():
            return f
    return None


def _date(v: str) -> dt.date | None:
    v = (v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


_CACHE: dict[int, list[dict]] = {}


def load_history(cycle: int = 2026) -> list[dict]:
    """[{date, pollster, approve, disapprove, population, source}], oldest first.

    `date` is the poll's END DATE, matching wiki_approval, so the two lists
    pool without either being re-keyed. A poll is dated by when it stopped
    interviewing because that is the last day it could have been known.
    """
    # CACHED, because the backfill asks for it once per archived date and the
    # file is 6,010 rows. Reparsing it 240 times turned a two-second job into a
    # multi-minute one. The cache is keyed on cycle and lives for the process,
    # which is right: a capture that lands mid-run should not change the
    # answers a run already gave.
    if cycle in _CACHE:
        return _CACHE[cycle]
    f = _poll_file(cycle)
    if f is None:
        _CACHE[cycle] = []
        return []
    out: list[dict] = []
    with f.open(encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        for need in ("subgroup", "pollster", "enddate", "approve"):
            if need not in cols:
                raise ValueError(
                    f"column {need!r} missing from {f.name} — the sheet's "
                    f"shape has changed. Columns seen: {cols[:24]}")
        for r in reader:
            if (r.get("subgroup") or "").strip() != SUBGROUP:
                continue
            d0 = _date(r.get("enddate", ""))
            ap = _f(r.get("approve"))
            if d0 is None or ap is None:
                continue
            out.append({
                "date": d0.isoformat(),
                "pollster": (r.get("pollster") or "").strip(),
                "approve": ap,
                "disapprove": _f(r.get("disapprove")),
                "population": (r.get("population") or "").strip().upper(),
                "source": "silver_bulletin",
            })
    out.sort(key=lambda p: (p["date"], p["pollster"]))
    _CACHE[cycle] = out
    return out


def field_average(polls: list[dict], asof: str,
                  days: int = WINDOW_DAYS) -> tuple[float, int] | None:
    """(mean, n) over the WHOLE field — every population, unshifted.

    This is not a model input. It is the number every published approval
    tracker is showing, and it exists so the page can print it beside ours
    instead of leaving a reader to conclude we have made an arithmetic error.
    The gap between the two is a population difference and is worth naming.
    """
    lo = (dt.date.fromisoformat(asof) - dt.timedelta(days=days)).isoformat()
    v = [p["approve"] for p in polls if lo < p["date"] <= asof]
    return (round(statistics.fmean(v), 2), len(v)) if v else None


def approval_on(polls: list[dict], asof: str,
                basis: str = "adults") -> tuple[float, str, int] | None:
    """(value, source string, n) on the adults scale, or None if nothing fits.

    FOUR BASES.
      "adults"         (default) the mean of adults-sample polls, unshifted.
                       The instrument the model is fed, end to end.
      "gallup_shifted" adults minus the measured 1.87. What this returned
                       before 2026-08-25; kept for comparison, not for use.
      "gallup_raw"     an actual Gallup reading where one exists, which is
                       2025 only. For checking the default against the thing
                       it stands in for.
      "all_field"      every population, unshifted — the tracker number.
    """
    end = dt.date.fromisoformat(asof)
    gallup = [p for p in polls if "gallup" in p["pollster"].lower()]
    field = [p for p in polls if "gallup" not in p["pollster"].lower()]

    def win(pool, days):
        lo = (end - dt.timedelta(days=days)).isoformat()
        return [p["approve"] for p in pool if lo < p["date"] <= asof]

    # GALLUP ITSELF IS OFF BY DEFAULT, and this is a deliberate repeat of the
    # call fundamentals.py already made for the Wikipedia route. Where a real
    # Gallup reading exists it is the exact instrument. But it exists for 2025
    # and nowhere after 2025-12-15, so preferring it would put a seam in the
    # middle of the series with 2026 reconstructed and 2025 not, and the join
    # would read as a change in the world rather than in the instrument.
    #
    # The twelve readings are worth more as a standing check on the
    # construction than as twelve points of a different instrument spliced onto
    # the front of it, so that is what they are used for: _self_test
    # re-derives the gap from them on every run.
    if basis == "gallup_raw":
        g = win(gallup, 40)
        if g:
            return (round(statistics.fmean(g), 2),
                    f"Gallup itself — mean of {len(g)} reading(s) in the 40 "
                    f"days to {asof}, the exact basis the coefficients were "
                    f"fit on. Available through {GALLUP_LAST} and not after "
                    f"(Silver Bulletin poll list, aggregate only)", len(g))

    if basis == "all_field":
        got = field_average(polls, asof)
        if got:
            return (got[0],
                    f"mean of {got[1]} approval poll(s) of ANY population in "
                    f"the {WINDOW_DAYS} days to {asof} — the whole-field "
                    f"number, comparable with published approval trackers and "
                    f"NOT what the models are fed (Silver Bulletin poll list, "
                    f"aggregate only)", got[1])

    adults = [p for p in field if p["population"] in ADULT_POPULATIONS]
    shift = GALLUP_VS_ADULTS if basis == "gallup_shifted" else 0.0
    for days in (WINDOW_DAYS, WINDOW_WIDE):
        v = win(adults, days)
        if len(v) >= MIN_N:
            note = (f", shifted {GALLUP_VS_ADULTS:+.2f} for Gallup's residual "
                    f"house effect" if shift else
                    ". Gallup interviewed adults, and so does this average; "
                    "the residual house effect between Gallup and the rest of "
                    "the adults field is under two points and cannot be "
                    "re-measured now that Gallup has stopped polling, so it "
                    "is left in the error bar rather than corrected for")
            return (round(statistics.fmean(v) + shift, 2),
                    f"mean of {len(v)} adults-sample approval poll(s) in the "
                    f"{days} days to {asof}{note} (Silver Bulletin poll list, "
                    f"aggregate only)", len(v))

    # LAST RUNG: the whole field, moved onto the adults scale, and it says so.
    # This is a much larger correction than the one dropped above — 3.5 points
    # against 1.9 — so a series that spends time down here is worth less than
    # one that does not, and the source string has to make that visible.
    pop_gap = GALLUP_VS_FIELD - GALLUP_VS_ADULTS      # field minus adults
    for days in (WINDOW_DAYS, WINDOW_WIDE):
        v = win(field, days)
        if len(v) >= MIN_N:
            return (round(statistics.fmean(v) + pop_gap, 2),
                    f"mean of {len(v)} approval poll(s) of any population in "
                    f"the {days} days to {asof}, moved {pop_gap:+.2f} onto the "
                    f"adults scale — a FALLBACK, used because too few "
                    f"adults-sample polls fell in the window, and a larger "
                    f"correction than the one this file otherwise refuses to "
                    f"make (Silver Bulletin poll list, aggregate only)", len(v))
    return None


def _self_test() -> int:
    fails = 0

    def ck(name, ok, detail=""):
        nonlocal fails
        if not ok:
            fails += 1
            print(f"  FAIL {name}  {detail}")

    ck("date m/d/Y", _date("8/12/2026") == dt.date(2026, 8, 12))
    ck("date iso", _date("2026-08-12") == dt.date(2026, 8, 12))
    ck("date junk", _date("n/a") is None)

    h = load_history(2026)
    if not h:
        print("  (no capture on disk — structural tests only)")
        return fails

    ck("history is sorted", all(h[i]["date"] <= h[i + 1]["date"]
                                for i in range(len(h) - 1)))
    ck("approve in range", all(0 < p["approve"] < 100 for p in h))
    # The subgroup filter is what keeps a poll from being counted five times.
    # If it ever silently stops matching, the count roughly quintuples.
    ck("subgroup filter held", len(h) < 2500,
       f"{len(h)} rows — the 'All polls' filter may have stopped matching")

    # Re-derive the two offsets from the file rather than trusting the
    # constants above. This is the check that catches a re-fit sheet.
    g = [p for p in h if "gallup" in p["pollster"].lower()]
    f = [p for p in h if "gallup" not in p["pollster"].lower()]
    ck("gallup readings present", len(g) >= 10, f"{len(g)} found")
    # THE OFFSETS ARE DOCUMENTATION, NOT INPUTS, since 2026-08-25 — nothing
    # downstream multiplies by them. They are still re-derived here because
    # they are the evidence for the population argument the module is built
    # on, and if a re-shaped sheet ever changed them the argument would need
    # re-reading. A drift of more than a third of a point is a FAIL.
    for pool, want, label in ((f, GALLUP_VS_FIELD, "field"),
                              ([p for p in f if p["population"]
                                in ADULT_POPULATIONS],
                               GALLUP_VS_ADULTS, "adults")):
        diffs = []
        for p in g:
            d0 = dt.date.fromisoformat(p["date"])
            w = [q["approve"] for q in pool
                 if abs((dt.date.fromisoformat(q["date"]) - d0).days) <= 14]
            if len(w) >= MIN_N:
                diffs.append(p["approve"] - statistics.fmean(w))
        if diffs:
            got = statistics.fmean(diffs)
            ck(f"offset vs {label} still {want:+.2f}", abs(got - want) < 0.35,
               f"re-measured {got:+.3f} on n={len(diffs)}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--series", action="store_true",
                    help="print the monthly series this source supports")
    a = ap.parse_args()

    f = _poll_file(a.cycle)
    if f is None:
        raise SystemExit("no silver_bulletin approval capture — run capture.py")
    print(f"  file: {f.parent.name}/{f.name}")
    h = load_history(a.cycle)
    print(f"  {len(h)} '{SUBGROUP}' row(s), {h[0]['date']} .. {h[-1]['date']}")
    by_pop: dict[str, int] = {}
    for p in h:
        by_pop[p["population"]] = by_pop.get(p["population"], 0) + 1
    print(f"  by population: {by_pop}")
    g = [p for p in h if "gallup" in p["pollster"].lower()]
    print(f"  {len(g)} Gallup reading(s), last {g[-1]['date'] if g else '-'}")

    if a.series:
        cur = dt.date.fromisoformat(h[0]["date"]).replace(day=1)
        last = dt.date.fromisoformat(h[-1]["date"])
        print(f"\n  {'month':9} {'approval':>9} {'n':>4}  source")
        while cur <= last:
            nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            on = min(nxt - dt.timedelta(days=1), last)
            got = approval_on(h, on.isoformat())
            if got:
                v, src, n = got
                print(f"  {on.strftime('%Y-%m'):9} {v:9.2f} {n:4}  "
                      f"{src.split(' — ')[0][:52]}")
            cur = nxt

    on = a.asof or dt.date.today().isoformat()
    print()
    for b in ("adults", "all_field", "gallup_shifted"):
        got = approval_on(h, on, basis=b)
        if got:
            print(f"  {b:15} {got[0]:6.2f}  (n={got[2]})")
    got = approval_on(h, on)
    if got:
        print(f"\n  model input: {got[0]:.2f} — {got[1]}")

    fails = _self_test()
    print(f"\n  self-test: {'PASS' if not fails else str(fails) + ' FAILURE(S)'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
