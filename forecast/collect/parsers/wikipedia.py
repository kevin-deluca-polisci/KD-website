"""
Wikipedia race ratings. Publication: individual (CC BY-SA 4.0, attribution required).

Rewritten 2026-08-19 against the REAL wikitext. The first version guessed at a
pipe-separated table and found nothing; the actual article uses MediaWiki
templates, one cell per line, which is both different and much easier:

    |-
    !{{ushr|AL|2|X}}                    <- the seat
    |{{shading PVI|R|7}}                <- Cook PVI (private, see below)
    | {{Party shading/Democratic}} |{{sortname|Shomari|Figures}}
    | ... |54.6% D                      <- last result
    |{{USRaceRating|Likely|R|Flip}}     <- one per forecaster, in header order
    |{{USRaceRating|Tossup}}

Ratings are collected but deliberately kept OUT of the dispersion figure. "Lean
R" does not average with a vote share, and building a crosswalk is a judgment
call worth more as a class discussion than as a silent assumption in a script.

THE PVI COLUMN IS PRIVATE. The table carries {{shading PVI|R|7}} sourced to
Cook's 2026 district list. Same rule as everywhere else: Wikipedia's CC BY-SA
covers Wikipedia's text, not Cook's compilation, so those rows are stamped
`private` and additionally sit in aggregate.py's NEVER_PUBLISH.

CAVEAT WORTH REMEMBERING: the table lags the forecasters. Each header carries
its own "as of" date, which is authoritative — not the revision timestamp.
"""
from __future__ import annotations

import re

from . import (Context, LoadedArtifact, RATING_LEVEL as LEVEL, Row,
               TOSSUP_LABELS as TOSSUP, is_state, race_id)

# Header cell -> our source id. Matched against the cleaned header text.
FORECASTERS = [
    ("COOK", "cook"), ("IE", "inside_elections"), ("INSIDE", "inside_elections"),
    ("ROTHENBERG", "inside_elections"), ("SABATO", "sabato"),
    ("CRYSTAL BALL", "sabato"), ("DDHQ", "ddhq"), ("DECISION DESK", "ddhq"),
    ("ECONOMIST", "economist"), ("SPLIT TICKET", "split_ticket"),
    ("ARGUMENT", "split_ticket"), ("VOTEHUB", "votehub"),
    ("RACE TO THE WH", "race_to_the_wh"), ("RTWH", "race_to_the_wh"),
    ("FOX", "fox_power_rankings"), ("CNALYSIS", "cnalysis"),
    ("ELECTIONS DAILY", "elections_daily"), ("JHK", "jhk_forecasts"),
]

_ROWSEP = re.compile(r"^\|-")
_USHR = re.compile(r"\{\{\s*ushr\s*\|\s*([A-Z]{2})\s*\|\s*(\d{1,2}|AL)\s*", re.I)
_USS = re.compile(r"\{\{\s*(?:ussenate|ussen)\s*\|\s*([A-Z]{2})", re.I)
# Split the template on pipes rather than trying to regex the arguments. A lazy
# quantifier plus an optional party group matches "L" of "Likely" and swallows
# the rest — which it silently did, producing zero ratings and no error.
_RATING = re.compile(r"\{\{\s*USRaceRating\s*\|([^{}]*)\}\}", re.I)
_PVI = re.compile(r"\{\{\s*shading\s*PVI\s*\|\s*([RD])\s*\|\s*(\d{1,2})", re.I)
_EVEN_PVI = re.compile(r"\{\{\s*shading\s*PVI\s*\|\s*EVEN", re.I)


def _clean(cell: str) -> str:
    """Strip refs, templates, wikilinks and markup down to readable text."""
    cell = re.sub(r"<ref[^>]*/>", " ", cell)
    cell = re.sub(r"<ref.*?</ref>", " ", cell, flags=re.S)
    cell = re.sub(r"\{\{\s*cite[^}]*\}\}", " ", cell, flags=re.I | re.S)
    cell = re.sub(r"\{\{\s*small\s*\|", " ", cell, flags=re.I)
    cell = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", cell)
    cell = re.sub(r"<[^>]+>", " ", cell)
    cell = re.sub(r"[{}|']", " ", cell)
    return re.sub(r"\s+", " ", cell).strip()


def _forecaster(header: str) -> str | None:
    h = _clean(header).upper()
    for needle, sid in FORECASTERS:
        if needle in h:
            return sid
    return None


def _rating(cell: str) -> tuple[str, float] | None:
    m = _RATING.search(cell)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
    if not parts:
        return None
    level = parts[0].upper()
    party = parts[1].upper() if len(parts) > 1 else ""
    if level.replace("-", "").replace(" ", "") in {t.replace("-", "").replace(" ", "")
                                                   for t in TOSSUP}:
        return "Toss-up", 5.0
    base = LEVEL.get(level)
    if base is None or party not in ("R", "D"):
        return None
    # LEVEL is expressed on the R side; mirror for D.
    numeric = base if party == "R" else 10.0 - base
    return f"{level.title()} {party}", numeric


