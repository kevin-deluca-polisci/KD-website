#!/usr/bin/env python3
"""
Phase 2 — parse stored captures into the long format.

NEVER touches the network. Reads forecast/data/<cycle>/raw/ and writes
forecast/data/<cycle>/parsed/<date>.csv.

Because it reads from storage rather than the web, this can be re-run over the
entire history at any time. Fix a parser in October, re-run over every date
ever captured, and the whole public series corrects itself retroactively. That
is the payoff for capture refusing to parse.

  python3 forecast/collect/parse.py                      # today
  python3 forecast/collect/parse.py --all                # every stored date
  python3 forecast/collect/parse.py --date 2026-09-01
  python3 forecast/collect/parse.py --only kalshi
  python3 forecast/collect/parse.py --inspect kalshi     # show stored structure

PRIVACY TIER
    parsed/ holds PER-FORECASTER values and is gitignored in the public repo.
    Only forecast/data/<cycle>/derived/ (category averages) is published.
    See aggregate.py, which enforces this rather than trusting it.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    import yaml
except ImportError:
    print("ERROR: pip install -r forecast/collect/requirements.txt", file=sys.stderr)
    raise SystemExit(2)

from forecast.collect import parsers as P

REPO_ROOT = Path(__file__).resolve().parents[2]
FORECAST_DIR = REPO_ROOT / "forecast"
DATA_DIR = FORECAST_DIR / "data"


def load_registry(cycle: int) -> dict:
    with (FORECAST_DIR / "sources" / f"{cycle}.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def stored_dates(cycle: int, source_id: str | None = None) -> list[str]:
    root = DATA_DIR / str(cycle) / "raw"
    if not root.is_dir():
        return []
    out: set[str] = set()
    for sdir in root.iterdir():
        if not sdir.is_dir() or (source_id and sdir.name != source_id):
            continue
        for d in sdir.iterdir():
            if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name):
                out.add(d.name)
    return sorted(out)


# --------------------------------------------------------------------------
# --inspect: show what is actually stored, so a parser gets written against
# reality rather than against a guess. This is the tool that makes writing the
# HTML parsers a 20-minute job instead of an afternoon.
# --------------------------------------------------------------------------

def sketch_json(obj, depth=0, max_depth=3, prefix="") -> list[str]:
    out = []
    pad = "  " * depth
    if depth > max_depth:
        return [f"{pad}..."]
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:14]:
            t = type(v).__name__
            if isinstance(v, (dict, list)):
                n = len(v)
                out.append(f"{pad}{k}: {t}[{n}]")
                if n:
                    out += sketch_json(v[0] if isinstance(v, list) else v,
                                       depth + 1, max_depth)
            else:
                s = repr(v)[:60]
                out.append(f"{pad}{k}: {t} = {s}")
    elif isinstance(obj, list) and obj:
        out.append(f"{pad}[{len(obj)} items, first:]")
        out += sketch_json(obj[0], depth + 1, max_depth)
    return out


def sketch_html(text: str) -> list[str]:
    out = []
    tables = re.findall(r"<table[^>]*>", text, re.I)
    out.append(f"{len(tables)} <table> elements")
    for h in re.findall(r"<h[12][^>]*>(.*?)</h[12]>", text, re.I | re.S)[:12]:
        out.append("  h: " + re.sub(r"<[^>]+>", "", h).strip()[:70])
    ths = re.findall(r"<th[^>]*>(.*?)</th>", text, re.I | re.S)[:16]
    if ths:
        out.append("  th: " + " | ".join(re.sub(r"<[^>]+>", "", t).strip()[:18] for t in ths))
    for m in re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                        text, re.I | re.S)[:2]:
        out.append(f"  embedded JSON block, {len(m)} chars  <- often the real data")
    if re.search(r"__NEXT_DATA__|__NUXT__|window\.__", text):
        out.append("  framework state blob present (__NEXT_DATA__ or similar)")
    return out


def inspect(cycle: int, source_id: str, date: str | None) -> int:
    dates = stored_dates(cycle, source_id)
    if not dates:
        print(f"No stored captures for {source_id!r}. Run capture.py first.")
        return 1
    d = date or dates[-1]
    arts = P.load(source_id, d, DATA_DIR / str(cycle) / "raw")
    print(f"{source_id} · {d} · {len(arts)} artifacts "
          f"({len(dates)} dates stored, {dates[0]} to {dates[-1]})\n")
    for name, art in arts.items():
        print(f"── {name}  ({len(art.body):,} bytes, "
              f"HTTP {art.meta.get('status')}, sha {art.sha256[:12]})")
        head = art.body.lstrip()[:1]
        try:
            if head in (b"{", b"["):
                for line in sketch_json(art.json())[:40]:
                    print("   " + line)
            else:
                for line in sketch_html(art.text())[:20]:
                    print("   " + line)
        except Exception as e:
            print(f"   could not sketch: {e}")
        print()
    return 0


# --------------------------------------------------------------------------

def parse_date(cycle: int, date: str, registry: dict,
               only: set[str] | None) -> tuple[list[P.Row], list[str]]:
    raw_root = DATA_DIR / str(cycle) / "raw"
    rows: list[P.Row] = []
    problems: list[str] = []
    missing: list[str] = []

    for src in registry.get("sources", []):
        sid = src["id"]
        if only and sid not in only:
            continue
        arts = (P.load_static(sid, date, raw_root) if src.get("static_artifacts")
                else P.load(sid, date, raw_root))
        if not arts:
            missing.append(sid)
            continue
        mod = P.get(sid)
        if mod is None:
            problems.append(f"{sid}: captured but NO PARSER written "
                            f"({len(arts)} artifacts waiting)")
            continue
        ctx = P.Context(source=src, snapshot_date=date)
        try:
            got = mod.parse(arts, ctx)
        except NotImplementedError as e:
            problems.append(f"{sid}: {e}")
            continue
        except Exception as e:
            # Loud, not silent. A parser returning nothing looks exactly like a
            # quiet week, which is the worst possible failure for an archive.
            problems.append(f"{sid}: PARSER FAILED — {type(e).__name__}: {e}")
            continue
        rows.extend(got)

    # A source that is enabled and permitted but has NO stored bytes almost
    # always means the private archive was never synced down — parse.py does not
    # sync, only run.sh stage 0 does. Say so, rather than silently reporting
    # fewer rows than expected.
    enabled = [s["id"] for s in registry.get("sources", [])
               if s.get("enabled") and s.get("license") == "permitted"
               and (not only or s["id"] in only)]
    absent = [s for s in missing if s in enabled]
    if absent:
        problems.append(
            "NO RAW DATA for enabled source(s): " + ", ".join(absent) +
            "\n      These are probably sitting in the private archive. parse.py does"
            "\n      not sync — run:  ./forecast/run.sh --from parse")
    return rows, problems


def write_parsed(cycle: int, date: str, rows: list[P.Row],
                 only: set[str] | None = None,
                 backdated: bool = False) -> Path:
    """
    Write one date's parsed rows.

    MERGES when --only is in play. Writing the file wholesale would silently
    delete every other source's rows for that date — `parse.py --only medsl`
    would leave you with a day containing nothing but MEDSL, and the loss is
    invisible until you go looking for something that used to be there.
    Re-parsing everything restores it, but only if you notice.

    A CAPTURED ROW IS NEVER DISPLACED BY A BACKDATED ONE, which is what
    `backdated` exists for, and the reason is a bug this function had for a
    week. Race to the WH publishes a trend running months back, so parsing
    2026-08-26 produces a bucket of rows whose own date is 2026-08-25. Those
    were written with the merge key {race_to_the_wh}, which drops that
    source's existing rows for 08-25 and replaces them — so the 1,419 rows
    CAPTURED from their site on the 25th were overwritten by the 38 rows their
    trend line happens to place on that date. Every day, silently, to the day
    before.

    The old comment here worried about exactly this shape of loss and guarded
    the wrong half: it protected the OTHER sources on a backdated date and left
    the source's own captured rows unprotected.

    So a backdated write may only FILL a date, never overwrite one. Where the
    source already has captured rows for that date, they stay and the backdated
    rows for it are dropped. Where it has none — most of the archive, since the
    trend reaches back further than our capture does — they land as before.
    """
    out = DATA_DIR / str(cycle) / "parsed"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{date}.csv"

    records = [{f: getattr(r, f) for f in P.FIELDS} for r in rows]

    if only and path.exists():
        kept = []
        protected: set[str] = set()
        with path.open(encoding="utf-8") as fh:
            for existing in csv.DictReader(fh):
                sid = existing.get("source_id")
                row = {f: existing.get(f, "") for f in P.FIELDS}
                if sid not in only:
                    kept.append(row)                      # someone else's day
                elif backdated and (existing.get("provenance") or "") == "captured":
                    kept.append(row)                      # captured beats backdated
                    protected.add(sid)
                # otherwise: this source is being re-parsed, so drop and rewrite
        if protected:
            records = [r for r in records
                       if r.get("source_id") not in protected]
        records = kept + records

    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=P.FIELDS)
        w.writeheader()
        w.writerows(records)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Parse stored captures (no network).")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--date")
    ap.add_argument("--all", action="store_true", help="every stored date")
    # WINDOWING A FULL RE-PARSE. --all over the whole archive is minutes of
    # work, which does not fit every shell it has to run in, and a run killed
    # halfway leaves the store half-rebuilt. These take a slice.
    #
    # ASCENDING ORDER IS LOad-BEARING, and slices must be run oldest first. A
    # date is written wholesale from its own captures; the backdated rows that
    # belong in it arrive later, from the parses of dates after it. Run a
    # slice out of order and the wholesale write lands after the backfill it
    # was supposed to keep, and the backfill is gone.
    ap.add_argument("--from", dest="date_from", default=None,
                    help="with --all, earliest date to parse (inclusive)")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="with --all, latest date to parse (inclusive)")
    ap.add_argument("--only", help="comma-separated source ids")
    ap.add_argument("--inspect", metavar="SOURCE",
                    help="print the stored structure for a source and exit")
    a = ap.parse_args(argv)

    registry = load_registry(a.cycle)
    if a.inspect:
        return inspect(a.cycle, a.inspect, a.date)

    only = {s.strip() for s in a.only.split(",")} if a.only else None
    if a.all:
        dates = stored_dates(a.cycle)
        if a.date_from:
            dates = [d for d in dates if d >= a.date_from]
        if a.date_to:
            dates = [d for d in dates if d <= a.date_to]
    else:
        dates = [a.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")]
    if not dates:
        print("Nothing stored to parse. Run capture.py first.")
        return 0

    print("=" * 70)
    print(f"parse · cycle {a.cycle} · {len(dates)} date(s)")
    print("=" * 70)

    total = 0
    all_problems: list[str] = []
    for d in dates:
        rows, problems = parse_date(a.cycle, d, registry, only)
        all_problems += [f"{d}  {p}" for p in problems]
        if rows:
            # Rows are bucketed by their OWN date, not by the capture date.
            # A parser may backdate — Race to the WH publishes a trend running
            # back to February — and those observations belong in February's
            # file. Writing them all into today's would compress six months of
            # movement into a single overnight jump.
            buckets: dict[str, list] = {}
            for r in rows:
                buckets.setdefault(r.snapshot_date, []).append(r)
            for asof, group in sorted(buckets.items()):
                # A backdated bucket carries only the sources that backfilled.
                # Writing it without a merge key would replace that date's whole
                # file, deleting every other source already parsed for it — the
                # exact failure write_parsed's docstring warns about, arriving
                # by a route it did not anticipate.
                key = only if asof == d else {r.source_id for r in group}
                write_parsed(a.cycle, asof, group, key, backdated=(asof != d))
            if len(buckets) > 1:
                back = sorted(k for k in buckets if k != d)
                print(f"  {d}  backfilled {len(rows) - len(buckets.get(d, [])):5d} "
                      f"rows into {len(back)} earlier date(s): "
                      f"{back[0]} .. {back[-1]}")
            by_src: dict[str, int] = {}
            for r in rows:
                by_src[r.source_id] = by_src.get(r.source_id, 0) + 1
            total += len(rows)
            if len(dates) <= 10:
                print(f"  {d}  {len(rows):5d} rows  "
                      + ", ".join(f"{k}:{v}" for k, v in sorted(by_src.items())))
    if len(dates) > 10:
        print(f"  {total} rows across {len(dates)} dates")

    if all_problems:
        print("\n  PROBLEMS")
        seen = set()
        for p in all_problems:
            key = p.split("  ", 1)[-1][:70]
            if key in seen:
                continue
            seen.add(key)
            print(f"    {p}")
    print("-" * 70)
    print(f"  {total} rows written to forecast/data/{a.cycle}/parsed/")
    print("  (parsed/ is per-forecaster and gitignored — only derived/ is published)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
