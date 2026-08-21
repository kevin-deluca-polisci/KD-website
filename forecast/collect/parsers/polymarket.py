"""
Polymarket — prediction market. Publication: individual (public order book).

Gamma returns events, each holding markets whose `outcomePrices` is a
JSON-encoded STRING array. That double encoding is the usual trip hazard.

THE SHAPE THAT MATTERS. A Polymarket "event" is a question; its "markets" are
the individual contracts under it. For chamber control the event is
"Which party will win the House in 2026?" and each market is a Yes/No on one
party, with the party in `groupItemTitle`:

    q="Will the Democratic Party control the House..."  groupItemTitle="Democratic Party"
    outcomes=["Yes","No"]  outcomePrices=["0.875","0.125"]

so P(D controls the House) is the price of YES on the Democratic sub-market,
not the first price in some other market's list.

TWO BUGS THIS FILE HAS HAD, both worth keeping in view because they failed
silently and produced numbers that looked reasonable:

1. NATIONAL BRANCH FIRST. The chamber-control test ran before the per-race
   tests and matched against the event title concatenated with the market
   question. Every per-district market inside a House-themed event therefore
   matched "house" + "win the" and was filed as NATIONAL_HOUSE. It went
   unnoticed while the capture only pulled the handful of headline events;
   the day paging was added and the capture started returning district and
   state markets too, 80 unrelated contracts landed on NATL_HOUSE_2026 in one
   run. The category average became the mean of eighty different questions,
   and the methods page — which takes the last row per source — showed
   whichever contract happened to sort last. It showed 0%.

   Specific beats general now: district, then governor, then state Senate,
   and only a market with no identifiable state or district can be chamber
   control.

2. PARTY BY ELIMINATION. When no outcome was labelled with a party the old
   code guessed: R if the text said "republican", otherwise D. Polymarket's
   multi-party control events carry placeholder sub-markets — "Will Party A
   control the House?", "Will another party control the House?" — and every
   one of them was recorded as a Democratic probability. A market whose party
   cannot be named is now skipped rather than assumed.
"""
from __future__ import annotations
import json, re
from . import (Context, LoadedArtifact, NATIONAL_HOUSE, NATIONAL_SENATE, Row,
               is_state, race_id, state_from_text)

# NO re.I on the state group. Under IGNORECASE "[A-Z]{2}" matches any two
# letters, which is how "Balance of power in the Senate" once filed every
# Senate market under SEN_OF_2026. is_state() is the second line of defence,
# not the first.
_DISTRICT = re.compile(r"\b([A-Z]{2})[-\s]?(\d{1,2})\b")
_HOUSE = re.compile(r"\bhouse\b|\bcongress", re.I)
_SENATE = re.compile(r"\bsenate\b", re.I)
_GOV = re.compile(r"\bgovernor|\bgubernatorial\b", re.I)
_CONTROL = re.compile(r"control|majority|win the|which party", re.I)
_DEM = re.compile(r"\bdemocrat", re.I)
_REP = re.compile(r"\brepublic|\bGOP\b", re.I)
_YES = re.compile(r"^\s*yes\s*$", re.I)
# Candidate-winner markets carry the party as a trailing tag rather than a
# word: "David Jolly (D)", "Byron Donalds (R) ". Without this the parser sees
# no party at all and drops a real market; WITH the old guess-by-elimination
# it saw no "republican" and filed Byron Donalds as a Democrat.
_PARTY_TAG = re.compile(r"\(\s*([DR])\s*\)")


def _prices(m: dict) -> list[float]:
    raw = m.get("outcomePrices")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for v in raw:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out


