"""
Kalshi — prediction market.

Publication: individual. Kalshi prices are a public order book in real time, so
there is nothing to protect by aggregating them, and the per-market detail is
the most interesting source-level data in the archive.

Prices are fixed-point STRINGS ("0.4200") since the integer-cent fields were
removed in Q1 2026. Do not coerce to int.
"""
from __future__ import annotations
import re
from . import (Context, LoadedArtifact, NATIONAL_HOUSE, NATIONAL_SENATE, Row,
               is_state, margin_ladder_expectation, race_id, state_from_text)

# Kalshi ticker conventions are not documented and shift. These patterns are a
# best effort; --inspect the first real capture and tighten them.
_SEN = re.compile(r"SENATE.*?([A-Z]{2})\b|\b([A-Z]{2})\b.*?SENATE", re.I)
_GOV = re.compile(r"GOV(?:ERNOR)?.*?([A-Z]{2})\b", re.I)
_HOU = re.compile(r"HOUSE.*?([A-Z]{2})[-_]?(\d{1,2})\b", re.I)
_CTRL_H = re.compile(r"(HOUSE).*(CONTROL|MAJORITY|PARTY)|((CONTROL|MAJORITY)).*(HOUSE)", re.I)
_CTRL_S = re.compile(r"(SENATE).*(CONTROL|MAJORITY|PARTY)|((CONTROL|MAJORITY)).*(SENATE)", re.I)


def _price(m: dict) -> float | None:
    """Mid-price as a probability.

    THE FIELD NAMES CARRY A SUFFIX. Kalshi removed the integer-cent fields in
    Q1 2026 and replaced them with fixed-point strings named `yes_bid_dollars`,
    `yes_ask_dollars`, `last_price_dollars`. This function was updated in its
    docstring and not in its code: it went on asking for `yes_bid`, got None
    every time, and returned None for every market Kalshi has ever served us.

    Nothing complained. `parse` treats "no market had a price" as the honest
    report that Kalshi has nothing open — which was TRUE of most of the series
    in the allowlist and so looked entirely plausible — and Kalshi contributed
    exactly zero rows to the archive from the day it was added. The suffixed
    names are tried first and the legacy ones kept as a fallback, so a replay
    of an older capture still parses.
    """
    for a, b in (("yes_bid_dollars", "yes_ask_dollars"),
                 ("last_price_dollars", None),
                 ("yes_bid", "yes_ask"), ("last_price", None)):
        va, vb = m.get(a), m.get(b) if b else None
        try:
            if va is not None and vb is not None:
                p = (float(va) + float(vb)) / 2
            elif va is not None:
                p = float(va)
            else:
                continue
        except (TypeError, ValueError):
            continue
        if p > 1.0:          # defensive: an older cents field would land here
            p /= 100.0
        if 0.0 <= p <= 1.0:
            return p
    return None


def _classify(ticker: str, title: str) -> tuple[str, str, str, str] | None:
    """-> (race_id, chamber, state, district) or None if not a race we track."""
    blob = f"{ticker} {title}"
    if _CTRL_H.search(blob):
        return NATIONAL_HOUSE, "national", "", ""
    if _CTRL_S.search(blob):
        return NATIONAL_SENATE, "national", "", ""
    # Every branch below checks is_state() before minting a race_id. These
    # matchers are case-insensitive, and [A-Z]{2} under IGNORECASE matches any
    # two letters — that is how polymarket ended up filing every Senate market
    # under a state called "OF" (from "balance OF power"). A two-letter match
    # is a candidate, not a state.
    m = _HOU.search(blob)
    if m and is_state(m.group(1)):
        st, d = m.group(1).upper(), m.group(2)
        return race_id("house", st, d), "house", st, f"{int(d):02d}"
    if re.search(r"GOV(ERNOR)?", blob, re.I) and (st := state_from_text(blob)):
        return race_id("governor", st), "governor", st, ""
    if re.search(r"SENATE", blob, re.I) and (st := state_from_text(blob)):
        return race_id("senate", st), "senate", st, ""
    return None


