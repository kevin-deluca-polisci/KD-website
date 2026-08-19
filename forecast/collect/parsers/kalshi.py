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
from . import Context, LoadedArtifact, Row, NATIONAL_HOUSE, NATIONAL_SENATE, race_id

# Kalshi ticker conventions are not documented and shift. These patterns are a
# best effort; --inspect the first real capture and tighten them.
_SEN = re.compile(r"SENATE.*?([A-Z]{2})\b|\b([A-Z]{2})\b.*?SENATE", re.I)
_GOV = re.compile(r"GOV(?:ERNOR)?.*?([A-Z]{2})\b", re.I)
_HOU = re.compile(r"HOUSE.*?([A-Z]{2})[-_]?(\d{1,2})\b", re.I)
_CTRL_H = re.compile(r"(HOUSE).*(CONTROL|MAJORITY|PARTY)|((CONTROL|MAJORITY)).*(HOUSE)", re.I)
_CTRL_S = re.compile(r"(SENATE).*(CONTROL|MAJORITY|PARTY)|((CONTROL|MAJORITY)).*(SENATE)", re.I)


def _price(m: dict) -> float | None:
    """Mid-price as a probability. Kalshi yes_bid/yes_ask are strings."""
    for a, b in (("yes_bid", "yes_ask"), ("last_price", None)):
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
    m = _HOU.search(blob)
    if m:
        st, d = m.group(1).upper(), m.group(2)
        return race_id("house", st, d), "house", st, f"{int(d):02d}"
    m = _GOV.search(blob)
    if m:
        st = m.group(1).upper()
        return race_id("governor", st), "governor", st, ""
    m = _SEN.search(blob)
    if m:
        st = (m.group(1) or m.group(2) or "").upper()
        if len(st) == 2:
            return race_id("senate", st), "senate", st, ""
    return None


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
    priced = 0                     # markets that had a usable price
    for art in market_arts:
        payload = art.json()
        markets = payload.get("markets", []) or []
        if not markets:
            # Verified 2026-08-19: HOUSE, SENATE, HOUSEMOV, SENATEMOV and KXHOUSE
            # all return {"cursor":"","markets":[]}. The series exist as shells;
            # Kalshi has not opened 2026 chamber-control markets under them. That
            # is a fact about Kalshi, not a parser bug, and the empty response is
            # itself worth archiving — the day they open, the archive shows it.
            empty_series.append(art.name.replace("markets-", ""))
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
    if not rows and priced == 0:
        # Captured fine; Kalshi simply has nothing priced and classifiable for
        # 2026 races today. That is a fact about Kalshi, not a parser fault, and
        # an empty list is the honest report. See the registry notes.
        return []

    if not rows:
        live = len(market_arts) - len(empty_series)
        if live == 0:
            # Every captured series was empty. Nothing to parse and nothing wrong.
            raise ValueError(
                f"all {len(empty_series)} captured Kalshi series are empty — no "
                f"open 2026 markets under: {sorted(empty_series)[:8]}. "
                f"Re-check series_include in the registry; the tickers may have "
                f"been renamed, or the markets may genuinely not be open yet.")
        raise ValueError(
            f"{live} series had markets but none classified to a race "
            f"({len(unmatched)} tickers unmatched). Sample: {unmatched[:5]}")
    return rows
