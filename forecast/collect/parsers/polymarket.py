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
               is_state, margin_ladder_expectation, race_id, state_from_text)

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


# --------------------------------------------------------------------------
# Microstructure: what the forecast would have cost to act on.
#
# `outcomePrices` is a midpoint and is the right number for "what does the
# market think". It is the wrong number for "what would this have cost you",
# because you buy at the ask and sell at the bid. Gamma publishes both sides
# and the depth beside them, so this records them rather than throwing the
# spread away and reconstructing it later from nothing.
#
# Emitted only for a Yes/No market whose YES is one party's win: on a
# multi-outcome market the book belongs to an outcome rather than to a party,
# and mapping one to the other would invent a quote nobody offered.
# --------------------------------------------------------------------------
_MICRO = (("bestBid", "price_bid", "prob"),
          ("bestAsk", "price_ask", "prob"),
          ("volumeNum", "market_volume", "count"),
          ("volume", "market_volume", "count"),
          ("liquidityNum", "market_liquidity", "count"),
          ("liquidity", "market_liquidity", "count"))
# Every one of them is a fact about ONE book, so every one takes the side.


def _micro_rows(m: dict, side: str, rid: str, chamber: str, state: str,
                district: str, art, ctx) -> list:
    out, seen = [], set()
    for field, quantity, unit in _MICRO:
        if quantity in seen:
            continue                      # first spelling wins; Gamma has two
        v = m.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        v = float(v)
        if unit == "prob":
            if not (0.0 <= v <= 1.0):
                continue
        elif v < 0:
            continue
        name = f"{quantity}_{side}"
        seen.add(quantity)
        out.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                           district=district, quantity=name,
                           value=round(v, 6), unit=unit))
    return out


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


# --------------------------------------------------------------------------
# The national House popular-vote margin ladder.
#
# Polymarket's "2026 Midterms: House Popular Vote Margin of Victory" quotes
# thirteen Yes/No markets covering the line in 2-point steps, and unlike
# Kalshi's version it resolves BOTH tails — Republicans 0-2, 2-4, 4-6 and 6%+
# rather than one undifferentiated "Republicans win". That makes it the better
# instrument for an expectation, and it is why both are collected: they answer
# the same question with different resolution, and the gap between them is
# itself worth watching.
#
# Each market is a Yes/No on one bucket, so the bucket is in the QUESTION
# rather than in an outcome label, and the probability is the price of Yes.
# The event as a whole is the distribution.
#
# This is the only market-implied national vote share that exists anywhere, so
# it fills the empty Markets cell on the margin comparison. It emits a margin
# and NOTHING else: the mass above zero here is P(D wins the popular vote),
# which is a different event from winning the chamber, and win_prob_D on this
# race already means the chamber.
# --------------------------------------------------------------------------

_PV_EVENT = re.compile(r"popular\s+vote\s+margin|margin\s+of\s+victory", re.I)

# "2026 Balance of Power: R Senate, R House" is a contract on BOTH chambers at
# once. Its price is not either chamber's probability and must never be filed
# as one.
_JOINT_CONTROL = re.compile(
    r"balance\s+of\s+power.*\b[RD]\s+senate\b.*\b[RD]\s+house\b"
    r"|\b[RD]\s+senate\s*,\s*[RD]\s+house\b", re.I)
# --------------------------------------------------------------------------
# THE JOINT EVENT IS NOT USELESS — IT IS A JOINT DISTRIBUTION
# --------------------------------------------------------------------------
# "Balance of Power: 2026 Midterms" lists one contract per (Senate, House)
# combination: Democrats Sweep, D Senate R House, R Senate D House,
# Republicans Sweep, Other. The rule above — never file one of those legs as a
# chamber probability — is right and stays. But the legs are mutually
# exclusive and jointly exhaustive, so a CHAMBER probability is a marginal of
# the joint, and marginalising is a sum, not a substitution:
#
#     P(D House)  = P(Democrats Sweep) + P(R Senate, D House)
#     P(D Senate) = P(Democrats Sweep) + P(D Senate, R House)
#
# WHY THIS IS WORTH THE CODE. Polymarket's dedicated chamber-control markets
# are captured from 2026-02-20. This event is captured from 2025-07-19 and
# priced coherently the whole way — its five legs summed to 1.009 on the first
# day. Reading it takes the Polymarket line back seven months and gives the
# market family a second contributor through late 2025, where it currently
# rests on a single thin Kalshi ladder.
#
#     2025-07-19   0.185 + 0.525 = 0.710
#     2025-11-01   0.250 + 0.405 = 0.655
#     2026-02-20   0.395 + 0.435 = 0.830
#
# THE SAME MASS TEST THE KALSHI LADDER USES, for the same reason: a marginal
# computed from a fragment of a joint distribution is not a marginal. If the
# legs do not sum to roughly one, the event is incomplete and we emit nothing.
_JOINT_EVENT = re.compile(r"balance\s+of\s+power", re.I)
_JOINT_MASS_MIN, _JOINT_MASS_MAX = 0.90, 1.15