# --------------------------------------------------------------------------
# Seat-count markets: a distribution, not a price.
#
# KXDHOUSESEATS and KXDSENATESEATS quote a LADDER of buckets — "218-221",
# "Above 249", "Below 210" — each priced as the probability the final seat
# count lands in it. That is a probability distribution over seat counts, and
# it is the only place any exchange gives us one. Everything else in the market
# category is a chamber-control price, which is why the seats column for
# Markets has been empty since the site was built.
#
# Two numbers come out of it, and both are the market's own:
#   seats_D      the expectation of the distribution
#   win_prob_D   the mass at or above the majority threshold
# Taking the probability from the SAME ladder as the expectation means the two
# cannot contradict each other, which they could if the probability came from
# a separate control market with its own spread.
#
# THE CYCLE TRAP. These series carry more than one Congress at a time: as of
# today KXDSENATESEATS lists both the 120th (event suffix -27, elected in 2026)
# and the 121st (-29, elected in 2028). Filtering by series alone would average
# a 2028 forecast into a 2026 one. The event suffix is the guard.
# --------------------------------------------------------------------------

# 2026 elects the 120th Congress, which is seated in January 2027.
_CYCLE_EVENT = re.compile(r"-27(?:$|[-A-Z0-9])")
_SEAT_SERIES = {
    "KXDHOUSESEATS": (NATIONAL_HOUSE, "house", 218),
    "KXDSENATESEATS": (NATIONAL_SENATE, "senate", 51),
}
_ABOVE = re.compile(r"^\s*above\s+(\d+)", re.I)
_BELOW = re.compile(r"^\s*(?:below|less than|fewer than)\s+(\d+)", re.I)
_RANGE = re.compile(r"^\s*(\d+)\s*[-\u2013]\s*(\d+)\s*$")
_EXACT = re.compile(r"^\s*(?:exactly\s+)?(\d+)\s*$")


def _bucket(label: str):
    """'246-249' -> (246, 249). Open ends come back as None on that side."""
    if not label:
        return None
    if (m := _RANGE.match(label)):
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)
    if (m := _ABOVE.match(label)):
        return (int(m.group(1)) + 1, None)
    if (m := _BELOW.match(label)):
        return (None, int(m.group(1)) - 1)
    if (m := _EXACT.match(label)):
        n = int(m.group(1))
        return (n, n)
    return None


def _seat_stats(buckets: list, threshold: int, total: int):
    """(expected_seats, P(at or above threshold)) from a priced ladder.

    buckets: [((lo, hi), price)] with None for an open end.

    Prices do not sum to one — a bid/ask midpoint on a dozen markets carries
    the spread on each — so everything is normalised by their sum rather than
    trusted as a distribution. An unclosed ladder would otherwise pull the
    expectation toward whichever end happens to be more liquid.

    Open-ended buckets need a representative value and there is no honest way
    to read one off the market: "above 249" is a claim about everything up to
    435. Rather than invent a tail, treat it as one more bucket of the same
    width as its neighbours. That understates the tail's reach on purpose —
    it keeps a thinly traded end bucket from dragging the mean — and the
    alternative, using the chamber maximum, would have "above 249" imply a
    342-seat average.
    """
    closed = [(lo, hi) for (lo, hi), _ in buckets if lo is not None and hi is not None]
    width = (sum(hi - lo + 1 for lo, hi in closed) / len(closed)) if closed else 1.0

    num = den = maj = 0.0
    for (lo, hi), p in buckets:
        if p is None or p <= 0:
            continue
        if lo is None and hi is None:
            continue
        if lo is None:                      # "below X"
            rep, blo, bhi = hi - (width - 1) / 2.0, hi - width + 1, hi
        elif hi is None:                    # "above X"
            rep, blo, bhi = lo + (width - 1) / 2.0, lo, lo + width - 1
        else:
            rep, blo, bhi = (lo + hi) / 2.0, lo, hi
        rep = max(0.0, min(float(total), rep))
        num += p * rep
        den += p
        # Mass at or above the threshold. A bucket that straddles it is split
        # in proportion rather than counted whole to one side — no bucket does
        # today, but "217-220" would, and silently rounding it either way
        # would move the majority probability by its entire price.
        span = bhi - blo + 1
        at_or_above = min(span, max(0, bhi - threshold + 1))
        maj += p * (at_or_above / span if span > 0 else 0.0)

    if den <= 0:
        return None, None
    return num / den, maj / den


