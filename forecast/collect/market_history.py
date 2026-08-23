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

3. A SYNTHESISED MARKET CARRIES THE CAPTURED MARKET'S OWN FIELDS. Only the
   price is replaced. See SHAPE_VERSION below — this rule was learned the
   expensive way and it is the one that makes rule "shaped like a live
   capture" true rather than merely intended.

WHAT WENT WRONG THE FIRST TIME — READ THIS BEFORE CHANGING THE SYNTHESIS

Version 1 built each historical market from four fields it chose by hand:
ticker, title, subtitle, last_price. That is not the artifact the live capture
would have written; it is a summary of one, and the fields it dropped were the
ones the ladder readers depend on.

collect/parsers/kalshi.py reads a seat ladder as a DISTRIBUTION across markets,
and to do that it needs `yes_sub_title` (the bucket label, "218-221") and
`event_ticker` (the cycle guard). Neither survived the summary. `_seat_rows`
therefore found fewer than three readable buckets, returned nothing, and the
markets fell through to the per-market classifier — which read every rung of
the ladder as a chamber-CONTROL price, because each carries "House" and
"party" in its text. Twelve bucket prices between 0.045 and 0.16 were filed as
P(Democratic House) for every day from 2025-12-21 to 2026-02-20.

Nothing raised. Every row was individually well-formed: a probability in
[0, 1] about the House is exactly what the validator checks for. The published
tracker showed the market line at 8% in January against 88% in August — a
collapse that never happened, drawn from real prices read as the wrong
quantity. It is the same failure the KXRHOUSESEATS comment in the parser
already describes, arriving by a different road, which is the argument for
copying the market dict wholesale rather than picking fields out of it: the
next reader of a captured field is not obliged to tell this module first.

Polymarket had the same defect for the same reason — `groupItemTitle` carries
the party for control markets and was not copied.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import re
import sys
import traceback
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
# The shape of a synthesised artifact, and how a wrong one gets taken back.
#
# A capture is evidence and is never rewritten. A BACKFILLED artifact is an
# inference this module made, and an inference made by a version we now know
# to have been wrong has to be withdrawable — otherwise the only way to unpick
# a bad reconstruction is to go into the private archive by hand, which is the
# kind of chore that does not get done and leaves the error published.
#
# So every synthesised artifact records the shape version that built it, and a
# run whose version is newer sweeps the older ones out of its own source's
# directories before writing, taking their parsed rows with them. Rule 1 is
# untouched: nothing without `provenance: backfilled` in its meta is ever
# considered, so a real capture cannot be caught by this even by accident.
#
#   1  ticker/title/subtitle/last_price, hand-picked. Broke both ladder
#      readers. See the module docstring.
#   2  the captured market dict copied whole, with the live price fields
#      removed and the historical close put back in their place.
#   3  the BOOK as well as the price: both legs of the bid/ask, the day's
#      volume and its open interest, all of which the candles carried and v2
#      threw away. Needed for the portfolio evaluation, which has to buy at
#      the ask and sell at the bid rather than transact at a midpoint nobody
#      was offering. Bumping the version is what makes the sweep withdraw the
#      v2 artifacts and rewrite them complete.
SHAPE_VERSION = 3

# Fields that describe the market AT CAPTURE TIME and would be a lie on a past
# date. Everything else — ticker, event_ticker, titles, sub-titles, strikes,
# rules, the group label — describes the contract itself and is as true in
# January as it is today, so it is copied through untouched.
_LIVE_ONLY = re.compile(
    r"(price|bid|ask|volume|open_interest|liquidity|dollar_recency|"
    r"settle|result|expiration_value|previous_)", re.I)


def _is_live_only(key: str) -> bool:
    """Is this field a fact about today rather than about the contract?

    Matched by pattern rather than by a list of names, because the failure to
    guard against is a field we have not met: Kalshi renamed its whole price
    surface once already (the `_dollars` suffix) and a deny-list written today
    would let the next such field through, silently stamping today's price on
    a January artifact. A pattern over-reaches instead, which costs archive
    detail and cannot mislead.

    The one exception is a STRIKE. `strike_price` and `custom_strike_price`
    name the contract's own boundary — the same number in January as today —
    and they are what a future reader would use to price a ladder without
    parsing its label text.
    """
    return bool(_LIVE_ONLY.search(key)) and "strike" not in key.lower()

