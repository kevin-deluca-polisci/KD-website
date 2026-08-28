"""Parser for `wiki_endorsements`: race-level poll AGGREGATES, Senate.

WHY THIS MODULE IS NAMED FOR ENDORSEMENTS AND PARSES POLLS

    Parsers dispatch by source id, and `wiki_endorsements` is the capture that
    already holds these bytes -- 152 titles a day, deduped, including every
    Senate race article. Standing up a second source to fetch the same pages
    again would double the requests to Wikipedia and store a second copy of
    identical bytes, to gain nothing but a tidier filename.

    Endorsement parsing is still unwritten. When it lands it belongs in this
    same module, emitting its own quantities, and those quantities must be
    added to NOT_A_FORECAST in aggregate.py BEFORE their first run -- an
    endorsement is an input to a model, not a forecast anyone is making. The
    poll-aggregate rows below are the opposite case: margin_D is a forecast
    quantity and is meant to reach the polling average.


WHAT THIS READS, AND WHAT IT DELIBERATELY DOES NOT

    Each Senate race article carries, under `General election > Polling`, a
    table headed "Source of poll aggregation": one row per forecaster, with
    that forecaster's own average of the polls in that race. This parser reads
    those rows and nothing else.

    It does NOT read the individual-poll tables sitting beside them. There are
    203 of those across the Senate and 274 more in the House articles, and
    every one of them would drag in house effects, likely-voter screens and,
    before a primary, the question of which hypothetical matchup a poll was
    even asking about. The aggregators have already done that work and publish
    the result. That decision was taken on 2026-08-28 and it has a measured
    cost: it is why the House gets no race-level polling at all, because no
    aggregator publishes district averages.

WHY THE ROWS ARE ATTRIBUTED, NOT FILED UNDER WIKIPEDIA

    Six aggregators in one table filed under `wiki_endorsements` would collapse
    to one contributor, because the aggregator counts sources by id. So each
    row is attributed to the forecaster it actually came from, which is what
    Context.row's attribution override exists for. Attribution carries the
    category and the tier with it; it is not a route around a licence.

WHY EVERY ROW IS PRIVATE

    These are other people's per-race averages. Reading DDHQ's number off
    Wikipedia does not make it ours to republish, and the same is true of RCP,
    whose site we do not collect from directly at all. The tier is `private`
    for all of them, uniformly, rather than a per-source map that would have to
    be kept in step with the registry by hand -- and the uniform choice is also
    the strictest, so it can never loosen a source's real tier.

    What we publish is the model output built from these: a national tide, a
    seat count, a win probability. That is our own computation and it is not a
    redistribution of anyone's dataset, the same call already taken for the
    House district margins on 2026-08-21.

HOW A MARGIN IS READ, AND WHY NOT FROM THE MARGIN COLUMN

    The table's last column says "Brown +6.0%", which needs a name-to-party
    resolution this parser has no business doing. The candidate columns carry
    the party in the header instead:

        ! style="width:100px;" |Jon<br />Husted (R)
        ! style="width:100px;" |Sherrod<br />Brown (D)

    so margin_D is D% - R%, read from the two share columns. Verified against
    Ohio on 2026-08-28, where the four aggregators' own margin strings say
    +6.0, +5.4, +4.3 and +3.8 and the share columns must reproduce them.

THE MARKUP TRAPS, ALL THREE OF WHICH BIT SOMETHING BEFORE THIS WAS WRITTEN

    Refs are full of pipes. `<ref>{{cite web |title=... |url=...}}</ref>` sits
    inside the first cell of nearly every row, so refs must go before any cell
    splitting or the forecaster's name comes back as a citation fragment.

    Templates are full of pipes too, and they nest. A cell reads
    `{{party shading/Democratic}} |'''50.5%'''`, and a header reads
    `Other/<wbr/>Undecided{{Efn|Calculated by...|name=|group=}}`.

    Markup sits INSIDE the header phrase: `!Source of poll<br/>aggregation`.
    A literal string search for "Source of poll aggregation" finds nothing,
    which is exactly how an earlier check wrongly concluded the tables were
    not there.
"""
from __future__ import annotations

import re

from . import Context, LoadedArtifact, Row, race_id, state_from_text

# ---------------------------------------------------------------- allowlist --
# ROW NAME -> REGISTRY SOURCE ID. An allowlist rather than "whatever the first
# cell says", because these tables are edited by anyone: the Texas article
# carries a row for The Texas Tribune, a local aggregator that is in nobody's
# registry. An unknown name is counted and skipped, never invented as a source.
#
# The aliases are not tidiness. The governor census found "G. Elliott Morris"
# six times and "FiftyPlusOne" twice for the same outlet, under its author's
# name and its masthead; unaliased, an average over these rows would weight a
# forecaster by how each article's editor chose to label it.
AGGREGATORS = {
    "270towin": "twoseventy",
    "270 to win": "twoseventy",
    "decision desk hq": "ddhq",
    "ddhq": "ddhq",
    "fiftyplusone": "fiftyplusone",
    "fifty plus one": "fiftyplusone",
    "g. elliott morris": "fiftyplusone",
    "strength in numbers": "fiftyplusone",
    "race to the wh": "race_to_the_wh",
    "race to the white house": "race_to_the_wh",
    "realclearpolitics": "rcp",
    "realclearpolling": "rcp",
    "rcp": "rcp",
    "silver bulletin": "silver_bulletin",
}

