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


# ---------------------------------------------------------------------------
# Presidential approval, as of a date
# ---------------------------------------------------------------------------
# THE FALLBACK IS THE POINT OF THIS FUNCTION, not an afterthought.
#
# Approval was a typed-in constant for the whole of this archive's life, and a
# constant is invisible: nothing downstream could tell "38.0 because the polls
# say so on this date" from "38.0 because nobody has wired up a feed". Both
# produced the same number and the same chart.
#
# So the two cases are now different objects. A value read from dated polls
# carries the window it was computed over and how many polls were in it. The
# constant carries a source string that says it is a constant, and every model
# that uses it propagates that string into its own output. A backfilled line
# built on the constant is therefore self-identifying: the reason it is flat is
# written on it.
from pathlib import Path as _Path

APPROVAL_FALLBACK = 38.0
APPROVAL_WINDOW_DAYS = 14


# GALLUP HAS STOPPED POLLING PRESIDENTIAL APPROVAL, and the model was fit on
# Gallup. That is the whole problem this constant exists to solve.
#
# The coefficients come from a Gallup-only column running back to 1946, so the
# model wants a number on the GALLUP SCALE. It no longer matters whether we
# would prefer Gallup; there is no more Gallup to prefer. The choice is between
# feeding it something else and pretending the scales match, or measuring the
# gap and correcting for it.
#
# MEASURED, on the 128 dated polls in the archive. Gallup published three
# readings before it stopped, and for each one we can compute what the rest of
# the field said in the same fortnight:
#
#     2025-01-27   gallup 47.0   field 50.53 (n=32)   -3.53
#     2025-02-16   gallup 45.0   field 49.47 (n=36)   -4.47
#     2025-03-16   gallup 43.0   field 47.08 (n=24)   -4.08
#                                        mean -4.03, sd 0.39
#
# Three points is a small sample and the spread across them is a third of a
# point, which is about as stable as a house effect ever looks. The approval
# coefficient is 0.1338 on the president's-party vote share, so 4.03 points of
# approval is 0.54 of share and about 1.08 points of D margin. That is the size
# of the bias we would carry by swapping the series without adjusting, and it
# is the same order as the per-cycle translation error we already carry
# knowingly. Too big to ignore, small enough that the n=3 uncertainty around it
# does not dominate.
#
# WHAT WOULD CHANGE THIS. The offset is measured on early-2025 data and a house
# effect can drift, particularly for a pollster that has since left the field
# and cannot be re-measured. If a better-attested Gallup-to-aggregate offset
# turns up, use it and say so. If the fundamentals model is ever refit on a
# non-Gallup column, delete this and the adjustment together.
GALLUP_HOUSE_EFFECT = -4.03     # gallup minus the field, in approval points
GALLUP_HOUSE_EFFECT_N = 3


# THE HISTORICAL COLUMN IS GALLUP, AND THAT DECIDES WHAT MAY BE FED IN.
#
# Both models that take approval — this one and the referendum model — were fit
# on a Gallup-only approval series running back to 1946. A pollster's house
# effect on presidential approval is worth a couple of points, this model's
# approval coefficient is large, and so substituting a multi-pollster average
# for Gallup does not make the model better informed. It makes it a different
# model reported under the same name, which is the one thing this file exists
# not to do. The CLI help has warned about it since the model was written; the
# archive lookup has to obey the same warning.
#
# Measured on the 2026-08-25 capture: 128 dated approval polls in the archive,
# of which THREE are Gallup, all from early 2025. So the basis we need is
# exactly the basis we do not have, and the honest response is to keep the
# constant and say why rather than quietly swap the series.
# GALLUP POLLS APPROVAL MONTHLY, so the window that suits a dense
# multi-pollster feed is the wrong window for it. Fourteen days would reject
# the very readings the basis check exists to prefer: one Gallup poll in a
# month IS the Gallup series, not a thin sample of it.
APPROVAL_WINDOW_GALLUP = 35


