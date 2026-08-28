#!/usr/bin/env python3
"""Census: how many 2026 races actually carry a poll-AGGREGATION table?

    python3 forecast/collect/wiki_coverage_census.py
    python3 forecast/collect/wiki_coverage_census.py --chamber senate
    python3 forecast/collect/wiki_coverage_census.py --chamber house --verbose
    python3 forecast/collect/wiki_coverage_census.py --batch 10

WRITES NOTHING. Read-only against the public API, same Fetcher and the same
User-Agent the daily capture presents.

WHY THIS RUNS BEFORE THE PARSER IS WRITTEN, AND NOT AFTER

    The race-level polling design gives a race an observed margin only where
    an aggregator average exists, and falls back to the generic ballot pushed
    through partisan lean everywhere else. So the value of the whole exercise
    is the COVERED COUNT, and nothing else about the design can be judged
    until that number is on the table. If 40 Senate races and 60 House seats
    carry an aggregator table, the delta term is doing real work. If it is 12
    and 15, the map is PVI with decoration and the parser is not worth a day.

    wiki_race_probe.py measured cost and change rate. wiki_poll_shape.py
    measured markup shape. Neither measured coverage, which is the one that
    decides whether to build.

THE THREE TITLE SHAPES, ONE OF WHICH IS A TRAP

    senate     2026 United States Senate election in {State}
               ... except Florida and Ohio, which are SPECIAL elections and
               say so in the title. An earlier probe read 0 KB for Ohio and
               that was a wrong title, not a dead page.
    governor   2026 {State} gubernatorial election
    house      2026 United States House of Representatives elections in {State}

    THE HOUSE ONE IS THE TRAP. There is no article per district for most
    seats; the districts live as sections inside one article per state. So
    House coverage is not "how many of 435 articles have a table" but "how
    many district SECTIONS inside ~44 state articles have one", and a parser
    written against the Senate shape will find nothing in the House. That is
    exactly the kind of thing a census is for, so the section path of every
    table found is printed rather than summarised away.

TELLING THE TWO TABLE SHAPES APART

    wiki_poll_shape.py established that a general-election polling section
    holds two kinds of table, distinguishable by their FIRST HEADER CELL:

        "Source of poll aggregation"  ->  forecaster averages   <- what we want
        "Poll source"                 ->  individual polls

    Only the first is in scope. The second is the thing we decided not to
    parse, because individual polls drag in house effects, likely-voter
    adjustments and, before a primary, nominee-matchup selection.

WHY THE SECTION PATH IS CHECKED AND NOT JUST THE TABLE

    Every race article nests polling under a phase heading:

        == Republican primary  >  === Polling
        == Democratic primary  >  === Polling
        == General election    >  === Polling      <- the only usable one

    A parser that takes every polling table would silently average a
    Democratic primary poll into a D-vs-R margin. So a table counts here only
    when NO heading above it names a primary, runoff, caucus or convention.
    Tables that fail that test are counted separately rather than dropped, so
    the size of the trap is visible.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import urllib.parse
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "forecast" / "collect"))
import capture  # noqa: E402

API = "https://en.wikipedia.org/w/api.php"

STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
]

# Senate seats filled at a special election this cycle. The article title says
# "special election", and getting this wrong reads as a missing page.
SENATE_SPECIAL = {"Florida", "Ohio"}

_F = None


def fetcher():
    global _F
    if _F is None:
        reg = yaml.safe_load(open(REPO / "forecast" / "sources" / "2026.yaml",
                                  encoding="utf-8"))
        _F = capture.Fetcher(reg.get("contact") or {}, reg.get("defaults") or {})
    return _F


def _get(params: dict) -> dict:
    q = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    body, _meta = fetcher().get(f"{API}?{q}")
    return json.loads(body.decode("utf-8"))


def titles_for(chamber: str) -> dict[str, str]:
    """{state: article title} for one chamber."""
    if chamber == "senate":
        return {s: (f"2026 United States Senate "
                    f"{'special election' if s in SENATE_SPECIAL else 'election'}"
                    f" in {s}") for s in STATES}
    if chamber == "governor":
        return {s: f"2026 {s} gubernatorial election" for s in STATES}
    return {s: f"2026 United States House of Representatives elections in {s}"
            for s in STATES}


def contents(titles: list[str], batch: int) -> dict[str, str | None]:
    """{title: wikitext or None} — up to `batch` articles per request.

    ONE REQUEST PER `batch` TITLES, not one per title. The whole census is
    ~150 articles, which is six requests at batch 25 and 150 at batch 1. The
    first version of wiki_race_probe.py looped one call per title and earned
    a 429 on its opening move.

    `redirects=1` matters more than it looks: several of these titles redirect
    (a state that renamed its article, a special election folded into the
    regular one), and without following them the page reads as missing. The
    response's own title is used, not the requested one.
    """
    out: dict[str, str | None] = {}
    for i in range(0, len(titles), batch):
        chunk = titles[i:i + batch]
        try:
            d = _get({"action": "query", "prop": "revisions",
                      "rvprop": "content", "rvslots": "main",
                      "redirects": 1, "titles": "|".join(chunk)})
        except Exception as e:
            for t in chunk:
                out[t] = None
            print(f"    batch failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        q = d.get("query") or {}
        # Map every name the API rewrote back to what we asked for.
        alias = {}
        for k in ("normalized", "redirects"):
            for m in q.get(k) or []:
                alias[m["to"]] = m["from"]
        for pg in q.get("pages") or []:
            t = pg.get("title", "")
            asked = alias.get(t, t)
            # A redirect can chain: normalized -> redirected.
            while asked in alias:
                asked = alias[asked]
            if pg.get("missing"):
                out[asked] = None
                continue
            revs = pg.get("revisions") or []
            slot = (revs[0].get("slots", {}).get("main", {}) if revs else {})
            out[asked] = slot.get("content")
        for t in chunk:
            out.setdefault(t, None)
    return out


HEAD = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)
# NO TRAILING \b. Written as `\b(primar|...)\b` first, which never matched
# "Republican primary" at all, because \b after "primar" needs a non-word
# character and the next character is "y". A primary-phase aggregation table
# would then have been counted as a general-election one, which is the single
# error this whole check exists to prevent.
PHASE = re.compile(r"(?i)\b(primar\w*|runoff|caucus|convention|nomination)")
AGG_HEADER = re.compile(r"(?i)^\s*source\s+of\s+poll\s+aggregation")
POLL_HEADER = re.compile(r"(?i)^\s*(poll\s+source|pollster|polling\s+firm)")


def headings(wt: str) -> list[tuple[int, str, int]]:
    return [(len(m.group(1)), m.group(2), m.start()) for m in HEAD.finditer(wt)]


def path_at(tree: list[tuple[int, str, int]], off: int) -> list[str]:
    """The heading stack above a character offset, outermost first."""
    stack: list[tuple[int, str]] = []
    for lvl, title, start in tree:
        if start > off:
            break
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        stack.append((lvl, title))
    return [t for _, t in stack]


def tables(wt: str) -> list[tuple[int, str]]:
    """[(offset, table text)] for every wikitable, nesting-aware.

    Depth-counted rather than regex-matched, because these articles do nest
    tables (a {{Election box}} inside a results table) and a non-greedy
    `\\{\\|.*?\\|\\}` stops at the inner close, truncating the outer table
    just before the header row we need.
    """
    out, depth, start, lines = [], 0, 0, wt.splitlines(keepends=True)
    pos = 0
    for ln in lines:
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


def first_header_cell(tbl: str) -> str:
    """The text of a table's first header cell, stripped of markup."""
    for ln in tbl.splitlines():
        s = ln.strip()
        if not s.startswith("!"):
            continue
        cell = re.split(r"!!|\|\|", s[1:])[0]
        # A header cell may carry attributes before a single pipe.
        if "|" in cell and not cell.strip().startswith("["):
            cell = cell.split("|", 1)[1]
        cell = re.sub(r"<ref[^>]*>.*?</ref>", "", cell, flags=re.S)
        cell = re.sub(r"\{\{[^}]*\}\}|<[^>]+>|\[\[|\]\]|'''|''", " ", cell)
        cell = re.sub(r"\s+", " ", cell).strip()
        if cell:
            return cell
    return ""


