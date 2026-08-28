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

from . import (Context, LoadedArtifact, NATIONAL_HOUSE,
               RATING_LEVEL as LEVEL, Row, TOSSUP_LABELS as TOSSUP,
               STATE_NAMES, is_state, race_id, state_from_text)

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
    ("FOX", "fox_power_rankings"),
    # 270toWin appears as a ROW in the poll-aggregation table, not as a
    # ratings COLUMN, so this needle resolves to the polling product. If it
    # ever turns up in a ratings table the parser has to disambiguate by
    # table shape before this mapping can stand.
    ("270TOWIN", "twoseventy"), ("270 TO WIN", "twoseventy"),
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
# "special" is optional and load-bearing: Florida and Ohio are special elections
# this cycle, and without it the two seats that happen to sit at the 50th and
# 51st positions on the ladder were the two the parser missed.
_ARTICLE_SENATE = re.compile(
    r"\[\[\s*20\d\d United States Senate (?:special )?election"
    r"\s+in\s+([A-Za-z .]+?)\s*[|\]]", re.I)
_ARTICLE_GOV = re.compile(
    r"\[\[\s*20\d\d ([A-Za-z .]+?) gubernatorial (?:special )?election", re.I)

# Which contest a page is about, from its title. The seat cell alone is not
# always enough — a gubernatorial table names the state and nothing else — and
# guessing from the cell is how the bug below happened.
_PAGE_CHAMBER = (
    ("house of representatives", "house"),
    ("senate", "senate"),
    ("gubernatorial", "governor"),
    ("governor", "governor"),
)


def page_chamber(name: str) -> str | None:
    n = name.replace("-", " ").lower()
    for needle, ch in _PAGE_CHAMBER:
        if needle in n:
            return ch
    return None


# A cell that is a state and NOTHING else: "Alabama", "AL", "[[...|Alabama]]".
# Deliberately anchored.
#
# THE BUG THIS FIXES. The previous fallback asked "does this cell contain a
# state name anywhere?" and answered yes for the gubernatorial table's own
# HEADER, because the "last election" column carries a footnote reading "with
# the exception of New Hampshire and Vermont". That header was therefore read
# as the first data row, the eight rater columns became its cells, only three
# headers survived, and the table was discarded as not-a-ratings-table. Thirty-
# six governor races went missing without a single error — the same failure
# mode as the old two-letter matcher, one level up.
_BARE_STATE = re.compile(r"^[A-Za-z .]{2,24}$")


def _seat(cell: str, chamber_hint: str | None = None) -> tuple[str, str, str] | None:
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

    m = _ARTICLE_GOV.search(cell)
    if m:
        st = state_from_text(m.group(1))
        return ("governor", st, "") if st else None

    # Fallback: the cell is a bare state, and the PAGE tells us the contest.
    # Both conditions, never either alone.
    if not chamber_hint:
        return None
    text = _clean(cell)
    if not _BARE_STATE.match(text):
        return None
    st = state_from_text(text)
    return (chamber_hint, st, "") if st and chamber_hint != "house" else None


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


def _tables(text: str, chamber_hint: str | None = None):
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
            is_seat = ln.startswith("!") and _seat(ln[1:], chamber_hint) is not None

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

        # The generic-ballot aggregate table lives on the same page as the
        # ratings and is a different shape and a different category.
        rows.extend(_generic_ballot(text, art, ctx))

        hint = page_chamber(name)
        for headers, table in _tables(text, hint):
            # Which column belongs to which forecaster?
            col_source = {i: sid for i, h in enumerate(headers)
                          if (sid := _forecaster(h))}
            if len(col_source) < 2:
                continue                       # not a ratings table
            seen_tables += 1
            for cells in table:
                if not cells:
                    continue
                got = _seat(cells[0], hint)
                if got is None:
                    continue
                chamber, st, d = got
                try:
                    if chamber == "house":
                        rid, dist = race_id("house", st, d), f"{int(d):02d}"
                    else:
                        rid, dist = race_id(chamber, st), ""
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


# ==========================================================================
# GENERIC-BALLOT AGGREGATE TABLE
#
# The House article carries a table headed "Source of poll aggregation" listing
# each major aggregator's current generic-ballot average — Decision Desk HQ,
# FiftyPlusOne, RealClearPolitics, Silver Bulletin, VoteHub, Race to the WH —
# with Republican share, Democratic share and margin. It is the only place in
# the archive where the polling category has more than one contributor.
#
# ATTRIBUTION AND LICENCE. Each row is filed under the AGGREGATOR's source id,
# not under "wikipedia", so six averages count as six contributors rather than
# one. It carries that aggregator's own publication tier, not Wikipedia's:
# Silver Bulletin's average is aggregate_only whether we read it from Silver
# Bulletin or from a Wikipedia table about Silver Bulletin. The point of the
# attribution is to make the CATEGORY AVERAGE publishable by having enough
# contributors, not to publish anybody's number by name.
# ==========================================================================

