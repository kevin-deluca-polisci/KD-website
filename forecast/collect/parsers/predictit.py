"""
PredictIt — real-money prediction market. Publication: individual.

The public market-data endpoint returns every open market in one JSON
document, so this is one artifact per day and no pagination to get wrong:

    {"markets": [{"id": ..., "name": "Which party will control the Senate
                  after the 2026 election?", "shortName": ..., "status": "Open",
                  "contracts": [{"name": "Republican",
                                 "lastTradePrice": 0.62, ...}, ...]}, ...]}

WHY THIS SOURCE IS WORTH HAVING. It is a real-money market like Kalshi and
Polymarket, but with a position cap of $850 per contract and a long academic
record, which makes it the market economists have actually studied. It also
lists individual Senate races, which as of this writing is the only per-race
market price in the archive — the other two exchanges carry chamber control
and governors but not Senate seats.

THE PRICE IS NOT THE PROBABILITY, QUITE. `lastTradePrice` is the last trade,
which on a thin market may be hours old and on either side of a wide spread.
Nothing here smooths that; the site labels the whole category as market prices
and the methods page explains what a price is and is not.
"""
from __future__ import annotations

import re

from . import (Context, LoadedArtifact, NATIONAL_HOUSE, NATIONAL_SENATE, Row,
               house_seats_from_r_ladder, independent_is_d_side,
               margin_ladder_expectation, race_id,
               seat_bucket, state_from_text)

# A market this cycle. PredictIt lists 2028 and other years in the same
# document, and "the Senate" without a year would have swept them in.
_CYCLE = re.compile(r"\b2026\b")
_SENATE = re.compile(r"\bsenate\b", re.I)
_HOUSE = re.compile(r"\bhouse\b", re.I)
_GOV = re.compile(r"\bgovernor|gubernatorial\b", re.I)
_CONTROL = re.compile(r"control|win the|majority|which party", re.I)
# "What will be the national vote margin in the 2026 House midterms?" The
# same ladder Kalshi and Polymarket quote ("Democrats 9-12%", "Democrats ≥15%",
# "Republicans >3%"), read with the same margin_ladder_expectation.
_PV_MARGIN = re.compile(r"national\s+vote\s+margin|popular\s+vote\s+margin", re.I)
_PV_BUCKET = re.compile(
    r"^\s*(democrats?|republicans?)\s*(?:(\u2265|>=|>)\s*(\d+(?:\.\d+)?)"
    r"|(\d+(?:\.\d+)?)\s*[-\u2013]\s*(\d+(?:\.\d+)?))\s*%?\s*$", re.I)


def _pv_bucket(label: str):
    """'Democrats 9-12%' -> (9, 12); 'Democrats ≥15%' -> (15, None);
    'Republicans 0-3%' -> (-3, 0); 'Republicans >3%' -> (None, -3)."""
    m = _PV_BUCKET.match(label or "")
    if not m:
        return None
    dem = m.group(1).lower().startswith("d")
    if m.group(3) is not None:
        a = float(m.group(3))
        return (a, None) if dem else (None, -a)
    lo, hi = sorted((float(m.group(4)), float(m.group(5))))
    return (lo, hi) if dem else (-hi, -lo)


_R_HOUSE_SEATS = re.compile(r"house\s+seats\s+will\s+(?:the\s+)?(?:republicans|gop)", re.I)

_DEM = re.compile(r"\bdemocrat", re.I)
_REP = re.compile(r"\brepublican|\bGOP\b", re.I)


def _price(c: dict) -> float | None:
    for k in ("lastTradePrice", "lastClosePrice"):
        v = c.get(k)
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 <= f <= 1.0:
            return f
    return None


# PredictIt names its two sides of the book explicitly, and they are the two
# numbers a bettor actually faces: `bestBuyYesCost` is what one YES contract
# costs to buy, `bestSellYesCost` what one sells for. That pair IS the ask and
# the bid. `lastTradePrice` sits somewhere between them and may be hours old,
# which is fine for "what does the market think" and useless for "what would
# this have cost". See parsers/__init__.py on why both are kept.
#
# Note for the portfolio work: PredictIt caps a position at $850 per contract
# and takes 5% of profits plus 5% on withdrawal. The cap is the depth figure
# for this exchange — there is no order book to record — and the fees belong in
# the evaluation rather than here.
_BOOK = (("bestBuyYesCost", "price_ask"), ("bestSellYesCost", "price_bid"))


def _book_rows(c: dict, side: str, rid: str, chamber: str, state: str,
               art, ctx) -> list:
    out = []
    for field, quantity in _BOOK:
        v = c.get(field)
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 <= f <= 1.0:
            out.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                               district="", quantity=f"{quantity}_{side}",
                               value=round(f, 6), unit="prob"))
    return out


