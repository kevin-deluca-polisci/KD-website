#!/usr/bin/env python3
"""Run the PLSC 2219 class model for one date or a range of dates.

    python3 forecast/class/run_class.py                  # today
    python3 forecast/class/run_class.py --date 2026-10-05
    python3 forecast/class/run_class.py --backfill --since 2026-01-05

The model is `class_forecast.R`, an unmodified copy of the course script
(see SOURCE.md). This runner only sets the script's four inputs for each date
and runs it. It does not change the model.

Inputs for a date D:
    approval   the site's average of the published approval aggregators,
               latest value on or before D (forecast/data/2026/derived/
               approval.json, series "aggregate")
    gas        percent change in the average EIA weekly U.S. regular retail
               gas price (FRED GASREGW): the latest 52 weeks dated on or
               before D against the 52 weeks before those
    income     percent change in real disposable income per capita (FRED
               A229RX0): the latest 12 months released by D against the 12
               months before those. A month is treated as released on the
               last day of the following month.
    The same one-year rule is used for every date, so the series is
    comparable from start to finish. The course script takes a 2026 level
    and computes the change from 2025, so the runner passes the 2025 value
    times (1 + change). `--economy course` uses the course's own definition
    instead (2026 average to date against 2025).

    Consumer sentiment (FRED UMCSENT, the same 12-month windows) is recorded
    for the alternative national models in national_variants.R. The class
    model does not use it.

District maps: the course race file has each district's partisan lean on
the lines used in November 2026. For an earlier date, districts in states
whose map in force on that date was different get their lean shifted by the
change between the two maps, measured from the site's district data
(forecast/model/maps.py, forecast/conditions/redistricting_effective.csv).
Candidates and incumbency are unchanged. The adjusted race file is written
only to a temporary folder; no district lean is saved.

FRED values are the current vintage, not the values available on each past
date. Past dates computed by this runner are recomputations with today's
model, and the site labels them that way.

The class model is shown on the site only. Its files live here, outside
forecast/data/, and are not part of the data archive.

Nothing is computed for dates after the lock, 1 November 2026.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPT = HERE / "class_forecast.R"
INPUTS = HERE / "inputs"
OUT = HERE / "output"
APPROVAL_JSON = REPO / "forecast" / "data" / "2026" / "derived" / "approval.json"

LOCK = dt.date(2026, 11, 1)
FIRST = dt.date(2025, 1, 20)         # start of the series (inauguration)
FIRST_2026 = dt.date(2026, 1, 5)     # first 2026 weekly gas reading (--economy course)
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
UA = "PLSC 2219 Forecast Archive (Yale University) (+https://kevinmdeluca.com/forecast/2026/)"

TIMESERIES = OUT / "class_timeseries.csv"
SENATE_HIST = OUT / "class_senate_history.csv"
INPUT_LOG = OUT / "class_inputs.csv"
LATEST_RACES = OUT / "forecast_class_latest.csv"
LATEST_SIMS = OUT / "seat_sims_class_latest.csv"

# The input lines in the course script, matched exactly. If the course script
# changes shape, the runner stops rather than guessing.
INPUT_LINES = {
    "AS_OF": r'^AS_OF\s*<-.*$',
    "APPROVAL_2026": r'^APPROVAL_2026\s*<-.*$',
    "GAS_2026": r'^GAS_2026\s*<-.*$',
    "REAL_INCOME_2026": r'^REAL_INCOME_2026\s*<-.*$',
    "INPUT_SOURCES": r'^INPUT_SOURCES\s*<-.*$',
    "DATA_DIR": r'^DATA_DIR\s*<-.*$',
    "OUT_DIR": r'^OUT_DIR\s*<-.*$',
}


# --------------------------------------------------------------------------
# district maps

sys.path.insert(0, str(REPO / "forecast" / "model"))
import maps  # noqa: E402

PARSED = REPO / "forecast" / "data" / "2026" / "parsed"
ELECTION = "2026-11-03"


def district_maps() -> tuple[dict, dict] | None:
    """Current and previous-lines district lean from the newest parsed file
    that has them. Private data: used in memory only."""
    if not PARSED.exists():
        return None
    for f in sorted(PARSED.glob("*.csv"), reverse=True):
        with open(f, newline="", encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("source_id") == "cook_pvi"]
        if rows:
            cur, pri = maps.split_rows(rows, "cook_pvi")
            if cur:
                return cur, pri
    return None


def map_shift(day: str, dm: tuple[dict, dict] | None) -> tuple[dict, str]:
    """{race_id: change in lean} for districts not on their November lines."""
    if dm is None:
        return {}, "November 2026 lines (no map data available)"
    cur, pri = dm
    final, _ = maps.baseline_asof(cur, pri, ELECTION)
    base, detail = maps.baseline_asof(cur, pri, day)
    shift = {rid: base[rid] - final[rid] for rid in base
             if rid in final and abs(base[rid] - final[rid]) > 1e-9}
    if not shift:
        return {}, "November 2026 lines"
    states = sorted({rid.split("_")[1] for rid in shift})
    return shift, f"{', '.join(states)} on the lines in force on {day}"


# --------------------------------------------------------------------------
# inputs

def fred_series(series_id: str, cache: Path | None = None) -> list[tuple[dt.date, float]]:
    """(date, value) pairs from FRED's public CSV, or from a local copy."""
    if cache and cache.exists():
        text = cache.read_text()
    else:
        req = urllib.request.Request(FRED.format(series_id), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as fh:
            text = fh.read().decode("utf-8")
    out = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 2 or not re.match(r"\d{4}-\d{2}-\d{2}$", r[0]):
            continue
        try:
            out.append((dt.date.fromisoformat(r[0]), float(r[1])))
        except ValueError:
            continue           # FRED writes "." for a missing value
    if not out:
        raise RuntimeError(f"FRED {series_id}: no observations read")
    return out


def approval_points() -> list[tuple[dt.date, float]]:
    d = json.loads(APPROVAL_JSON.read_text())
    pts = d["series"]["aggregate"]["points"]
    return sorted((dt.date.fromisoformat(p["date"]), float(p["approve"])) for p in pts)


def _month_end(y: int, m: int) -> dt.date:
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def _released_months(income, day: dt.date) -> list[tuple[dt.date, float]]:
    """Monthly values published by `day` (a month counts as published on the
    last day of the following month)."""
    out = []
    for d, v in income:
        nm_y, nm_m = (d.year + (d.month == 12), d.month % 12 + 1)
        if _month_end(nm_y, nm_m) <= day:
            out.append((d, v))
    return sorted(out)


def _mean(xs) -> float:
    return statistics.fmean(xs)


def one_year(day: dt.date, gas, income, sentiment) -> dict | None:
    """The one-year economic changes for `day`, the same rule for every date.

    gas: mean of the latest 52 weekly prices dated on or before `day` against
    the mean of the 52 before them. income and sentiment: mean of the latest
    12 released months against the 12 before them."""
    weeks = [v for d, v in gas if d <= day]
    months = [v for _, v in _released_months(income, day)]
    if len(weeks) < 104 or len(months) < 24:
        return None
    g1, g0 = _mean(weeks[-52:]), _mean(weeks[-104:-52])
    i1, i0 = _mean(months[-12:]), _mean(months[-24:-12])
    rec = {"gas_12m": round(g1, 4), "gas_prev_12m": round(g0, 4),
           "gas_1yr": round(100 * (g1 / g0 - 1), 4),
           "income_12m": round(i1, 1), "income_prev_12m": round(i0, 1),
           "income_1yr": round(100 * (i1 / i0 - 1), 4),
           "income_last_month": _released_months(income, day)[-1][0].strftime("%Y-%m")}
    sm = [v for _, v in _released_months(sentiment, day)] if sentiment else []
    if len(sm) >= 24:
        rec.update({"sentiment_12m": round(_mean(sm[-12:]), 3),
                    "sentiment_prev_12m": round(_mean(sm[-24:-12]), 3),
                    "sentiment_last_month":
                        _released_months(sentiment, day)[-1][0].strftime("%Y-%m")})
    return rec


def inputs_for(day: dt.date, approval, gas, income, sentiment, base_2025: dict,
               rule: str = "one_year") -> dict | None:
    """The inputs for `day`.

    rule "one_year" (default): the one-year changes from one_year(), for
    every date. The course script computes the change from its 2025 row, so
    it is given 2026 values equal to the 2025 values times (1 + change).

    rule "course": the course's own definition from 2026-01-05 on (2026
    average to date against 2025); one-year changes before that.
    """
    appr = [(d, v) for d, v in approval if d <= day]
    if not appr:
        return None
    econ = one_year(day, gas, income, sentiment)
    if econ is None:
        return None
    rec = {"date": day.isoformat(),
           "approval": round(appr[-1][1], 2),
           "approval_as_of": appr[-1][0].isoformat(),
           "economy_rule": "one-year change", **econ}

    if rule == "course" and day >= FIRST_2026:
        gas_2026 = [v for d, v in gas if dt.date(2026, 1, 1) <= d <= day]
        released = [v for d, v in _released_months(income, day) if d.year == 2026]
        g = _mean(gas_2026)
        i = _mean(released) if released else base_2025["income"]
        rec.update({"economy_rule": "2026 to date (course)",
                    "gas_1yr": round(100 * (g / base_2025["gas"] - 1), 4),
                    "income_1yr": round(100 * (i / base_2025["income"] - 1), 4)})
    rec["gas_input"] = round(base_2025["gas"] * (1 + rec["gas_1yr"] / 100), 6)
    rec["income_input"] = round(base_2025["income"] * (1 + rec["income_1yr"] / 100), 3)
    # A row computed after its own date is a recomputation; the site says so.
    rec["computed_on"] = dt.date.today().isoformat()
    return rec


# --------------------------------------------------------------------------
# running the course script

# Appended after the course script. It only WRITES what the fitted models
# already hold (coefficients, standard errors, residual SEs, race counts) so the
# site can print the specification; it changes nothing the script computes.
SPEC_EPILOGUE = r"""

# ---- added by run_class.py: model specification for the site (output only)
.spec_of <- function(m, name) {
  cf <- summary(m)$coefficients
  tibble(model = name, term = rownames(cf), estimate = cf[, 1], std_error = cf[, 2])
}
write_csv(bind_rows(.spec_of(m_nat, "national"), .spec_of(m_house, "house"),
                    .spec_of(m_senate, "senate")),
          file.path(OUT_DIR, "class_spec_coef.csv"))
write_csv(tibble(model = c("national", "house", "senate"),
                 resid_se = c(summary(m_nat)$sigma, sigma(m_house), sigma(m_senate)),
                 n = c(nobs(m_nat), nobs(m_house), nobs(m_senate)),
                 nat_error_per_race = c(NA, e_h["nat_race"], e_s["nat_race"]),
                 total_error = c(sigma_nat, e_h["total"], e_s["total"]),
                 first_year = c(min(national_data$year), min(cycles), min(cycles)),
                 last_year = c(max(national_data$year), max(cycles), max(cycles)),
                 rep_prev_share = c(rep_prev, NA, NA)),
          file.path(OUT_DIR, "class_spec_fit.csv"))
"""


def _patched_script(inp: dict, out_dir: Path, data_dir: Path | None = None) -> str:
    src = SCRIPT.read_text()
    vals = {
        "AS_OF": f'AS_OF            <- as.Date("{inp["date"]}")',
        "APPROVAL_2026": f'APPROVAL_2026    <- {inp["approval"]}',
        "GAS_2026": f'GAS_2026         <- {inp["gas_input"]}',
        "REAL_INCOME_2026": f'REAL_INCOME_2026 <- {inp["income_input"]}',
        "INPUT_SOURCES": ('INPUT_SOURCES    <- "Approval: site aggregator average, '
                          f'{inp["approval_as_of"]}. Gas: FRED GASREGW, '
                          f'{inp.get("economy_rule", "given")}. Income: FRED A229RX0, '
                          f'{inp.get("economy_rule", "given")}."'),
        "DATA_DIR": f'DATA_DIR    <- "{(data_dir or INPUTS).as_posix()}"',
        "OUT_DIR": f'OUT_DIR     <- "{out_dir.as_posix()}"',
    }
    for key, pat in INPUT_LINES.items():
        n = len(re.findall(pat, src, flags=re.M))
        if n != 1:
            raise RuntimeError(f"class_forecast.R: expected one `{key} <-` line, found {n}. "
                               "The course script has changed shape; update this runner.")
        src = re.sub(pat, lambda _m, v=vals[key]: v, src, count=1, flags=re.M)
    return src + SPEC_EPILOGUE


def _inputs_with_shift(dest: Path, shift: dict) -> Path:
    """Copy the course inputs, shifting House districts' lean by `shift`."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in INPUTS.glob("*.csv"):
        if f.name != "races_2026.csv":
            shutil.copy(f, dest / f.name)
    with open(INPUTS / "races_2026.csv", newline="") as fh:
        rd = csv.DictReader(fh)
        fields, rows = rd.fieldnames, list(rd)
    for r in rows:
        d = shift.get(r["race_id"])
        if d and r.get("office") == "house" and r.get("pvi") not in ("", "NA", None):
            r["pvi"] = repr(round(float(r["pvi"]) + d, 4))
    with open(dest / "races_2026.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return dest


def run_one(inp: dict, shift: dict | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out_dir = tmp / "output"
        data_dir = _inputs_with_shift(tmp / "inputs", shift) if shift else None
        (tmp / "class_forecast.R").write_text(_patched_script(inp, out_dir, data_dir))
        res = subprocess.run(["Rscript", "class_forecast.R"], cwd=tmp,
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Rscript failed for {inp['date']}:\n{res.stderr[-2000:]}")
        stamp = inp["date"]
        with open(out_dir / "class_timeseries.csv", newline="") as fh:
            summary = list(csv.DictReader(fh))[-1]
        # The script's gas_ytd column is the level passed in (2025 x (1 +
        # change)); record the actual average price over the window instead.
        summary = {("gas_avg" if k == "gas_ytd" else k):
                   (inp.get("gas_12m", v) if k == "gas_ytd" else v)
                   for k, v in summary.items()}
        with open(out_dir / f"forecast_class_{stamp}.csv", newline="") as fh:
            races = list(csv.DictReader(fh))
        sims = (out_dir / f"seat_sims_class_{stamp}.csv").read_text()
        spec = {n: (out_dir / f"class_spec_{n}.csv").read_text() for n in ("coef", "fit")}
    return {"summary": summary, "races": races, "sims": sims, "spec": spec}


# --------------------------------------------------------------------------
# outputs

def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _write(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", restval="")
        w.writeheader()
        w.writerows(rows)


def store(results: list[tuple[dict, dict]]) -> None:
    done = {inp["date"] for inp, _ in results}

    ts = [r for r in _read(TIMESERIES) if r["as_of"] not in done]
    ts += [res["summary"] for _, res in results]
    ts.sort(key=lambda r: r["as_of"])
    # Rows from an earlier version of the runner can have other columns; keep
    # the union so a partial rerun never fails (a full --backfill --redo
    # rewrites every row with the current columns).
    _write(TIMESERIES, ts, list(dict.fromkeys(
        k for r in [res["summary"] for _, res in results] + ts for k in r)))

    fields = ["as_of", "race_id", "state", "dem_candidate", "rep_candidate",
              "pred_dem_share", "p_dem_win", "lo_80", "hi_80"]
    hist = [r for r in _read(SENATE_HIST) if r["as_of"] not in done]
    for inp, res in results:
        for r in res["races"]:
            if r["office"] == "senate":
                hist.append({"as_of": inp["date"], **{k: r[k] for k in fields[1:]}})
    hist.sort(key=lambda r: (r["as_of"], r["race_id"]))
    _write(SENATE_HIST, hist, fields)

    log = [r for r in _read(INPUT_LOG) if r["date"] not in done]
    log += [inp for inp, _ in results]
    log.sort(key=lambda r: r["date"])
    fields = list(dict.fromkeys(k for r in [inp for inp, _ in results] + log for k in r))
    _write(INPUT_LOG, log, fields)

    # "latest" means the newest date in the time series, not the newest date
    # in this particular run.
    newest = max(results, key=lambda x: x[0]["date"])
    if newest[0]["date"] >= ts[-1]["as_of"]:
        _write(LATEST_RACES, newest[1]["races"], list(newest[1]["races"][0].keys()))
        LATEST_SIMS.write_text(newest[1]["sims"])
        for n, text in newest[1]["spec"].items():
            (OUT / f"class_spec_{n}.csv").write_text(text)


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the PLSC 2219 class model.")
    ap.add_argument("--date", help="date to compute (default: today)")
    ap.add_argument("--backfill", action="store_true",
                    help="compute every date from --since to --date")
    ap.add_argument("--since", default=FIRST.isoformat())
    ap.add_argument("--redo", action="store_true",
                    help="recompute dates that already have a row")
    ap.add_argument("--approval", type=float, help="override approval (one date only)")
    ap.add_argument("--gas", type=float, help="override gas, 1-year %% change (one date only)")
    ap.add_argument("--income", type=float, help="override income, 1-year %% change (one date only)")
    ap.add_argument("--economy", choices=["one_year", "course"], default="one_year",
                    help="economy rule (default: one-year change for every date)")
    ap.add_argument("--out", type=Path, help="write outputs here instead (testing)")
    ap.add_argument("--gas-csv", type=Path, help="local copy of FRED GASREGW (testing)")
    ap.add_argument("--income-csv", type=Path, help="local copy of FRED A229RX0 (testing)")
    ap.add_argument("--sentiment-csv", type=Path, help="local copy of FRED UMCSENT (testing)")
    a = ap.parse_args(argv)

    global OUT, TIMESERIES, SENATE_HIST, INPUT_LOG, LATEST_RACES, LATEST_SIMS
    if a.out:
        OUT = a.out
        TIMESERIES, SENATE_HIST, INPUT_LOG = (OUT / "class_timeseries.csv",
            OUT / "class_senate_history.csv", OUT / "class_inputs.csv")
        LATEST_RACES, LATEST_SIMS = OUT / "forecast_class_latest.csv", OUT / "seat_sims_class_latest.csv"

    end = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    if end > LOCK:
        print(f"  the class model is locked on {LOCK}; computing {LOCK} at most")
        end = LOCK
    start = dt.date.fromisoformat(a.since) if a.backfill else end
    if start < FIRST:
        start = FIRST

    with open(INPUTS / "economy_sept.csv", newline="") as fh:
        row_2025 = next(r for r in csv.DictReader(fh) if r["year"] == "2025")
        base_2025 = {"income": float(row_2025["real_disp_income"]),
                     "gas": float(row_2025["gas_price"])}

    if a.approval is not None and a.gas is not None and a.income is not None:
        if a.backfill:
            print("  --approval/--gas/--income apply to one date; drop --backfill")
            return 2
        inp = {"date": end.isoformat(), "approval": a.approval, "approval_as_of": end.isoformat(),
               "economy_rule": "given", "gas_1yr": a.gas, "income_1yr": a.income,
               "gas_input": round(base_2025["gas"] * (1 + a.gas / 100), 6),
               "income_input": round(base_2025["income"] * (1 + a.income / 100), 3),
               "computed_on": dt.date.today().isoformat()}
        shift, label = map_shift(end.isoformat(), district_maps())
        inp["district_map"], inp["districts_shifted"] = label, len(shift)
        store([(inp, run_one(inp, shift))])
        print(f"  computed {end} from the inputs given")
        return 0

    approval = approval_points()
    gas = fred_series("GASREGW", a.gas_csv)
    income = fred_series("A229RX0", a.income_csv)
    try:
        sentiment = fred_series("UMCSENT", a.sentiment_csv)
    except Exception as e:                    # only the comparison models use it
        print(f"  consumer sentiment not read ({e}); continuing without it")
        sentiment = []

    have = {r["as_of"] for r in _read(TIMESERIES)}
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    todo = [d for d in days if a.redo or d.isoformat() not in have or d == end]

    dm = district_maps()
    if dm is None:
        print("  no district map data found; every date uses the November lines")
    results, skipped = [], []
    for day in todo:
        inp = inputs_for(day, approval, gas, income, sentiment, base_2025, a.economy)
        if inp is None:
            skipped.append(day.isoformat())
            continue
        shift, label = map_shift(day.isoformat(), dm)
        inp["district_map"] = label
        inp["districts_shifted"] = len(shift)
        results.append((inp, run_one(inp, shift)))
    if skipped:
        print(f"  skipped {len(skipped)} date(s) without approval or two years of economic data "
              f"({skipped[0]} .. {skipped[-1]})")
    if not results:
        print("  nothing computed")
        return 0
    store(results)
    last = results[-1][1]["summary"]
    print(f"  computed {len(results)} date(s), {results[0][0]['date']} .. {results[-1][0]['date']}")
    print(f"  latest: national D share {float(last['nat_dem_share']):.2f}, "
          f"P(House) {float(last['p_house']):.2f}, P(Senate) {float(last['p_senate']):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