#                       leg label              -> (senate, house)
_JOINT_LEGS = (
    (re.compile(r"democrat\w*\s+sweep", re.I),                    ("D", "D")),
    (re.compile(r"republican\w*\s+sweep", re.I),                  ("R", "R")),
    (re.compile(r"\bD\s+senate\b.*\bR\s+house\b", re.I),          ("D", "R")),
    (re.compile(r"\bR\s+senate\b.*\bD\s+house\b", re.I),          ("R", "D")),
)


def _joint_rows(ev: dict, title: str, art, ctx) -> list:
    """P(D House) and P(D Senate) marginalised out of the joint event."""
    mass = 0.0
    marg = {"house": 0.0, "senate": 0.0}
    seen = 0
    for m in ev.get("markets", []) or []:
        label = str(m.get("groupItemTitle") or m.get("question") or "")
        prices = _prices(m)
        if not prices:
            continue
        yes = prices[0]
        mass += yes                      # "Other" counts toward mass, no marginal
        for pat, (sen, hou) in _JOINT_LEGS:
            if pat.search(label):
                seen += 1
                if sen == "D":
                    marg["senate"] += yes
                if hou == "D":
                    marg["house"] += yes
                break
    if seen < 4 or not (_JOINT_MASS_MIN <= mass <= _JOINT_MASS_MAX):
        return []                        # incomplete joint — no marginal from it
    out = []
    for chamber, rid in (("house", NATIONAL_HOUSE), ("senate", NATIONAL_SENATE)):
        out.append(ctx.row(art, race_id=rid, chamber="national", state="",
                           district="", quantity="win_prob_D",
                           value=round(marg[chamber] / mass, 4), unit="prob"))
    return out


_PV_BETWEEN = re.compile(
    r"\b(democratic|republican)\b.{0,120}?between\s+(\d+(?:\.\d+)?)\s*%?\s*and\s+"
    r"(\d+(?:\.\d+)?)\s*%", re.I | re.S)
_PV_ORMORE = re.compile(
    r"\b(democratic|republican)\b.{0,120}?by\s+(\d+(?:\.\d+)?)\s*%\s*or\s+more",
    re.I | re.S)


def _pv_bucket(q: str):
    """A popular-vote question -> (lo, hi) in points of DEMOCRATIC margin.

    Signed, so a Republican bucket comes back negative and the whole ladder
    lives on one number line. Matching on the question text rather than on a
    group label because these are Yes/No markets whose bucket is only stated
    in the question.
    """
    if (m := _PV_BETWEEN.search(q)):
        party, a, b = m.group(1).lower(), float(m.group(2)), float(m.group(3))
        lo, hi = min(a, b), max(a, b)
        return (lo, hi) if party.startswith("d") else (-hi, -lo)
    if (m := _PV_ORMORE.search(q)):
        party, a = m.group(1).lower(), float(m.group(2))
        return (a, None) if party.startswith("d") else (None, -a)
    return None


