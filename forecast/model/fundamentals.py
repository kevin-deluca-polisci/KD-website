#!/usr/bin/env python3
"""
Stage 3 — the class fundamentals model.

Predicts the president's party share of the national two-party House vote from
approval, real income growth, and seats defended. No polls, no markets, no
candidate quality. Fitted on all 20 midterms 1946-2022 and scored by
leave-one-election-out, because with n=20 an in-sample R-squared is meaningless.

The headline finding, which is worth a class session: approval ALONE has an
R-squared of 0.02. The referendum story only appears once you condition on
exposure. Much of "the midterm penalty" is mean reversion in seat exposure, not
a verdict on the president.

Seats come from a deterministic curve given district baselines, so all the
uncertainty lives in one number (the national margin) and no simulation is
needed — which matters, because the course does not cover simulation.

Publication: `individual`. It is our model; we can publish whatever we like.
"""
from __future__ import annotations
import argparse, csv, glob, json, statistics, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# year, pres_party, gallup_approval, pres_party_house_2pv, seats_before, d_real_income
# Sources: Brookings Vital Statistics on Congress tables 2-2 and 2-4;
# Gallup presidential approval; FRED A229RX0A048NBEA.
HISTORY = [
 (1946,"D",33,45.3,242,-2.16),(1950,"D",39,50.0,263, 7.44),(1954,"R",61,47.4,221,-0.34),
 (1958,"R",57,44.0,201,-0.61),(1962,"D",61,52.5,262, 3.26),(1966,"D",44,51.3,295, 4.12),
 (1970,"R",58,45.6,192, 3.42),(1974,"R",54,41.5,192,-2.03),(1978,"D",49,54.4,292, 3.44),
 (1982,"R",42,44.0,192, 1.21),(1986,"R",63,45.0,182, 2.87),(1990,"R",58,46.0,175, 0.93),
 (1994,"D",46,46.4,258, 1.49),(1998,"D",66,49.5,207, 4.67),(2002,"R",63,52.4,221, 2.10),
 (2006,"R",38,46.7,232, 2.65),(2010,"D",45,46.6,257, 1.04),(2014,"D",44,47.3,201, 2.64),
 (2018,"R",41,45.5,241, 3.02),(2022,"D",41,48.7,222,-6.11),
]

def _lstsq(X, y):
    """Normal equations via Gaussian elimination. No numpy dependency."""
    k = len(X[0]); n = len(y)
    A = [[sum(X[i][a]*X[i][b] for i in range(n)) for b in range(k)] + 
         [sum(X[i][a]*y[i] for i in range(n))] for a in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(A[r][c])); A[c], A[p] = A[p], A[c]
        if abs(A[c][c]) < 1e-12: raise ValueError("singular design matrix")
        for r in range(k):
            if r == c: continue
            f = A[r][c]/A[c][c]
            for j in range(c, k+1): A[r][j] -= f*A[c][j]
    return [A[i][k]/A[i][i] for i in range(k)]

def fit(winsorise_2022=True):
    inc = [r[5] for r in HISTORY]
    floor = min(v for r, v in zip(HISTORY, inc) if r[0] != 2022)
    rows = [(r[2], (floor if (winsorise_2022 and r[0]==2022) else r[5]), r[4], r[3])
            for r in HISTORY]
    X = [[1.0, a, i, s] for a, i, s, _ in rows]
    y = [v for *_, v in rows]
    b = _lstsq(X, y)
    # leave-one-election-out
    errs = []
    for i in range(len(y)):
        Xi = [x for j, x in enumerate(X) if j != i]; yi = [v for j, v in enumerate(y) if j != i]
        bi = _lstsq(Xi, yi)
        errs.append(y[i] - sum(c*x for c, x in zip(bi, X[i])))
    loeo = (sum(e*e for e in errs)/len(errs)) ** 0.5
    ybar = statistics.fmean(y)
    resid = [y[i] - sum(c*x for c, x in zip(b, X[i])) for i in range(len(y))]
    r2 = 1 - sum(e*e for e in resid)/sum((v-ybar)**2 for v in y)
    return b, loeo, r2