# --------------------------------------------------------------------------
# The national House popular-vote margin ladder.
#
# KXHOUSEPOPVOTEMARGIN quotes the same shape as the seat ladders, but over vote
# share instead of seats: "Democrats, 8 to 10%", "Democrats, 16% and above",
# "Republicans win". It is the only market-implied NATIONAL MARGIN that exists
# anywhere, and it fills the one empty cell in the four-way comparison — until
# now the markets column had probabilities and seat counts and no vote share,
# because chamber-control markets do not imply one.
#
# WHY THIS NEEDS ITS OWN STATS FUNCTION. _seat_stats() counts discrete seats: a
# bucket "246-249" holds four outcomes and its span is hi - lo + 1. A margin is
# continuous, so "8 to 10%" spans 2 points and not 3, and using the seat
# arithmetic here would inflate every bucket's width by one point and drag the
# expectation toward whichever tail is wider.
#
# THE REPUBLICAN BUCKET IS COARSE, AND THAT IS WORTH STATING. Kalshi resolves
# the Democratic side in 2-point steps but collapses every Republican outcome
# into one "Republicans win" market. That bucket is open-ended over the whole
# R half of the line, so a value has to be assumed for it. Measured on the live
# book at 9% mass: the neighbour-width convention puts the answer at D+7.83,
# assuming R+3 gives D+7.65, and an implausible R+8 still only gives D+7.19.
# The whole sensitivity is under two thirds of a point and the plausible part
# of it under two tenths, so the same convention the seat ladders use is good
# enough — but it is an assumption and the methods page should say so.
# Polymarket resolves both tails and is the better instrument where both quote.
# --------------------------------------------------------------------------

_MARGIN_SERIES = {"KXHOUSEPOPVOTEMARGIN": NATIONAL_HOUSE}

_M_RANGE = re.compile(
    r"(democrat|republican)\w*,?\s*(\d+(?:\.\d+)?)\s*(?:to|[-–])\s*(\d+(?:\.\d+)?)\s*%",
    re.I)
_M_OPEN = re.compile(
    r"(democrat|republican)\w*,?\s*(\d+(?:\.\d+)?)\s*%?\s*(?:and above|or more|\+)",
    re.I)
_M_WIN = re.compile(r"(democrat|republican)\w*\s+win", re.I)


def _margin_bucket(label: str):
    """A bucket label -> (lo, hi) in points of DEMOCRATIC margin, signed.

    'Democrats, 8 to 10%'      -> (8.0, 10.0)
    'Democrats, 16% and above' -> (16.0, None)
    'Republicans, 2 to 4%'     -> (-4.0, -2.0)
    'Republicans win'          -> (None, 0.0)
    """
    if not label:
        return None
    if (m := _M_RANGE.search(label)):
        party, a, b = m.group(1).lower(), float(m.group(2)), float(m.group(3))
        lo, hi = min(a, b), max(a, b)
        return (lo, hi) if party.startswith("d") else (-hi, -lo)
    if (m := _M_OPEN.search(label)):
        party, a = m.group(1).lower(), float(m.group(2))
        return (a, None) if party.startswith("d") else (None, -a)
    if (m := _M_WIN.search(label)):
        # No threshold named: this side simply wins. One open half-line.
        return (0.0, None) if m.group(1).lower().startswith("d") else (None, 0.0)
    return None