def _outcomes(m: dict) -> list[str]:
    raw = m.get("outcomes")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _sides(m: dict, q: str, outs: list[str], prices: list[float]) -> dict:
    """{"D": p, "R": p} for this market, or {} if it names no party.

    Two market shapes. A multi-outcome market labels its outcomes with the
    parties, and each price belongs to the outcome beside it. A Yes/No market
    names one party in `groupItemTitle` (or in its own question) and the
    probability is the price of YES — the price of NO is the complement and
    recording it as the other party's chance would be wrong the moment a
    third party is on the ballot.
    """
    found: dict[str, float] = {}
    for i, o in enumerate(outs):
        if i >= len(prices):
            break
        if _REP.search(o):
            found.setdefault("R", prices[i])
        elif _DEM.search(o):
            found.setdefault("D", prices[i])
    if found:
        return found

    # Yes/No. Whose Yes is it?
    label = str(m.get("groupItemTitle") or "") or q
    tag = _PARTY_TAG.search(label)
    side = (tag.group(1).upper() if tag else
            "D" if _DEM.search(label) else "R" if _REP.search(label) else None)
    if side is None:
        # "Party A", "Party B", "another party" — placeholders on a
        # multi-party control event. Not a forecast about anyone.
        return {}
    for i, o in enumerate(outs):
        if _YES.match(o) and i < len(prices):
            return {side: prices[i]}
    return {side: prices[0]} if prices else {}


def _target(blob: str, q: str):
    """(race_id, chamber, state, district) or None.

    Most specific first. A market that names a district is about that
    district even when it sits inside an event titled "Which party will win
    the House"; only a market naming no place at all can be chamber control.
    """
    if _HOUSE.search(blob):
        mm = _DISTRICT.search(q) or _DISTRICT.search(blob)
        if mm and is_state(mm.group(1)):
            st, d = mm.group(1).upper(), mm.group(2)
            return race_id("house", st, d), "house", st, f"{int(d):02d}"

    st = state_from_text(blob)
    if st and _GOV.search(blob):
        return race_id("governor", st), "governor", st, ""
    if st and _SENATE.search(blob):
        return race_id("senate", st), "senate", st, ""

    # Chamber control, and only with nothing more specific in the text.
    if st:
        return None
    if _HOUSE.search(blob) and _CONTROL.search(blob):
        return NATIONAL_HOUSE, "national", "", ""
    if _SENATE.search(blob) and _CONTROL.search(blob):
        return NATIONAL_SENATE, "national", "", ""
    return None


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    if not artifacts:
        raise ValueError("no Polymarket artifacts stored for this date")
    rows: list[Row] = []
    seen_events = 0
    for art in artifacts.values():
        payload = art.json()
        events = payload if isinstance(payload, list) else payload.get("data", [payload])
        for ev in events:
            if not isinstance(ev, dict):
                continue
            seen_events += 1
            title = str(ev.get("title") or ev.get("question") or "")
            # Accumulate per EVENT, then emit. A candidate-winner event lists
            # one market per candidate, so a party's chance is the SUM over
            # its candidates, not any one of them and not their mean — two
            # Democrats at 0.3 and 0.3 make the party a 0.6 favourite, while
            # averaging would report 0.3 and averaging is what aggregate.py
            # would otherwise have done to two rows carrying the same key.
            # Kept inside the artifact loop: the same event appears in several
            # pages of a paged capture, and summing across pages would
            # multiply every probability by the page count.
            agg: dict[tuple, dict[str, float]] = {}
            for m in ev.get("markets", []) or []:
                q = str(m.get("question") or m.get("groupItemTitle") or title)
                prices, outs = _prices(m), _outcomes(m)
                if not prices:
                    continue
                found = _sides(m, q, outs, prices)
                if not found:
                    continue
                got = _target(f"{title} {q}", q)
                if got is None:
                    continue
                bucket = agg.setdefault(got, {})
                for side, price in found.items():
                    if 0.0 <= price <= 1.0:
                        bucket[side] = round(bucket.get(side, 0.0) + price, 6)
            for (rid, chamber, state, district), sides in agg.items():
                for side, price in sides.items():
                    # A sum over candidates can drift past 1 on a market whose
                    # prices do not quite close. Record it as certainty rather
                    # than as an out-of-range value the validator would reject.
                    price = min(price, 1.0)
                    rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                                        district=district, quantity=f"win_prob_{side}",
                                        value=round(price, 4), unit="prob"))
    if not rows:
        raise ValueError(f"parsed 0 rows from {seen_events} Polymarket events")
    return rows
