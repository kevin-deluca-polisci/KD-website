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
               race_id, state_from_text)

# A market this cycle. PredictIt lists 2028 and other years in the same
# document, and "the Senate" without a year would have swept them in.
_CYCLE = re.compile(r"\b2026\b")
_SENATE = re.compile(r"\bsenate\b", re.I)
_HOUSE = re.compile(r"\bhouse\b", re.I)
_GOV = re.compile(r"\bgovernor|gubernatorial\b", re.I)
_CONTROL = re.compile(r"control|win the|majority|which party", re.I)

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
            for c in m.get("contracts") or []:
                if not isinstance(c, dict):
                    continue
                label = str(c.get("name") or c.get("shortName") or "")
                p = _price(c)
                if p is None:
                    continue
                if _DEM.search(label):
                    found.setdefault("D", p)
                elif _REP.search(label):
                    found.setdefault("R", p)
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