# The Polymarket equivalents. Gamma's market dict is flat camelCase.
_POLY_LIVE_ONLY = {
    "outcomePrices", "lastTradePrice", "bestBid", "bestAsk", "spread",
    "volume", "volumeNum", "volume24hr", "volume1wk", "volume1mo", "volume1yr",
    "liquidity", "liquidityNum", "oneDayPriceChange", "oneHourPriceChange",
    "oneWeekPriceChange", "oneMonthPriceChange", "lastTradeTime",
    "umaResolutionStatus", "competitive",
}


def _kalshi_market(m: dict, px_cents: float, book: dict | None = None) -> dict:
    """The captured Kalshi market, re-priced to a past day's close.

    The price goes back as `last_price_dollars` — the modern, dollar-
    denominated, string-valued field the parser reads first among the
    single-sided names. Writing it as a bare `last_price` in cents would work
    today and would sit one API rename away from breaking, and it has one
    live failure mode besides: the parser divides by 100 only when the value
    exceeds 1, so a genuine 1-cent bucket would arrive as a probability of
    1.0. In dollars there is no such ambiguity.
    """
    out = {k: v for k, v in m.items() if not _is_live_only(k)}
    out["last_price_dollars"] = f"{px_cents / 100.0:.4f}"
    # The two legs and the day's activity, where the bar carried them. Written
    # in the same fixed-point string form the live endpoint uses, so the parser
    # reads a reconstructed day exactly as it reads a captured one.
    for k, v in (book or {}).items():
        out[k] = v
    # A contract with a traded bar on that date was open on that date. Say so
    # explicitly rather than inheriting today's status, which for a settled
    # market would make the ladder readers skip a day they can see the price of.
    out["status"] = "active"
    out["backfilled"] = True
    return out


def _poly_market(m: dict, p: float) -> dict:
    """The captured Polymarket market, re-priced to a past day's close."""
    out = {k: v for k, v in m.items() if k not in _POLY_LIVE_ONLY}
    out["outcomePrices"] = json.dumps([f"{p:.4f}", f"{1 - p:.4f}"])
    out["backfilled"] = True
    return out


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


def _candle_book(c: dict) -> dict:
    """Both legs, the volume and the open interest from one daily bar.

    THE MID WAS NEVER ENOUGH, and this is the repair. A synthesised artifact
    used to carry one number — the bid/ask midpoint — which is the right answer
    to "what did the market think that day" and the wrong one to "what would
    that forecast have cost to act on". You buy at the ask and sell at the bid,
    and on a three-cent spread the difference decides the sign of every
    marginal bet in a portfolio.

    Kalshi's candles carry both legs and the day's volume already, so none of
    this needs a new fetch: it was being discarded on the way in. Recovering it
    means the whole historical book comes back with the price, and the
    portfolio evaluation can run over the archive rather than over the eleven
    weeks since live capture began.
    """
    out: dict = {}
    bid, ask = (_leg_close(c, leg) for leg in _BID_ASK)
    if bid is not None:
        out["yes_bid_dollars"] = f"{bid / 100.0:.4f}"
    if ask is not None:
        out["yes_ask_dollars"] = f"{ask / 100.0:.4f}"
    for src, dest in (("volume_fp", "volume"), ("volume", "volume"),
                      ("open_interest_fp", "open_interest"),
                      ("open_interest", "open_interest")):
        if dest in out or src not in c:
            continue
        try:
            v = float(c[src])
        except (TypeError, ValueError):
            continue
        if v >= 0:
            out[dest] = v
    return out


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