# Below this many recognised aggregators, the race gets no rows at all.
#
# A one-source average is not "what the polls say" in that race, it is one
# forecaster's private judgment about it -- and we measured how private:
# corr(delta) between two forecasters' race-level residuals came out at 0.179.
# On 2026-08-28 this rule dropped Florida, Idaho, Minnesota and South Dakota,
# all four single-sourced on Race to the WH, and cost nothing else.
MIN_AGGREGATORS = 2

# ------------------------------------------------------------------ markup --
REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
TAG = re.compile(r"<[^>]+>")
HEAD = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)
PHASE = re.compile(r"(?i)\b(primar\w*|runoff|caucus|convention|nomination)")
AGG_HEADER = re.compile(r"(?i)^\s*source\s+of\s+poll\s*aggregation")
PARTY = re.compile(r"\(\s*([A-Za-z]+)\s*\)\s*$")
PCT = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
TITLE = re.compile(r"Senate-(?:special-)?election-in-(.+)$")


def _clean(cell: str) -> str:
    """One table cell to plain text.

    ORDER MATTERS AND IS THE WHOLE FUNCTION. Refs first, because they contain
    templates; then templates repeatedly, because they nest; then the attribute
    or shading segment before the last pipe; then tags.
    """
    s = REF.sub("", cell)
    for _ in range(6):
        s2 = TEMPLATE.sub("", s)
        if s2 == s:
            break
        s = s2
    s = s.split("|")[-1]
    s = TAG.sub(" ", s).replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", s).strip()


def _link_name(cell: str) -> str:
    """A first-column forecaster name, preferring the wikilink target."""
    s = REF.sub("", cell)
    m = re.search(r"\[\[([^\]|]+)", s)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else _clean(cell)


def _tables(wt: str) -> list[tuple[int, str]]:
    """[(offset, text)] for every table, nesting-aware.

    Depth-counted rather than regex-matched: these articles nest tables, and a
    non-greedy {|...|} stops at the inner close, truncating the outer table
    just before the rows we need.
    """
    out, depth, start, pos = [], 0, 0, 0
    for ln in wt.splitlines(keepends=True):
        s = ln.lstrip()
        if s.startswith("{|"):
            if depth == 0:
                start = pos
            depth += 1
        elif s.startswith("|}") and depth:
            depth -= 1
            if depth == 0:
                out.append((start, wt[start:pos + len(ln)]))
        pos += len(ln)
    return out


def _path(wt: str, off: int) -> list[str]:
    stack: list[tuple[int, str]] = []
    for m in HEAD.finditer(wt):
        if m.start() > off:
            break
        lvl = len(m.group(1))
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        stack.append((lvl, m.group(2)))
    return [t for _, t in stack]


def _cells(block: str, marker: str) -> list[str]:
    """Cells of a header block or a data row, in column order."""
    out = []
    for ln in block.splitlines():
        s = ln.strip()
        if not s or s.startswith("|-") or s.startswith("|}") or s.startswith("{|"):
            continue
        if s[0] != marker:
            continue
        # `!a !! b` and `|a || b` put several cells on one line; the Senate
        # articles use one per line, but both shapes appear across states.
        for part in re.split(r"!!|\|\|", s[1:]):
            out.append(part)
    return out


def _party_columns(headers: list[str]) -> dict[str, int]:
    """{'D': i, 'R': j} from candidate headers ending in (D) / (R).

    Returns {} unless exactly one D and one R column are found. A race with two
    candidates of the same party -- Louisiana's runoff shape -- has no
    two-party margin to read, and guessing one would be worse than none.
    """
    found: dict[str, list[int]] = {}
    for i, h in enumerate(headers):
        m = PARTY.search(_clean(h))
        if not m:
            continue
        p = m.group(1).upper()[:1]
        if p in ("D", "R"):
            found.setdefault(p, []).append(i)
    if sorted(found) != ["D", "R"] or any(len(v) != 1 for v in found.values()):
        return {}
    return {k: v[0] for k, v in found.items()}


def _pct(cell: str) -> float | None:
    m = PCT.search(_clean(cell))
    return float(m.group(1)) if m else None


