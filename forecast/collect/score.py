#!/usr/bin/env python3
"""
Stage 6 — scoring. Written before the election, run after it.

    python3 forecast/collect/score.py --coverage      # what will be scorable
    python3 forecast/collect/score.py --self-test     # prove the machinery runs
    python3 forecast/collect/score.py --results FILE  # the real thing, after

WHY THIS EXISTS IN AUGUST

The archive's claim is that it can say afterwards which way of forecasting was
right. Nothing in the pipeline could compute that until this file. Writing it
in November would have been worth much less: every rule in forecast/score/
RULES.md — which horizon counts, what "control" means, whether unopposed races
are in the denominator — changes who comes out ahead, and a rule chosen after
the answer is known cannot be told apart from a rule chosen because the answer
is known.

So the rules are fixed in advance, this module implements exactly them, and it
runs today against a synthetic outcome to prove it works. What it cannot do
today is score anything, because there is nothing to score.

THE RULES FILE IS PART OF THE CODE. Its hash is checked on every run. Change
the rules and the run stops until you say why, and the reason is written into
the output beside the new hash. Revising a rule is fine. Revising it quietly
is the thing this prevents.

WHAT --coverage IS FOR, and it is the reason to build this now rather than
later. It answers, today, which sources will have a value at each future
horizon given what they have published so far. A gap it finds in August is a
gap there is still time to close. The same gap found on election night is a
permanent hole in the archive.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aggregate as AGG      # noqa: E402 — read_parsed, and its notion of a row
from parsers import REALTIME_PROVENANCE   # noqa: E402 — §10, one definition only

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"
RULES_PATH = REPO / "forecast" / "score" / "RULES.md"

# --------------------------------------------------------------------------
# The rules, as constants. Every one of these is stated in RULES.md; if the two
# disagree, RULES.md is what was pre-registered and this file has the bug.
# --------------------------------------------------------------------------
ELECTION_DAY = "2026-11-03"
FINAL_CUTOFF = "2026-11-02"          # newest value on or before polls opening
RESOLUTION_DATE = "2027-01-06"
HORIZON_DAYS = (180, 120, 90, 60, 30, 14, 7, 1)
STALENESS_DAYS = 21                  # §3
LOG_CLIP = 0.001                     # §5
NATIONAL_HOUSE = "NATL_HOUSE_2026"
NATIONAL_SENATE = "NATL_SENATE_2026"
HOUSE_MAJORITY = 218
SENATE_D_CONTROL = 51                # §4: the VP is Republican, so 50 is not control

# The hash of RULES.md as pre-registered on 2026-08-22. See _check_rules.
RULES_SHA256 = "6d67d5c10513156bb3cfb7de279ea59bd544f51f9141e94f7ef266aee68a66e2"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_rules(changed_reason: str | None) -> dict:
    """Refuse to run against rules that have moved, unless told why."""
    if not RULES_PATH.exists():
        raise SystemExit(f"missing pre-registration: {RULES_PATH}")
    got = _sha(RULES_PATH)
    if RULES_SHA256 == "PLACEHOLDER_FILLED_ON_FIRST_RUN":
        # First run after the file was written: report the hash so it can be
        # pinned. Deliberately not self-writing — a program that edits its own
        # integrity check is not an integrity check.
        print(f"  RULES.md sha256 = {got}")
        print("  Pin it: set RULES_SHA256 in score.py to that value and commit.")
        return {"sha256": got, "pinned": False, "changed_reason": None}
    if got != RULES_SHA256:
        if not changed_reason:
            raise SystemExit(
                "REFUSING TO RUN: forecast/score/RULES.md has changed since the "
                f"rules were pinned.\n  pinned:  {RULES_SHA256}\n  on disk: {got}\n"
                "  If the change is deliberate, re-run with\n"
                '    --rules-changed "why this rule changed"\n'
                "  and update RULES_SHA256. The reason is written into the output.")
        return {"sha256": got, "pinned": True, "changed_reason": changed_reason,
                "previous_sha256": RULES_SHA256}
    return {"sha256": got, "pinned": True, "changed_reason": None}


# --------------------------------------------------------------------------
# Horizons
# --------------------------------------------------------------------------

# Set ONLY by --self-test, which re-anchors the horizon grid on the newest date
# in the archive so the whole path — including `final` — runs against real rows.
# Without it every horizon inside the tolerance is in the future, the final
# table comes out empty, and the self-test passes by not testing anything.
_ELECTION_OVERRIDE: str | None = None


def horizon_dates() -> list[tuple[str, str]]:
    """[(label, date)] oldest first, ending with ('final', the day before)."""
    e = dt.date.fromisoformat(_ELECTION_OVERRIDE or ELECTION_DAY)
    out = [(f"{d}d", (e - dt.timedelta(days=d)).isoformat())
           for d in sorted(HORIZON_DAYS, reverse=True)]
    out.append(("final", (e - dt.timedelta(days=1)).isoformat()))
    return out


def pick(series: dict[str, float], horizon: str) -> tuple[str, float] | None:
    """The newest value dated on or before `horizon`, if it is fresh enough.

    §3. Not carried forward past the tolerance and not penalised either: an
    absent value is absent, and coverage is reported separately. Fair posts a
    handful of times a year, and scoring him at a horizon on a number he had
    already superseded would be scoring a forecast he did not make.
    """
    cand = [d for d in series if d <= horizon]
    if not cand:
        return None
    d0 = max(cand)
    gap = (dt.date.fromisoformat(horizon) - dt.date.fromisoformat(d0)).days
    if gap > STALENESS_DAYS:
        return None
    return d0, series[d0]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def brier(p: float, outcome: int) -> float:
    return (p - outcome) ** 2


def log_score(p: float, outcome: int) -> float:
    p = min(max(p, LOG_CLIP), 1 - LOG_CLIP)
    return -math.log(p if outcome else 1 - p)


def calibration(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    """Ten bins of predicted probability against realised frequency."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        got = [(p, o) for p, o in pairs if (lo <= p < hi or (b == bins - 1 and p == 1.0))]
        if not got:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": 0,
                        "mean_p": None, "observed": None})
            continue
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(got),
                    "mean_p": round(statistics.fmean(p for p, _ in got), 4),
                    "observed": round(statistics.fmean(o for _, o in got), 4)})
    return out


