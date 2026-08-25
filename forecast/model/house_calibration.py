#!/usr/bin/env python3
"""
Calibrate the House model against House results, on the House's own baseline.

    python3 forecast/model/house_calibration.py
    python3 forecast/model/house_calibration.py --json forecast/data/2026/derived/house_calibration.json

-----------------------------------------------------------------------------
WHY THIS EXISTS

`polling.calibrate_sigma` estimates the spread of SENATE margins around a
presidential PVI, and the House model then uses that number. Two different
predictors and two different offices, and the substitution was never tested
because there was nothing to test it against. There is now: DRA district
composites on one side, MEDSL House returns 1976-2024 on the other.

Measured on 2022 and 2024, the residual spread among genuinely competitive
House districts is 10-11 points of margin. The model has been using 6.55. Every
displayed win probability, the majority odds, and -- least obviously -- the
size of the redistricting counterfactual all scale with that number, because
the steepness of the seat-vote curve goes roughly as 1/sigma. Too small a sigma
makes a gerrymander look more decisive than it is.

THE RULE THIS ENFORCES: sigma must be calibrated on the SAME predictor the
model uses. Not a similar one, and not one from a different office.

THREE THINGS COME OUT OF ONE REGRESSION

    margin_D  =  cycle effect  +  b x baseline margin  +  c x incumbency  +  e

  b  Does uniform swing hold? Cook's PVI carries the identity
     margin = tide + 2 x PVI, because PVI is a SHARE deviation and a share gap
     is half a margin gap. Expressed in margin-versus-margin terms that says
     b = 1. The DRA composite has no such identity behind it, so b is a
     question rather than an assumption, and this is where it gets answered.
  c  The incumbency advantage, in points of margin, estimated rather than
     assumed. It has fallen a long way since the 1980s and quoting an old
     number would be worse than quoting none.
  e  The residual, whose SD is sigma -- reported overall and by how
     competitive the district is, because the two differ and only the
     competitive one governs whether seats flip.

WHY INCUMBENCY BELONGS IN THE FUNDAMENTALS, AND WHY IT MAKES LIFE HARDER

Adding it lowers sigma, which improves every probability on the site. It also
deliberately raises the bar for `endorsement_quality`: a quality measure that
merely rediscovers incumbency is not measuring quality, and with incumbency
already in the baseline it cannot get credit for doing so. The quality
coefficient then estimates what it claims to -- the part of a candidate's
performance that incumbency does not explain.

That is a harder test on purpose, and it is the right one.

INCUMBENCY IS DEFINED ACROSS DISTRICT NUMBERS, NOT WITHIN THEM. A member whose
seat is redrawn often runs under a different number, so matching on
(state, district) would record them as a newcomer. The rule here is that a
candidate is an incumbent if they won ANY House seat in that state in the
previous cycle -- which is also the reading the conditions sheet settled on.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "collect"))
REPO = HERE.parents[1]
DERIVED = REPO / "forecast" / "data" / "2026" / "derived"


def ols(X: list[list[float]], y: list[float]) -> list[float]:
    n, k = len(X), len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)]
         + [sum(X[i][a] * y[i] for i in range(n))] for a in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(A[r][c]))
        A[c], A[piv] = A[piv], A[c]
        if abs(A[c][c]) < 1e-12:
            raise ValueError("singular design")
        for r in range(k):
            if r == c:
                continue
            f = A[r][c] / A[c][c]
            for j in range(c, k + 1):
                A[r][j] -= f * A[c][j]
    return [A[i][k] / A[i][i] for i in range(k)]


# States that redrew for 2026. In 2022 and 2024 they voted on their PRIOR
# lines, so a 2026-map baseline describes districts that did not yet exist.
REDREW_2026 = {"TX", "CA", "FL", "OH", "NC", "MO", "TN", "LA", "AL", "UT"}
# States whose "current" export is a 2024-vintage map because they redrew
# mid-decade BEFORE 2026. Right for 2024, wrong for 2022, and we hold no
# 2022-vintage export for them.
REDREW_2024 = {"GA", "NY"}


def load_baseline_cook(cycle: int = 2026) -> dict[tuple, dict[str, float]]:
    """Cook's index as a MARGIN, current and prior lines, from manual.json.

    THE POINT OF HAVING BOTH LOADERS. A sigma is only meaningful against the
    predictor it was fitted to. The House forecast currently builds margins
    from Cook's PVI, so a sigma fitted against the DRA composite -- a different
    index with a different spread -- is not the sigma of the model in use, and
    a slope fitted against DRA would shrink Cook's index by the wrong amount.

    Calibrating against whichever baseline the forecast actually uses is the
    whole rule, so `--baseline` exists and polling.py passes the one it is
    running on. When the House model moves to DRA, the flag moves with it and
    nothing else has to change.
    """
    base = REPO / "forecast" / "data" / str(cycle) / "raw" / "cook_pvi"
    caps = sorted(base.glob("*/manual.json")) if base.exists() else []
    if not caps:
        return {}
    out: dict[tuple, dict[str, float]] = {}
    for r in json.loads(caps[-1].read_text(encoding="utf-8")).get("rows", []):
        st, d = str(r.get("state", "")).upper(), r.get("district")
        if not st or d is None:
            continue
        k = (st, f"{int(d):02d}")
        if r.get("pvi") is not None:
            out.setdefault(k, {})["current"] = 2.0 * float(r["pvi"])
        if r.get("pvi_prior") is not None:
            out.setdefault(k, {})["prior"] = 2.0 * float(r["pvi_prior"])
    return out


def load_baseline_dra() -> dict[tuple, dict[str, float]]:
    """{(state, district): {version: margin}} for both DRA map versions.

    THE MISTAKE THIS PREVENTS, which cost a whole round of wrong conclusions:
    calibrating 2022 and 2024 outcomes against the 2026 lines. In the ten
    states that redrew, those elections were held in districts that did not
    exist yet. 181 districts with a baseline describing someone else's
    territory is measurement error in the regressor, and it does what
    measurement error always does -- attenuates the slope toward zero and
    pushes the unexplained part into whatever else is in the model. Here it
    attenuated the slope from 0.92 to 0.68, inflated sigma among competitive
    districts from 6.32 to 13.63, and more than doubled the apparent
    incumbency advantage, from +6.5 points to +14.8.

    None of that looked like an error. It looked like a finding.
    """
    import dra_import as di
    out: dict[tuple, dict[str, float]] = {}
    for f in (REPO / "forecast" / "data" / "DRA").rglob("*.csv"):
        ver = "current" if "current" in f.parent.name.lower() else "prior"
        st = di.infer(f.name)["state"]
        if not st:
            continue
        rows, _sw = di.read_export(f)
        for r in rows:
            out.setdefault((st, r["district"]), {})[ver] = \
                2.0 * r["two_party_D"] - 100.0
    return out


def baseline_for(base: dict, state: str, district: str,
                 cycle: int) -> float | None:
    """The lines actually in force for that state in that cycle."""
    e = base.get((state, district))
    if not e:
        return None
    if cycle >= 2026:
        return e.get("current")
    if state in REDREW_2026:
        return e.get("prior")           # 2022 and 2024 predate the 2026 redraw
    if cycle <= 2022 and state in REDREW_2024:
        return None                     # no 2022-vintage export exists
    return e.get("current")


def load_house(path: Path) -> list[dict]:
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8")):
        if r["chamber"] != "house" or r["special"] == "True":
            continue
        r["cycle"] = int(r["cycle"])
        for f in ("votes", "margin_D", "two_party_D"):
            r[f] = float(r[f]) if r.get(f) not in ("", None) else None
        for b in ("won", "uncontested", "votes_unreliable", "runoff"):
            r[b] = str(r[b]).lower() == "true"
        out.append(r)
    return out


def incumbency(rows: list[dict]) -> dict[tuple, int]:
    """{(cycle, state, district): +1 D incumbent, -1 R incumbent, 0 open}.

    Keyed on the PERSON and the state, never on the district number, so a
    member redrawn into a renumbered seat is still an incumbent. Both parties
    fielding an incumbent -- which redistricting does produce when two members
    are drawn together -- cancels to 0, which is the honest encoding: neither
    side has the advantage over the other.
    """
    won_by = defaultdict(set)          # (cycle, state) -> {candidate_key}
    party_of = {}
    for r in rows:
        if r["won"] and r["candidate_key"]:
            won_by[(r["cycle"], r["state"])].add(r["candidate_key"])
            party_of[(r["cycle"], r["state"], r["candidate_key"])] = r["party"]
    out: dict[tuple, int] = {}
    for r in rows:
        if not r["candidate_key"]:
            continue
        prev = (r["cycle"] - 2, r["state"])
        if r["candidate_key"] not in won_by.get(prev, ()):
            continue
        if party_of.get((prev[0], prev[1], r["candidate_key"])) != r["party"]:
            continue                    # switched party: not the same seat-hold
        k = (r["cycle"], r["state"], r["district"])
        s = 1 if r["party"] == "DEMOCRAT" else -1 if r["party"] == "REPUBLICAN" else 0
        out[k] = out.get(k, 0) + s
    return out


def variance_components(resid: list[float], groups: list[str]) -> dict:
    """Split residual variance into a state-correlated part and a district part.

    THE NAIVE VERSION IS BIASED AND I USED IT FIRST. Taking the SD of the
    per-state mean residuals looks like the state-level spread, but a state's
    mean is itself noisy -- Delaware's "state effect" is one district's error,
    Vermont's is one district's error, and that sampling noise inflates the
    estimate. Wyoming would appear to have a large state effect purely because
    n=1.

    The one-way random-effects (ANOVA) estimator removes it:

        MSB = between-group mean square, MSW = within-group mean square
        sigma_state^2 = (MSB - MSW) / n_effective

    where n_effective corrects for unequal group sizes. When MSB < MSW the
    estimate is negative, which means the data show no state-level component
    at all; it is clamped at zero and reported, not hidden.

    WHY THIS MATTERS MORE THAN ITS SIZE SUGGESTS. A state-level error is shared
    by every district in that state, so it does not average away across the
    435. It moves the whole delegation together, which is exactly what widens
    the tails of a seat total -- and the tails are where P(majority) lives. An
    independent district error of the same size would barely register there.
    """
    by = defaultdict(list)
    for r, g in zip(resid, groups):
        by[g].append(r)
    by = {g: v for g, v in by.items() if v}
    k = len(by)
    n = sum(len(v) for v in by.values())
    if k < 2 or n <= k:
        return {"ok": False, "why": "not enough groups"}
    grand = sum(sum(v) for v in by.values()) / n
    ssb = sum(len(v) * (statistics.mean(v) - grand) ** 2 for v in by.values())
    ssw = sum(sum((x - statistics.mean(v)) ** 2 for x in v)
              for v in by.values())
    msb, msw = ssb / (k - 1), ssw / (n - k)
    # Unequal group sizes: the ANOVA n_effective, not the plain average.
    n_eff = (n - sum(len(v) ** 2 for v in by.values()) / n) / (k - 1)
    var_state = (msb - msw) / n_eff if n_eff > 0 else 0.0
    naive = statistics.pstdev([statistics.mean(v) for v in by.values()])
    return {"ok": True, "groups": k, "n": n,
            "sigma_state": round(math.sqrt(max(var_state, 0.0)), 2),
            "sigma_district": round(math.sqrt(max(msw, 0.0)), 2),
            "negative_estimate": var_state < 0,
            "naive_sd_of_state_means": round(naive, 2),
            "n_effective": round(n_eff, 2)}


BUCKETS = [("competitive (|margin| < 10)", 0, 10),
           ("lean (10-25)", 10, 25),
           ("safe (25+)", 25, 999)]


def fit(obs: list[dict], use_inc: bool) -> dict:
    """One regression, with a separate national shift per cycle."""
    cycles = sorted({o["cycle"] for o in obs})
    X, y = [], []
    for o in obs:
        row = [1.0 if o["cycle"] == c else 0.0 for c in cycles]
        row.append(o["baseline"])
        if use_inc:
            row.append(float(o["inc"]))
        X.append(row)
        y.append(o["actual"])
    beta = ols(X, y)
    pred = [sum(b * x for b, x in zip(beta, xx)) for xx in X]
    resid = [a - p for a, p in zip(y, pred)]
    n, k = len(y), len(beta)
    dof = math.sqrt(n / max(1, n - k))
    by_bucket = {}
    for name, lo, hi in BUCKETS:
        g = [r for r, o in zip(resid, obs) if lo <= abs(o["baseline"]) < hi]
        if len(g) >= 10:
            by_bucket[name] = {"n": len(g),
                               "sd": round(statistics.pstdev(g) * dof, 2)}
    # The part correlated within a state, which no national shift removes.
    bys = defaultdict(list)
    for r, o in zip(resid, obs):
        bys[(o["cycle"], o["state"])].append(r)
    between = statistics.pstdev([statistics.mean(v) for v in bys.values()])
    vc = variance_components(resid, [f'{o["cycle"]}_{o["state"]}' for o in obs])
    return {
        "n": n, "cycles": cycles,
        "cycle_shift": {str(c): round(b, 2) for c, b in zip(cycles, beta)},
        "slope_baseline": round(beta[len(cycles)], 4),
        "incumbency_pts": (round(beta[len(cycles) + 1], 2) if use_inc else None),
        "sigma_all": round(statistics.pstdev(resid) * dof, 2),
        "sigma_by_bucket": by_bucket,
        "sigma_between_state": round(between, 2),
        "components": vc,
        "r2": round(1 - statistics.pvariance(resid) / statistics.pvariance(y), 4),
    }


def report(base: dict, inc: dict) -> None:
    print("=" * 74)
    print(f"  House calibration on {base['n']} contested district-cycles "
          f"({', '.join(str(c) for c in base['cycles'])})")
    print("=" * 74)
    print("\n  margin_D = cycle shift + b x baseline margin"
          " [+ c x incumbency] + e\n")
    print(f"  {'':<26}{'baseline only':>16}{'+ incumbency':>16}")
    print(f"  {'b (slope on baseline)':<26}{base['slope_baseline']:>16.3f}"
          f"{inc['slope_baseline']:>16.3f}")
    print(f"  {'c (incumbency, margin pts)':<26}{'--':>16}"
          f"{inc['incumbency_pts']:>16.2f}")
    print(f"  {'R-squared':<26}{base['r2']:>16.3f}{inc['r2']:>16.3f}")
    print(f"  {'sigma, all districts':<26}{base['sigma_all']:>16.2f}"
          f"{inc['sigma_all']:>16.2f}")
    for name, _lo, _hi in BUCKETS:
        b = base["sigma_by_bucket"].get(name)
        i = inc["sigma_by_bucket"].get(name)
        if not b:
            continue
        print(f"  {'  ' + name:<26}{b['sd']:>16.2f}"
              f"{(i['sd'] if i else float('nan')):>16.2f}   (n={b['n']})")
    for label, key in (("sigma_state (correlated)", "sigma_state"),
                       ("sigma_district (indep.)", "sigma_district")):
        b = base["components"].get(key)
        i = inc["components"].get(key)
        if b is not None:
            print(f"  {label:<26}{b:>16.2f}{i:>16.2f}")
    print(f"  {'  (naive SD of state means)':<26}"
          f"{base['components'].get('naive_sd_of_state_means', 0):>16.2f}"
          f"{inc['components'].get('naive_sd_of_state_means', 0):>16.2f}")
    print("\n  cycle shifts (the national environment relative to the "
          "composite):")
    for c, v in inc["cycle_shift"].items():
        print(f"    {c}: {v:+.2f} margin pts")

    print("\n  WHAT b MEANS")
    b = inc["slope_baseline"]
    if abs(b - 1.0) < 0.05:
        print(f"    b = {b:.3f}. Uniform swing on the composite holds: a "
              f"district that is\n    one point more Democratic than another "
              f"finishes one point more Democratic.\n    The same identity "
              f"Cook's PVI carries, now measured for DRA rather\n    than "
              f"inherited.")
    elif b < 1.0:
        print(f"    b = {b:.3f}, below 1. Districts are LESS spread out on "
              f"election day than\n    the composite says -- the composite "
              f"over-states how far apart they are,\n    by about "
              f"{100*(1-b):.0f}%. Carrying Cook's 2x identity over to this "
              f"index would have\n    exaggerated every safe seat and "
              f"understated how many are in play.")
    else:
        print(f"    b = {b:.3f}, above 1. Districts finish FURTHER apart than "
              f"the composite\n    says, by about {100*(b-1):.0f}%.")

    print("\n  WHAT c MEANS")
    c = inc["incumbency_pts"]
    print(f"    {c:+.2f} points of margin for holding the seat, estimated on "
          f"these cycles.")
    drop = base["sigma_by_bucket"].get("competitive (|margin| < 10)")
    dropi = inc["sigma_by_bucket"].get("competitive (|margin| < 10)")
    if drop and dropi:
        print(f"    Among competitive districts it takes sigma from "
              f"{drop['sd']:.2f} to {dropi['sd']:.2f},")
        print(f"    a {100*(1-dropi['sd']/drop['sd']):.0f}% reduction. That is "
              f"the bar endorsement_quality\n    now has to clear: whatever it "
              f"explains has to be on top of this.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--returns", default=str(DERIVED / "returns.csv"))
    ap.add_argument("--cycles", default="2022,2024")
    ap.add_argument("--baseline", choices=["dra", "cook"], default="dra",
                    help="which district index to calibrate against. It must "
                         "be the one the forecast uses; see load_baseline_cook")
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    want = {int(x) for x in a.cycles.split(",")}
    rows = load_house(Path(a.returns).expanduser())
    base = (load_baseline_cook(2026) if a.baseline == "cook"
            else load_baseline_dra())
    inc_map = incumbency(rows)

    obs = []
    for r in rows:
        if r["cycle"] not in want or r["runoff"]:
            continue
        # DEMOCRAT rows carry margin_D for the whole race; one row per race.
        if r["party"] != "DEMOCRAT" or r["uncontested"] or r["votes_unreliable"]:
            continue
        bl = baseline_for(base, r["state"], r["district"], r["cycle"])
        if bl is None or r["margin_D"] is None:
            continue
        obs.append({"cycle": r["cycle"], "state": r["state"],
                    "district": r["district"], "baseline": bl,
                    "actual": r["margin_D"],
                    "inc": inc_map.get((r["cycle"], r["state"],
                                        r["district"]), 0)})
    if len(obs) < 50:
        raise SystemExit(f"only {len(obs)} usable observations")

    n_inc = sum(1 for o in obs if o["inc"] != 0)
    print(f"  baseline: {a.baseline}")
    print(f"  {len(obs)} contested races; {n_inc} with an incumbent "
          f"({100*n_inc/len(obs):.0f}%), {len(obs)-n_inc} open")
    res_base = fit(obs, use_inc=False)
    res_inc = fit(obs, use_inc=True)
    report(res_base, res_inc)

    print("\n  COMPARE WITH WHAT THE MODEL USES TODAY")
    try:
        import polling
        cal = polling.calibrate_sigma(2026)
        if cal.get("ok"):
            print(f"    polling.calibrate_sigma (SENATE-based): "
                  f"sigma_total {cal['sigma_total']}, n={cal['n']}, "
                  f"slope {cal['slope']}")
            print(f"    -> sigma_state currently "
                  f"{math.sqrt(max(cal['sigma_total']**2 - polling.SIGMA_NATIONAL**2, polling.SIGMA_STATE_FLOOR**2)):.2f}")
    except Exception as e:
        print(f"    (could not read the Senate calibration: {e})")
    comp = res_inc["sigma_by_bucket"].get("competitive (|margin| < 10)")
    if comp:
        print(f"    HOUSE, competitive districts, with incumbency: "
              f"{comp['sd']:.2f}")
        print("    These are not interchangeable and the House number is the")
        print("    one the House model should be using.")

    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"baseline_only": res_base,
                                 "with_incumbency": res_inc,
                                 "n_obs": len(obs)}, indent=1),
                     encoding="utf-8")
        print(f"\n  wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
