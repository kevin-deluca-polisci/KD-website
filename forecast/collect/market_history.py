#!/usr/bin/env python3
"""
Backfill market history from the exchanges' own price archives.

    python3 forecast/collect/market_history.py --cycle 2026
    python3 forecast/collect/market_history.py --cycle 2026 --only kalshi
    python3 forecast/collect/market_history.py --cycle 2026 --dry-run

WHY THIS EXISTS

Markets are the shortest series on the site — two points — and not because
they move slowly. Daily capture began on 2026-08-19, and before that nobody
was writing the prices down. Every other family got history some other way:
the professionals from Wikipedia's revision record, polling and academic from
Silver's poll-level file. Markets had no equivalent, so the tracker showed a
line that started last week beside lines running back to January 2025.

Both exchanges publish their own history, free and without a key:

    Kalshi      GET /series/{series}/markets/{ticker}/candlesticks
                start_ts, end_ts, period_interval=1440 (daily bars)
                Settled contracts move to /historical/markets/{ticker}/...

    Polymarket  GET https://clob.polymarket.com/prices-history
                market={clob_token_id}, interval=max, fidelity=1440

WHAT THIS WRITES, AND WHY IT IS SHAPED LIKE THIS

Not a new data format. It reconstructs, for each past date, the SAME artifact
the live capture would have written that day, and drops it into that day's raw
directory. The parsers then read it exactly as they read a real capture — no
new parser, no branch for historical data, no second definition of how a
ladder is priced. The ladder logic is subtle enough that a second
implementation of it would be a liability.

The cost of that choice is that a synthesised artifact must be honest about
being synthesised, which is what the meta file is for: every one carries
`provenance: backfilled`, the endpoint it came from, and the timestamp of the
bar it was built from.

TWO RULES IT WILL NOT BREAK

1. NEVER OVERWRITE A REAL CAPTURE. If a date already holds an artifact for
   that series, it is left alone, whatever this script thinks the price was.
   A captured byte is evidence; a reconstruction is an inference, and the
   archive's whole value rests on not confusing the two.

2. NO PRICE, NO FILE. Where a contract has no bar for a date — it had not
   opened, or nothing traded — nothing is written. A market with no price is
   not a market at zero.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture  # noqa: E402  — Fetcher, registry loading, and its manners

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

POLY_CLOB = "https://clob.polymarket.com/prices-history"

# A synthesised bar is a DAILY close. Anything finer would imply we know the
# intraday path, which we do not keep and would not publish.
KALSHI_PERIOD_MINUTES = 1440
POLY_FIDELITY_MINUTES = 1440


def _newest_capture_day(cycle: int, source: str) -> Path | None:
    base = DATA / str(cycle) / "raw" / source
    if not base.is_dir():
        return None
    days = sorted(d for d in base.iterdir() if d.is_dir())
    return days[-1] if days else None


def _write(cycle: int, source: str, date: str, name: str,
           payload, meta_extra: dict, dry: bool) -> bool:
    """Write one synthesised artifact. Returns True if written."""
    day = DATA / str(cycle) / "raw" / source / date
    target = day / f"{name}.json"
    if target.exists():
        return False                      # rule 1: never overwrite a capture
    if dry:
        return True
    day.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload).encode("utf-8")
    target.write_bytes(body)
    meta = {
        "bytes": len(body),
        "provenance": "backfilled",
        "synthesised_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": ("Reconstructed from the exchange's own price history, not "
                 "captured on this date. Shaped like a live capture so the "
                 "parsers need no special case."),
        **meta_extra,
    }
    (day / f"{name}.meta.json").write_text(json.dumps(meta, indent=2))
    return True


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------

def kalshi_history(cycle: int, fetcher, since: str, dry: bool) -> tuple[int, int]:
    """Rebuild markets-<SERIES>.json for every past date we can price."""
    day = _newest_capture_day(cycle, "kalshi")
    if day is None:
        print("  kalshi: no capture to learn contract identities from — run "
              "capture.py first")
        return 0, 0

    cfg = {}
    for s in capture.load_registry(cycle).get("sources", []):
        if s["id"] == "kalshi":
            cfg = s.get("config") or {}
    base = (cfg.get("api_base") or "").rstrip("/")
    if not base:
        print("  kalshi: no api_base in the registry")
        return 0, 0

    start_ts = int(dt.datetime.fromisoformat(since + "T00:00:00+00:00").timestamp())
    end_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())

    # series -> {date -> [market dicts]}
    rebuilt: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    n_contracts = 0

    for f in sorted(day.glob("markets-*.json")):
        if f.name.endswith(".meta.json"):
            continue
        series = f.name[len("markets-"):-len(".json")]
        try:
            markets = (json.loads(f.read_text()) or {}).get("markets") or []
        except json.JSONDecodeError:
            continue
        for m in markets:
            ticker = m.get("ticker")
            if not ticker:
                continue
            n_contracts += 1
            q = urllib.parse.urlencode({"start_ts": start_ts, "end_ts": end_ts,
                                        "period_interval": KALSHI_PERIOD_MINUTES})
            url = f"{base}/series/{series}/markets/{ticker}/candlesticks?{q}"
            try:
                body, _meta = fetcher.get(url)
            except Exception as e:                      # noqa: BLE001
                print(f"    {ticker}: {type(e).__name__} — skipped")
                continue
            if dry or not body:
                continue
            try:
                candles = (json.loads(body) or {}).get("candlesticks") or []
            except json.JSONDecodeError:
                continue
            for c in candles:
                ts = c.get("end_period_ts") or c.get("ts")
                if ts is None:
                    continue
                # Kalshi reports the yes price in cents under a few shapes
                # depending on how the bar closed. Preferring the mid keeps
                # this consistent with the live parser's _price().
                px = None
                for path in (("price", "close"), ("yes_ask", "close"),
                             ("yes_bid", "close")):
                    node = c
                    for k in path:
                        node = (node or {}).get(k) if isinstance(node, dict) else None
                    if isinstance(node, (int, float)):
                        px = node
                        break
                if px is None:
                    continue                        # rule 2: no price, no row
                d0 = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
                rebuilt[series][d0].append({
                    "ticker": ticker,
                    "title": m.get("title", ""),
                    "subtitle": m.get("subtitle", ""),
                    "last_price": px,
                })

    written = 0
    for series, by_date in rebuilt.items():
        for d0, markets in by_date.items():
            if _write(cycle, "kalshi", d0, f"markets-{series}",
                      {"cursor": "", "markets": markets},
                      {"endpoint": f"{base}/series/{series}/markets/"
                                   f"{{ticker}}/candlesticks",
                       "contracts": len(markets)}, dry):
                written += 1
    return n_contracts, written


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------

def polymarket_history(cycle: int, fetcher, since: str, dry: bool) -> tuple[int, int]:
    day = _newest_capture_day(cycle, "polymarket")
    if day is None:
        print("  polymarket: no capture to learn contract identities from")
        return 0, 0

    start_ts = int(dt.datetime.fromisoformat(since + "T00:00:00+00:00").timestamp())
    n_contracts = 0
    # artifact name -> {date -> event payload}
    rebuilt: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for f in sorted(day.glob("event-*.json")):
        if f.name.endswith(".meta.json"):
            continue
        name = f.stem
        try:
            payload = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        events = payload if isinstance(payload, list) else [payload]
        for ev in events:
            for m in (ev.get("markets") or []):
                try:
                    tokens = json.loads(m.get("clobTokenIds") or "[]")
                except (TypeError, json.JSONDecodeError):
                    tokens = []
                if not tokens:
                    continue
                n_contracts += 1
                # The FIRST token is the "Yes" side, matching the order of
                # `outcomes`. Fetching only that one and deriving No as 1-p
                # keeps the pair internally consistent; fetching both invites
                # two independently-rounded prices that do not sum to 1.
                q = urllib.parse.urlencode({"market": tokens[0],
                                            "interval": "max",
                                            "fidelity": POLY_FIDELITY_MINUTES})
                try:
                    body, _meta = fetcher.get(f"{POLY_CLOB}?{q}")
                except Exception as e:                  # noqa: BLE001
                    print(f"    {m.get('question','?')[:40]}: "
                          f"{type(e).__name__} — skipped")
                    continue
                if dry or not body:
                    continue
                try:
                    hist = (json.loads(body) or {}).get("history") or []
                except json.JSONDecodeError:
                    continue
                # One bar per day: the last observation on each date.
                per_day: dict[str, float] = {}
                for pt in hist:
                    ts, p = pt.get("t"), pt.get("p")
                    if ts is None or p is None:
                        continue
                    d0 = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
                    if d0 >= since:
                        per_day[d0] = float(p)
                for d0, p in per_day.items():
                    rebuilt[name][d0].append({
                        "question": m.get("question", ""),
                        "outcomes": m.get("outcomes") or '["Yes", "No"]',
                        "outcomePrices": json.dumps([f"{p:.4f}", f"{1-p:.4f}"]),
                        "_event_title": ev.get("title", ""),
                    })

    written = 0
    for name, by_date in rebuilt.items():
        for d0, markets in by_date.items():
            title = markets[0].pop("_event_title", "") if markets else ""
            for mm in markets:
                mm.pop("_event_title", None)
            if _write(cycle, "polymarket", d0, name,
                      [{"title": title, "markets": markets}],
                      {"endpoint": POLY_CLOB, "contracts": len(markets)}, dry):
                written += 1
    return n_contracts, written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--since", default="2025-01-01",
                    help="earliest date to reconstruct (default 2025-01-01)")
    ap.add_argument("--only", default="",
                    help="kalshi, polymarket, or blank for both")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch nothing and write nothing; report the plan")
    a = ap.parse_args(argv)

    reg = capture.load_registry(a.cycle)
    fetcher = capture.Fetcher(reg.get("contact") or {}, reg.get("defaults") or {},
                              dry_run=a.dry_run)

    want = {s.strip() for s in a.only.split(",") if s.strip()} or {"kalshi",
                                                                   "polymarket"}
    print("=" * 68)
    print(f"market history · cycle {a.cycle} · since {a.since}"
          f"{' · DRY RUN' if a.dry_run else ''}")
    print("=" * 68)

    total_written = 0
    if "kalshi" in want:
        n, w = kalshi_history(a.cycle, fetcher, a.since, a.dry_run)
        print(f"  kalshi:     {n} contract(s) queried, {w} date-artifact(s) written")
        total_written += w
    if "polymarket" in want:
        n, w = polymarket_history(a.cycle, fetcher, a.since, a.dry_run)
        print(f"  polymarket: {n} contract(s) queried, {w} date-artifact(s) written")
        total_written += w

    print(f"\n  {total_written} artifact(s) written. Existing captures were "
          f"left untouched.")
    if total_written and not a.dry_run:
        print(f"  next: python3 forecast/collect/parse.py --cycle {a.cycle} --all")
        print(f"        then aggregate and publish as usual")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