# --------------------------------------------------------------------------
# The truth
# --------------------------------------------------------------------------

TRUTH_FIELDS = ("house_seats_D", "senate_seats_D", "house_margin_D",
                "house_margin_D_contested_only", "race_winners",
                "races_dropped", "source", "resolved_on")


def load_truth(path: Path) -> dict:
    t = json.loads(path.read_text())
    missing = [f for f in ("house_seats_D", "senate_seats_D", "house_margin_D")
               if f not in t]
    if missing:
        raise SystemExit(f"results file is missing {missing}")
    t.setdefault("race_winners", {})
    t.setdefault("races_dropped", [])
    t.setdefault("source", "unstated")
    t.setdefault("resolved_on", RESOLUTION_DATE)
    t["house_control_D"] = int(t["house_seats_D"] >= HOUSE_MAJORITY)
    t["senate_control_D"] = int(t["senate_seats_D"] >= SENATE_D_CONTROL)
    return t


def synthetic_truth() -> dict:
    """A made-up outcome, for --self-test ONLY.

    Not a forecast and not a guess worth anything: it exists so the scoring
    path can be exercised end to end while the real answer does not exist. The
    numbers are deliberately unremarkable — a modest Democratic House win and a
    Republican Senate hold — because a self-test that used an extreme outcome
    would hide arithmetic that breaks in the middle of the range.
    """
    return load_truth_dict({
        "house_seats_D": 226, "senate_seats_D": 48,
        "house_margin_D": 5.4, "house_margin_D_contested_only": 4.9,
        "race_winners": {}, "races_dropped": [],
        "source": "SYNTHETIC — self-test only, not a result",
        "resolved_on": "n/a",
    })


def load_truth_dict(d: dict) -> dict:
    d = dict(d)
    d["house_control_D"] = int(d["house_seats_D"] >= HOUSE_MAJORITY)
    d["senate_control_D"] = int(d["senate_seats_D"] >= SENATE_D_CONTROL)
    return d


# --------------------------------------------------------------------------
# Loading forecasts
# --------------------------------------------------------------------------