def _margin_rows(markets: list, art, ctx) -> list:
    """One margin_D row for the chamber, from the priced margin ladder."""
    by_race: dict[str, list] = {}
    for m in markets:
        ticker = str(m.get("ticker", ""))
        series = _series_of(ticker)
        rid = _MARGIN_SERIES.get(series)
        if rid is None:
            continue
        # Same cycle guard the seat ladders use: these series carry more than
        # one Congress at a time and a 2028 bucket must not price a 2026 row.
        if not _CYCLE_EVENT.search(str(m.get("event_ticker") or ticker)):
            continue
        b = _margin_bucket(str(m.get("yes_sub_title")
                                or m.get("subtitle") or m.get("title") or ""))
        p = _price(m)
        if b is None or p is None:
            continue
        by_race.setdefault(rid, []).append((b, p))

    out = []
    for rid, buckets in by_race.items():
        if len(buckets) < 3:
            # Two priced buckets is not a distribution, it is a fragment, and
            # an expectation taken over it would be confidently wrong.
            continue
        exp = margin_ladder_expectation(buckets)
        if exp is None:
            continue
        out.append(ctx.row(art, race_id=rid, chamber="national", state="",
                           district="", quantity="margin_D",
                           value=round(exp, 4), unit="margin"))
    return out


def _seat_rows(markets: list, art, ctx) -> list:
    """One seats_D and one win_prob_D per chamber, from the priced ladder."""
    out = []
    for series, (rid, chamber, threshold) in _SEAT_SERIES.items():
        picked = []
        for m in markets:
            ticker = str(m.get("ticker", ""))
            if not ticker.startswith(series):
                continue
            if not _CYCLE_EVENT.search(str(m.get("event_ticker", "")) or ticker):
                continue          # a different Congress on the same ladder
            if str(m.get("status", "active")).lower() not in ("active", "open"):
                continue
            label = str(m.get("yes_sub_title") or m.get("subtitle") or "")
            b = _bucket(label)
            p = _price(m)
            if b is None or p is None:
                continue
            picked.append((b, p))
        if len(picked) < 3:
            # Two buckets is not a distribution. Better no number than a mean
            # of whichever half of the ladder happened to be quoted.
            continue
        total = 435 if chamber == "house" else 100
        exp, maj = _seat_stats(picked, threshold, total)
        if exp is None:
            continue
        out.append(ctx.row(art, race_id=rid, chamber="national", state="",
                           district="", quantity="seats_D",
                           value=round(exp, 2), unit="seats"))
        out.append(ctx.row(art, race_id=rid, chamber="national", state="",
                           district="", quantity="win_prob_D",
                           value=round(maj, 4), unit="prob"))
    return out


# Series we capture on purpose and do not parse, with the reason. Anything
# unclassified from one of these is expected and must not be reported as a
# parser failure — an error that fires every day for a permanent, understood
# condition is one nobody reads, and it would bury a real one.
_UNPARSED_SERIES = {
    "KXRHOUSESEATS":
        "the Republican-side mirror of the seat-count ladder. Captured because "
        "bytes are cheap and a series we already hold history for is one we can "
        "backfill; not parsed because the D ladder already gives us the whole "
        "distribution and reading both would double-count it.",
    "KXRSENATESEATS":
        "as KXRHOUSESEATS, for the Senate.",
    "KXGENERICBALLOTVOTEHUB":
        "weekly threshold ladder on what VoteHub's generic-ballot AVERAGE will "
        "read on a given date. A market about a poll aggregator's number, not "
        "about the election, so it is not a forecast of any race. Captured "
        "because it is a market-implied nowcast of the same quantity our "
        "polling category tracks, and worth having history for if we ever "
        "decide what to do with it.",
}


def _series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0]