NAME = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


# THE ROW SHAPE THESE ARTICLES ACTUALLY USE, which is not the one the wikitext
# manual leads you to expect. Cells are separated by a SINGLE pipe on ONE line:
#
#   |[[270toWin]] |July 27 - August 24, 2026 |August 27, 2026 |43.2% |46.0%
#
# not by `||`. So the forecaster is everything up to the first pipe, and a
# naive lstrip("|") returns the entire row. Two further traps sit in here: a
# piped link [[Decision Desk HQ|DDHQ]] carries a pipe that is NOT a cell
# boundary, and the Average row opens with `!` and HTML attributes rather than
# a plain pipe. The first census run reported `colspan="3" |Average` as one of
# the commonest "aggregators" in the Senate, which is not a forecaster at all.
CELL_ATTRS = re.compile(
    r'^\s*[a-zA-Z-]+\s*=\s*"[^"]*"(?:\s+[a-zA-Z-]+\s*=\s*"[^"]*")*\s*\|')
AVERAGE_ROW = ("average", "averages", "aggregate", "mean")

# ONE FORECASTER, TWO NAMES. The governor census reported "G. Elliott Morris"
# six times and "FiftyPlusOne" twice; they are the same outlet under its
# author's name and its masthead. Left unaliased, an average over the rows
# would weight that source by how each article's editor chose to label it.
ALIASES = {
    "g. elliott morris": "FiftyPlusOne",
    "fifty plus one": "FiftyPlusOne",
    "strength in numbers": "FiftyPlusOne",
    "realclearpolling": "RealClearPolitics",
    "ddhq": "Decision Desk HQ",
    "race to the white house": "Race to the WH",
    "270 to win": "270toWin",
}
MARKUP = re.compile(r"<[^>]+>|\{\{[^}]*\}\}|'{2,3}|\[\[|\]\]")
# A citation template inside a name cell is often NESTED or unterminated
# within the slice we hold, so the balanced pattern above misses it and
# "Silver Bulletin{{Cite web" reaches the report as a forecaster name.
# Anything from an unmatched {{ to the end of the cell is markup.
TAIL_TEMPLATE = re.compile(r"\{\{.*$", re.S)


