#!/usr/bin/env python3
"""fit_model.py — the pre-registered endorsement-quality fit.

    python3 forecast/endorse_quality/fit_model.py

Runs ONCE. Read PREREGISTRATION.md first: the specification, the sample, the
inclusion rules and the robustness checks are all fixed there, and section 2b
records why measure (b) is descriptive rather than estimated.

    two-party D margin
        = a
        + b1 * district partisanship   (DRA, vintage-matched)
        + b2 * cycle (2024 = 1)
        + b3 * incumbency              (+1 D holds, -1 R holds, 0 open)
        + b4 * endorsement share differential

OLS by normal equations with numpy. statsmodels is not installed here and this
does not need it: five parameters on 75 rows, with HC1 standard errors because
race-level variance plainly is not constant across a sample running from
uncontested to a tossup.
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DERIVED = REPO_ROOT / "forecast" / "data" / "2026" / "derived"
DRA_DIR = REPO_ROOT / "forecast" / "data" / "DRA"

_spec = importlib.util.spec_from_file_location("eq", HERE / "endorse_quality.py")
eq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eq)

NOPARTY = {"Organizations", "Labor unions", "Newspapers", "Political parties"}


# ---------------------------------------------------------------------------
# DRA partisanship, vintage-matched
# ---------------------------------------------------------------------------

def load_dra() -> tuple[dict, dict]:
    """(state, year, type) -> {district: (dem, rep, pop)}, plus a type index.

    The file TYPE matters. Three at-large states are covered by state-
    legislative maps rather than congressional ones -- there is only one
    congressional district, so DRA has no congressional map to publish. Those
    aggregate correctly to a statewide figure and must never serve a
    district-level lookup, which is why the kind is carried rather than
    inferred from the filename at the point of use.
    """
    maps, kind = collections.defaultdict(dict), {}
    raw = []
    for path in glob.glob(str(DRA_DIR / "**" / "*.csv"), recursive=True):
        base = os.path.basename(path)
        m = re.match(r"([A-Z]{2})-(\d{4})-(.+?)-district-statistics", base)
        if not m:
            continue
        raw.append((path, m.group(1), int(m.group(2)), m.group(3),
                    os.path.basename(os.path.dirname(path))))

    # THE ERA FOLDER, AND WHY THE FILENAME YEAR IS NOT ALWAYS THE ANSWER.
    #
    # `maps2018-2020/` holds the 2010-round maps. DRA labels nearly all of them
    # 2020, but they were in effect for BOTH 2018 and 2020, and the vintage rule
    # takes the latest map at or before the race year -- so read literally,
    # every 2018 race would find no map and drop out silently. Ninety-five
    # races, no error message.
    #
    # So for a state with ONE file in that folder, the effective year is pulled
    # back to the era's start. For a state with SEVERAL, the filename years are
    # authoritative and left alone: North Carolina really did run different
    # lines in 2018 and 2020 -- the 2016 remedial map, then the November 2019
    # court-approved one -- and collapsing those to a single era would apply
    # the wrong districts to one of the two cycles.
    era_counts = collections.Counter()
    for _, st, _, _, folder in raw:
        era_counts[(st, folder)] += 1

    for path, st, yr, typ, folder in raw:
        era = re.match(r"maps(\d{4})-(\d{4})$", folder)
        if era and era_counts[(st, folder)] == 1:
            yr = int(era.group(1))
        key = (st, yr, typ)
        kind[key] = ("congressional" if typ.lower().startswith("congressional")
                     else "stateleg")
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                did = (r.get("ID") or "").strip()
                if not did or did == "Un":
                    continue
                try:
                    d, rep = float(r["Dem"] or 0), float(r["Rep"] or 0)
                    pop = float(r.get("Total Pop") or 0)
                except (ValueError, KeyError):
                    continue
                if d + rep > 0:
                    maps[key][did] = (d, rep, pop)
    return maps, kind


def _vintage(maps, kind, st, year, only=None):
    ks = [k for k in maps if k[0] == st and k[1] <= year
          and (only is None or kind[k] == only)]
    return max(ks, key=lambda k: k[1]) if ks else None


def partisanship(maps, kind, chamber, st, dist, year) -> float | None:
    """Two-party Democratic share of the district (House) or state (Senate)."""
    if chamber == "senate" or not dist:
        k = _vintage(maps, kind, st, year)
        if not k:
            return None
        td = tr = 0.0
        for d, rep, pop in maps[k].values():
            w = pop or 1.0
            td += d * w
            tr += rep * w
        return 100 * td / (td + tr) if td + tr else None
    k = _vintage(maps, kind, st, year, only="congressional")
    if not k:
        return None
    e = maps[k].get(str(dist).lstrip("0")) or maps[k].get(str(dist))
    if not e:
        return None
    d, rep, _ = e
    return 100 * d / (d + rep)



# ---------------------------------------------------------------------------
# Measure (c): primary consensus. See PREREGISTRATION 2c.
# ---------------------------------------------------------------------------

def _hp_party(h: str | None) -> str | None:
    """The primary's party, from the section heading.

    `candidate_party` is filled on 2% of primary rows. The heading carries it
    on nearly all of them -- "Republican primary > Endorsements" -- which is
    the difference between 177 usable rows and 14,214.
    """
    h = (h or "").lower()
    if "republican primary" in h or "republican runoff" in h:
        return "R"
    if "democratic primary" in h or "democratic runoff" in h:
        return "D"
    return None


def _hp_district(h: str | None, current) -> str | None:
    if current:
        return str(current).strip().lstrip("0") or None
    m = re.search(r"district\s+(\d+)", h or "", re.I)
    return str(int(m.group(1))) if m else None


def primary_consensus() -> dict:
    """race key -> (differential, contested, share_D, share_R)."""
    per = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for cy in (2018, 2020, 2022, 2024):
        f = DERIVED / f"endorsements_{cy}.json"
        if not f.exists():
            continue
        for r in json.loads(f.read_text()):
            if r.get("phase") not in ("primary", "runoff") or r.get("duplicate_key"):
                continue
            party = r.get("candidate_party") or _hp_party(r.get("heading_path"))
            name = r.get("candidate")
            if party not in ("D", "R") or not name:
                continue
            key = (cy, r.get("chamber"), r.get("state"),
                   _hp_district(r.get("heading_path"), r.get("race_district")))
            per[key][party][name] += 1
    return per


def _share(counts: dict, nominee: str):
    """The nominee's share of their own party's primary endorsements."""
    if not counts:
        return None, 0
    total = sum(counts.values())
    want = eq.surnames_of(nominee)
    got = sum(v for k, v in counts.items() if want & eq.surnames_of(k))
    return (got / total if total else None), len(counts)


# ---------------------------------------------------------------------------
# OLS
# ---------------------------------------------------------------------------

def ols(y: np.ndarray, X: np.ndarray, names: list[str]) -> dict:
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    dof = n - k

    # HC1. A homoskedastic SE is not defensible on a sample that runs from an
    # uncontested seat to a half-point race.
    meat = X.T @ np.diag(resid ** 2) @ X
    cov = XtX_inv @ meat @ XtX_inv * (n / dof)
    se = np.sqrt(np.diag(cov))
    tstat = beta / se

    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "names": names, "beta": beta, "se": se, "t": tstat, "n": n, "k": k,
        "r2": 1 - ss_res / ss_tot,
        "adj_r2": 1 - (ss_res / dof) / (ss_tot / (n - 1)),
        "rmse": float(np.sqrt(ss_res / dof)),
    }


def show(title: str, m: dict) -> None:
    print(f"\n  {title}")
    print(f"  {'term':<26}{'coef':>10}{'HC1 se':>10}{'t':>8}")
    print("  " + "-" * 54)
    for nm, b, s, t in zip(m["names"], m["beta"], m["se"], m["t"]):
        star = "***" if abs(t) > 2.68 else "**" if abs(t) > 2.01 else "*" if abs(t) > 1.67 else ""
        print(f"  {nm:<26}{b:>10.3f}{s:>10.3f}{t:>8.2f} {star}")
    print(f"  n={m['n']}  adj R2={m['adj_r2']:.3f}  RMSE={m['rmse']:.2f}")


# ---------------------------------------------------------------------------

def assemble(include_primary=False):
    maps, kind = load_dra()
    look = {}
    sc = HERE / "endorser_party.csv"
    if sc.exists():
        with sc.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                look[r["endorser_key"]] = r["party"]

    pri = primary_consensus()
    rows = [r for r in eq.build(include_primary=include_primary) if r["joined"]]
    keep = []
    for r in rows:
        p = partisanship(maps, kind, r["chamber"], r["state"], r["district"], r["cycle"])
        if p is None or r["share_D"] is None:
            continue
        r = dict(r)
        r["dra_D"] = p
        key = (r["cycle"], r["chamber"], r["state"], r["district"])
        cD, nD = _share(pri.get(key, {}).get("D", {}), r["cand_D"])
        cR, nR = _share(pri.get(key, {}).get("R", {}), r["cand_R"])
        r["pri_diff"] = (cD - cR) if (cD is not None and cR is not None) else None
        r["pri_contested"] = 1.0 if (nD > 1 or nR > 1) else 0.0
        keep.append(r)
    return keep, look


def descriptive_cross(rows, look):
    """Measure (b) as a table, not a coefficient. See PREREGISTRATION 2b."""
    def cat(r):
        return (r.get("category_label") or r.get("category") or "")
    fit = {(r["cycle"], r["chamber"], r["state"], r["district"]) for r in rows}
    agg = collections.Counter()
    for cy in (2018, 2020, 2022, 2024):
        f = DERIVED / f"endorsements_{cy}.json"
        for e in json.loads(f.read_text()):
            if e.get("phase") != "general" or e.get("duplicate_key") or cat(e) in NOPARTY:
                continue
            p = e.get("candidate_party")
            k = (cy, e.get("chamber"), e.get("state"),
                 (str(e.get("race_district") or "").strip().lstrip("0") or None))
            if p not in ("D", "R") or k not in fit:
                continue
            ep = e.get("endorser_party") or look.get(e.get("endorser_key") or "")
            if ep in ("D", "R"):
                agg["known"] += 1
                if ep != p:
                    agg["cross"] += 1
    return agg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)

    rows, look = assemble()
    if not rows:
        print("no rows assembled")
        return 1

    y = np.array([r["margin_D"] for r in rows], float)
    dra = np.array([r["dra_D"] for r in rows], float)
    c24 = np.array([1.0 if r["cycle"] == 2024 else 0.0 for r in rows])
    inc = np.array([float(r["incumbent"]) for r in rows])
    shr = np.array([float(r["share_D"]) for r in rows])
    one = np.ones(len(rows))

    print("=" * 60)
    print("ENDORSEMENT QUALITY — the pre-registered fit")
    print("=" * 60)
    print(f"  sample: {len(rows)} races   "
          f"({sum(1 for r in rows if r['chamber']=='senate')} senate, "
          f"{sum(1 for r in rows if r['chamber']=='house')} house)")
    print(f"  outcome: two-party D margin, mean {y.mean():+.1f}, sd {y.std(ddof=1):.1f}")

    X = np.column_stack([one, dra, c24, inc, shr])
    names = ["(intercept)", "DRA partisanship", "cycle 2024",
             "incumbency", "endorsement share"]
    show("PRIMARY", ols(y, X, names))

    # Registered robustness: incumbency is a control that cannot be fully
    # verified, so its removal must not move the endorsement coefficient.
    show("ROBUSTNESS — incumbency dropped",
         ols(y, np.column_stack([one, dra, c24, shr]),
             ["(intercept)", "DRA partisanship", "cycle 2024", "endorsement share"]))

    # Registered robustness: primaries folded in. Reported, never substituted.
    prim, _ = assemble(include_primary=True)
    if prim:
        yp = np.array([r["margin_D"] for r in prim], float)
        Xp = np.column_stack([
            np.ones(len(prim)),
            np.array([r["dra_D"] for r in prim], float),
            np.array([1.0 if r["cycle"] == 2024 else 0.0 for r in prim]),
            np.array([float(r["incumbent"]) for r in prim]),
            np.array([float(r["share_D"]) for r in prim]),
        ])
        show(f"ROBUSTNESS — primaries included (n={len(prim)})", ols(yp, Xp, names))

    # Registered robustness: the House lag/redistricting worry lives here.
    sen = [r for r in rows if r["chamber"] == "senate"]
    if len(sen) > 10:
        ys = np.array([r["margin_D"] for r in sen], float)
        Xs = np.column_stack([
            np.ones(len(sen)),
            np.array([r["dra_D"] for r in sen], float),
            np.array([1.0 if r["cycle"] == 2024 else 0.0 for r in sen]),
            np.array([float(r["incumbent"]) for r in sen]),
            np.array([float(r["share_D"]) for r in sen]),
        ])
        show(f"ROBUSTNESS — Senate only (n={len(sen)})", ols(ys, Xs, names))

    # MEASURE (c). Fit on its own subsample and reported beside the primary,
    # never merged into it: the sample is different, so the coefficients are
    # not comparable line by line and printing them in one table would invite
    # exactly that comparison.
    sub = [r for r in rows if r.get("pri_diff") is not None]
    if len(sub) > 15:
        ys = np.array([r["margin_D"] for r in sub], float)
        cols = [np.ones(len(sub)),
                np.array([r["dra_D"] for r in sub], float),
                np.array([1.0 if r["cycle"] == 2024 else 0.0 for r in sub]),
                np.array([float(r["incumbent"]) for r in sub]),
                np.array([float(r["share_D"]) for r in sub]),
                np.array([float(r["pri_diff"]) for r in sub]),
                np.array([float(r["pri_contested"]) for r in sub])]
        nm = names + ["primary consensus", "primary contested"]
        show(f"WITH MEASURE (c) — primary consensus (n={len(sub)})",
             ols(ys, np.column_stack(cols), nm))
        show(f"  (c) — incumbency dropped (n={len(sub)})",
             ols(ys, np.column_stack([cols[0], cols[1], cols[2], cols[4], cols[5], cols[6]]),
                 [n for n in nm if n != "incumbency"]))
        base = ols(ys, np.column_stack(cols[:5]), names)
        print(f"\n    same {len(sub)} races WITHOUT (c): adj R2={base['adj_r2']:.3f}"
              f"  vs {ols(ys, np.column_stack(cols), nm)['adj_r2']:.3f} with it")

    agg = descriptive_cross(rows, look)
    print("\n  MEASURE (b), DESCRIPTIVE — not estimated, see PREREGISTRATION 2b")
    nz = sum(1 for r in rows if r.get("cross_diff", 0) != 0)
    print(f"    cross-party endorsements: {agg['cross']} of {agg['known']} known-party "
          f"({100*agg['cross']/max(agg['known'],1):.0f}%)")
    print(f"    races with a non-zero differential: {nz} of {len(rows)}")
    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