# Which of the three FRED variants the model consumes, and why, is argued in
# collect/parsers/fred.py. Short version: the fitted quantity is an annual
# average against an annual average, and income_growth_ytd is the only variant
# with that shape.
INCOME_QUANTITY = "income_growth_ytd"


def income_from_archive(cycle: int) -> tuple[float, str] | None:
    """(value, provenance) from the newest parsed date that carries FRED."""
    for f in sorted(glob.glob(str(DATA / str(cycle) / "parsed" / "*.csv")), reverse=True):
        vals = {}
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["source_id"] == "fred":
                    try:
                        vals[r["quantity"]] = float(r["value"])
                    except (TypeError, ValueError):
                        pass
        if INCOME_QUANTITY in vals:
            months = vals.get("income_ytd_months")
            where = Path(f).stem
            prov = (f"FRED {INCOME_QUANTITY} as of {where}"
                    + (f", {int(months)} month(s) of the year in hand" if months else ""))
            return vals[INCOME_QUANTITY], prov
    return None


def predict(b, approval, income, seats_before):
    return b[0] + b[1]*approval + b[2]*income + b[3]*seats_before

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--approval", type=float, default=38.0,
                    help="president's approval (Gallup basis — do NOT feed a poll "
                         "average, the historical column is Gallup-only and the "
                         "house effect is a couple of points)")
    ap.add_argument("--income", type=float, default=None,
                    help="real income growth, pct. Default: read from the FRED "
                         "capture (income_growth_ytd). Pass a value to override.")
    ap.add_argument("--seats-before", type=float, default=220,
                    help="seats the president's party won last time (R won 220 in 2024)")
    ap.add_argument("--date", default=None)
    a = ap.parse_args(argv)

    b, loeo, r2 = fit()
    if a.income is not None:
        income, income_prov, placeholder = a.income, "command line", False
    else:
        got = income_from_archive(a.cycle)
        if got:
            income, income_prov, placeholder = got[0], got[1], False
        else:
            # No FRED capture yet. Fall back, but say so loudly and mark it in
            # the output so the page can label it rather than implying the
            # number is grounded.
            income, income_prov, placeholder = 1.5, "PLACEHOLDER (no FRED capture found)", True
    a.income = income

    pp = predict(b, a.approval, a.income, a.seats_before)   # president's party share
    margin_d = 100 - 2*pp                                    # D minus R
    z = 1.2816                                               # 80%
    out = {
        "cycle": a.cycle,
        "fitted_on": f"{len(HISTORY)} midterms, {HISTORY[0][0]}-{HISTORY[-1][0]}",
        "coefficients": {"intercept": round(b[0],4), "approval": round(b[1],4),
                         "income_growth": round(b[2],4), "seats_before": round(b[3],4)},
        "r2": round(r2,3), "loeo_rmse": round(loeo,3),
        "inputs": {"approval": a.approval, "income_growth": a.income,
                   "seats_before": a.seats_before,
                   "income_is_placeholder": placeholder,
                   "income_source": income_prov,
                   "approval_source": "hand-set; no live approval feed yet"},
        "pres_party_two_party_vote": round(pp,2),
        "margin_D": round(margin_d,2),
        "margin_D_80_low": round(100-2*(pp+z*loeo),2),
        "margin_D_80_high": round(100-2*(pp-z*loeo),2),
        "sensitivity": [{"approval": v,
                         "margin_D": round(100-2*predict(b,v,a.income,a.seats_before),2)}
                        for v in (34,36,38,40,42)],
    }
    d = DATA / str(a.cycle) / "derived"; d.mkdir(parents=True, exist_ok=True)
    (d / "fundamentals_model.json").write_text(json.dumps(out, indent=2))
    print(f"  fundamentals: D+{out['margin_D']:.1f} "
          f"(80% D+{out['margin_D_80_low']:.1f} to D+{out['margin_D_80_high']:.1f}), "
          f"LOEO {loeo:.2f}")
    if out["inputs"]["income_is_placeholder"]:
        print("  WARNING: income growth is still the 1.5 placeholder — the FRED")
        print("           capture has not run yet. ./forecast/run.sh will fix it.")
    else:
        print(f"  income growth {a.income:+.2f}%  ({income_prov})")
    print(f"  approval {a.approval:.1f} — hand-set, not pulled from a live feed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
