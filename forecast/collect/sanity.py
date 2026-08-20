#!/usr/bin/env python3
"""
Stage 6 — refuse to publish numbers that are obviously wrong.

    python3 forecast/collect/sanity.py            # check, exit 1 on failure
    python3 forecast/collect/sanity.py --explain  # also print what each rule is for

WHY THIS EXISTS

Every bug this pipeline has produced so far has been a PLAUSIBLE number, not a
crash. In one day of work:

  · Idaho appeared at D+11.6 and Alabama at D+3.9, because a workbook sheet
    named "Margin" held the CHANGE in margin and sorted ahead of the real one
  · House seats_D and seats_R both came out 24, from sheets named
    "Seats Dems can Flip"
  · every Senate market in the archive was filed under a state called "OF",
    from "balance OF power", and later under Indiana, Oregon and Maine, from
    "win the Senate IN 2026"
  · the polling model forecast 15 Senate races in states with no 2026 Senate
    race at all

Not one of those raised an exception. Every one produced output that looked
like a forecast, and would have been published unread the moment the daily
Action started pushing. A test suite cannot catch them because they are not
wrong in the code — they are wrong about the world.

So these are assertions about the WORLD, not about the code. They are
deliberately blunt: a Senate cycle has 35 races, a probability lies in [0,1],
the two parties' House seats sum to 435, a national margin does not move nine
points overnight. Anything that trips one of these is either a real bug or a
genuinely extraordinary day, and both deserve a human before they go live.

FAILURE BEHAVIOUR
    Exit 1 and publish nothing. The previous day's page stays up, which is a
    far better failure than a confidently wrong one. The workflow turns a
    non-zero exit into a GitHub issue.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# A 2026 Senate cycle: 33 Class 2 seats plus the Ohio and Florida specials.
EXPECTED_SENATE_RACES = 35
HOUSE_SEATS = 435
# Largest overnight move in a national margin we will publish without a human.
# Real movement of this size exists, but it is rare enough that seeing it
# should mean reading the diff rather than trusting it.
MAX_DAILY_MARGIN_SHIFT = 5.0

POSTAL = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes: list[str] = []

    def ok(self, cond: bool, name: str, detail: str = "") -> bool:
        if cond:
            self.passes.append(name)
        else:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return cond


def check_site_payload(c: Checks, cycle: int) -> None:
    """The file Hugo actually renders. If this is wrong, the page is wrong."""
    p = REPO / "assets" / f"forecast_{cycle}.json"
    if not c.ok(p.exists(), "site payload exists", str(p)):
        return
    try:
        d = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        c.ok(False, "site payload is valid JSON", str(e))
        return
    c.ok(bool(d.get("latest_snapshot")), "site payload has a snapshot date")
    c.ok(bool(d.get("headline")), "site payload has headline rows")

    pm = d.get("polling_model")
    if pm:
        races = pm.get("races") or []
        c.ok(len(races) == EXPECTED_SENATE_RACES,
             "Senate race count",
             f"{len(races)} races, expected {EXPECTED_SENATE_RACES}. Either the "
             f"map changed or a source is feeding races that do not exist.")
        bad_state = [r["state"] for r in races if r.get("state") not in POSTAL]
        c.ok(not bad_state, "Senate races are real states", str(bad_state[:6]))
        bad_p = [f"{r['state']}={r['win_prob_D']}" for r in races
                 if not (0.0 <= float(r.get("win_prob_D", -1)) <= 1.0)]
        c.ok(not bad_p, "Senate probabilities in [0,1]", str(bad_p[:6]))
        bad_m = [f"{r['state']}={r['expected_margin_D']}" for r in races
                 if abs(float(r.get("expected_margin_D", 0))) > 100]
        c.ok(not bad_m, "Senate margins within +/-100", str(bad_m[:6]))
        # A tide that has drifted far outside any plausible generic ballot is
        # the signature of a units error, not an election.
        tide = float(pm.get("tide_D", 0))
        c.ok(abs(tide) <= 25, "national tide is plausible", f"D{tide:+.1f}")


def check_derived(c: Checks, cycle: int) -> None:
    d = DATA / str(cycle) / "derived"
    avg = d / "category_averages.csv"
    if not c.ok(avg.exists(), "category_averages.csv exists"):
        return
    rows = list(csv.DictReader(avg.open(encoding="utf-8")))
    c.ok(bool(rows), "category_averages has rows")

    bad_unit = []
    for r in rows:
        try:
            v = float(r["mean"])
        except (TypeError, ValueError):
            continue
        u = r.get("unit")
        if u == "prob" and not (0.0 <= v <= 1.0):
            bad_unit.append(f"{r['race_id']}/{r['quantity']}={v}")
        elif u == "pct" and abs(v) > 100:
            bad_unit.append(f"{r['race_id']}/{r['quantity']}={v}")
        elif u == "seats" and not (0 <= v <= 535):
            bad_unit.append(f"{r['race_id']}/{r['quantity']}={v}")
    c.ok(not bad_unit, "published values within their unit's range",
         str(bad_unit[:6]))

    bad_race = sorted({r["race_id"] for r in rows
                       if r.get("state") and r["state"] not in POSTAL})
    c.ok(not bad_race, "every published race is a real seat", str(bad_race[:6]))

    # Seat toplines must add up. 24-24 was a real bug that looked like a number.
    seats = {}
    for r in rows:
        if r["quantity"] in ("seats_D", "seats_R") and r["race_id"].startswith("NATL_HOUSE"):
            try:
                seats[r["quantity"]] = float(r["mean"])
            except (TypeError, ValueError):
                pass
    if len(seats) == 2:
        total = seats["seats_D"] + seats["seats_R"]
        c.ok(abs(total - HOUSE_SEATS) <= 1.0, "House seat totals sum to 435",
             f"seats_D {seats['seats_D']} + seats_R {seats['seats_R']} = {total}")


def check_model_freshness(c: Checks, cycle: int) -> None:
    """
    Did every model actually re-run against today's data?

    A model that silently stops updating is the worst failure this pipeline can
    have, because nothing looks broken: the page renders, the number is
    plausible, the date at the top is today's. It just is not the number the
    model would produce if you ran it.

    This happened. fundamentals.py was omitted from the daily workflow, and the
    site served D+9.5 for days after the model had moved to D+10.5, because the
    model had started reading live income data and nobody re-ran it.

    The check: the inputs the model recorded must match what is in the archive
    right now. If FRED has moved and the model has not, the numbers disagree and
    we refuse to publish.
    """
    d = DATA / str(cycle) / "derived"
    fm = d / "fundamentals_model.json"
    if not c.ok(fm.exists(), "fundamentals model exists"):
        return
    model = json.loads(fm.read_text())
    recorded = (model.get("inputs") or {}).get("income_growth")
    if recorded is None:
        c.ok(False, "fundamentals model records its inputs",
             "no inputs.income_growth — cannot verify freshness")
        return

    # What does the archive say right now?
    files = sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")))
    live = None
    for f in reversed(files):
        for r in csv.DictReader(Path(f).open(encoding="utf-8")):
            if r["source_id"] == "fred" and r["quantity"] == "income_growth_ytd":
                live = float(r["value"])
                break
        if live is not None:
            break
    if live is None:
        c.passes.append("fundamentals freshness (no FRED rows yet, skipped)")
        return
    c.ok(abs(float(recorded) - live) < 1e-6,
         "fundamentals model is current",
         f"model was built with income_growth={recorded} but the archive now says "
         f"{live}. Re-run forecast/model/fundamentals.py — it is probably missing "
         f"from the daily workflow.")

    # And the second link in the chain, which is the one that actually broke:
    # the model can be current while the PAYLOAD still carries an older run,
    # if publish.py ran before the model did (or did not run at all). Checking
    # the model against the archive would not have caught that. Checking the
    # payload against the model does.
    site = REPO / "assets" / f"forecast_{cycle}.json"
    if site.exists():
        pub = (json.loads(site.read_text()).get("fundamentals_model") or {}).get("margin_D")
        mod = model.get("margin_D")
        if pub is not None and mod is not None:
            c.ok(abs(float(pub) - float(mod)) < 1e-6,
                 "published page matches the fundamentals model",
                 f"the page says D{float(pub):+.2f} but the model now says "
                 f"D{float(mod):+.2f}. publish.py needs to run after the models.")


def check_movement(c: Checks, cycle: int) -> None:
    """
    Did anything jump overnight?

    Compares the two most recent snapshots of each category's national House
    margin. This is the check that would have caught the Infogram
    'Biggest Shifts' mix-up, where the numbers were internally consistent and
    individually plausible but wrong by ten to thirty points.
    """
    files = sorted(glob.glob(str(DATA / str(cycle) / "derived" / "category_averages.csv")))
    if not files:
        return
    rows = list(csv.DictReader(Path(files[0]).open(encoding="utf-8")))
    by_date: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["race_id"].startswith("NATL_HOUSE") and r["quantity"] == "margin_D":
            try:
                by_date.setdefault(r["snapshot_date"], {})[r["category"]] = float(r["mean"])
            except (TypeError, ValueError):
                pass
    dates = sorted(by_date)
    if len(dates) < 2:
        c.passes.append("day-over-day movement (only one snapshot, skipped)")
        return
    prev, cur = by_date[dates[-2]], by_date[dates[-1]]
    jumps = [f"{k}: {prev[k]:+.1f} -> {cur[k]:+.1f}" for k in cur
             if k in prev and abs(cur[k] - prev[k]) > MAX_DAILY_MARGIN_SHIFT]
    c.ok(not jumps, f"no category moved more than {MAX_DAILY_MARGIN_SHIFT} points overnight",
         "; ".join(jumps))


def check_privacy(c: Checks, cycle: int) -> None:
    """
    Nothing from a gated tier may appear in the published payload.

    aggregate.py already re-derives this from its own output and refuses to
    write. This is the same guarantee checked one step later, against the file
    that actually ships — because the failure that matters is not "the
    aggregator was wrong" but "the wrong bytes reached the site".
    """
    p = REPO / "assets" / f"forecast_{cycle}.json"
    if not p.exists():
        return
    d = json.loads(p.read_text())

    # A `pvi` field in the Senate table is fine: that is OUR statewide
    # reconstruction from CC0 returns, published deliberately. What must never
    # ship is a DISTRICT-level row, because given the national tide a district
    # margin yields the licensed index exactly: PVI = (margin - tide) / 2.
    districts = [r for r in (d.get("polling_model") or {}).get("races", [])
                 if r.get("chamber") == "house" or r.get("district")]
    c.ok(not districts, "no district-level rows in the published payload",
         f"{len(districts)} found — district margins reveal the licensed index")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Refuse to publish obviously wrong numbers.")
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)

    c = Checks()
    check_site_payload(c, a.cycle)
    check_derived(c, a.cycle)
    check_movement(c, a.cycle)
    check_model_freshness(c, a.cycle)
    check_privacy(c, a.cycle)

    print("=" * 68)
    print(f"sanity · cycle {a.cycle}")
    print("=" * 68)
    for name in c.passes:
        print(f"  pass  {name}")
    if c.failures:
        print()
        for f in c.failures:
            print(f"  FAIL  {f}")
        print("-" * 68)
        print(f"  {len(c.failures)} check(s) failed — PUBLISHING NOTHING.")
        print("  Yesterday's page stays up. That is the correct outcome: every")
        print("  bug this pipeline has produced looked like a real number.")
        return 1
    print("-" * 68)
    print(f"  all {len(c.passes)} checks passed — safe to publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
