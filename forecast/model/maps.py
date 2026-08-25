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

Every flip is done by 2026-06-02, so any projection dated after that gets
exactly today's baseline and today's published numbers do not move at all.
Only the backfilled history changes.

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
            if len(st) == 2 and len(d) == 10:
                out[st] = {"date": d, "basis": (r.get("basis") or "").strip(),
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

    for rid, v in current.items():
        st = _state_of(rid)
        flip = (dates.get(st) or {}).get("date")
        use_prior = bool(asof and flip and asof < flip)
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
    return (f"mixed as of {detail['asof']}: "
            f"{','.join(old)} on previous lines")


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

    live = effective_dates()
    check(len(live) == 10, f"the real table has 10 states (got {len(live)})")
    check(max(v["date"] for v in live.values()) == "2026-06-02",
          "the last flip is 2026-06-02, so today is unaffected")
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
        old = sorted(s for s, v in dates.items() if a.show < v["date"])
        new = sorted(s for s, v in dates.items() if a.show >= v["date"])
        print(f"as of {a.show}")
        print(f"  previous lines: {', '.join(old) or 'none'}")
        print(f"  current lines : {', '.join(new) or 'none'}")
        return 0

    print(f"{'state':<7}{'effective':<13}basis")
    for st, v in sorted(dates.items(), key=lambda kv: kv[1]["date"]):
        print(f"{st:<7}{v['date']:<13}{v['basis']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
