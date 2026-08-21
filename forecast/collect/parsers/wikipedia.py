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
               TOSSUP_LABELS as TOSSUP, STATE_NAMES, is_state, race_id,
               state_from_text)

# Header cell -> our source id.
#
# Matched against the header with wikilinks expanded to "target display", not
# against the display text alone. The Senate table abbreviates every column to
# initials — [[Race to the WH|WH]], [[The Economist|Econ]], [[FiftyPlusOne|FPO]],
# [[Split Ticket (website)|ST]] — so display-only matching found almost nothing
# there, and adding two-letter needles to compensate would be worse: "IE" is a
# substring of a great many words. The link target carries the full name, so
# matching on target-plus-display keeps the needles long and unambiguous.
#
# Order matters: the first needle that appears wins.
FORECASTERS = [
    ("INSIDE ELECTIONS", "inside_elections"), ("ROTHENBERG", "inside_elections"),
    ("COOK", "cook"),
    ("SABATO", "sabato"), ("CRYSTAL BALL", "sabato"),
    ("DECISION DESK", "ddhq"), ("DDHQ", "ddhq"),
    ("ECONOMIST", "economist"),
    ("SPLIT TICKET", "split_ticket"), ("ARGUMENT", "split_ticket"),
    ("VOTEHUB", "votehub"),
    ("RACE TO THE WH", "race_to_the_wh"), ("RTWH", "race_to_the_wh"),
    ("FIFTYPLUSONE", "fiftyplusone"), ("FIFTY PLUS ONE", "fiftyplusone"),
    ("SILVER BULLETIN", "silver_bulletin"),
    ("REALCLEARPOLITICS", "rcp"), ("REALCLEARPOLLING", "rcp"), ("RCP", "rcp"),
    ("FOX", "fox_power_rankings"), ("CNALYSIS", "cnalysis"),
    ("ELECTIONS DAILY", "elections_daily"), ("JHK", "jhk_forecasts"),
]

# Headers that must never be read as a forecaster column, checked first.
#
# The partisan-index column links to [[Cook Partisan Voting Index|PVI]], and
# with link targets in scope that header contains the word COOK. Without this
# guard the index column would be read as Cook's rating column, which is both
# wrong and the most confusing possible way to be wrong.
NOT_A_FORECASTER = ("PARTISAN VOTING INDEX", "COOK PVI")

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


def _expand_links(cell: str) -> str:
    """[[Target|Display]] -> "Target Display", so both halves are searchable."""
    return re.sub(r"\[\[([^|\]]*)\|([^\]]+)\]\]", r"\1 \2", cell)


def _forecaster(header: str) -> str | None:
    h = _clean(_expand_links(header)).upper()
    if any(bad in h for bad in NOT_A_FORECASTER):
        return None
    for needle, sid in FORECASTERS:
        if needle in h:
            return sid
    return None


# A seat cell, in any of the three forms the two articles actually use.
#
#   {{ushr|AL|2}}                                            House, template
#   {{ussenate|AL}}                                          Senate, template
#   [[2026 United States Senate election in Alabama|...]]    Senate, article link
#
# The third form is why the Senate ratings never parsed: the walker recognised
# a data row only by the presence of a seat TEMPLATE, and the Senate article
# does not use one. Twelve raters times thirty-five races went silently
# missing, and silently is the operative word — the table was skipped whole, so
# the "no rows at all" guard at the bottom never fired either.
_ARTICLE_HOUSE = re.compile(
    r"\[\[\s*20\d\d United States House of Representatives election"
    r"s?\s+in\s+([A-Za-z .]+?)(?:'s\s+(\d{1,2})\w{0,2}|\s+at-large)"
    r"\s+congressional district", re.I)
_ARTICLE_SENATE = re.compile(
    r"\[\[\s*20\d\d United States Senate election\s+in\s+([A-Za-z .]+?)\s*[|\]]", re.I)


def _seat(cell: str) -> tuple[str, str, str] | None:
    """-> (chamber, postal, district) or None."""
    m = _USHR.search(cell)
    if m:
        st, d = m.group(1).upper(), m.group(2).upper()
        return ("house", st, "1" if d == "AL" else d) if is_state(st) else None

    m = _ARTICLE_HOUSE.search(cell)
    if m:
        st = state_from_text(m.group(1))
        return ("house", st, m.group(2) or "1") if st else None

    m = _USS.search(cell)
    if m:
        st = m.group(1).upper()
        return ("senate", st, "") if is_state(st) else None

    m = _ARTICLE_SENATE.search(cell)
    if m:
        st = state_from_text(m.group(1))
        return ("senate", st, "") if st else None

    # Last resort: a bare state name or postal code in a header cell. Kept for
    # tables that name the state and nothing else, and deliberately narrow —
    # race_id refuses non-states outright, and a stray two-letter match used to
    # abort an entire parse.
    st = state_from_text(_clean(cell))
    return ("senate", st, "") if st else None


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
            # Editors annotate each rating cell with the rater's name in an
            # HTML comment — "<!--Cook--> | {{USRaceRating|Solid|R}}" — so the
            # line does not begin with a pipe and the cell was dropped. Strip
            # leading comments before deciding what kind of line this is.
            ln = re.sub(r"^\s*(?:<!--.*?-->\s*)+", "", ln)
            if not ln:
                continue
            is_seat = ln.startswith("!") and _seat(ln[1:]) is not None

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
                got = _seat(cells[0])
                if got is None:
                    continue
                chamber, st, d = got
                try:
                    if chamber == "house":
                        rid, dist = race_id("house", st, d), f"{int(d):02d}"
                    else:
                        rid, dist = race_id("senate", st), ""
                except (ValueError, TypeError):
                    continue

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