# Row label -> (source id, publication tier). Tiers mirror the registry; a
# forecaster we may not republish stays un-republishable by this route too.
_AGG_SOURCES = [
    ("DECISION DESK", ("ddhq", "aggregate_only")),
    ("FIFTYPLUSONE", ("fiftyplusone", "private")),
    ("FIFTY PLUS ONE", ("fiftyplusone", "private")),
    ("REALCLEARPOLI", ("rcp", "aggregate_only")),
    ("SILVER BULLETIN", ("silver_bulletin", "aggregate_only")),
    ("VOTEHUB", ("votehub", "private")),
    ("RACE TO THE WH", ("race_to_the_wh", "aggregate_only")),
    ("SPLIT TICKET", ("split_ticket", "private")),
    ("ECONOMIST", ("economist", "aggregate_only")),
]

# Aggregators this project fetches from the source itself. For these, the
# Wikipedia table is a duplicate reading and must not enter an average — see
# the note at the emit site in _generic_ballot(). Add an id here the moment a
# dedicated parser starts producing that source's national margin, or the
# category quietly counts them twice.
_CAPTURED_DIRECTLY = {"silver_bulletin"}
# The literal wikitext reads "Source of poll<br>aggregation". Neither \s+ nor
# \W+ bridges that gap — \s+ because <br> is not whitespace, \W+ because the
# "b" and "r" inside the tag are word characters. A bounded any-character gap
# is the thing that actually works, and a header test that never fires means a
# table that is never found and no error to say so.
_AGG_HEADER = re.compile(r"source\b.{0,24}?\bpoll.{0,24}?aggregation", re.I | re.S)
_PCT = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_MARGIN_TEXT = re.compile(r"(democrat|republican)[a-z]*\s*\+\s*(\d+(?:\.\d+)?)", re.I)


def _agg_source(cell: str) -> tuple[str, str] | None:
    h = _clean(_expand_links(cell)).upper()
    for needle, got in _AGG_SOURCES:
        if needle in h:
            return got
    return None


def _generic_ballot(text: str, art: LoadedArtifact, ctx: Context) -> list[Row]:
    rows: list[Row] = []
    for block in re.split(r"\n\{\|", text)[1:]:
        block = block.split("\n|}")[0]
        if not _AGG_HEADER.search(block):
            continue
        # Column order is read from the header rather than assumed: the table
        # lists Republicans BEFORE Democrats, which is the opposite of every
        # other table in this file and exactly the sort of thing that silently
        # flips a sign.
        # A header cell carries its own styling before the pipe —
        # `!style="width:100px;" |Republicans` — so the column name is not at
        # the start of the string and an anchored match finds nothing. Take
        # what follows the last pipe, then match.
        head = [_clean(l[1:].rsplit("|", 1)[-1]) for l in block.splitlines()
                if l.startswith("!")]
        def col(pat):
            return next((i for i, h in enumerate(head)
                         if re.search(pat, h, re.I)), None)
        col_r, col_d, col_m = col(r"republican"), col(r"democrat"), col(r"^margin")
        if col_d is None or col_r is None:
            continue

        for chunk in re.split(r"\n\|-", block)[1:]:
            cells = [c[1:].strip() for c in chunk.splitlines() if c.startswith("|")]
            if len(cells) <= max(col_d, col_r):
                continue
            got = _agg_source(cells[0])
            if got is None:
                continue                  # the "Average" row, and anything new
            sid, tier = got

            def pct(i):
                m = _PCT.search(_clean(cells[i])) if i is not None and i < len(cells) else None
                return float(m.group(1)) if m else None

            d_share, r_share = pct(col_d), pct(col_r)
            margin = None
            if col_m is not None and col_m < len(cells):
                mm = _MARGIN_TEXT.search(_clean(cells[col_m]))
                if mm:
                    margin = float(mm.group(2))
                    if mm.group(1).lower().startswith("republic"):
                        margin = -margin
            if margin is None and d_share is not None and r_share is not None:
                margin = round(d_share - r_share, 2)
            if margin is None:
                continue

            # margin_D only. The two vote SHARES are in the table and would be
            # interesting — the gap between them is the undecided share, which
            # differs by a factor of two across aggregators — but they are not
            # a registered quantity, and inventing one here would put a column
            # into the archive that nothing else knows how to average.
            #
            # EXCEPT for aggregators we capture ourselves. For those this table
            # is a SECOND reading of the same forecaster, rounded to one
            # decimal and refreshed by whoever last edited the article — about
            # every three days, with runs as long as thirteen. Filing it as
            # margin_D put Silver into the polling average twice, at two values
            # 1.1 points apart, and let a stale rounded figure reach
            # model/polling.py's generic_ballot() as though it were his own
            # file. It is kept as a cross-check quantity instead: same row,
            # same tier, never averaged.
            q = ("margin_D_wikipedia_reported" if sid in _CAPTURED_DIRECTLY
                 else "margin_D")
            rows.append(ctx.row(art, source_id=sid, publication=tier,
                                category="polling", race_id=NATIONAL_HOUSE,
                                chamber="national", quantity=q,
                                value=round(margin, 3), unit="pct"))
    return rows