def _target(name: str) -> tuple[str, str, str] | None:
    """Market name -> (race_id, chamber, state) or None."""
    if not _CYCLE.search(name):
        return None

    # A state name in the title makes it a per-race market; without one it is
    # a chamber-control market. Order matters: "the 2026 U.S. Senate election
    # in Georgia" matches both the chamber test and the state test, and it is
    # the state that decides which it is.
    st = state_from_text(name)
    if _SENATE.search(name):
        if st:
            return race_id("senate", st), "senate", st
        if _CONTROL.search(name):
            return NATIONAL_SENATE, "national", ""
        return None
    if _HOUSE.search(name):
        if _CONTROL.search(name) and not st:
            return NATIONAL_HOUSE, "national", ""
        # District markets would need a district number; PredictIt names them
        # in prose ("the 2026 election in California's 22nd"), and guessing is
        # how a chamber-control price ends up filed as a district.
        return None
    if _GOV.search(name) and st:
        return race_id("governor", st), "governor", st
    return None


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    if not artifacts:
        raise ValueError("no PredictIt artifacts stored for this date")
    rows: list[Row] = []
    seen_markets = 0
    matched = 0

    for art in artifacts.values():
        payload = art.json()
        markets = payload.get("markets") if isinstance(payload, dict) else payload
        if not isinstance(markets, list):
            continue
        for m in markets:
            if not isinstance(m, dict):
                continue
            seen_markets += 1
            name = str(m.get("name") or m.get("shortName") or "")
            # "How many House seats will Republicans win in the 2026 midterm
            # election?" A seat ladder over R seats, read as one distribution
            # (see house_seats_from_r_ladder in parsers/__init__.py).
            if _CYCLE.search(name) and _R_HOUSE_SEATS.search(name):
                buckets = [(seat_bucket(str(c.get("name") or "")), _price(c))
                           for c in m.get("contracts") or [] if isinstance(c, dict)]
                exp = house_seats_from_r_ladder(buckets)
                if exp is not None:
                    matched += 1
                    rows.append(ctx.row(art, race_id=NATIONAL_HOUSE,
                                        chamber="national", state="",
                                        district="", quantity="seats_D",
                                        value=round(exp, 2), unit="seats"))
                continue
            if _CYCLE.search(name) and _PV_MARGIN.search(name):
                buckets = [(_pv_bucket(str(c.get("name") or "")), _price(c))
                           for c in m.get("contracts") or [] if isinstance(c, dict)]
                buckets = [(b, p) for b, p in buckets if b is not None and p is not None]
                mass = sum(p for _, p in buckets)
                if len(buckets) >= 3 and 0.70 <= mass <= 1.60:
                    exp = margin_ladder_expectation(buckets)
                    if exp is not None:
                        matched += 1
                        rows.append(ctx.row(art, race_id=NATIONAL_HOUSE,
                                            chamber="national", state="",
                                            district="", quantity="margin_D",
                                            value=round(exp, 4), unit="pct"))
                continue
            got = _target(name)
            if got is None:
                continue
            rid, chamber, state = got
            matched += 1

            # Record every party contract the market carries, rather than the
            # first one found. A two-outcome market stored one side and
            # dropped the other in the Polymarket parser, and which side
            # survived depended on listing order; same trap, same fix.
            found: dict[str, float] = {}
            books: dict[str, dict] = {}
            # AN INDEPENDENT STANDING IN FOR THE NOMINEE IS THE D SIDE. In
            # Nebraska PredictIt lists Republican 0.70, Independent 0.29 and
            # Democratic 0.01; reading "Democratic" put Osborn's race at 1%.
            # Same rule as the Kalshi and Polymarket parsers.
            ind_d = independent_is_d_side(rid)
            for c in m.get("contracts") or []:
                if not isinstance(c, dict):
                    continue
                label = str(c.get("name") or c.get("shortName") or "")
                p = _price(c)
                if p is None:
                    continue
                if ind_d and re.search(r"\bindependent\b", label, re.I):
                    found.setdefault("D", p)
                    books.setdefault("D", c)
                elif ind_d and _DEM.search(label):
                    continue                  # a token Democrat, not the D side
                elif _DEM.search(label):
                    found.setdefault("D", p)
                    books.setdefault("D", c)
                elif _REP.search(label):
                    found.setdefault("R", p)
                    books.setdefault("R", c)
            for side, c in books.items():
                rows.extend(_book_rows(c, side, rid, chamber, state, art, ctx))
            for side, p in found.items():
                rows.append(ctx.row(art, race_id=rid, chamber=chamber,
                                    state=state, district="",
                                    quantity=f"win_prob_{side}",
                                    value=round(p, 4), unit="prob"))

    if not rows:
        raise ValueError(
            f"read {seen_markets} PredictIt markets, matched {matched} as 2026 "
            f"congressional or gubernatorial, and extracted no prices — either "
            f"the market names changed or the contract schema did")
    return rows