def _updated(headers: list[str], cells: list[str]) -> str | None:
    """The row's 'Dates updated' as YYYY-MM-DD, if that column exists."""
    idx = next((i for i, h in enumerate(headers)
                if "updated" in _clean(h).lower()), None)
    if idx is None or idx >= len(cells):
        return None
    txt = _clean(cells[idx])
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})", txt)
    if not m:
        return None
    months = {m_: i + 1 for i, m_ in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"])}
    mo = months.get(m.group(1))
    return f"{m.group(3)}-{mo:02d}-{int(m.group(2)):02d}" if mo else None


def _state_of(name: str) -> str | None:
    m = TITLE.search(name)
    if not m:
        return None
    return state_from_text(m.group(1).replace("-", " "))


def read_table(tbl: str) -> tuple[list[tuple[str, float, str | None]], int]:
    """[(source_id, margin_D, updated_date)], and how many names were unknown."""
    blocks = re.split(r"^\|-", tbl, flags=re.M)
    headers = _cells(blocks[0], "!")
    cols = _party_columns(headers)
    if not cols:
        return [], 0
    out, unknown = [], 0
    for blk in blocks[1:]:
        cells = _cells(blk, "|") or _cells(blk, "!")
        if not cells:
            continue
        who = AGGREGATORS.get(_link_name(cells[0]).lower())
        if not who:
            # The Average row is a row too, and it is ours to compute, not to
            # read: Wikipedia averages whichever forecasters an editor
            # included on whichever dates they last updated.
            unknown += int(_clean(cells[0]).lower() not in
                           ("average", "averages", "aggregate", "mean", ""))
            continue
        if max(cols.values()) >= len(cells):
            continue
        d, r = _pct(cells[cols["D"]]), _pct(cells[cols["R"]])
        if d is None or r is None:
            continue
        out.append((who, round(d - r, 2), _updated(headers, cells)))
    return out, unknown


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    rows: list[Row] = []
    for name, art in artifacts.items():
        st = _state_of(name)
        if not st:
            continue
        try:
            wt = art.json()["parse"]["wikitext"]
        except (ValueError, KeyError, TypeError):
            continue
        wt = wt["*"] if isinstance(wt, dict) else wt
        if not isinstance(wt, str) or wt.lstrip()[:9].upper() == "#REDIRECT":
            continue

        # ONE TABLE PER RACE, THE MOST RECENTLY UPDATED ONE.
        #
        # Georgia carries three aggregate tables under the same heading:
        # Ossoff v Collins updated 18 August, Ossoff v Dooley updated 5 March,
        # Ossoff v Carter updated 5 March. Michigan carries three the same way.
        # They are alternative nominee matchups kept from before the primary,
        # and only one of them is the race now being run.
        #
        # An earlier version of this took the last table in the document and
        # let later rows overwrite earlier ones per forecaster. That produced,
        # for Georgia, FiftyPlusOne's August number for the real matchup beside
        # RCP's March number for a candidate who lost the primary -- a single
        # "average" spanning three different Republican nominees and five
        # months. It looked plausible and every individual number in it was
        # real, which is what made it dangerous.
        #
        # Newest-updated wins, because that is what distinguishes the live
        # matchup from a frozen hypothetical: aggregators keep updating the
        # race that is happening and stop updating the ones that are not. It
        # needs no candidate roster and no primary results, so it cannot go
        # stale the way a hardcoded matchup list would.
        best: list[tuple[str, float, str | None]] = []
        best_date = ""
        for off, tbl in _tables(wt):
            hdr = _cells(re.split(r"^\|-", tbl, flags=re.M)[0], "!")
            if not hdr or not AGG_HEADER.match(_clean(hdr[0])):
                continue
            path = _path(wt, off)
            if any(PHASE.search(p) for p in path):
                continue
            got, _unknown = read_table(tbl)
            if not got:
                continue
            # A table with no readable date sorts oldest, so a dated table
            # always beats an undated one; ties go to the later table.
            when = max((u or "" for _w, _m, u in got), default="")
            if when >= best_date:
                best_date, best = when, got

        seen = {who: (margin, upd) for who, margin, upd in best}
        if len(seen) < MIN_AGGREGATORS:
            continue
        # EVERY ROW ON THE CAPTURE DATE, DELIBERATELY NOT BACKDATED.
        #
        # The `Dates updated` column is used above to pick the live table, and
        # it is tempting to file each row under its own update date as well.
        # That was the first version and it was wrong. Ohio's six aggregators
        # carry two different update dates -- five say 17 August, Silver
        # Bulletin says the 12th -- so the race's rows landed on two different
        # days, and aggregate.py groups by (date, category, race, quantity,
        # unit). The result was a "polling average" for Ohio on 12 August
        # consisting of Silver Bulletin alone, and another on the 17th
        # consisting of the other five. MIN_AGGREGATORS could not prevent it,
        # because the count is taken here, before the split.
        #
        # Backdating is right for a source that publishes a time series -- Race
        # to the WH ships a trend sheet, Ray Fair dates each forecast. This is
        # not one. It is one observation of what several forecasters were
        # saying at the moment we looked, and they refresh on their own
        # staggered schedules. The honest date for that observation is the day
        # we made it, and the staggered refresh is a property of the sources,
        # not a set of separate historical facts.
        rid = race_id("senate", st)
        for who, (margin, _upd) in sorted(seen.items()):
            rows.append(ctx.row(
                art, publication="private", source_id=who, category="polling",
                race_id=rid, chamber="senate", state=st,
                quantity="margin_D", value=margin, unit="pct"))
    return rows
