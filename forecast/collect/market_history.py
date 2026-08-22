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
import collections
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


# ---------------------------------------------------------------------------
# Shape tolerance
# ---------------------------------------------------------------------------
# WRITTEN BLIND, so written to survive being wrong. The first version of this
# module hard-coded the response shape from the API docs and produced zero
# artifacts against the real endpoint, with no way to tell whether the fetch
# had failed, the key was named something else, or the price sat one level
# deeper. A backfill that silently writes nothing is the same failure mode as
# the Kalshi capture it was meant to repair.
#
# So: find the list wherever it is, find the price whatever it is called, and
# print the shape of the first response from each source so the next run
# reports the truth instead of a zero.

_SHAPE_REPORTED: set = set()


def _report_shape(source: str, ident: str, payload, body: bytes,
                  found: list | None = None) -> None:
    """Print the shape of the FIRST response per source, once."""
    if source in _SHAPE_REPORTED:
        return
    _SHAPE_REPORTED.add(source)
    print(f"    [shape] {source} first response for {ident}")
    if payload is None:
        print(f"    [shape]   not JSON; {len(body or b'')} bytes, "
              f"starts {str((body or b'')[:80])}")
        return
    if isinstance(payload, dict):
        print(f"    [shape]   top-level keys: {sorted(payload.keys())}")
    else:
        print(f"    [shape]   top level is {type(payload).__name__}")
    if found:
        print(f"    [shape]   list of {len(found)}; first element keys: "
              f"{sorted(found[0].keys()) if isinstance(found[0], dict) else type(found[0])}")
        if isinstance(found[0], dict):
            for k, v in found[0].items():
                if isinstance(v, dict):
                    print(f"    [shape]     {k} -> {sorted(v.keys())}")
    else:
        print(f"    [shape]   no list of dicts found in the payload")