def approval_from_archive(cycle: int, asof: str | None = None,
                          basis: str = "gallup") -> tuple[float, str, int]:
    """(approval, source, n). Three tiers, and each says which one it used.

    1. INDIVIDUAL POLLS whose field period ended on or before `asof`, from the
       raw Wikipedia captures. This is the only tier that makes a past value a
       computation rather than a reconstruction, and it is the one the model
       backfill needs.
    2. THE AGGREGATOR TABLE, if no poll falls in the window. Their numbers are
       real and dated, but they are somebody's model output rather than raw
       interviews, and for a past date they are only as good as the day we
       happened to capture them.
    3. THE CONSTANT, which announces itself.

    COVERAGE, measured on 2026-08-25: 123 individual polls in 2025 and 5 in
    2026. The page has largely stopped carrying monthly nationwide tables for
    the election year. So tier 1 supports a 2025 backfill and does NOT support
    a 2026 one, which is the half the fundamentals models actually need. Silver
    Bulletin publishes the same polls densely and is the intended second
    source; nothing here assumes Wikipedia is the only one.
    """
    import datetime as _dt
    import sys as _sys
    _sys.path.insert(0, str(REPO / "forecast" / "collect"))
    try:
        import wiki_approval as _wa
    except ImportError:
        return (APPROVAL_FALLBACK,
                "hand-set constant — collect/wiki_approval.py not importable",
                0)

    everything = _wa.load_history(cycle)
    gallup = [p for p in everything
              if "gallup" in (p.get("pollster") or "").lower()]
    field = [p for p in everything
             if "gallup" not in (p.get("pollster") or "").lower()]

    # THE END DATE IS THE DATE BEING ASKED ABOUT, never "the last date this
    # particular subset happens to hold". Defaulting it per-subset is how the
    # first version of this returned March 2025's Gallup reading as today's
    # approval: Gallup stopped publishing, so `gallup[-1]` is seventeen months
    # old, and the model ran on it without a word. `asof=None` means now.
    end = asof or _dt.date.today().isoformat()

    # ROUTE ONE IS OFF BY DEFAULT, and the reason is consistency rather than
    # accuracy. Where a real Gallup reading exists it is unarguably the better
    # number: it is the exact basis the coefficients were fit on and it needs
    # no adjustment. But Gallup has left the field, so it exists for the first
    # three months of 2025 and nowhere else, and preferring it would put a
    # visible seam in the middle of every backfilled series — unadjusted Gallup
    # until March 2025, shifted field thereafter, with the join reading as a
    # change in the world rather than a change in the instrument.
    #
    # One instrument for the whole series, even where a better one exists for
    # part of it, is the same trade the chained-index decision made and it goes
    # the same way. `basis="gallup_raw"` still gets the unadjusted readings for
    # anyone comparing the two.
    if basis == "gallup_raw" and gallup:
        cut = (_dt.date.fromisoformat(end)
               - _dt.timedelta(days=APPROVAL_WINDOW_GALLUP)).isoformat()
        win = [p["approve"] for p in gallup if cut <= p["date"] <= end]
        if win:
            return (round(sum(win) / len(win), 2),
                    f"mean of {len(win)} Gallup reading(s) in the "
                    f"{APPROVAL_WINDOW_GALLUP} days to {end} — the basis the "
                    f"coefficients were fit on, unadjusted "
                    f"(Wikipedia, CC BY-SA)", len(win))

    # ROUTE TWO: the rest of the field, shifted onto the Gallup scale.
    polls = field if basis == "gallup" else everything
    adj = GALLUP_HOUSE_EFFECT if basis == "gallup" else 0.0
    if polls and end:
        cut = (_dt.date.fromisoformat(end)
               - _dt.timedelta(days=APPROVAL_WINDOW_DAYS)).isoformat()
        win = [p["approve"] for p in polls if cut <= p["date"] <= end]
        if win:
            raw = sum(win) / len(win)
            note = (f" then shifted {GALLUP_HOUSE_EFFECT:+.2f} onto the Gallup "
                    f"scale the model was fit on (offset measured on "
                    f"{GALLUP_HOUSE_EFFECT_N} overlapping readings)"
                    if adj else "")
            return (round(raw + adj, 2),
                    f"mean of {len(win)} approval poll(s) in the "
                    f"{APPROVAL_WINDOW_DAYS} days to {end}{note} "
                    f"(Wikipedia, CC BY-SA)", len(win))

    # TIER 2 GETS THE SAME SHIFT. An aggregator's average is multi-pollster by
    # definition, so it sits on the field scale rather than the Gallup one and
    # needs the identical correction. An earlier version of this refused tier 2
    # on a Gallup basis entirely, which was right while the plan was to feed
    # the model actual Gallup and wrong once Gallup stopped publishing: it left
    # the model on a hand-set constant forever rather than on the best
    # available number, correctly scaled.
    #
    # Tier 2 only fires for a date at or after the capture that holds it: an
    # aggregator row is stamped with the day it was read, and reaching back
    # with it would put August's reading on a March projection.
    import glob as _glob
    import json as _json
    files = sorted(f for f in _glob.glob(str(
        DATA / str(cycle) / "raw" / "wiki_approval" / "*" / "*.json"))
        if not f.endswith(".meta.json"))
    for f in reversed(files):
        day = _Path(f).parent.name
        if asof and day > asof:
            continue
        try:
            got = _wa.extract(_wa.read_capture(_Path(f)))
        except Exception:
            continue
        vals = [g["approve"] for g in got["aggregators"]
                if g.get("approve") is not None]
        if vals:
            adj = GALLUP_HOUSE_EFFECT if basis == "gallup" else 0.0
            note = (f", shifted {GALLUP_HOUSE_EFFECT:+.2f} onto the Gallup "
                    f"scale" if adj else "")
            return (round(sum(vals) / len(vals) + adj, 2),
                    f"mean of {len(vals)} published aggregator(s) as read on "
                    f"{day}{note} — aggregates, NOT individual polls "
                    f"(Wikipedia, CC BY-SA)", len(vals))

    if basis == "gallup":
        return (APPROVAL_FALLBACK,
                f"hand-set constant — the archive holds {len(polls)} Gallup "
                f"reading(s), and this model was fit on a Gallup-only column, "
                f"so a multi-pollster average would be a different model under "
                f"the same name. Anything driven by this is flat by "
                f"construction and not by evidence", len(polls))
    return (APPROVAL_FALLBACK,
            "hand-set constant — no dated approval in the archive, so anything "
            "driven by this is flat by construction and not by evidence", 0)


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
    # WHEN THE LIVE APPROVAL FEED LANDS, THIS DEFAULT AND ITS CONSUMERS MOVE
    # TOGETHER. Approval is the single largest lever in this family: 0.27
    # margin points per approval point here, and the referendum model in
    # model/academic.py reads the same number through its own --approval.
    # Three things to do, not one:
    #   1. replace this default with the feed, and record its source in
    #      `approval_source` so the methods page stops saying "hand-set";
    #   2. re-run the academic backfill, which currently holds approval
    #      CONSTANT across the whole reconstructed history and says so — any
    #      line driven by approval is flat by construction until it is re-run
    #      against a real approval series;
    #   3. check the referendum model separately. Its fitted approval
    #      coefficient is 0.026, so a live feed will move this model and barely
    #      touch that one, and that difference is the finding rather than a bug.
    ap.add_argument("--approval", type=float, default=None,
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

    # APPROVAL COMES FROM THE ARCHIVE UNLESS FORCED. --approval still wins, so
    # a class can ask what the model says at 45%; what changed is which way
    # round the default runs.
    if a.approval is None:
        a.approval, approval_src, approval_n = approval_from_archive(a.cycle)
    else:
        approval_src = (f"forced to {a.approval} on the command line, not read "
                        f"from the archive")
        approval_n = 0
    pp = predict(b, a.approval, a.income, a.seats_before)   # president's party share
    margin_d = 100 - 2*pp                                    # D minus R
    z = 1.2816                                               # 80%
    out = {
        "cycle": a.cycle,
        "fitted_on": f"{len(HISTORY)} midterms, {HISTORY[0][0]}-{HISTORY[-1][0]}",
        "coefficients": {"intercept": round(b[0],4), "approval": round(b[1],4),
                         "income_growth": round(b[2],4), "seats_before": round(b[3],4)},
        "r2": round(r2,3), "loeo_rmse": round(loeo,3),
        "inputs": {"approval": a.approval, "approval_source": approval_src,
                   "approval_n": approval_n, "income_growth": a.income,
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
    print(f"  approval {a.approval:.2f} — {approval_src}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