def _first_cell(row: str) -> str:
    """The first cell of a table row, as plain text, or ''."""
    for ln in row.splitlines():
        s = ln.strip()
        if not s or s[0] not in "|!" or s.startswith("|-") or s.startswith("|}"):
            continue
        s = CELL_ATTRS.sub("", s[1:].strip()).strip()
        if s.startswith("[["):
            m = NAME.match(s)
            if m:
                return m.group(1).strip()
        s = TAIL_TEMPLATE.sub("", MARKUP.sub("", s.split("|", 1)[0]))
        return re.sub(r"\s+", " ", s).strip()
    return ""


def aggregators_in(tbl: str) -> tuple[list[str], bool]:
    """(forecaster names in row order, whether the table carries its own Average).

    THE AVERAGE ROW IS REPORTED, NOT COLLECTED, and that decides a design
    question rather than a display one. Wikipedia computes its own mean of the
    rows above it, over whichever forecasters an editor included and whichever
    dates they last updated. Our average has to be built from the constituent
    rows instead, because we need to control what goes into it: some of these
    sources carry publication tiers, some are downstream of each other, and at
    least one of them is already a line on our own tracker.
    """
    names, has_avg = [], False
    for row in re.split(r"^\|-", tbl, flags=re.M)[1:]:
        got = _first_cell(row)[:40]
        if not got:
            continue
        if got.lower() in AVERAGE_ROW:
            has_avg = True
            continue
        names.append(ALIASES.get(got.lower(), got))
    return names, has_avg