def national_series(cycle: int) -> dict[tuple, dict[str, float]]:
    """(who, kind, race_id, quantity) -> {date: value}.

    `who` is a source id or a category name; `kind` is "source" or "category",
    so the two never collide and both are scored by the same code.
    """
    out: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in AGG.read_parsed(cycle):
        if r["race_id"] not in (NATIONAL_HOUSE, NATIONAL_SENATE):
            continue
        if r["quantity"] not in ("margin_D", "seats_D", "win_prob_D"):
            continue
        try:
            v = float(r["value"])
        except (TypeError, ValueError):
            continue
        out[(r["source_id"], "source", r["race_id"],
             r["quantity"])][r["snapshot_date"]] = (
                 v, r.get("provenance") or "captured")

    p = DATA / str(cycle) / "derived" / "category_averages.csv"
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["race_id"] not in (NATIONAL_HOUSE, NATIONAL_SENATE):
                    continue
                if r["quantity"] not in ("margin_D", "seats_D", "win_prob_D"):
                    continue
                try:
                    v = float(r["mean"])
                except (TypeError, ValueError):
                    continue
                # A category average is real-time only if every contributor is.
                # §10: an average with any retrospective member is not counted
                # as evidence about what was knowable that day, because there is
                # no honest way to count it partially.
                try:
                    n_retro = int(r.get("n_retrospective") or 0)
                except (TypeError, ValueError):
                    n_retro = 0
                out[(r["category"], "category", r["race_id"],
                     r["quantity"])][r["snapshot_date"]] = (
                         v, "retrospective" if n_retro else "captured")
    return out


def race_series(cycle: int) -> dict[tuple, dict[str, dict[str, float]]]:
    """(source, race_id) -> {date: win_prob_D}, for non-national races."""
    out: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in AGG.read_parsed(cycle):
        if r["race_id"] in (NATIONAL_HOUSE, NATIONAL_SENATE):
            continue
        if r["quantity"] != "win_prob_D" or r["unit"] != "prob":
            continue
        try:
            v = float(r["value"])
        except (TypeError, ValueError):
            continue
        out[(r["source_id"], r["race_id"])][r["snapshot_date"]] = (
            v, r.get("provenance") or "captured")
    return out


# --------------------------------------------------------------------------
# Baselines (§6)
# --------------------------------------------------------------------------