def _pv_rows(ev: dict, title: str, art, ctx) -> list:
    """One margin_D row, if this event is the popular-vote margin ladder."""
    if not _PV_EVENT.search(title):
        return []
    buckets = []
    for m in ev.get("markets", []) or []:
        q = str(m.get("question") or m.get("groupItemTitle") or "")
        b = _pv_bucket(q)
        if b is None:
            continue
        prices, outs = _prices(m), _outcomes(m)
        if not prices:
            continue
        yes = next((prices[i] for i, o in enumerate(outs)
                    if _YES.match(o) and i < len(prices)), prices[0])
        if yes is None or not (0.0 <= yes <= 1.0):
            continue
        buckets.append((b, yes))
    if len(buckets) < 3:
        # Two priced buckets is a fragment, not a distribution.
        return []
    exp = margin_ladder_expectation(buckets)
    if exp is None:
        return []
    return [ctx.row(art, race_id=NATIONAL_HOUSE, chamber="national", state="",
                    district="", quantity="margin_D", value=round(exp, 4),
                    unit="pct")]


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    if not artifacts:
        raise ValueError("no Polymarket artifacts stored for this date")
    rows: list[Row] = []
    joint: dict[str, Row] = {}      # race_id -> marginal from the joint event
    seen_events = 0
    pv_only = True          # every event so far was one we recognise and skip
    for art in artifacts.values():
        payload = art.json()
        events = payload if isinstance(payload, list) else payload.get("data", [payload])
        for ev in events:
            if not isinstance(ev, dict):
                continue
            seen_events += 1
            title = str(ev.get("title") or ev.get("question") or "")
            # The margin ladder is read across markets, so it cannot go through
            # the per-market loop below. Handled first and the event is done.
            # THE GATE IS THE TITLE, NOT WHETHER THE HANDLER SUCCEEDED.
            #
            # This read `if (pv := _pv_rows(...)):`, which skips the generic
            # path only when a margin row could actually be built. When the
            # ladder is too thin to have an expectation — Polymarket sometimes
            # lists two buckets and no more — _pv_rows returns nothing, the
            # `continue` never fires, and the event's sub-markets fall through
            # to the chamber-control reader.
            #
            # On 2026-04-08 and 04-10 that put "Will the Democratic Party win
            # the popular vote ... by 8 to 10 points" on the board as
            # win_prob_D for NATL_HOUSE at 0.19, against Kalshi's 0.93 for the
            # actual chamber, and the market line on the tracker dropped from
            # 93% to 55% for a day. The comment above _pv_rows already said
            # win_prob_D on this event would be wrong; the code just did not
            # enforce it when the ladder was short.
            if _PV_EVENT.search(title):
                rows.extend(_pv_rows(ev, title, art, ctx) or [])
                continue
            # MARGINALISE THE JOINT EVENT BEFORE SKIPPING IT. Same reasoning
            # as the popular-vote gate above: recognise the event by its title
            # and act on that, rather than letting a handler's success decide
            # whether the skip fires.
            if _JOINT_EVENT.search(title):
                # HELD BACK, NOT EMITTED. A marginal of the joint distribution
                # and Polymarket's own chamber-control market are the same
                # exchange pricing the same question two ways; emitting both
                # would put one venue into the market average twice. Keyed by
                # race so a paged capture listing the event more than once
                # contributes it once.
                for r in _joint_rows(ev, title, art, ctx):
                    joint[r.race_id] = r
                continue
            if _JOINT_CONTROL.search(title) or all(
                    _JOINT_CONTROL.search(str(m.get("question") or ""))
                    for m in (ev.get("markets") or []) or [{}]):
                continue                      # joint-chamber contract, not ours
            pv_only = False
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
                # The book, for the one shape where a quote belongs to a party.
                # `found` has a single entry exactly when this is a Yes/No
                # market on one party's win; a multi-outcome market yields two
                # or more and its bid/ask cannot be assigned to a side.
                if len(found) == 1:
                    rid, chamber, state, district = got
                    rows.extend(_micro_rows(m, next(iter(found)), rid, chamber,
                                            state, district, art, ctx))
            for (rid, chamber, state, district), sides in agg.items():
                for side, price in sides.items():
                    # A sum over candidates can drift past 1 on a market whose
                    # prices do not quite close. Record it as certainty rather
                    # than as an out-of-range value the validator would reject.
                    price = min(price, 1.0)
                    rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                                        district=district, quantity=f"win_prob_{side}",
                                        value=round(price, 4), unit="prob"))
    # THE DEDICATED MARKET WINS; THE MARGINAL FILLS THE GAP BEFORE IT EXISTED.
    #
    # Polymarket listed chamber-control contracts from 2026-02-20. Before that
    # the joint event is the only thing they priced on the question, and it is
    # priced coherently back to 2025-07-19. So the marginal is used exactly
    # where the direct market is silent, which is a seven-month extension at
    # the front of the series and nothing at all after February.
    direct = {r.race_id for r in rows if r.quantity == "win_prob_D"}
    rows.extend(r for rid, r in sorted(joint.items()) if rid not in direct)

    if not rows:
        # NOTHING TO EMIT IS NOT THE SAME AS NOTHING UNDERSTOOD.
        #
        # Some days Polymarket's House capture holds only a popular-vote margin
        # ladder too thin to have an expectation, plus a JOINT "R Senate, R
        # House" contract, which is a different event from either chamber and
        # must not be filed as one. Every event was recognised and correctly
        # produced no row. Raising there marks a healthy day as a parser
        # failure and, worse, invites the next person to loosen the filters
        # until something comes out.
        if pv_only:
            return []
        raise ValueError(
            f"parsed 0 rows from {seen_events} Polymarket event(s), and none "
            f"of them was a shape this parser recognises")
    return rows