def _expected_to_parse(series: str, ctx: Context) -> bool:
    """Should this series produce rows today?

    THE POSITIVE EXPECTATION, AND WHY IT REPLACED A NEGATIVE ONE.

    The old test was "did anything at all classify?" — if a run ended with
    markets seen and no rows, it raised. That reads as a strong check and is in
    fact a very weak one, because it is a claim about the WHOLE capture rather
    than about any series we care about. The first real archive replay showed
    how it fails: the 2026-08-19 capture predates the allowlist tightening, so
    it holds 137 series of Kalshi's Politics category — impeachment calls,
    congressional pay rises, tariff votes, Epstein files, foreign elections, a
    Super Bowl market — 89 tickers of which can never classify to a 2026 race
    because they are not about one. Nothing was wrong and nothing could be
    fixed, and the parser raised on that day, and would have raised on it every
    single run from then to November.

    An error that fires forever on a permanent, understood condition is one
    nobody reads, and it buries the real one. So the question is asked about
    each series instead, and asked the other way round: this series is one we
    said we wanted (it matches the registry's series_include) and is not on the
    knowingly-unparsed list, therefore it OWES us rows. A series outside the
    allowlist owes us nothing — it is either historical noise or something a
    future capture will stop collecting, and either way its silence is data
    about Kalshi rather than a fault in this file.

    Deriving the expectation from series_include rather than a list in here
    means tightening the registry tightens the check, with nothing to keep in
    sync by hand.
    """
    if series in _UNPARSED_SERIES:
        return False
    pat = (ctx.source.get("config") or {}).get("series_include")
    if not pat:
        # No allowlist configured: we asked for everything, so we cannot claim
        # any particular series was promised to us.
        return False
    try:
        return bool(re.match(pat, series))
    except re.error:
        return False


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    market_arts = [a for n, a in artifacts.items() if n.startswith("markets-")]
    if not market_arts:
        # Discovery ran but matched nothing, or capture failed. Either way a
        # human should look — silently returning [] would read as "quiet week".
        raise ValueError(
            "no markets-* artifacts found; check the series_pattern in the registry "
            "against the stored series-page-*.json")

    rows: list[Row] = []
    unmatched: list[str] = []
    empty_series: list[str] = []
    unparsed_seen: list[str] = []   # captured on purpose, read by nothing
    rows_by_series: dict[str, int] = {}   # series -> rows it produced
    priced = 0                     # markets that had a usable price
    seen_markets = 0               # markets present, priced or not
    for art in market_arts:
        series = art.name.replace("markets-", "")
        rows_by_series.setdefault(series, 0)
        payload = art.json()
        markets = payload.get("markets", []) or []
        if not markets:
            # Verified 2026-08-19: HOUSE, SENATE, HOUSEMOV, SENATEMOV and KXHOUSE
            # all return {"cursor":"","markets":[]}. The series exist as shells;
            # Kalshi has not opened 2026 chamber-control markets under them. That
            # is a fact about Kalshi, not a parser bug, and the empty response is
            # itself worth archiving — the day they open, the archive shows it.
            empty_series.append(series)
            continue

        # KNOWINGLY-UNPARSED SERIES ARE SKIPPED HERE, BEFORE ANYTHING ELSE.
        #
        # Declaring a series unparsed used to mean only that no ladder reader
        # claimed it — and then its markets fell straight through to the
        # per-market classifier below, which is worse than parsing it wrong on
        # purpose. KXRHOUSESEATS is the case that proved it. Every bucket of
        # the Republican seat ladder carries "HOUSE" in its ticker and "party"
        # in its title, so _CTRL_H read each one as a chamber-CONTROL market;
        # "Republican" contains no standalone REP or R token, so the side test
        # called every one of them Democratic. The result was eleven bucket
        # prices — 0.015, 0.0255, 0.0465, 0.084 and so on — filed as
        # P(Democratic House) beside the one real value of 0.797, dragging
        # Kalshi's contribution to about 0.15 and the whole markets category
        # from 0.88 down to 0.61 while Polymarket and PredictIt both said 0.81
        # or better.
        #
        # Nothing complained, because each row was individually well-formed.
        # A ladder bucket is a probability in [0,1] about the House, which is
        # exactly what the validator checks for.
        #
        # Skipped before `seen_markets` too, so these markets are invisible to
        # the price-field accounting as well: they are not evidence that prices
        # parse, and they must not be evidence that prices are broken either.
        if series in _UNPARSED_SERIES:
            unparsed_seen.append(series)
            continue

        seen_markets += len(markets)
        # Ladders first: both kinds are read as a distribution ACROSS markets,
        # not one market at a time, so neither can go through the per-market
        # loop below.
        got = _seat_rows(markets, art, ctx) or _margin_rows(markets, art, ctx)
        if got:
            rows.extend(got)
            rows_by_series[series] += len(got)
            priced += len(markets)
            continue
        for m in markets:
            ticker = str(m.get("ticker", ""))
            title = str(m.get("title") or m.get("subtitle") or "")
            p = _price(m)
            if p is None:
                continue
            priced += 1
            hit = _classify(ticker, title)
            if hit is None:
                unmatched.append(ticker)
                continue
            rid, chamber, state, district = hit
            # Kalshi markets resolve YES on a stated outcome; whether YES means
            # "Republican" depends on the market. Encode what we can defend:
            # the raw probability, tagged by the side the ticker names.
            side = "R" if re.search(r"\b(REP|GOP|R)\b", f"{ticker} {title}", re.I) else "D"
            rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                                district=district,
                                quantity=f"win_prob_{side}", value=round(p, 4),
                                unit="prob"))
            rows_by_series[series] += 1
    if seen_markets and priced == 0:
        # MARKETS EXIST AND NOT ONE OF THEM HAD A READABLE PRICE. That is a
        # parser fault, not a fact about Kalshi, and it must be loud.
        #
        # This branch used to return [] on the same condition, on the reasoning
        # that "nothing priced" meant Kalshi had nothing open. The two are not
        # the same thing and telling them apart is the whole point: when the
        # price fields were renamed with a `_dollars` suffix, every market went
        # unpriced, this returned [], and Kalshi contributed nothing to the
        # archive for as long as it was registered without a single complaint.
        # An empty series list is the honest "nothing open" signal; markets
        # with unreadable prices is a bug.
        sample = next((m for a in market_arts
                       for m in (a.json().get("markets") or [])), {})
        raise ValueError(
            f"{seen_markets} Kalshi markets carried no readable price. The "
            f"price field names have probably changed again. Fields on the "
            f"first market: "
            f"{sorted(k for k in sample if 'price' in k or 'bid' in k or 'ask' in k)}")

    if not market_arts or len(empty_series) == len(market_arts):
        # Every captured series was a shell. Nothing to parse — but also nothing
        # collected, on a day we asked for a dozen series, so a human should
        # look at whether the tickers were renamed.
        raise ValueError(
            f"all {len(empty_series)} captured Kalshi series are empty — no "
            f"open 2026 markets under: {sorted(empty_series)[:8]}. "
            f"Re-check series_include in the registry; the tickers may have "
            f"been renamed, or the markets may genuinely not be open yet.")

    # THE CHECK: every series the registry asked for, that carried markets, and
    # that we did not declare unparsed, must have produced at least one row.
    # See _expected_to_parse for why this is asked per series rather than of the
    # capture as a whole.
    starved = sorted(s for s, n in rows_by_series.items()
                     if n == 0 and s not in empty_series
                     and _expected_to_parse(s, ctx))
    if starved:
        sample = [t for t in unmatched if _series_of(t) in set(starved)][:5]
        raise ValueError(
            f"{len(starved)} allowlisted Kalshi series carried markets but "
            f"produced no rows: {starved[:6]}. These are series the registry "
            f"asks for and this parser claims to read, so a classifier or a "
            f"price field has probably changed. Unmatched tickers from them: "
            f"{sample}")

    # Unclassified tickers from series outside the allowlist are historical
    # noise: captures taken before series_include was tightened hold Kalshi's
    # whole Politics category. Say so once, quietly, and carry on — this is
    # information about what is in the archive, not a fault.
    noise = sorted({_series_of(t) for t in unmatched
                    if _series_of(t) not in _UNPARSED_SERIES
                    and not _expected_to_parse(_series_of(t), ctx)})
    if noise:
        n = sum(1 for t in unmatched if _series_of(t) in set(noise))
        print(f"      kalshi: {n} ticker(s) from {len(noise)} non-allowlisted "
              f"series ignored (capture predates the allowlist): "
              f"{noise[:6]}{' …' if len(noise) > 6 else ''}")
    return rows
