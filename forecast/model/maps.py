#!/usr/bin/env python3
"""
Which district map was in effect on a given date, and the baseline that goes
with it.

    python3 forecast/model/maps.py --self-test
    python3 forecast/model/maps.py --show 2025-12-01

-----------------------------------------------------------------------------
THE PROBLEM THIS FIXES

`seats.py --backfill-history` re-projects every past date from a national
tide, and until now every one of those projections used TODAY'S district
index. Ten states redrew during this cycle and 123 of 435 districts moved, so
a seat count dated March 2025 was being computed on lines that did not exist
until months later. The margin was fine — a national two-party margin does not
care where the lines are — but the seat count and the majority probability
derived from it were counterfactual, and nothing in the archive said so.

-----------------------------------------------------------------------------
WHY A DATED BASELINE IS POSSIBLE AT ALL

The Cook capture carries `pvi` and `pvi_prior` for all 435 districts, and the
pair turns out to be a clean map difference rather than two data vintages:

    312 of 435 districts have pvi EXACTLY equal to pvi_prior
    all 123 that differ are in the ten states that redrew
    not one district differs by less than half a point

If the two columns had been computed from different presidential data, the
unchanged districts would drift by small amounts. None of them do. So
`pvi_prior` is the same district under the previous lines, and subtracting is
subtracting a redraw.

No state redrew twice this cycle, which is what makes two versions enough. A
state that had redrawn twice would need an intermediate the capture cannot
supply, and this module would have to refuse rather than guess.

Missouri is the near miss, and it is handled. It did not redraw twice: it
enacted new lines on 2025-09-28 and had them BLOCKED on 2026-09-11, which
returns it to the same `pvi_prior` it started from. Two versions still
suffice, because the second transition is a return to the first. What it does
need is an END date, so `redistricting_effective.csv` carries an optional
`superseded_date` and a state is on prior lines when the date is before its
flip OR on/after its supersede.

-----------------------------------------------------------------------------
THE RULE

A baseline is not "the old map" or "the new map". It is a PER-STATE selection:
each district takes `pvi` if its state's map was in effect on the date being
projected, and `pvi_prior` otherwise. On 2025-12-01 that is Texas, Missouri,
North Carolina, Ohio, California and Utah on new lines and Florida, Tennessee,
Alabama and Louisiana still on old ones — one assembled 435-row baseline,
mixed by construction.

Effective dates come from `conditions/redistricting_effective.csv`, which is
data and not code, so changing one is changing one cell.

-----------------------------------------------------------------------------
WHAT THIS DOES NOT CHANGE

Every forward flip is done by 2026-06-02. That USED to mean today's published
numbers could not move, and it stopped being true on 2026-09-11, when the US
Supreme Court blocked Missouri's 7-1 map and sent the state back to its 2022
lines for November. A reversion inside the live window moves the CURRENT
number, not just the archive: MO-5 goes from a Republican-leaning seat to a
safely Democratic one.

So both things now change. Projections dated on or after 2026-09-11 use
Missouri's old lines, and the eleven months in between still use the new ones,
because the map was genuinely operative law for that window -- the Missouri
Supreme Court upheld it on 2026-03-24.

And it does not change PROVENANCE. A backfilled seat projection computed on
the correct dated map is still `retrospective` under RULES.md §10 — we are
still computing it now, with a specification chosen now. This makes those rows
accurate. It does not make them admissible as real-time evidence, and the
scoring split stays exactly where it was.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EFFECTIVE = REPO / "forecast" / "conditions" / "redistricting_effective.csv"


def effective_dates(path: Path | None = None) -> dict[str, dict]:
    """{state: {"date": "YYYY-MM-DD", "basis": ..., "notes": ...}}"""
    p = path or EFFECTIVE
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    with p.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            st = (r.get("state") or "").strip().upper()
            d = (r.get("effective_date") or "").strip()
            sup = (r.get("superseded_date") or "").strip()
            if len(st) == 2 and len(d) == 10:
                out[st] = {"date": d, "basis": (r.get("basis") or "").strip(),
                           "superseded": sup if len(sup) == 10 else "",
                           "notes": (r.get("notes") or "").strip()}
    return out


def _state_of(race_id: str) -> str:
    parts = (race_id or "").split("_")
    return parts[1] if len(parts) > 2 else ""


def baseline_asof(current: dict[str, float], prior: dict[str, float],
                  asof: str | None,
                  dates: dict[str, dict] | None = None) -> tuple[dict, dict]:
    """
    Pick a district index per district for the date being projected.

    `current` and `prior` are {race_id: pvi}. Returns (baseline, detail).

    asof=None means "today's map", which is the behaviour every caller had
    before this module existed. That default is deliberate: a caller that
    forgets to pass a date gets what it used to get, not silently something
    else.
    """
    dates = effective_dates() if dates is None else dates
    baseline: dict[str, float] = {}
    on_old: set[str] = set()
    no_prior = 0

    reverted: set[str] = set()

    for rid, v in current.items():
        st = _state_of(rid)
        ent = dates.get(st) or {}
        flip = ent.get("date")
        # A NEW map can stop being operative. Missouri enacted 7-1 lines on
        # 2025-09-28 and the US Supreme Court blocked them on 2026-09-11, so
        # the state votes its previous lines in November. That is a window,
        # not a single flip: the new map really did govern for the eleven
        # months in between, and a backfilled projection dated inside that
        # window must use it.
        #
        # This is the case the module header said it would refuse to guess at.
        # It is not a guess now, because the end date is recorded in the same
        # table as the start date and is just as much an observed fact.
        sup = ent.get("superseded")
        before = bool(asof and flip and asof < flip)
        after = bool(asof and sup and asof >= sup)
        use_prior = before or after
        if after and not before:
            reverted.add(st)
        if use_prior:
            if rid in prior:
                baseline[rid] = prior[rid]
                on_old.add(st)
                continue
            # A state that redrew but has no prior value for this district.
            # Fall back to the current index rather than dropping the seat —
            # 434 districts would silently change the answer more than one
            # slightly wrong district does — and COUNT it, so the caller can
            # see it happened.
            no_prior += 1
        baseline[rid] = v

    redrawn = set(dates)
    detail = {
        "asof": asof,
        "n_districts": len(baseline),
        "states_on_previous_lines": sorted(on_old),
        "states_on_current_lines": sorted(redrawn - on_old),
        # On old lines because the new map was struck down, NOT because the
        # date precedes it. Same baseline, opposite reason, and a reader of
        # the archive should not have to infer which.
        "states_reverted": sorted(reverted & on_old),
        "n_districts_from_prior": sum(
            1 for rid in baseline if _state_of(rid) in on_old and rid in prior),
        "missing_prior": no_prior,
    }
    detail["vintage"] = vintage_label(detail)
    return baseline, detail


def vintage_label(detail: dict) -> str:
    """A short string stamped into every projection saying which map it used.

    A seat count whose baseline is not recorded is a seat count nobody can
    reproduce, and this archive has already been bitten once by a number whose
    inputs were invisible.
    """
    old = detail.get("states_on_previous_lines") or []
    if not detail.get("asof"):
        return "current map (no date supplied)"
    if not old:
        return f"current map as of {detail['asof']}"
    rev = detail.get("states_reverted") or []
    pre = [s for s in old if s not in rev]
    parts = []
    if pre:
        parts.append(f"{','.join(pre)} on previous lines")
    if rev:
        parts.append(f"{','.join(rev)} reverted to previous lines")
    return f"mixed as of {detail['asof']}: " + "; ".join(parts)


def split_rows(rows: list[dict], source: str,
               quantities: tuple[str, str] = ("pvi", "pvi_prior"),
               ) -> tuple[dict, dict]:
    """Pull {race_id: value} for a current/prior quantity pair from parsed rows.

    `quantities` defaults to Cook's pair so every existing caller is unchanged.
    Dave's Redistricting stores the same current/prior structure under
    ("composite_share", "composite_share_prior") because its units are an
    absolute share rather than a deviation from the nation, and filing two
    incompatible scales under one quantity name would make every consumer guess
    which one it had.
    """
    q_cur, q_pri = quantities
    cur: dict[str, float] = {}
    pri: dict[str, float] = {}
    for r in rows:
        if r.get("source_id") != source or r.get("chamber") != "house":
            continue
        rid = r.get("race_id")
        if not rid:
            continue
        q = r.get("quantity")
        try:
            v = float(r["value"])
        except (TypeError, ValueError, KeyError):
            continue
        if q == q_cur:
            cur.setdefault(rid, v)
        elif q == q_pri:
            pri.setdefault(rid, v)
    return cur, pri


# ---------------------------------------------------------------------------
def _self_test() -> int:
    dates = {"TX": {"date": "2025-08-29", "basis": "signed"},
             "FL": {"date": "2026-05-04", "basis": "signed"}}
    cur = {"HOU_TX_09_2026": -9.0, "HOU_FL_16_2026": -3.0,
           "HOU_NY_01_2026": -2.0}
    pri = {"HOU_TX_09_2026": 24.0, "HOU_FL_16_2026": -8.0,
           "HOU_NY_01_2026": -2.0}
    fails = 0

    def check(cond, msg):
        nonlocal fails
        if not cond:
            fails += 1
            print(f"  FAIL {msg}")
        else:
            print(f"  ok   {msg}")

    b, d = baseline_asof(cur, pri, "2025-01-20", dates)
    check(b["HOU_TX_09_2026"] == 24.0, "before any flip, Texas is on old lines")
    check(b["HOU_FL_16_2026"] == -8.0, "before any flip, Florida is on old lines")
    check(d["states_on_previous_lines"] == ["FL", "TX"], "both states flagged")

    b, d = baseline_asof(cur, pri, "2025-12-01", dates)
    check(b["HOU_TX_09_2026"] == -9.0, "after the Texas signature, Texas is new")
    check(b["HOU_FL_16_2026"] == -8.0, "Florida still old on the same date")
    check(d["states_on_previous_lines"] == ["FL"], "only Florida flagged")

    b, d = baseline_asof(cur, pri, "2026-08-01", dates)
    check(b == cur, "after every flip, the baseline IS today's map")
    check("current map as of" in d["vintage"], "vintage says so")

    b, d = baseline_asof(cur, pri, None, dates)
    check(b == cur, "no date supplied behaves exactly as before")

    b, d = baseline_asof(cur, {}, "2025-01-20", dates)
    check(b == cur and d["missing_prior"] == 2,
          "a missing prior falls back to current AND is counted")

    on_the_day, _ = baseline_asof(cur, pri, "2025-08-29", dates)
    check(on_the_day["HOU_TX_09_2026"] == -9.0,
          "the effective date itself counts as the NEW map (>= not >)")

    # --- a map that stopped being operative (Missouri) --------------------
    mo = {"MO": {"date": "2025-09-28", "basis": "signed; blocked_by_scotus",
                 "superseded": "2026-09-11"}}
    mcur = {"HOU_MO_05_2026": -8.0}
    mpri = {"HOU_MO_05_2026": 13.0}

    b, d = baseline_asof(mcur, mpri, "2025-06-01", mo)
    check(b["HOU_MO_05_2026"] == 13.0, "MO before the signature is on old lines")
    check(d["states_reverted"] == [], "  and is NOT flagged as reverted")

    b, d = baseline_asof(mcur, mpri, "2026-03-01", mo)
    check(b["HOU_MO_05_2026"] == -8.0,
          "MO inside the operative window uses the NEW lines")

    b, d = baseline_asof(mcur, mpri, "2026-09-10", mo)
    check(b["HOU_MO_05_2026"] == -8.0, "the day before the block, still new")

    b, d = baseline_asof(mcur, mpri, "2026-09-11", mo)
    check(b["HOU_MO_05_2026"] == 13.0,
          "on the supersede date itself, back to old lines (>= not >)")
    check(d["states_reverted"] == ["MO"], "  and IS flagged as reverted")
    check("reverted to previous lines" in d["vintage"],
          "  the vintage label says reverted, not merely 'previous'")

    b, d = baseline_asof(mcur, mpri, "2026-11-03", mo)
    check(b["HOU_MO_05_2026"] == 13.0, "election day uses the 6-2 lines")

    # A state with no supersede date must behave exactly as before.
    b, d = baseline_asof(cur, pri, "2026-08-01", dates)
    check(b == cur and not d["states_reverted"],
          "states with no supersede date are untouched by the new rule")

    live = effective_dates()
    check(len(live) == 10, f"the real table has 10 states (got {len(live)})")
    check(live["MO"]["superseded"] == "2026-09-11",
          "the real table carries Missouri's supersede date")
    check(sum(1 for v in live.values() if v.get("superseded")) == 1,
          "exactly one state is superseded")
    check(max(v["date"] for v in live.values()) == "2026-06-02",
          "the last FORWARD flip is 2026-06-02")
    # This check used to read "...so today is unaffected". That stopped being
    # true on 2026-09-11, when Missouri reverted. Today IS affected, and a
    # self-test that prints a reassurance it can no longer justify is worse
    # than one that prints nothing.
    check(max([v["date"] for v in live.values()]
              + [v["superseded"] for v in live.values() if v.get("superseded")])
          == "2026-09-11",
          "the most recent map change of ANY kind is Missouri's 2026-09-11 reversion")
    print("\n  self-test:", "PASSED" if not fails else f"{fails} FAILURE(S)")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--show", metavar="DATE",
                    help="print which states were on which lines on a date")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    dates = effective_dates()
    if a.show:
        # Derive this from baseline_asof rather than re-deriving the rule, so
        # the display cannot drift from what the model actually does. It said
        # Missouri was on current lines on 2026-09-15 for exactly that reason:
        # a second copy of the comparison that nobody updated.
        probe = {f"HOU_{s}_01_2026": 0.0 for s in dates}
        _, d = baseline_asof(probe, {k: 1.0 for k in probe}, a.show, dates)
        oldl = d["states_on_previous_lines"]
        rev = set(d["states_reverted"])
        pre = [s for s in oldl if s not in rev]
        print(f"as of {a.show}")
        print(f"  previous lines: {', '.join(pre) or 'none'}")
        print(f"  reverted      : {', '.join(sorted(rev)) or 'none'}")
        print(f"  current lines : {', '.join(d['states_on_current_lines']) or 'none'}")
        return 0

    print(f"{'state':<7}{'effective':<13}{'superseded':<13}basis")
    for st, v in sorted(dates.items(), key=lambda kv: kv[1]["date"]):
        print(f"{st:<7}{v['date']:<13}{v.get('superseded') or '-':<13}{v['basis']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