def baselines(cycle: int) -> dict[str, dict]:
    """Naive forecasts, scored by the same code as everything else.

    A score with no reference point is not interpretable. "No change" is the
    one that matters most: a forecast that cannot beat carrying the last result
    forward has not demonstrated anything, and that should be visible in the
    table rather than left to be worked out.
    """
    sys.path.insert(0, str(REPO / "forecast" / "model"))
    try:
        import fundamentals as F
        lost = sum(1 for (_y, _p, _a, pv, _s, _i) in F.HISTORY if pv < 50.0)
        climatology = round(lost / len(F.HISTORY), 4)
        n_hist = len(F.HISTORY)
    except Exception:                                        # noqa: BLE001
        climatology, n_hist = 0.5, 0

    return {
        "coin_flip": {
            "note": "P(D) = 0.5 in both chambers at every horizon",
            "house_win_prob_D": 0.5, "senate_win_prob_D": 0.5,
        },
        "climatology": {
            "note": f"the president's party lost the national House two-party "
                    f"vote in {climatology:.0%} of the {n_hist} midterms the "
                    f"fundamentals model is fitted on; applied as a constant",
            "house_win_prob_D": climatology, "senate_win_prob_D": climatology,
        },
        "no_change": {
            "note": "the 2024 result carried forward unchanged",
            "house_seats_D": 215.0, "senate_seats_D": 47.0,
            "house_margin_D": -2.7,
        },
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score_national(series: dict, truth: dict) -> list[dict]:
    rows = []
    for (who, kind, race, qty), by_date in sorted(series.items()):
        for label, hd in horizon_dates():
            got = pick(by_date, hd)
            if got is None:
                continue
            as_of, (v, prov) = got
            rec = {"who": who, "kind": kind, "race_id": race, "quantity": qty,
                   "horizon": label, "horizon_date": hd, "as_of": as_of,
                   # §10. The headline table is the realtime rows; the rest are
                   # reported separately and never mixed into it.
                   "provenance": prov,
                   "realtime": prov in REALTIME_PROVENANCE,
                   "days_stale": (dt.date.fromisoformat(hd)
                                  - dt.date.fromisoformat(as_of)).days,
                   "value": round(v, 4)}
            if qty == "win_prob_D":
                o = truth["house_control_D"] if race == NATIONAL_HOUSE \
                    else truth["senate_control_D"]
                rec.update(outcome=o, brier=round(brier(v, o), 6),
                           log_score=round(log_score(v, o), 6))
            elif qty == "seats_D":
                a = truth["house_seats_D"] if race == NATIONAL_HOUSE \
                    else truth["senate_seats_D"]
                rec.update(actual=a, error=round(v - a, 4),
                           abs_error=round(abs(v - a), 4))
            elif qty == "margin_D":
                if race != NATIONAL_HOUSE:
                    continue
                a = truth["house_margin_D"]
                rec.update(actual=a, error=round(v - a, 4),
                           abs_error=round(abs(v - a), 4))
            rows.append(rec)
    return rows


def score_races(series: dict, truth: dict) -> tuple[list[dict], dict]:
    """Per-source race-level Brier, plus the common-universe comparison (§5)."""
    winners = truth.get("race_winners") or {}
    dropped = set(truth.get("races_dropped") or ())
    if not winners:
        return [], {}

    covered: dict[str, set] = defaultdict(set)
    picked: dict[tuple, tuple] = {}
    for (src, race), by_date in series.items():
        if race in dropped or race not in winners:
            continue
        got = pick(by_date, horizon_dates()[-1][1])
        if got is None:
            continue
        covered[src].add(race)
        picked[(src, race)] = got

    common = set.intersection(*covered.values()) if covered else set()
    rows = []
    for src, races in sorted(covered.items()):
        pairs, pairs_common = [], []
        for race in races:
            _as_of, (p, _prov) = picked[(src, race)]
            o = int(winners[race] == "D")
            pairs.append((p, o))
            if race in common:
                pairs_common.append((p, o))
        rows.append({
            "who": src, "kind": "source", "horizon": "final",
            "n_races": len(pairs),
            "brier": round(statistics.fmean(brier(p, o) for p, o in pairs), 6),
            "brier_common_universe": (
                round(statistics.fmean(brier(p, o) for p, o in pairs_common), 6)
                if pairs_common else None),
            "n_common": len(pairs_common),
            "calibration": calibration(pairs),
        })
    return rows, {"n_common_races": len(common),
                  "note": "brier_common_universe is computed over the races "
                          "every scored source covered, so the averages are "
                          "comparable; `brier` is over each source's own set"}


# --------------------------------------------------------------------------
# Coverage (§8) — the part that is useful today
# --------------------------------------------------------------------------

def coverage(series: dict) -> dict:
    hs = horizon_dates()
    today = dt.date.today().isoformat()
    by_who: dict[tuple, dict] = defaultdict(dict)
    for (who, kind, race, qty), by_date in series.items():
        cells = {}
        for label, hd in hs:
            if hd > today:
                cells[label] = "future"          # cannot be known yet
            else:
                cells[label] = "yes" if pick(by_date, hd) else "no"
        by_who[(who, kind)][f"{race}/{qty}"] = cells
    return {
        "as_of": today,
        "horizons": [{"label": l, "date": d, "reached": d <= today} for l, d in hs],
        "rows": [{"who": who, "kind": kind, "quantities": qs}
                 for (who, kind), qs in sorted(by_who.items())],
    }


def print_coverage(cov: dict) -> None:
    hs = [h for h in cov["horizons"]]
    reached = [h for h in hs if h["reached"]]
    print(f"  coverage as of {cov['as_of']} — {len(reached)} of {len(hs)} "
          f"horizons already passed")
    print()
    head = "  " + f"{'who':22s} {'quantity':34s} " + " ".join(
        f"{h['label']:>6s}" for h in hs)
    print(head)
    print("  " + "-" * (len(head) - 2))
    for row in cov["rows"]:
        for qty, cells in sorted(row["quantities"].items()):
            marks = " ".join(
                f"{('•' if cells[h['label']] == 'yes' else ('·' if cells[h['label']] == 'no' else ' ')):>6s}"
                for h in hs)
            print(f"  {row['who'][:22]:22s} {qty[:34]:34s} {marks}")
    print()
    print("  • has a value within the staleness tolerance    · does not    "
          "(blank) horizon not yet reached")


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score the archive against the result.")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--coverage", action="store_true",
                    help="report which sources will be scorable at each horizon")
    ap.add_argument("--self-test", action="store_true",
                    help="run the whole scoring path against a SYNTHETIC outcome")
    ap.add_argument("--results", type=Path,
                    help="JSON file of certified results — the real run")
    ap.add_argument("--rules-changed", metavar="REASON",
                    help="acknowledge a deliberate change to RULES.md")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)

    print("=" * 70)
    print(f"score · cycle {a.cycle}")
    print("=" * 70)
    rules = _check_rules(a.rules_changed)

    series = national_series(a.cycle)
    if not series:
        print("  no parsed rows — run parse.py first (and restore the private "
              "archive; parsed/ does not live in the public repo)")
        return 1

    if a.coverage or (not a.self_test and not a.results):
        cov = coverage(series)
        print_coverage(cov)
        if a.out:
            a.out.write_text(json.dumps({"rules": rules, "coverage": cov}, indent=1))
            print(f"\n  wrote {a.out}")
        return 0

    if a.self_test:
        global _ELECTION_OVERRIDE
        newest = max(d for by_date in series.values() for d in by_date)
        _ELECTION_OVERRIDE = (dt.date.fromisoformat(newest)
                              + dt.timedelta(days=1)).isoformat()
        truth = synthetic_truth()
        print("  SELF-TEST: scoring against a SYNTHETIC outcome, with the "
              "horizon grid re-anchored")
        print(f"  on {_ELECTION_OVERRIDE} (the day after the newest row) so "
              f"every horizon lands on real data.")
        print("  These numbers are not results and the real grid is unchanged.")
    else:
        truth = load_truth(a.results)
        if dt.date.today().isoformat() < ELECTION_DAY:
            raise SystemExit(
                "REFUSING TO RUN: a results file was supplied but the election "
                f"({ELECTION_DAY}) has not happened. Use --self-test to "
                "exercise the machinery.")

    nat = score_national(series, truth)
    races, race_meta = score_races(race_series(a.cycle), truth)

    print(f"  {len(nat)} national score(s) across "
          f"{len({r['who'] for r in nat})} forecaster(s)/categor(ies)")
    if races:
        print(f"  {len(races)} race-level score(s), "
              f"{race_meta.get('n_common_races')} races in the common universe")

    # A readable summary of the final horizon, which is the one people ask about.
    print()
    print("  FINAL HORIZON")
    print(f"  {'who':24s} {'quantity':12s} {'value':>9s} {'actual':>9s} {'metric':>10s}")
    for r in sorted(nat, key=lambda r: (r["quantity"], r["who"])):
        if r["horizon"] != "final" or r["race_id"] != NATIONAL_HOUSE:
            continue
        metric = r.get("brier", r.get("abs_error"))
        actual = r.get("outcome", r.get("actual"))
        print(f"  {r['who'][:24]:24s} {r['quantity']:12s} {r['value']:9.3f} "
              f"{actual if actual is not None else '':>9} "
              f"{metric if metric is not None else '':>10}")

    out = {
        "cycle": a.cycle,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rules": {**rules, "path": "forecast/score/RULES.md"},
        "self_test": bool(a.self_test),
        "truth": truth,
        "baselines": baselines(a.cycle),
        "horizons": [{"label": l, "date": d} for l, d in horizon_dates()],
        "staleness_days": STALENESS_DAYS,
        "national": nat,
        "races": races,
        "race_note": race_meta,
    }
    # SELF-TEST OUTPUT DOES NOT GO IN derived/. Everything in that directory is
    # published, and the workflow commits it by allowlist; a file of synthetic
    # scores sitting beside the real ones is exactly the sort of thing that gets
    # picked up by a glob one day and read as a result the next.
    dest = a.out or (DATA / str(a.cycle) / "derived" / "scores.json"
                     if not a.self_test
                     else REPO / "forecast" / "score" / "selftest.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))
    print(f"\n  wrote {dest.relative_to(REPO)}")
    if a.self_test:
        print("  (self-test output. It is named so nothing mistakes it for a result.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