def _first_list(payload) -> list:
    """The first list-of-dicts in the payload, wherever it hides."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    # Named candidates first so a well-behaved response is taken literally.
    for k in ("candlesticks", "candles", "history", "data", "results"):
        v = payload.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    for v in payload.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


# THE ACTUAL SHAPE, read off a real response rather than guessed at.
#
#   {'end_period_ts', 'open_interest_fp', 'price', 'volume_fp',
#    'yes_ask', 'yes_bid'}
#     price   -> {}                       <- EMPTY on these contracts
#     yes_ask -> {'close_dollars', 'high_dollars', 'low_dollars', 'open_dollars'}
#     yes_bid -> {'close_dollars', 'high_dollars', 'low_dollars', 'open_dollars'}
#
# Two things defeated the first attempt, and both are worth naming because
# either alone would have been enough.
#
# `price` EXISTS BUT IS EMPTY. A lookup that tests `if "price" in candle` finds
# it, walks into a dict with no keys, and comes back with nothing — which is
# indistinguishable from a missing field unless you look. Preferring it was
# reasonable and wrong.
#
# THE SUB-KEYS ARE DOLLAR-DENOMINATED AND SUFFIXED. Not `close` but
# `close_dollars`, carrying 0-1 rather than 0-100. Kalshi has been moving the
# whole API this way — the markets endpoint now returns `yes_bid_dollars`
# beside the older `yes_bid` — so the suffix is the direction of travel and not
# a quirk of candlesticks.
#
# THE MID, NOT ONE SIDE. collect/parsers/kalshi.py prices a live market as the
# midpoint of bid and ask. A backfilled bar taken from the ask alone would sit
# systematically above the live series it joins, putting a step at the join
# that is a change of method wearing the clothes of a change of price. Taking
# the mid here keeps the two halves of the series measuring the same thing.
_BID_ASK = ("yes_bid", "yes_ask")
_CLOSE_KEYS = ("close_dollars", "close", "mean_dollars", "mean",
               "last_dollars", "last")


def _leg_close(c: dict, leg: str) -> float | None:
    node = c.get(leg)
    if not isinstance(node, dict):
        return _as_cents(node)
    for k in _CLOSE_KEYS:
        got = _as_cents(node.get(k))
        if got is not None:
            return got
    return None


def _as_cents(v) -> float | None:
    """Kalshi quotes either cents (0-100) or dollars (0-1). Normalise to cents.

    STRINGS COUNT. This rejected them, which is why 8,996 candles in a row
    reported "no price" while the shape report cheerfully listed
    `yes_bid -> close_dollars` right above it. Kalshi sends its prices as
    JSON strings — collect/parsers/kalshi.py has known this since it was
    written ("Kalshi yes_bid/yes_ask are strings") and this module did not.
    Two readers of the same API, one of which had already learned the lesson.

    The 0-1 test is a heuristic with one real failure mode: a genuine cents
    price of exactly 0 or 1 reads as dollars and is multiplied by a hundred. At
    those extremes the contract is at the edge of its range, the mid is
    dominated by the other leg, and a bucket priced at 1 cent contributes
    almost nothing to a normalised ladder. Worth knowing; not worth a more
    elaborate rule that would need its own explanation.
    """
    if isinstance(v, bool) or v is None:
        return None
    try:
        v = float(v)                      # accepts "0.34" as well as 0.34
    except (TypeError, ValueError):
        return None
    if v != v:                            # NaN
        return None
    return v * 100.0 if 0.0 <= v <= 1.0 else v


def _candle_price(c: dict) -> float | None:
    """A yes-price in CENTS from one candle: the bid/ask mid where both exist."""
    bid, ask = (_leg_close(c, leg) for leg in _BID_ASK)
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    # `price` last, and only if it actually holds something — on the seat
    # ladders it is an empty dict.
    node = c.get("price")
    if isinstance(node, dict):
        for k in _CLOSE_KEYS:
            got = _as_cents(node.get(k))
            if got is not None:
                return got
    for k in ("last_price", "last_price_dollars", "close"):
        got = _as_cents(c.get(k))
        if got is not None:
            return got
    return None


def _capture_days(cycle: int, source: str) -> list[Path]:
    """Every captured day, oldest first.

    NOT JUST THE NEWEST, which is what this used to read and which made the
    result depend on which day happened to be last. A capture day holds only
    the series that were fetchable that morning: 2026-08-19 carries 99
    contracts, 2026-08-20 carries 10, and the seat ladders appear on neither
    because they were not being fetched until series_always was implemented.
    Reading one day therefore backfilled whatever that day happened to know
    about, and a probe run against a thin day looked like a broken script.

    Taking the union across days means a contract seen on ANY day gets its
    history fetched, which is what we actually want — a market that closed in
    June is exactly the kind of thing worth recovering, and it will never
    appear in the newest capture.
    """
    base = DATA / str(cycle) / "raw" / source
    if not base.is_dir():
        return []
    return sorted(d for d in base.iterdir() if d.is_dir())


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
    days = _capture_days(cycle, "kalshi")
    if not days:
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

    # (series, ticker) -> market dict, unioned across every captured day.
    contracts: dict[tuple, dict] = {}
    for day in days:
        for f in sorted(day.glob("markets-*.json")):
            if f.name.endswith(".meta.json"):
                continue
            series = f.name[len("markets-"):-len(".json")]
            try:
                markets = (json.loads(f.read_text()) or {}).get("markets") or []
            except json.JSONDecodeError:
                continue
            for m in markets:
                if m.get("ticker"):
                    contracts[(series, m["ticker"])] = m
    print(f"    {len(contracts)} distinct contract(s) across {len(days)} "
          f"capture day(s)")

    # COUNTERS AT EVERY STAGE. Twice now this module has reported "0 written"
    # without saying whether the fetch failed, the candles were empty, the
    # price was unreadable, or every date already existed. Those need different
    # fixes and the summary could not tell them apart.
    stats = collections.Counter()
    for (series, ticker), m in sorted(contracts.items()):
        if True:
            n_contracts += 1
            q = urllib.parse.urlencode({"start_ts": start_ts, "end_ts": end_ts,
                                        "period_interval": KALSHI_PERIOD_MINUTES})
            url = f"{base}/series/{series}/markets/{ticker}/candlesticks?{q}"
            try:
                body, _meta = fetcher.get(url)
            except Exception as e:                      # noqa: BLE001
                print(f"    {ticker}: {type(e).__name__} — skipped")
                stats["fetch_failed"] += 1
                continue
            if dry or not body:
                stats["no_body"] += 1
                continue
            try:
                payload = json.loads(body) or {}
            except json.JSONDecodeError:
                _report_shape("kalshi", ticker, None, body)
                continue
            candles = _first_list(payload)
            _report_shape("kalshi", ticker, payload, body, candles)
            if not candles:
                stats["no_candles"] += 1
                continue
            for c in candles:
                ts = (c.get("end_period_ts") or c.get("ts")
                      or c.get("timestamp") or c.get("period_end_ts"))
                stats["candles"] += 1
                if ts is None:
                    stats["no_timestamp"] += 1
                    continue
                px = _candle_price(c)
                if px is None:
                    stats["no_price"] += 1
                    continue      # rule 2: no price, no row
                stats["priced"] += 1
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
            stats["dates"] += 1
            if _write(cycle, "kalshi", d0, f"markets-{series}",
                      {"cursor": "", "markets": markets},
                      {"endpoint": f"{base}/series/{series}/markets/"
                                   f"{{ticker}}/candlesticks",
                       "contracts": len(markets)}, dry):
                written += 1
            else:
                stats["skipped_exists"] += 1
    if stats:
        print("    kalshi stages: " + ", ".join(f"{k}={v}" for k, v in
                                                sorted(stats.items())))
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
                    payload = json.loads(body) or {}
                except json.JSONDecodeError:
                    _report_shape("polymarket", m.get("question", "?"), None, body)
                    continue
                hist = _first_list(payload)
                _report_shape("polymarket", m.get("question", "?"), payload,
                              body, hist)
                if not hist:
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