def survey(chamber: str, batch: int, verbose: bool) -> dict:
    want = titles_for(chamber)
    got = contents(list(want.values()), batch)

    present = agg_races = 0
    agg_tables = primary_tables = poll_tables = 0
    who: collections.Counter = collections.Counter()
    own_avg = 0
    covered: list[str] = []
    missing: list[str] = []
    paths: collections.Counter = collections.Counter()
    heads: collections.Counter = collections.Counter()

    for state, title in want.items():
        wt = got.get(title)
        if not wt:
            missing.append(state)
            continue
        present += 1
        tree = headings(wt)
        hits = []
        for off, tbl in tables(wt):
            head = first_header_cell(tbl)
            # EVERY header inside a polling section, matched or not. A zero
            # coverage count means one of two very different things -- nobody
            # publishes an aggregate for these races, or they do and this
            # detector does not recognise the header. Counting the headers we
            # REJECTED is what tells those apart, and it is the difference
            # between a finding and a bug.
            if any("poll" in h.lower() for h in path_at(tree, off)):
                heads[head[:58] or "(no header cell)"] += 1
            if AGG_HEADER.match(head):
                path = path_at(tree, off)
                if any(PHASE.search(p) for p in path):
                    primary_tables += 1
                    continue
                agg_tables += 1
                names, has_avg = aggregators_in(tbl)
                own_avg += int(has_avg)
                hits.append((path, names))
                who.update(names)
                paths[" > ".join(path[-2:]) or "(top level)"] += 1
            elif POLL_HEADER.match(head):
                poll_tables += 1
        if hits:
            agg_races += 1
            covered.append(state)
            if verbose:
                print(f"    {state}")
                for path, names in hits:
                    print(f"      {' > '.join(path) or '(top level)'}")
                    print(f"        {', '.join(names) or '(no names read)'}")

    return {"present": present, "missing": missing, "covered": covered,
            "agg_races": agg_races, "agg_tables": agg_tables,
            "primary_tables": primary_tables, "poll_tables": poll_tables,
            "who": who, "paths": paths, "n": len(want),
            "own_avg": own_avg, "heads": heads}


def report(chamber: str, r: dict, show_heads: bool = False) -> None:
    print(f"\n  {chamber.upper()}")
    print(f"    articles: {r['present']}/{r['n']} exist")
    print(f"    with a general-election aggregation table: {r['agg_races']}")
    print(f"    aggregation tables found: {r['agg_tables']}"
          f"   (excluded as primary-phase: {r['primary_tables']})")
    print(f"    individual-poll tables seen: {r['poll_tables']}")
    print(f"    tables carrying their own Average row: {r['own_avg']}"
          f"   (reported, never collected; we build our own)")
    if r["covered"]:
        print(f"    covered: {', '.join(r['covered'])}")
    if r["missing"]:
        m = r["missing"]
        print(f"    no article: {len(m)}"
              + (f"  ({', '.join(m[:12])}{' ...' if len(m) > 12 else ''})"))
    if r["paths"]:
        print("    section paths carrying a table:")
        for p, n in r["paths"].most_common(6):
            print(f"      {n:4d}  {p}")
    if show_heads and r["heads"]:
        print("    EVERY first-header-cell seen inside a polling section,")
        print("    matched or not — read this to tell a true zero from a miss:")
        for h, n in r["heads"].most_common(25):
            mark = ("AGG  " if AGG_HEADER.match(h)
                    else "polls" if POLL_HEADER.match(h) else "  ?  ")
            print(f"      {n:4d}  [{mark}] {h}")
    if r["who"]:
        print("    aggregators named:")
        for k, v in r["who"].most_common(15):
            print(f"      {v:4d}  {k}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chamber", nargs="+",
                    default=["senate", "governor", "house"],
                    choices=["senate", "governor", "house"])
    ap.add_argument("--batch", type=int, default=25,
                    help="articles per API request (max 50)")
    ap.add_argument("--headers", action="store_true",
                    help="print every first-header-cell found in a polling "
                         "section, matched or not")
    ap.add_argument("--verbose", action="store_true",
                    help="print every table's section path and row names")
    a = ap.parse_args(argv)

    print("=" * 78)
    print("wikipedia race-level COVERAGE census — writes nothing")
    print("=" * 78)
    print("  counts races whose GENERAL-ELECTION polling section carries a")
    print("  table headed 'Source of poll aggregation'. That count is the")
    print("  number of races the delta term can speak for; every other race")
    print("  falls back to the generic ballot through partisan lean.")

    out = {}
    for ch in a.chamber:
        if a.verbose:
            print(f"\n  --- {ch} ---")
        out[ch] = survey(ch, min(max(a.batch, 1), 50), a.verbose)
        report(ch, out[ch], a.headers)

    print("\n" + "=" * 78)
    tot = sum(r["agg_races"] for r in out.values())
    print(f"  TOTAL articles with a usable aggregation table: {tot}")
    print("\n  Read it this way. For the SENATE the article IS the race, so a")
    print("  covered count is a race count directly. For the HOUSE one article")
    print("  holds many districts, so read 'aggregation tables found' rather")
    print("  than 'articles with a table', and check the section paths above to")
    print("  see whether the tables sit under a district heading.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
