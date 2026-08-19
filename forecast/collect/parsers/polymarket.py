"""
Polymarket — prediction market. Publication: individual (public order book).

Gamma returns events, each holding markets whose `outcomePrices` is a
JSON-encoded STRING array. That double encoding is the usual trip hazard.
"""
from __future__ import annotations
import json, re
from . import Context, LoadedArtifact, Row, NATIONAL_HOUSE, NATIONAL_SENATE, race_id

_HOU = re.compile(r"\b([A-Z]{2})[-\s]?(\d{1,2})\b.*(house|congress)", re.I)
_SEN = re.compile(r"senate.*\b([A-Z]{2})\b|\b([A-Z]{2})\b.*senate", re.I)
_GOV = re.compile(r"governor.*\b([A-Z]{2})\b|\b([A-Z]{2})\b.*governor", re.I)


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
            for m in ev.get("markets", []) or []:
                q = str(m.get("question") or m.get("groupItemTitle") or title)
                prices, outs = _prices(m), _outcomes(m)
                if not prices:
                    continue
                blob = f"{title} {q}"
                if re.search(r"house", blob, re.I) and re.search(r"control|majority|win the", blob, re.I):
                    rid, chamber, state, district = NATIONAL_HOUSE, "national", "", ""
                elif re.search(r"senate", blob, re.I) and re.search(r"control|majority|win the", blob, re.I):
                    rid, chamber, state, district = NATIONAL_SENATE, "national", "", ""
                elif (mm := _HOU.search(blob)):
                    st, d = mm.group(1).upper(), mm.group(2)
                    rid, chamber, state, district = race_id("house", st, d), "house", st, f"{int(d):02d}"
                elif (mm := _GOV.search(blob)):
                    st = (mm.group(1) or mm.group(2)).upper()
                    rid, chamber, state, district = race_id("governor", st), "governor", st, ""
                elif (mm := _SEN.search(blob)):
                    st = (mm.group(1) or mm.group(2)).upper()
                    rid, chamber, state, district = race_id("senate", st), "senate", st, ""
                else:
                    continue
                # Prefer the outcome explicitly naming a party.
                side, price = None, None
                for i, o in enumerate(outs):
                    if i < len(prices) and re.search(r"republic|GOP", o, re.I):
                        side, price = "R", prices[i]; break
                    if i < len(prices) and re.search(r"democrat", o, re.I):
                        side, price = "D", prices[i]; break
                if side is None:
                    side, price = ("R" if re.search(r"republic|GOP", blob, re.I) else "D"), prices[0]
                if not (0.0 <= price <= 1.0):
                    continue
                rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                                    district=district, quantity=f"win_prob_{side}",
                                    value=round(price, 4), unit="prob"))
    if not rows:
        raise ValueError(f"parsed 0 rows from {seen_events} Polymarket events")
    return rows
