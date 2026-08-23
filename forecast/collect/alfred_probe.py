#!/usr/bin/env python3
"""
ALFRED vintage probe: find out whether we can fetch an economic series AS IT
WAS PUBLISHED on a past date, and prove it rather than assume it.

    python3 forecast/collect/alfred_probe.py
    python3 forecast/collect/alfred_probe.py --series A229RX0 --json /tmp/alfred.json

WRITES NOTHING TO THE ARCHIVE. Like probe.py, this composes the real fetcher
with no storage step, so a probe run can never be mistaken later for a capture.

-----------------------------------------------------------------------------
WHY THIS EXISTS

The fundamentals model takes real disposable personal income per capita from
FRED. FRED serves ONE number per date: the number as it stands today, revised.
That is the wrong number for a backfilled forecast. If we say "here is what the
fundamentals model would have said on 2025-06-01", and we feed it income data
that BEA did not publish until 2026, the model is not a June 2025 forecast — it
is an August 2026 forecast wearing a June 2025 date. RULES.md §10 already draws
this line and calls that row `retrospective`, not `archival`.

ALFRED is FRED's archival twin: it serves each series as it stood on a chosen
vintage date. If it works without an API key, then the income input becomes
recoverable at every past date, and a backfilled fundamentals run graduates
from `retrospective` (not scored as real-time) to something much closer to
`archival` (scored as real-time) — because the number really was on the record
that day and we are reading a dated commitment rather than reconstructing one.

That is the whole prize, and it is worth a probe before it is worth a parser.

-----------------------------------------------------------------------------
THE TRAP THIS PROBE IS BUILT AROUND

An HTTP 200 is not evidence that a query parameter was honoured. A server that
does not recognise `vintage_date` will usually ignore it and serve the current
series with a cheerful 200. The bytes look perfect. The parser works. Every
backfilled date silently gets today's revised data, and the resulting archive
is wrong in a way nothing downstream can detect, because there is nothing to
detect: the file is well-formed and the numbers are real, they are just from
the wrong year.

So every candidate here is tested by FALSIFICATION, not by status code:

  1. Fetch the same series at two vintages fourteen months apart.
  2. If the two responses are byte-identical, the parameter was IGNORED.
     Verdict `ignored` — which is the dangerous outcome, and the one this
     script exists to catch.
  3. If they differ, check that the older vintage does not contain
     observations dated after its own vintage date. A vintage that knows the
     future is not a vintage.
  4. Report the column header. ALFRED names the column after the vintage
     (`A229RX0_20250602`), so a header that carries the date we asked for is
     independent confirmation that the server understood the question.

A candidate has to pass all four to be reported as usable.

-----------------------------------------------------------------------------
POLITENESS

fred.stlouisfed.org publishes a wildcard Crawl-delay of 1 and disallows
/graph/graph-landing.php, /graph/image.php, /graph/fredgraph.png,
/searchresults and /seriesBeta — the .csv endpoints are not disallowed (checked
2026-08-20, see the `fred` entry in sources/2026.yaml). alfred.stlouisfed.org
is a DIFFERENT HOST with its own robots.txt, which this script fetches and
prints first. It does not decide for you: registering a new source still means
a human reads that robots.txt and writes the date into `robots_checked`.

Total request count is bounded and printed. The default run is well under
twenty requests.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# The real fetcher if it is importable (same user agent, throttle, backoff and
# TLS context as capture), a stdlib fallback if this file is run on its own.
try:
    import capture                                   # noqa: E402
    from parse import load_registry                  # noqa: E402
    _HAVE_CAPTURE = True
except Exception:                                    # pragma: no cover
    _HAVE_CAPTURE = False


# --------------------------------------------------------------------------
# Candidates. Each is one guess at how a vintage is requested. {id} is the
# series, {v} the vintage date as YYYY-MM-DD, {vc} the same date compacted to
# YYYYMMDD, {start}/{end} an observation window.
# --------------------------------------------------------------------------
ALFRED = "https://alfred.stlouisfed.org"
FRED = "https://fred.stlouisfed.org"

CANDIDATES = [
    # The documented one, and the one we expect to win.
    ("alfredgraph vintage_date",
     ALFRED + "/graph/alfredgraph.csv?id={id}&vintage_date={v}"),

    # Same, with an explicit observation window. Worth testing separately
    # because a server can honour one parameter and ignore the other, and the
    # backfill will want a window.
    ("alfredgraph vintage_date + window",
     ALFRED + "/graph/alfredgraph.csv?id={id}&vintage_date={v}"
              "&cosd={start}&coed={end}"),

    # ALFRED's vintage-series-id convention. If this works it is the cheapest
    # possible request: no extra parameters to be silently dropped.
    ("alfredgraph vintage series id",
     ALFRED + "/graph/alfredgraph.csv?id={id}_{vc}"),

    # The FRED host with the ALFRED parameter. If FRED honours it we do not
    # need a second host in the registry at all; if FRED IGNORES it we very
    # much need to know, because it is the failure that looks like success.
    ("fredgraph + vintage_date (ignore-test)",
     FRED + "/graph/fredgraph.csv?id={id}&vintage_date={v}"),

    # A deliberate nonsense parameter. This is the control: if the server
    # returns different bytes for two values of a parameter that cannot mean
    # anything, then byte-difference is not evidence of anything either and
    # every other verdict in this run is suspect.
    ("CONTROL nonsense parameter",
     FRED + "/graph/fredgraph.csv?id={id}&kd_not_a_real_param={v}"),
]

# Fetched once, not per-vintage: ALFRED's whole-history download. If this
# returns something parseable it is strictly better than per-date requests —
# one fetch, every vintage, and the backfill becomes a local computation.
BULK = ("all vintages in one file",
        ALFRED + "/series/downloaddata?seid={id}&cosd={start}&coed={end}")

# Fourteen months apart, which spans at least one annual NIPA revision, so a
# genuine vintage difference is guaranteed for an income series.
V_OLD = "2025-06-02"
V_NEW = "2026-08-03"


# --------------------------------------------------------------------------
def _stdlib_get(url: str, ua: str, timeout: int = 30) -> tuple[bytes, dict]:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", ua)
    req.add_header("Accept", "text/csv, text/plain, */*;q=0.8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return body, {"status": resp.status, "final_url": resp.geturl(),
                      "content_type": resp.headers.get("Content-Type", "")}


class Client:
    """capture.Fetcher when available, urllib otherwise. Counts requests."""

    def __init__(self, cycle: int = 2026):
        self.n = 0
        self.impl = "urllib"
        self.ua = ("PLSC 2219 Forecast Archive (Yale University) "
                   "(+https://kevinmdeluca.com/forecast/2026/; "
                   "kevin.deluca@yale.edu)")
        self._f = None
        if _HAVE_CAPTURE:
            try:
                reg = load_registry(cycle)
                self._f = capture.Fetcher(reg.get("contact", {}),
                                          reg.get("defaults", {}))
                self.ua = self._f.user_agent
                self.impl = "capture.Fetcher"
            except Exception as e:
                print(f"  (registry unavailable, using stdlib: {e})")

    def get(self, url: str) -> tuple[bytes | None, dict]:
        self.n += 1
        try:
            if self._f is not None:
                body, meta = self._f.get(url)
                return body, {"status": meta.get("status"),
                              "final_url": meta.get("final_url"),
                              "content_type": (meta.get("headers") or {})
                              .get("Content-Type", "")}
            return _stdlib_get(url, self.ua)
        except urllib.error.HTTPError as e:
            return None, {"status": e.code, "error": f"HTTP {e.code}"}
        except Exception as e:
            return None, {"status": None, "error": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------
def read_csv(body: bytes) -> dict:
    """Pull the shape facts a vintage claim can be checked against."""
    out = {"rows": 0, "columns": [], "first_date": None, "last_date": None,
           "last_value": None, "parse_error": None}
    try:
        text = body.decode("utf-8", "replace")
        rdr = csv.reader(io.StringIO(text))
        header = next(rdr, [])
        out["columns"] = [c.strip() for c in header]
        dates, last_val = [], None
        for row in rdr:
            if not row or not row[0].strip():
                continue
            dates.append(row[0].strip())
            if len(row) > 1 and row[1].strip() not in ("", "."):
                last_val = row[1].strip()
        out["rows"] = len(dates)
        if dates:
            out["first_date"], out["last_date"] = dates[0], dates[-1]
        out["last_value"] = last_val
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"
    return out


def looks_like_csv(body: bytes, meta: dict) -> bool:
    if not body:
        return False
    head = body[:400].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    return b"," in body[:2000]


def judge(series: str, old: dict, new: dict) -> tuple[str, list[str]]:
    """Verdict plus the reasons for it. Order matters: `ignored` outranks."""
    notes: list[str] = []

    if old.get("error") or new.get("error"):
        return "unsupported", [old.get("error") or new.get("error", "")]
    if not old["is_csv"] or not new["is_csv"]:
        return "not_csv", ["response was not CSV (HTML error page?)"]

    if old["sha256"] == new["sha256"]:
        return "ignored", [
            "two vintages fourteen months apart returned BYTE-IDENTICAL "
            "responses — the parameter was accepted and discarded"]

    notes.append("vintages differ (parameter is doing something)")

    # A vintage cannot contain observations published after itself.
    ld = old["csv"]["last_date"] or ""
    if ld and ld > V_OLD:
        notes.append(f"BUT the {V_OLD} vintage carries an observation dated "
                     f"{ld}, which is after its own vintage date")
        return "suspect", notes
    if ld:
        notes.append(f"{V_OLD} vintage stops at {ld}, correctly in the past")

    # ALFRED names the column after the vintage. Independent confirmation.
    cols = " ".join(old["csv"]["columns"])
    if V_OLD.replace("-", "") in cols:
        notes.append(f"column header names the vintage: {cols!r}")
    else:
        notes.append(f"column header does not name the vintage: {cols!r} "
                     f"(not fatal — check the values below by hand)")

    if old["csv"]["last_value"] and new["csv"]["last_value"]:
        notes.append(f"last value {old['csv']['last_value']} (old vintage) "
                     f"vs {new['csv']['last_value']} (new vintage)")

    return "works", notes


def probe_one(cli: Client, label: str, tmpl: str, series: str,
              start: str, end: str) -> dict:
    res = {"candidate": label, "series": series, "vintages": {}}
    for tag, v in (("old", V_OLD), ("new", V_NEW)):
        url = tmpl.format(id=series, v=v, vc=v.replace("-", ""),
                          start=start, end=end)
        body, meta = cli.get(url)
        entry = {"url": url, "status": meta.get("status"),
                 "error": meta.get("error")}
        if body:
            entry["bytes"] = len(body)
            entry["sha256"] = hashlib.sha256(body).hexdigest()
            entry["is_csv"] = looks_like_csv(body, meta)
            entry["csv"] = read_csv(body) if entry["is_csv"] else {}
            entry["head"] = body[:200].decode("utf-8", "replace")
        else:
            entry.update(bytes=0, sha256="", is_csv=False, csv={}, head="")
        res["vintages"][tag] = entry
    verdict, notes = judge(series, res["vintages"]["old"],
                           res["vintages"]["new"])
    res["verdict"], res["notes"] = verdict, notes
    return res


def probe_robots(cli: Client) -> dict:
    body, meta = cli.get(ALFRED + "/robots.txt")
    text = body.decode("utf-8", "replace") if body else ""
    return {"status": meta.get("status"), "error": meta.get("error"),
            "text": text}


def probe_bulk(cli: Client, series: str, start: str, end: str) -> dict:
    label, tmpl = BULK
    url = tmpl.format(id=series, start=start, end=end)
    body, meta = cli.get(url)
    out = {"candidate": label, "url": url, "status": meta.get("status"),
           "error": meta.get("error"),
           "content_type": meta.get("content_type", "")}
    if body:
        out["bytes"] = len(body)
        out["head"] = body[:200].decode("utf-8", "replace")
        out["is_zip"] = body[:2] == b"PK"
        out["is_csv"] = looks_like_csv(body, meta)
        out["csv"] = read_csv(body) if out["is_csv"] else {}
    return out


# --------------------------------------------------------------------------
BANNER = "=" * 72


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe ALFRED for vintage data. Writes nothing to data/.")
    ap.add_argument("--series", default="A229RX0",
                    help="FRED series id (default A229RX0, the monthly real "
                         "disposable personal income per capita the "
                         "fundamentals model uses)")
    ap.add_argument("--also-annual", action="store_true",
                    help="repeat the winning shape on A229RX0A048NBEA, the "
                         "annual series the model was actually fitted on")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-08-01")
    ap.add_argument("--json", metavar="PATH",
                    help="write the full report here (send me this file)")
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)

    cli = Client(a.cycle)
    report = {"probed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "series": a.series, "vintage_old": V_OLD, "vintage_new": V_NEW,
              "fetcher": cli.impl, "user_agent": cli.ua}

    print(BANNER)
    print(f"ALFRED vintage probe · {a.series} · {V_OLD} vs {V_NEW}")
    print(BANNER)
    print(f"  fetcher     {cli.impl}")
    print(f"  user agent  {cli.ua}")
    print("\n-- robots.txt (alfred.stlouisfed.org) "
          "-------------------------------")
    rob = probe_robots(cli)
    report["robots"] = rob
    if rob.get("text"):
        for line in rob["text"].splitlines():
            print(f"  {line}")
    else:
        print(f"  could not fetch: {rob.get('error')}")
    print("\n  A human still has to read that and set robots_checked before "
          "any\n  of this becomes a registered source. This script does not "
          "decide it.")

    print("\n-- candidates --------------------------------------------------"
          "--------")
    results = []
    for label, tmpl in CANDIDATES:
        r = probe_one(cli, label, tmpl, a.series, a.start, a.end)
        results.append(r)
        mark = {"works": "WORKS", "ignored": "IGNORED",
                "unsupported": "no", "not_csv": "no", "suspect": "SUSPECT"}
        print(f"\n  [{mark.get(r['verdict'], r['verdict']):>9}] {label}")
        print(f"      {r['vintages']['old']['url']}")
        for n in r["notes"]:
            print(f"      · {n}")
    report["candidates"] = results

    print("\n-- bulk download ----------------------------------------------"
          "---------")
    bulk = probe_bulk(cli, a.series, a.start, a.end)
    report["bulk"] = bulk
    print(f"  {bulk['url']}")
    print(f"  status {bulk.get('status')} "
          f"type {bulk.get('content_type', '')!r} "
          f"bytes {bulk.get('bytes', 0)}")
    if bulk.get("error"):
        print(f"  {bulk['error']}")
    elif bulk.get("is_zip"):
        print("  looks like a ZIP — probably every vintage in one archive, "
              "which is\n  the best possible outcome: one fetch, then the "
              "backfill is local.")
    elif bulk.get("is_csv"):
        cols = bulk.get("csv", {}).get("columns", [])
        print(f"  CSV with {len(cols)} columns; first few: {cols[:6]}")
        print("  If those column names are vintage dates, this is the whole "
              "history\n  in one file.")
    else:
        print(f"  head: {bulk.get('head', '')[:120]!r}")

    # ----------------------------------------------------------------------
    winners = [r for r in results if r["verdict"] == "works"]
    ignored = [r for r in results if r["verdict"] == "ignored"]
    control = next((r for r in results
                    if r["candidate"].startswith("CONTROL")), None)

    print("\n" + BANNER)
    print("VERDICT")
    print(BANNER)

    if control is not None and control["verdict"] == "works":
        print("  CONTROL FAILED. A parameter that cannot possibly mean "
              "anything\n  changed the response, so byte-difference is not "
              "evidence here and\n  every verdict above should be treated as "
              "unproven. Send me the JSON.")
        report["control_ok"] = False
    else:
        report["control_ok"] = True

    if winners:
        w = winners[0]
        print(f"  USABLE: {w['candidate']}")
        print(f"    {w['vintages']['old']['url']}")
        print("\n  What this buys: the fundamentals income input becomes "
              "recoverable at\n  any past date, so a backfilled fundamentals "
              "run is computed from data\n  that was genuinely on the record "
              "that day. Under RULES.md §10 that is\n  the difference between "
              "`retrospective` (excluded from the real-time\n  table) and "
              "`archival` (scored). It does not by itself make the whole\n"
              "  backfilled model archival — the coefficients are still ours "
              "and still\n  fitted in 2026 — but it removes the input "
              "anachronism, which is the\n  part we could otherwise never "
              "defend.")
        print("\n  Registry sketch (NOT added by this script):\n")
        print(f"""    - id: alfred
      name: ALFRED — {a.series} as published on a past date
      category: fundamentals
      method: http
      enabled: false            # flip after robots_checked is filled in
      cadence: sporadic
      publication: individual
      declared_inputs: []
      license: permitted
      robots_checked: ""        # a human reads the robots.txt above
      config:
        urls:
          - name: income_vintage
            url: {w['vintages']['old']['url']}
""")
    elif ignored:
        print("  NOT USABLE, AND DANGEROUSLY SO. Every candidate that "
              "returned data\n  returned the SAME data for both vintages: the "
              "parameter is accepted\n  and discarded. Anything built on it "
              "would look right and be wrong.")
    else:
        print("  No candidate returned usable CSV. The endpoint may have "
              "moved, or\n  may need the keyed FRED API "
              "(fred/series/observations with\n  realtime_start), which needs "
              "a free API key and a registry entry that\n  can hold a secret.")

    print(f"\n  {cli.n} requests made. Nothing was written to data/.")

    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  report: {p}")

    return 0 if winners else 1


if __name__ == "__main__":
    raise SystemExit(main())