def _withdraw_stale(cycle: int, source: str, dry: bool) -> int:
    """Delete this source's backfilled artifacts written by an older shape.

    AND THE PARSED ROWS THEY PRODUCED. Deleting the artifact alone would not
    unpublish anything: parse.py rewrites a date's file only when that date
    parses to at least one row, so a date whose only Kalshi artifact had just
    been removed would keep its old rows forever and the bad numbers would
    stay on the site through any number of clean re-runs.

    Only files whose meta says `provenance: backfilled` are considered, so a
    real capture cannot be reached from here. parsed/ is per-forecaster and
    gitignored; it is rebuilt from raw/ on the next `parse.py --all`.
    """
    base = DATA / str(cycle) / "raw" / source
    if not base.is_dir():
        return 0
    stale_dates: set[str] = set()
    removed = 0
    for meta_path in sorted(base.glob("*/*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if meta.get("provenance") != "backfilled":
            continue                      # a capture, or something else's file
        if int(meta.get("shape_version") or 1) >= SHAPE_VERSION:
            continue                      # current shape: leave it alone
        stale_dates.add(meta_path.parent.name)
        removed += 1
        if dry:
            continue
        artifact = meta_path.with_name(meta_path.name[:-len(".meta.json")] + ".json")
        artifact.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
    if not removed:
        return 0
    print(f"    withdrew {removed} artifact(s) from shape version < "
          f"{SHAPE_VERSION} across {len(stale_dates)} date(s)"
          f"{' (dry run)' if dry else ''}")
    if dry:
        return removed
    # Empty day directories would otherwise read as "captured but no bytes",
    # which parse.py reports as a missing private archive on every run.
    for d in sorted(stale_dates):
        day = base / d
        if day.is_dir() and not any(day.iterdir()):
            day.rmdir()
    dropped, touched = _drop_parsed_rows(cycle, source, stale_dates)
    if dropped:
        print(f"    dropped {dropped} stale parsed row(s) for {source} "
              f"across {len(touched)} date(s)")
    cleared = _clear_timeline_dates(cycle, touched)
    if cleared:
        print(f"    cleared {cleared} timeline row(s) on those dates — they "
              f"refill from the corrected averages in this same run")
    return removed


def _clear_timeline_dates(cycle: int, dates: set[str]) -> int:
    """Delete every timeline row on the given dates, so the chart refills them.

    THE WITHDRAWAL HAS TO REACH AS FAR AS THE ERROR DID. Pulling the artifact
    and its parsed rows corrects category_averages.csv, and the comparison
    table and the movement card read that file directly, so they are right
    again on the next run. The CHART does not: timeline.csv accumulates, and
    its self-healing pass is additive — it fills dates it has never seen and
    leaves rows it already holds exactly as they were. A wrong number that is
    already in there stays in there, and the chart is the part of the site
    people actually look at.

    Deleting the date entirely rather than just this source's series is
    deliberate: charts.collect_today() rebuilds a date from that date's
    published averages in one pass, so a half-cleared date would come back
    half-old. Every series on these dates is re-derived from the averages,
    which is where the authority lives.

    Scoped to dates whose parsed rows actually changed — not every date the
    sweep touched — because rewriting a date whose numbers did not move gains
    nothing and risks losing a row written from inputs we no longer hold.
    """
    if not dates:
        return 0
    path = DATA / str(cycle) / "derived" / "timeline.csv"
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        rows = list(reader)
    kept = [r for r in rows if r.get("snapshot_date") not in dates]
    if len(kept) == len(rows):
        return 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(kept)
    return len(rows) - len(kept)


def _drop_parsed_rows(cycle: int, source: str,
                      dates: set[str]) -> tuple[int, set[str]]:
    """Remove one source's rows from the given dates' parsed files.

    Returns (rows dropped, the dates that actually lost a row).
    """
    parsed = DATA / str(cycle) / "parsed"
    dropped = 0
    touched: set[str] = set()
    for d in sorted(dates):
        path = parsed / f"{d}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            rows = list(reader)
        kept = [r for r in rows if r.get("source_id") != source]
        dropped_here = len(rows) - len(kept)
        if not dropped_here:
            continue
        dropped += dropped_here
        touched.add(d)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(kept)
    return dropped, touched


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
        "shape_version": SHAPE_VERSION,
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
    _withdraw_stale(cycle, "kalshi", dry)
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
    # series -> [first date priced, last date priced, {dates}]. Printed at the
    # end because "the backfill stopped in February" is a question about what
    # the exchange served, and until this was recorded there was no way to tell
    # a short candle history from a date we declined to write.
    spans: dict[str, list] = collections.defaultdict(lambda: ["", "", set()])
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
                rebuilt[series][d0].append(
                    _kalshi_market(m, px, _candle_book(c)))
                span = spans[series]
                span[0] = min(span[0], d0) if span[0] else d0
                span[1] = max(span[1], d0) if span[1] else d0
                span[2].add(d0)

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
    for series, (lo, hi, dates) in sorted(spans.items()):
        print(f"    candles {series:22s} {lo} .. {hi}  ({len(dates)} date(s))")
    return n_contracts, written


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------

def polymarket_history(cycle: int, fetcher, since: str, dry: bool) -> tuple[int, int]:
    _withdraw_stale(cycle, "polymarket", dry)
    days = _capture_days(cycle, "polymarket")
    if not days:
        print("  polymarket: no capture to learn contract identities from")
        return 0, 0

    start_ts = int(dt.datetime.fromisoformat(since + "T00:00:00+00:00").timestamp())
    n_contracts = 0
    stats = collections.Counter()
    # artifact name -> {date -> {event title -> [market dicts]}}
    #
    # GROUPED BY EVENT, because a Polymarket artifact is a LIST OF EVENTS and
    # each event is a question whose markets only mean anything together. The
    # first version flattened every market of the file into one anonymous
    # event with one title, which put the per-district markets of one event
    # under the control question of another.
    rebuilt: dict[str, dict[str, dict[str, list]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list)))
    # (artifact name, event title) -> the event's own fields, markets removed.
    ev_meta: dict[tuple, dict] = {}

    # (artifact name, event title, market question) -> market dict, unioned
    # across every captured day for the same reason kalshi does it: a market
    # that closed before the newest capture is exactly the history worth
    # having, and it appears in no recent file.
    seen: dict[tuple, tuple] = {}
    for day in days:
        for f in sorted(day.glob("event-*.json")):
            if f.name.endswith(".meta.json"):
                continue
            try:
                payload = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            events = payload if isinstance(payload, list) else [payload]
            for ev in events:
                for m in (ev.get("markets") or []):
                    key = (f.stem, ev.get("title", ""), m.get("question", ""))
                    seen[key] = (f.stem, ev, m)
    print(f"    {len(seen)} distinct contract(s) across {len(days)} "
          f"capture day(s)")

    for (name, _t, _q), (name2, ev, m) in sorted(seen.items()):
            if True:
                try:
                    tokens = json.loads(m.get("clobTokenIds") or "[]")
                except (TypeError, json.JSONDecodeError):
                    tokens = []
                if not tokens:
                    stats["no_token"] += 1
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
                    stats["fetch_failed"] += 1
                    continue
                if dry or not body:
                    stats["no_body"] += 1
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
                    stats["no_history"] += 1
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
                        stats["points"] += 1
                title = str(ev.get("title") or "")
                ev_meta[(name, title)] = {k: v for k, v in ev.items()
                                          if k != "markets"}
                for d0, p in per_day.items():
                    rebuilt[name][d0][title].append(_poly_market(m, p))

    written = 0
    for name, by_date in rebuilt.items():
        for d0, by_event in by_date.items():
            payload = [{**ev_meta.get((name, title), {"title": title}),
                        "markets": markets}
                       for title, markets in sorted(by_event.items())]
            n_markets = sum(len(ms) for ms in by_event.values())
            stats["dates"] += 1
            if _write(cycle, "polymarket", d0, name, payload,
                      {"endpoint": POLY_CLOB, "contracts": n_markets}, dry):
                written += 1
            else:
                stats["skipped_exists"] += 1
    if stats:
        print("    polymarket stages: " + ", ".join(f"{k}={v}" for k, v in
                                                    sorted(stats.items())))
    return n_contracts, written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--since", default="2025-01-20",
                    help="earliest date to reconstruct (default 2025-01-20, "
                         "inauguration day — where every series on the site "
                         "starts; see model/academic.py SERIES_START)")
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

    # ONE SOURCE'S CRASH MUST NOT DISCARD THE OTHER'S WORK.
    #
    # It already did once. Kalshi finished cleanly — 20,230 candles priced,
    # 3,565 artifacts written — and then Polymarket raised a NameError, the
    # script exited 1, the workflow step failed, the job stopped before the
    # raw push, and every one of those 3,565 files went into the bin with the
    # runner. The work was done and correct and thrown away on account of a
    # typo in an unrelated function.
    #
    # So each source is isolated, its traceback is printed in full rather than
    # summarised, and the exit code reflects what actually happened: zero if
    # anything was written and can be pushed, one only if the whole attempt
    # produced nothing. A partial success that reports itself loudly is worth
    # more than a clean failure that loses the good half.
    total_written = 0
    failed: list[str] = []
    for src, fn in (("kalshi", kalshi_history),
                    ("polymarket", polymarket_history)):
        if src not in want:
            continue
        try:
            n, w = fn(a.cycle, fetcher, a.since, a.dry_run)
            print(f"  {src+':':12} {n} contract(s) queried, "
                  f"{w} date-artifact(s) written")
            total_written += w
        except Exception:                                   # noqa: BLE001
            failed.append(src)
            print(f"\n  !! {src} FAILED — its traceback follows. Anything the "
                  f"other source wrote is kept.\n")
            traceback.print_exc()

    print(f"\n  {total_written} artifact(s) written. Existing captures were "
          f"left untouched.")
    if failed:
        print(f"  WARNING: {', '.join(failed)} failed. The artifacts above are "
              f"still good and will be pushed; re-run for the rest.")
    if total_written and not a.dry_run:
        print(f"  next: python3 forecast/collect/parse.py --cycle {a.cycle} --all")
        print(f"        then aggregate and publish as usual")
    # Non-zero only when the whole attempt was fruitless.
    return 0 if total_written or a.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