def _pvi(cell: str) -> float | None:
    if _EVEN_PVI.search(cell):
        return 0.0
    m = _PVI.search(cell)
    if not m:
        return None
    mag = float(m.group(2))
    return -mag if m.group(1).upper() == "R" else mag


def _tables(text: str):
    """
    Yield (headers, rows) for every wikitable, cells split one per line.

    The subtlety that broke the first attempt: these tables open with a layout
    row of colspan banners, so a naive "|- starts a data row" walker treats the
    real header lines as data and ends up with no headers at all. And the seat
    cell of each DATA row is itself a `!` line ({{ushr|AL|2}}), so `!` alone
    cannot distinguish header from row either.

    The rule that actually works: a `!` line carrying a seat template starts a
    data row; every other `!` line before the first data row is a header.
    """
    for block in re.split(r"\n\{\|", text)[1:]:
        block = block.split("\n|}")[0]
        headers: list[str] = []
        rows: list[list[str]] = []
        cur: list[str] | None = None

        for ln in (l.rstrip() for l in block.splitlines()):
            is_seat = ln.startswith("!") and re.search(
                r"\{\{\s*(ushr|ussen|uss)\b", ln, re.I)

            if is_seat:
                if cur:
                    rows.append(cur)
                cur = [ln[1:]]
                continue
            if _ROWSEP.match(ln):
                continue                       # row separators carry no data
            if ln.startswith("!"):
                cell = ln[1:]
                if re.match(r"\s*colspan", cell, re.I):
                    continue                   # layout banner, not a header
                if cur is None:
                    headers.append(cell)
                else:
                    cur.append(cell)
                continue
            if ln.startswith("|") and cur is not None:
                cur.append(ln[1:])

        if cur:
            rows.append(cur)
        if headers and rows:
            yield headers, rows


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    rows: list[Row] = []
    seen_tables = 0

    for name, art in artifacts.items():
        payload = art.json()
        text = (payload.get("parse", {}) or {}).get("wikitext", "")
        if isinstance(text, dict):
            text = text.get("*", "")
        if not text:
            continue

        for headers, table in _tables(text):
            # Which column belongs to which forecaster?
            col_source = {i: sid for i, h in enumerate(headers)
                          if (sid := _forecaster(h))}
            if len(col_source) < 2:
                continue                       # not a ratings table
            seen_tables += 1
            for cells in table:
                if not cells:
                    continue
                seat = cells[0]
                m = _USHR.search(seat)
                if m:
                    st, d = m.group(1).upper(), m.group(2).upper()
                    d = "1" if d == "AL" else d
                    try:
                        rid, chamber, dist = race_id("house", st, d), "house", f"{int(d):02d}"
                    except (ValueError, TypeError):
                        continue
                else:
                    m = _USS.search(seat) or re.search(r"\b([A-Z]{2})\b", _clean(seat))
                    if not m:
                        continue
                    st = m.group(1).upper()
                    # The fallback is a bare two-letter match, which catches
                    # abbreviations that are not states. race_id now refuses
                    # those outright, so check first rather than letting one
                    # stray cell abort the whole 1,989-row parse.
                    if not is_state(st):
                        continue
                    rid, chamber, dist = race_id("senate", st), "senate", ""

                # Cook PVI rides in this table too. PRIVATE — see the docstring.
                for c in cells[1:4]:
                    v = _pvi(c)
                    if v is not None:
                        rows.append(ctx.row(art, publication="private",
                                            race_id=rid, chamber=chamber, state=st,
                                            district=dist, quantity="pvi",
                                            value=v, unit="pct"))
                        break

                for i, cell in enumerate(cells):
                    sid = col_source.get(i)
                    if sid is None:
                        continue
                    got = _rating(cell)
                    if got is None:
                        continue
                    label, numeric = got
                    # source_id stays `wikipedia` — this is Wikipedia's
                    # transcription of a rating, not a direct capture from the
                    # forecaster. The forecaster is named in the value.
                    rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=st,
                                        district=dist, quantity="rating_ordinal",
                                        value=f"{sid}:{label}", unit="ordinal"))
                    rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=st,
                                        district=dist, quantity="rating_numeric",
                                        value=numeric, unit="ordinal"))

    if not rows:
        raise ValueError(
            f"read {len(artifacts)} artifact(s), found {seen_tables} ratings "
            f"table(s), but extracted no rows — the template markup has changed. "
            f"Expected {{{{ushr|XX|N}}}} seats and {{{{USRaceRating|Level|Party}}}} cells.")
    return rows
