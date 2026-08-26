#!/usr/bin/env python3
"""
Real disposable income per capita AS IT WAS PUBLISHED, one vintage per month.

    python3 forecast/collect/alfred_income.py --capture      # fetch missing vintages
    python3 forecast/collect/alfred_income.py                # report what is on disk

WHAT PROBLEM THIS SOLVES. The fundamentals model takes three inputs and, once
the approval feed landed, two of them were datable and one was not. FRED serves
exactly one number per series: the number as it stands today, revised. Feeding
that to a backfilled June 2025 forecast makes it an August 2026 forecast in a
June 2025 costume, so income_from_archive refused to reach back past the first
FRED capture on 2026-08-20 and the backfill produced six points.

ALFRED is FRED's archival twin and serves a series as it stood on a chosen
vintage date. collect/alfred_probe.py already established, by falsification
rather than by status code, that `vintage_date` is HONOURED on the alfredgraph
CSV endpoint: two vintages fourteen months apart differ, and the 2025-06-02
vintage stops at 2025-04-01 rather than knowing about months it could not have.
Three other candidate endpoints were probed and IGNORED the parameter while
returning a cheerful 200, which is the trap the probe was built around. This
module uses only the candidate that passed.

WHAT IT BUYS. A backfilled point stops being `retrospective` — today's data
wearing an old date — and becomes something much closer to `archival`: BEA's
own published figure, on the record that day, read from a dated commitment
rather than reconstructed from a revised one. That is the distinction
score/RULES.md §10 draws, and it is the difference between a series that can be
scored as real-time and one that cannot.

CADENCE: ONE VINTAGE A MONTH, and it is not a corner cut. A229RX0 is a monthly
series released once a month, so consecutive daily vintages are byte-identical
by construction. Monthly vintages capture every genuine change and 300 daily
ones would capture the same information twenty times over, at twenty times the
politeness cost to a public server.

TWO-PHASE, like everything else here: this writes raw bytes and a meta file and
parses nothing. model/fundamentals.py reads what lands.

LICENCE: U.S. Bureau of Economic Analysis via ALFRED, "Public Domain: Citation
Requested". ROBOTS (checked 2026-08-23 by alfred_probe.py, response stored in
its report): alfred.stlouisfed.org gives the wildcard Crawl-delay 2 and
disallows /graph/graph-landing.php, /graph/image.php, /graph/alfredgraph.png
and /search. The .csv endpoint used here is NOT disallowed. GPTBot,
ChatGPT-User and Google-Extended get a 30-day crawl-delay; ClaudeBot is not
named and falls through to the wildcard.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

SERIES = "A229RX0"          # real disposable personal income per capita, monthly
URL = ("https://alfred.stlouisfed.org/graph/alfredgraph.csv"
       "?id={series}&vintage_date={vintage}")
ARTIFACT = "income_monthly.csv"

# The first vintage worth having is the one in force when the archive opens.
FIRST_VINTAGE = "2025-01-01"


def vintage_dir(cycle: int, vintage: str) -> Path:
    return DATA / str(cycle) / "raw" / "alfred" / vintage


def wanted_vintages(first: str = FIRST_VINTAGE,
                    last: str | None = None) -> list[str]:
    """The first of every month from `first` to `last`, plus `last` itself.

    `last` is included even mid-month so that today's forecast reads a vintage
    of today rather than one from up to four weeks ago. Everything before it is
    monthly, because the series only moves monthly.
    """
    end = dt.date.fromisoformat(last or dt.date.today().isoformat())
    cur = dt.date.fromisoformat(first)
    out = []
    while cur <= end:
        out.append(cur.isoformat())
        cur = (cur.replace(day=28) + dt.timedelta(days=8)).replace(day=1)
    if out and out[-1] != end.isoformat():
        out.append(end.isoformat())
    return out


def capture(cycle: int, vintages: list[str], dry_run: bool = False) -> int:
    import capture as _cap

    # The contact block comes from the registry so this module cannot present
    # a different identity from the daily run.
    reg = _cap.load_registry(cycle)
    contact = reg.get("contact") or reg.get("defaults", {}).get("contact") or {}
    # Crawl-delay 2 on the wildcard, and this is a bulk historical read rather
    # than a daily one, so it is honoured rather than rounded down.
    f = _cap.Fetcher(contact, {"min_interval_seconds": 2.5}, dry_run=dry_run)

    got = new = 0
    for v in vintages:
        d = vintage_dir(cycle, v)
        if (d / ARTIFACT).exists():
            got += 1
            continue
        url = URL.format(series=SERIES, vintage=v)
        body, meta = f.get(url)
        status = meta.get("status")
        text = body.decode("utf-8", "replace")

        # A VINTAGE THAT KNOWS THE FUTURE IS NOT A VINTAGE. This is the same
        # falsification the probe used, applied to every capture rather than
        # once: if the parameter is quietly stopped being honoured, the file
        # will contain observations dated after its own vintage date, and it
        # is better to fail loudly here than to backfill a year of forecasts
        # with data nobody had.
        last_obs = None
        for row in csv.reader(io.StringIO(text)):
            if row and row[0][:2] in ("19", "20"):
                last_obs = row[0].strip()
        if last_obs and last_obs > v:
            raise SystemExit(
                f"REFUSING {v}: the response carries an observation dated "
                f"{last_obs}, after its own vintage date. vintage_date is no "
                f"longer being honoured — re-run alfred_probe.py before "
                f"trusting anything in raw/alfred/.")

        if dry_run:
            print(f"  would fetch {v}  ({len(text)} bytes, last obs {last_obs})")
            continue
        d.mkdir(parents=True, exist_ok=True)
        (d / ARTIFACT).write_text(text, encoding="utf-8")
        (d / f"{ARTIFACT.split('.')[0]}.meta.json").write_text(json.dumps({
            "url": url, "status": status,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "bytes": len(text.encode()),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "vintage_date": v, "series": SERIES,
            "last_observation": last_obs,
            "note": "ALFRED vintage — the series AS PUBLISHED on vintage_date. "
                    "Verified on capture: no observation postdates the vintage.",
        }, indent=2))
        new += 1
        print(f"  {v}  last obs {last_obs}  {len(text.encode()):>6} bytes")
    print(f"  {got} already on disk, {new} new")
    return 0


def read_vintage(cycle: int, vintage: str) -> dict[str, float]:
    """{observation_date: value} from one stored vintage."""
    f = vintage_dir(cycle, vintage) / ARTIFACT
    if not f.exists():
        return {}
    out: dict[str, float] = {}
    for row in csv.reader(f.open(encoding="utf-8", errors="replace")):
        if len(row) < 2 or not row[0][:1].isdigit():
            continue
        try:
            out[row[0].strip()] = float(row[1])
        except ValueError:
            continue     # ALFRED writes "." for a missing observation
    return out


def vintages_on_disk(cycle: int) -> list[str]:
    base = DATA / str(cycle) / "raw" / "alfred"
    if not base.exists():
        return []
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and (d / ARTIFACT).exists())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--first", default=FIRST_VINTAGE)
    a = ap.parse_args()

    if a.capture or a.dry_run:
        return capture(a.cycle, wanted_vintages(a.first), a.dry_run)

    vs = vintages_on_disk(a.cycle)
    if not vs:
        print("  nothing captured yet — run with --capture")
        return 0
    print(f"  {len(vs)} vintage(s) on disk, {vs[0]} .. {vs[-1]}")
    for v in (vs[0], vs[len(vs) // 2], vs[-1]):
        obs = read_vintage(a.cycle, v)
        last = max(obs) if obs else "-"
        print(f"      {v}   {len(obs):>4} observation(s), last {last} "
              f"= {obs.get(last, float('nan')):,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
