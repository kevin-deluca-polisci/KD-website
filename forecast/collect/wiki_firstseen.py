#!/usr/bin/env python3
"""
First-seen extractor: turn the Wikipedia revision archive into DATED EVENTS.

    python3 forecast/collect/wiki_firstseen.py --survey
    python3 forecast/collect/wiki_firstseen.py --survey --section-lines 8
    python3 forecast/collect/wiki_firstseen.py --extract
    python3 forecast/collect/wiki_firstseen.py --extract --page "2026 United States Senate elections"

Reads only what wiki_history.py already stored. Fetches nothing, writes
nothing under data/. Output goes to forecast/conditions/drafts/.

-----------------------------------------------------------------------------
WHAT IT IS FOR

forecast/conditions/ wants two things this project cannot currently see: when
each incumbent stopped being on the ballot, and when each redistricting plan
moved. Both are dates, and dates are exactly what a revision archive is made
of. We hold one revision per day for four pages back to 2025-01-20 — so for
any fact that ever appeared on those pages, we can ask when it FIRST appeared,
and that is a defensible first draft of when it became public.

The draft is the product. Hand-collecting these tables from scratch is a week;
correcting a generated table is an afternoon, and the corrections land on rows
that carry their own evidence.

-----------------------------------------------------------------------------
WHAT A FIRST-SEEN DATE IS, AND WHAT IT IS NOT

It is the first day OUR ARCHIVE holds a revision containing the fact. That is
three steps removed from the event:

    the thing happens
      -> someone reports it
        -> someone edits Wikipedia          <- `wiki_first_seen` dates THIS
          -> our archive holds that day's revision

Each step adds lag, and the last one adds lag we can measure: if the previous
revision we hold for that page is six days earlier, the true first appearance
could be anywhere in that window. Every row therefore carries
`_resolution_days`, the size of that window. A row with `_resolution_days = 1`
is a good date. A row with `_resolution_days = 9` is a date with a nine-day
error bar and should be checked against a primary source before anything
depends on it.

This is why the output goes to `known_by` and NOT to `event_date`.
`event_date` is left empty on purpose: we do not know when the thing happened,
only when the page said so, and writing the same date into both columns would
manufacture a fact the archive does not contain. Filling `event_date` in is
the human's job, from the press release.

-----------------------------------------------------------------------------
THE TWO FAILURE MODES IT IS BUILT AROUND

**Rewording.** Editors rewrite the same fact constantly. A line-text-keyed
extractor reports a new event every time somebody changes "is retiring" to
"will not seek re-election". So facts are keyed on the ENTITY — the person,
the race, and the kind of event — and the line text is kept only as evidence.

**Vandalism and error.** A fact that appears for one day and vanishes is
usually not a fact. So every row carries how many days it was present, whether
it ever disappeared, and a confidence grade derived from both. `low` rows are
emitted anyway, clearly marked, because the alternative is silently deciding
for you.

-----------------------------------------------------------------------------
RUN --survey FIRST

The patterns below are informed guesses at how these pages are written. Page
structure is not knowable from here without reading the pages, and a matcher
tuned against imagination is worth nothing. `--survey` prints, per page, the
dates held, the section headings that exist and when they appeared, and a
sample of the lines the patterns currently match. Read it, tune `TOPICS`,
re-run. That loop is the intended way to use this file, not an apology for it.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import difflib
import json
import os
import re
import unicodedata
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import capture                                   # noqa: E402
import parsers as P                              # noqa: E402

REPO = HERE.parents[1]
DATA = REPO / "forecast" / "data"
OUT = REPO / "forecast" / "conditions" / "drafts"

# THE RAW ARCHIVE IS NOT IN THE WEBSITE REPO. `forecast/data/.gitignore`
# excludes */raw/, and run.sh rsyncs the private clone in when it runs — so a
# working tree that has not had a full run.sh lately holds only the last day
# or two, while the actual 579-day archive sits in the private repo. The first
# survey of the real tree found two days and looked like a failed backfill.
# Both roots have the same shape, {root}/{cycle}/raw/wikipedia, so either one
# can be read directly.
RAW_REPO_DEFAULT = Path(
    os.environ.get("PLSC_RAW_REPO")
    or (Path.home() / "Documents" / "Claude" / "nondropbox data"
        / "plsc2219-raw"))

# The pages wiki_history.py backfills, and what each is mostly about. The
# chamber here is only a DEFAULT: a line that names a district wins over it.
PAGES = {
    "2026 United States House of Representatives election ratings": "house",
    "2026 United States House of Representatives elections": "house",
    "2026 United States Senate elections": "senate",
    "2026 United States gubernatorial elections": "governor",
}

# ---------------------------------------------------------------------------
# TOPICS. Ordered, and the order is load-bearing: "retiring to run for
# governor" is a `seeking_other_office` event, not a `retiring` event, so the
# more specific patterns are tested first and the first match wins.
# ---------------------------------------------------------------------------
CANDIDACY = [
    ("lost_primary", re.compile(
        r"lost (?:the |his |her |their )?(?:renomination|primary)"
        r"|defeated in the (?:\w+ )?primary"
        r"|primary (?:defeat|loss)", re.I)),
    ("died", re.compile(r"\bdied\b|\bdeath of\b|\bposthumous", re.I)),
    ("resigned", re.compile(r"\bresign(?:ed|ing|ation)\b", re.I)),
    ("withdrew", re.compile(
        r"\bwithdrew\b|\bwithdrawn from\b|\bdropped out\b"
        r"|ended (?:his|her|their) campaign", re.I)),
    ("seeking_other_office", re.compile(
        r"running for (?:the )?(?:U\.?S\.? )?(?:Senate|House|governor"
        r"|President|attorney general)"
        r"|seeking (?:the )?(?:office of )?(?:governor|Senate)"
        r"|candidate for (?:the )?(?:U\.?S\.? )?(?:Senate|governor)"
        r"|to run for", re.I)),
    ("retiring", re.compile(
        r"\bretir(?:e|es|ed|ing|ement)\b"
        r"|not (?:seek|seeking|running for|standing for) (?:re-?election"
        r"|another term|a (?:\w+ )?term)"
        r"|will not (?:seek|run)"
        r"|stepping down"
        r"|\bopen seat\b", re.I)),
]

REDISTRICTING = re.compile(
    r"redistrict|redraw|redrawn|new (?:congressional )?map"
    r"|mid-?decade|gerrymander|court-?ordered map", re.I)

# Section headings that are ABOUT these topics. When a page has them, only
# those sections are scanned, which kills most false positives. When it does
# not, the whole page is scanned and the confidence of everything drops.
# "incumbent" alone is too generous: the Senate page's rating tables live
# under "Potentially competitive seats > Republican incumbents", which is not
# a candidacy section, and scanning it produced footnote text about Al
# Franken's 2018 resignation as though it were a 2026 event. "Incumbents
# defeated" is kept because that heading is real and is where primary losses
# are recorded.
SEC_CANDIDACY = re.compile(
    r"retir|not seeking|open seat|vacan|member.*not return"
    r"|incumbents defeated|resignations?\b", re.I)
SEC_REDISTRICT = re.compile(r"redistrict|redraw|new maps?\b|boundar", re.I)

# Wikilink targets that are never a person.
NOT_A_PERSON = re.compile(
    r"congressional district|United States|election|Senate|House of"
    r"|Democratic|Republican|Party|Governor|County|\bClass \w+\b"
    r"|^\d{4}$|^[A-Z]{2}$", re.I)

_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
_DISTRICT = re.compile(
    r"\[\[([A-Za-z ]+?)'s (\d+)(?:st|nd|rd|th) congressional district", re.I)
_DISTRICT_ATL = re.compile(
    r"\[\[([A-Za-z ]+?)'s at-large congressional district", re.I)
_DISTRICT_SHORT = re.compile(r"\b([A-Z]{2})[--](\d{1,2})\b")

# {{ushr|CA|11|X}} — how the House elections page names a seat. Also the
# at-large form {{ushr|DC|AL|X}}.
_USHR = re.compile(r"\{\{\s*ushr\s*\|\s*([A-Za-z ]{2,20})\s*\|\s*(\d+|AL)\s*[|}]",
                   re.I)
# Same thing, but consuming the whole template, so display() can replace the
# seat with "CA-11" instead of leaving the template's tail behind.
_USHR_FULL = re.compile(
    r"\{\{\s*ushr\s*\|\s*([A-Za-z ]{2,20})\s*\|\s*(\d+|AL)\s*"
    r"(?:\|[^{}]*)?\}\}", re.I)

# | {{Party shading/Republican}} |David|Schweikert<br>(retiring)
# The ratings table writes a member as separate first/last cells with no
# wikilink at all, so the ordinary link-based person reader finds nothing.
_SHADED_NAME = re.compile(
    r"\{\{\s*Party shading/[^}]*\}\}\s*\|\s*([A-Z][A-Za-z.'’-]+)\s*\|\s*"
    r"([A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+)?)", re.I)

_STATE_ROW_NAME = re.compile(
    r"^\s*[!|]\s*([A-Za-z ]{4,25}?)\s*\|\s*([A-Z][A-Za-z.'\u2019-]+)\s*\|\s*"
    r"([A-Z][A-Za-z.'\u2019-]+(?:\s+[A-Z][A-Za-z.'\u2019-]+)?)\s*\|")

_REF = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^/>]*/>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TEMPLATE_SMALL = re.compile(r"\{\{(?:small|nowrap|sortname)\|([^}]*)\}\}", re.I)
# Footnotes are commentary, usually historical, and they are where "resignation
# of Al Franken" lives. Cite templates are metadata about a source, and their
# titles quote the very sentences we are matching on ("announces she will not
# seek re-election"), so leaving them in double-counts every event as itself
# plus its own headline.
_EFN = re.compile(r"\{\{\s*(?:efn|refn|sfn|cite[^|}]*)\|.*?\}\}", re.S | re.I)


# A STATUS TABLE, WHICH IS BETTER THAN PROSE.
# The House elections page keeps one row per state in its redistricting
# section, and the row carries a short status cell — "Litigation pending",
# "Previous districts left in place", "Pending legislative action", "TBD
# congressional map". That is the plan's state, written by hand, dated by the
# revision it appears in. A CHANGE in that cell is a redistricting event, and
# it is far more reliable than trying to infer "this passed the legislature"
# from the paragraph next to it.
#
# The vocabulary is not hard-coded, because guessing it wrong would silently
# drop states. Anything short that is not markup is treated as status text and
# reported verbatim for a human to map onto the status_after vocabulary in
# conditions/README.md.
_CELL_MARKUP = re.compile(
    r"data-sort-value|bgcolor|style\s*=|colspan|rowspan|align\s*=|^\s*$",
    re.I)


def status_cells(rec: str, state_name_len: int = 0) -> str:
    """The short, non-markup cells of a table row, joined. '' if none."""
    parts = [c.strip() for c in rec.split("|")]
    out = []
    for i, c in enumerate(parts):
        if not c or _CELL_MARKUP.search(c):
            continue
        if i == 0:                      # the state cell itself
            continue
        if len(c) > 60:                 # the description paragraph
            continue
        # Seat-count columns were added to this table part-way through the
        # cycle. "4 / 3 / 1" is not a status, and treating it as one turned
        # every column addition into a redistricting event.
        if re.fullmatch(r"[\d\s/.,%+-]+", c):
            continue
        out.append(c)
    return " / ".join(out[:3])


# ---------------------------------------------------------------------------
def choose_root(explicit: str | None, cycle: int) -> Path:
    """Pick the data root, and say out loud which one and why.

    Silently reading the emptier of two archives is the failure this function
    exists to prevent: the output is not an error, it is a short table, and a
    short table looks like a finding.
    """
    if explicit:
        return Path(explicit).expanduser()
    here = DATA
    there = RAW_REPO_DEFAULT
    n_here = _n_days(here, cycle)
    n_there = _n_days(there, cycle)
    if n_there > n_here:
        print(f"  data root: {there}")
        print(f"             ({n_there} day(s) there vs {n_here} in the "
              f"working tree — using the private raw clone)")
        return there
    print(f"  data root: {here} ({n_here} day(s))")
    return here


def _n_days(root: Path, cycle: int) -> int:
    d = root / str(cycle) / "raw" / "wikipedia"
    if not d.is_dir():
        return 0
    return sum(1 for p in d.iterdir()
               if p.is_dir() and re.fullmatch(r"\d{4}-\d\d-\d\d", p.name))


def _artifact_name(title: str) -> str:
    return capture.RawStore._slugify(f"current-{title}")


def wiki_days(cycle: int) -> list[str]:
    d = DATA / str(cycle) / "raw" / "wikipedia"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and re.fullmatch(r"\d{4}-\d\d-\d\d", p.name))


def wikitext(cycle: int, date: str, title: str) -> str | None:
    p = (DATA / str(cycle) / "raw" / "wikipedia" / date
         / f"{_artifact_name(title)}.json")
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    # Live capture and wiki_history both store the action=parse payload; be
    # forgiving about the two shapes MediaWiki uses for wikitext.
    txt = (payload.get("parse") or {}).get("wikitext")
    if isinstance(txt, dict):
        txt = txt.get("*", "")
    if not txt and isinstance(payload.get("wikitext"), str):
        txt = payload["wikitext"]
    return txt or None


def prepare(text: str) -> str:
    """Strip the parts of the page that are ABOUT the page rather than in it.

    This has to happen on the WHOLE TEXT before it is split into lines, and
    the first version did it per line, which silently did almost nothing: a
    <ref> holding a cite template wraps across four or five lines, so a
    line-scoped regex never matched the closing tag and every fragment stayed
    in. The survey showed the cost — the citation for Julia Brownley's
    retirement is titled "U.S. Rep. Julia Brownley announces she will not seek
    re-election", so the reference to the event matched the event patterns and
    became a second, fake event.
    """
    text = _REF.sub(" ", text)
    text = _COMMENT.sub(" ", text)
    for _ in range(3):                    # templates nest; a few passes is fine
        new = _EFN.sub(" ", text)
        if new == text:
            break
        text = new
    return text


def clean(line: str) -> str:
    line = _TEMPLATE_SMALL.sub(r"\1", line)
    return re.sub(r"[ \t]+", " ", line).strip()


def display(s: str) -> str:
    """What a reader sees: links resolved to their visible text.

    Classification runs on THIS, not on the wikitext. "#{{ushr|IL|2|X}}:
    [[Robin Kelly]] is retiring to [[2026 United States Senate election in
    Illinois|run for the U.S. Senate]]" is a `seeking_other_office` event, but
    in raw wikitext the words "retiring to" and "run for" are separated by a
    link target, so the more specific pattern cannot see itself and the row
    comes out as a plain retirement.
    """
    s = _LINK.sub(lambda m: (m.group(2) or m.group(1)), s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    # Any other stray tag. <wbr/> is invisible to a reader and was showing up
    # as a status CHANGE every time someone added a line-break hint to a table
    # cell — five fake redistricting events in South Carolina alone.
    s = re.sub(r"<[^>]{1,40}>", " ", s)
    # Render the seat template as the seat, so the evidence column a human
    # reviews still says which district the row is about.
    s = _USHR_FULL.sub(lambda m: f"{m.group(1).upper()}-{m.group(2)}", s)
    s = re.sub(r"\{\{[^{}]*\}\}", " ", s)
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"(\s*\|\s*)+", " | ", s)      # table cell runs
    return re.sub(r"\s+", " ", s).strip(" |")


# A record is an entry, not a paragraph. List items start with # or *, table
# cells with | or !. Summary prose ("As of August 2026, five governors have
# announced they will not seek reelection") matches every pattern we have and
# is a COUNT, not an event, so it is excluded unless --include-prose.
_ENTRY = re.compile(r"^\s*[#*|!;:]")


def records(lines: list[str]):
    """Group a section's lines into logical records.

    Wikitables put every cell on its own line, so the seat and the member of
    one row are on different lines and a line-scoped reader can never join
    them — which is exactly why the ratings page produced twelve retirements
    with no race attached to any of them. `|-` is the row separator, so a
    table row becomes one record and the join happens for free.
    """
    buf: list[str] = []
    for line in lines:
        if re.match(r"^\s*\|-", line) or re.match(r"^\s*\{\|", line) \
                or re.match(r"^\s*\|\}", line):
            if buf:
                yield " ".join(buf)
            buf = []
            continue
        if line.startswith(("|", "!")):
            buf.append(line)              # a cell: accumulate into the row
            continue
        if buf:
            yield " ".join(buf)
            buf = []
        yield line
    if buf:
        yield " ".join(buf)


def sections(text: str):
    """Yield (heading_path, [clean lines]).

    The path matters and the first version of this got it wrong. These pages
    are written as

        == Retirements ==
        === Democrats ===
        # ...district...: ...is retiring.
        === Republicans ===
        # ...

    so the heading immediately above almost every line we want is a party
    name, and a section filter matching "Retirements" against the immediate
    heading throws away the entire contents of the section it just matched.
    Yielding "Retirements > Democrats" makes the filter behave the way anyone
    reading the page would expect: a subsection of a retirements section is
    still about retirements.
    """
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def path() -> str:
        return " > ".join(h for _, h in stack)

    for raw in text.splitlines():
        m = re.match(r"\s*(={2,6})\s*(.+?)\s*\1\s*$", raw)
        if m:
            if buf:
                yield path(), buf
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2)))
            buf = []
            continue
        c = clean(raw)
        if c:
            buf.append(c)
    if buf:
        yield path(), buf


# ---------------------------------------------------------------------------
def person_from(line: str) -> str:
    for target, _shown in _LINK.findall(line):
        t = target.strip()
        # "[[Danny Davis (Illinois politician)|Danny Davis]]" — the
        # disambiguator is part of the article title, not part of the name.
        t = re.sub(r"\s*\([^)]*\)\s*$", "", t)
        if NOT_A_PERSON.search(t):
            continue
        if " " not in t:
            continue
        if not t[0].isupper():
            continue
        return t
    # Unlinked table form: {{Party shading/Republican}} |David|Schweikert
    m = _SHADED_NAME.search(line)
    if m:
        return f"{m.group(1)} {m.group(2)}".strip()
    # The governor and Senate tables use the same split-name convention but
    # lead with the state instead of a shading template:
    #   ! Minnesota | Tim | Walz | DFL | 2018 | ... | Incumbent retiring
    # Without this the row produces an event with no person, or worse picks
    # up the first linked name in the cell after it, which is a challenger.
    m = _STATE_ROW_NAME.search(line)
    if m and P.STATE_NAMES.get(m.group(1).strip().upper()):
        return f"{m.group(2)} {m.group(3)}".strip()
    return ""


# Seats whose holder is a non-voting delegate. They are not among the 435,
# nothing else in the archive has a race for them, and minting one would
# create a race no other source can ever match.
NON_VOTING = {"DC", "PR", "VI", "GU", "AS", "MP"}


def race_from(line: str, default_chamber: str, cycle: int) -> tuple[str, str]:
    """(race_id, why). Empty race_id is fine: the human fills it in."""
    m = _USHR.search(line)
    if m:
        raw = m.group(1).strip()
        st = raw.upper() if raw.upper() in P.POSTAL else \
            P.STATE_NAMES.get(raw.upper(), "")
        if st in NON_VOTING:
            return "", "non-voting delegate"
        if st:
            dist = m.group(2)
            try:
                if dist.upper() == "AL":
                    return P.race_id("house", st, 1, cycle), "ushr at-large (=01)"
                return P.race_id("house", st, dist, cycle), "ushr template"
            except ValueError:
                pass
    m = _DISTRICT.search(line)
    if m:
        st = P.STATE_NAMES.get(m.group(1).strip().upper())
        if st in NON_VOTING:
            return "", "non-voting delegate"
        if st:
            try:
                return P.race_id("house", st, m.group(2), cycle), "district link"
            except ValueError:
                pass
    m = _DISTRICT_ATL.search(line)
    if m:
        st = P.STATE_NAMES.get(m.group(1).strip().upper())
        if st in NON_VOTING:
            return "", "non-voting delegate"
        if st:
            # At-large seats are district 1 here, matching how the site's other
            # readers number them. Flagged in `why` so it can be checked.
            try:
                return P.race_id("house", st, 1, cycle), "at-large link (=01)"
            except ValueError:
                pass
    # "NY-17" IS ONLY A SEAT ON A HOUSE PAGE. On the Senate and governor
    # pages this pattern fires inside candidate prose — "U.S. Representative
    # Mike Lawler of NY-17 is widely seen as a potential challenger" — and
    # mints a House race for a sentence that is about a Senate primary. Six
    # rows in the first real run were this, including one that attached
    # Kathy Hochul to a New York House district by way of a sentence about
    # Andrew Cuomo.
    m = _DISTRICT_SHORT.search(line)
    if m and m.group(1) in P.POSTAL and default_chamber == "house":
        try:
            return P.race_id("house", m.group(1), m.group(2), cycle), "XX-NN"
        except ValueError:
            pass
    if default_chamber in ("senate", "governor"):
        st = P.state_from_text(line)
        if st:
            try:
                return P.race_id(default_chamber, st, cycle=cycle), "state name"
            except ValueError:
                pass
    return "", "not identified"


def classify(line: str) -> str:
    for kind, rx in CANDIDACY:
        if rx.search(line):
            return kind
    return ""


# ---------------------------------------------------------------------------
def person_key(name: str) -> str:
    """Accent- and case-insensitive key for one person.

    The House elections page writes "Chuy Garc\u00eda" and an editor once wrote
    "Chuy Garcia"; keyed on the raw string those are two people, and the
    second one shows up as a one-day fact graded `low`, which is the grade
    reserved for vandalism. Folding accents merges them and keeps the
    first-seen date of the earlier spelling.
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", n.lower()).strip()


class Fact:
    """One entity-keyed fact and the days it was visible."""

    __slots__ = ("key", "pages", "kind", "race_id", "race_why", "person",
                 "days", "first_line", "sections_seen")

    def __init__(self, key, page, kind, race_id, race_why, person, line, sec):
        self.key, self.kind = key, kind
        self.pages = {page}
        self.race_id, self.race_why, self.person = race_id, race_why, person
        self.days: list[str] = []
        self.first_line = line
        self.sections_seen = {sec}

    @property
    def first_seen(self) -> str:
        return self.days[0]

    @property
    def last_seen(self) -> str:
        return self.days[-1]


def scan(text: str, chamber: str, cycle: int, whole_page: bool,
         include_prose: bool):
    """One page on one day -> (candidacy hits, redistricting hits, skipped).

    Split out of the day loop so --survey and --extract see EXACTLY the same
    matches. A survey that disagreed with the extractor would be worse than no
    survey, because the tuning loop it exists to support would be tuning
    against the wrong thing.
    """
    secs = list(sections(prepare(text)))
    cand_secs = [(h, ls) for h, ls in secs if SEC_CANDIDACY.search(h)]
    red_secs = [(h, ls) for h, ls in secs if SEC_REDISTRICT.search(h)]
    if whole_page or not cand_secs:
        cand_secs = secs
    if whole_page or not red_secs:
        red_secs = secs

    cand, red = [], []
    skipped_prose = 0

    for h, lines in cand_secs:
        for rec in records(lines):
            shown = display(rec)
            if not shown:
                continue
            kind = classify(shown)
            if not kind:
                continue
            entry = _ENTRY.match(rec) or _USHR.search(rec)
            # A long sentence INSIDE a table cell passes the entry test on a
            # technicality — the record starts with "|" because the cell does.
            # Those cells are candidate-list paragraphs, and they are where a
            # person gets bolted onto a race they have nothing to do with.
            #
            # Length alone is the wrong test, and the first attempt at it took
            # out every Senate and governor row in the archive: those tables
            # carry a candidate plainlist in the last cell, so a perfectly
            # ordinary incumbent row runs to forty words. What separates the
            # two is SHAPE, not size. A table row is pipe-dense — state, name,
            # party, year, margin, status, each its own cell. A paragraph that
            # happens to live in a cell has one pipe, at the start.
            #
            # COUNT THE PIPES IN THE DISPLAY TEXT, NOT THE WIKITEXT. Every
            # piped wikilink [[target|shown]] contributes a pipe, so a
            # 121-word paragraph full of links counts as pipe-dense and
            # survives a filter written against the raw string. display() has
            # already resolved the links, so what is left is table structure.
            paragraph = (len(shown.split()) > 25
                         and shown.count("|") < 3
                         and not rec.lstrip().startswith(("#", "*")))
            if not include_prose and (not entry or paragraph):
                # "As of August 2026, five governors have announced they will
                # not seek reelection" is a count of events, not an event.
                skipped_prose += 1
                continue
            # Raw first, because that is where the wikilinks are and a linked
            # name is the best evidence of a person. Display second, because
            # the governor and Senate incumbent tables write the name as bare
            # template arguments — "{{sortname|Muriel|Bowser}}" — which only
            # looks like "| Muriel | Bowser |" once the template is resolved.
            person = person_from(rec) or person_from(shown)
            rid, why = race_from(rec, chamber, cycle)
            if not rid:
                rid, why = race_from(shown, chamber, cycle)
            if not person and not rid:
                continue                     # nothing to key on; not a fact
            cand.append((h, kind, person, rid, why, shown))

    stat = []
    for h, lines in red_secs:
        for rec in records(lines):
            shown = display(rec)
            if not shown or not REDISTRICTING.search(shown):
                continue
            red.append((h, shown))

    # The status table is scanned separately and WITHOUT the redistricting
    # keyword filter: "Litigation pending" does not contain the word
    # "redistricting", and the row it sits in is the whole point.
    for h, lines in red_secs:
        if not SEC_REDISTRICT.search(h):
            continue
        for rec in records(lines):
            if not rec.lstrip().startswith(("!", "|")):
                continue
            shown = display(rec)
            st = P.state_from_text(shown.split("|")[0]) if "|" in shown else None
            if not st:
                continue
            cells = status_cells(shown)
            if not cells:
                continue
            stat.append((st, cells, shown))

    return cand, red, skipped_prose, stat


def extract(cycle: int, only_page: str | None, whole_page: bool,
            include_prose: bool = False):
    days = wiki_days(cycle)
    if not days:
        return {}, {}, [], {}, []

    facts: dict[tuple, Fact] = {}
    redis_lines: dict[str, dict] = {}     # normalised line -> record
    status_now: dict[str, tuple[str, str]] = {}   # state -> (status, date)
    status_log: list[dict] = []
    page_days: dict[str, list[str]] = collections.defaultdict(list)

    for date in days:
        for title, chamber in PAGES.items():
            if only_page and title != only_page:
                continue
            text = wikitext(cycle, date, title)
            if text is None:
                continue
            page_days[title].append(date)
            cand, red, _, stat = scan(text, chamber, cycle, whole_page,
                                      include_prose)

            for h, kind, person, rid, why, shown in cand:
                # NOT keyed on the page. The same retirement appears on the
                # ratings page and on the elections page, and keying by page
                # reports it twice with two different dates — when what those
                # two sightings actually are is one fact with two witnesses,
                # whose true first-seen is the EARLIER of the two.
                key = (kind, rid, person_key(person))
                f = facts.get(key)
                if f is None:
                    f = facts[key] = Fact(key, title, kind, rid, why,
                                          person, shown, h)
                f.pages.add(title)
                f.sections_seen.add(h)
                if not f.race_id and rid:          # a better witness
                    f.race_id, f.race_why = rid, why
                if not f.days or f.days[-1] != date:
                    f.days.append(date)

            seen_today: set[str] = set()
            for st, cells, shown in stat:
                if st in seen_today:
                    # Some states appear in two tables on the same page. Taking
                    # both makes the status flip back and forth within a single
                    # day, which is how Wisconsin produced six "changes" across
                    # three dates. First row of the day wins.
                    continue
                seen_today.add(st)
                prev = status_now.get(st)
                if prev is None or prev[0] != cells:
                    status_log.append({
                        "state": st, "known_by": date, "status_raw": cells,
                        "previous_status_raw": prev[0] if prev else "",
                        "page": title, "evidence": shown[:300]})
                status_now[st] = (cells, date)

            for h, shown in red:
                norm = re.sub(r"[^a-z0-9 ]", "", shown.lower())[:300]
                r = redis_lines.get(norm)
                if r is None:
                    r = redis_lines[norm] = {
                        "page": title, "section": h, "line": shown,
                        "state": P.state_from_text(shown) or "",
                        "days": []}
                if not r["days"] or r["days"][-1] != date:
                    r["days"].append(date)

    return facts, redis_lines, days, page_days, status_log


# ---------------------------------------------------------------------------
def resolution(page_dates: list[str], first: str) -> int:
    """Days between the previous revision we hold and the one it appeared in.

    1 means consecutive days and a tight date. Larger means the fact could
    have appeared any time inside that window.
    """
    try:
        i = page_dates.index(first)
    except ValueError:
        return -1
    if i == 0:
        return -1                      # appeared in the first day we hold
    prev = dt.date.fromisoformat(page_dates[i - 1])
    return (dt.date.fromisoformat(first) - prev).days


def grade(days_present: int, gaps: int, res: int, has_race: bool,
          right_censored: bool = False) -> str:
    """high / medium / low / new.

    `new` exists because the first draft graded Cory Mills's primary loss
    `low` — the grade meaning "probably vandalism" — for the sole reason that
    it appeared on the last day the archive holds and so had had no chance to
    persist. A fact at the right-hand edge of the window is the one kind of
    one-day fact that is most likely to be true, and grading it as junk would
    have sent the newest events to the bottom of the review pile.
    """
    if days_present <= 1 and right_censored:
        return "new"
    if days_present <= 1:
        return "low"                   # one day only: usually vandalism
    if gaps > 0 or res > 7 or res < 0 or not has_race:
        return "medium"
    if days_present >= 3:
        return "high"
    return "medium"


def union_days(pages, page_days) -> list[str]:
    """Every day we hold any of the pages this fact was seen on."""
    out: set[str] = set()
    for pg in pages:
        out.update(page_days.get(pg, []))
    return sorted(out)


def right_censored(days: list[str], page_dates: list[str]) -> bool:
    """True when the fact was still present on (or next to) the newest day we
    hold, so its run was cut off by the edge of the archive rather than by
    anyone removing it."""
    if not days or not page_dates:
        return False
    return days[-1] in page_dates[-2:]


def gaps_in(days: list[str], page_dates: list[str]) -> int:
    """Times the fact vanished and came back, counted against the days we
    actually hold for that page rather than the calendar."""
    if not days:
        return 0
    idx = {d: i for i, d in enumerate(page_dates)}
    seq = sorted(idx[d] for d in days if d in idx)
    return sum(1 for a, b in zip(seq, seq[1:]) if b - a > 1)


# ---------------------------------------------------------------------------
def write_candidacy(facts, page_days, path: Path) -> int:
    cols = ["race_id", "person", "bioguide", "party", "is_incumbent",
            "event_type", "event_date", "known_by", "date_basis",
            "incumbent_on_ballot_after", "source_url", "notes",
            "_confidence", "_left_censored", "_right_censored",
            "_resolution_days", "_days_present", "_gaps", "_last_seen",
            "_pages", "_section", "_race_why", "_evidence"]
    rows = []
    for f in facts.values():
        pd_ = union_days(f.pages, page_days)
        res = resolution(pd_, f.first_seen)
        g = gaps_in(f.days, pd_)
        rc = right_censored(f.days, pd_)
        conf = grade(len(f.days), g, res, bool(f.race_id), rc)
        rows.append({
            "race_id": f.race_id,
            "person": f.person,
            "bioguide": "",
            "party": "",
            "is_incumbent": "",
            "event_type": f.kind,
            "event_date": "",                      # deliberately empty
            "known_by": f.first_seen,
            "date_basis": "wiki_first_seen",
            # The one field the models read. Every event_type here except
            # `filed`/`nominated`/`unretired` means the sitting member is off
            # the ballot, but "off the ballot" is a claim about a person, and
            # the extractor cannot always tell whose line it read. Left blank
            # rather than guessed.
            "incumbent_on_ballot_after": "",
            "source_url": "; ".join(
                f"https://en.wikipedia.org/wiki/{p_.replace(' ', '_')}"
                for p_ in sorted(f.pages)),
            "notes": "",
            "_confidence": conf,
            # The fact was ALREADY on the page in the oldest revision we hold,
            # so `known_by` is an upper bound and the truth is earlier —
            # a different thing from a loose window, and the one case where
            # the generated date is wrong in a known direction.
            "_left_censored": "yes" if res < 0 else "",
            # Still present at the newest day we hold: its run was cut off by
            # the edge of the archive, not by anyone removing it.
            "_right_censored": "yes" if rc else "",
            "_resolution_days": res,
            "_days_present": len(f.days),
            "_gaps": g,
            "_last_seen": f.last_seen,
            "_pages": "; ".join(sorted(f.pages)),
            "_section": "; ".join(sorted(x for x in f.sections_seen if x))[:120],
            "_race_why": f.race_why,
            "_evidence": f.first_line[:300],
        })
    order = {"new": 0, "high": 1, "medium": 2, "low": 3}
    rows.sort(key=lambda r: (order[r["_confidence"]], r["known_by"],
                             r["race_id"], r["person"]))
    _write_csv(path, cols, rows)
    return len(rows)


def write_redistricting(redis, page_days, path: Path) -> int:
    cols = ["plan_id", "event_date", "known_by", "date_basis", "event_type",
            "status_after", "in_effect_for_2026", "court_case", "source_url",
            "notes", "_state", "_confidence", "_left_censored",
            "_resolution_days", "_days_present", "_last_seen", "_page",
            "_section", "_evidence"]
    rows = []
    for r in redis.values():
        pd_ = page_days.get(r["page"], [])
        res = resolution(pd_, r["days"][0])
        conf = grade(len(r["days"]), gaps_in(r["days"], pd_), res, True)
        rows.append({
            "plan_id": "", "event_date": "", "known_by": r["days"][0],
            "date_basis": "wiki_first_seen", "event_type": "",
            "status_after": "", "in_effect_for_2026": "", "court_case": "",
            "source_url": f"https://en.wikipedia.org/wiki/"
                          f"{r['page'].replace(' ', '_')}",
            "notes": "", "_state": r["state"], "_confidence": conf,
            "_left_censored": "yes" if res < 0 else "",
            "_resolution_days": res, "_days_present": len(r["days"]),
            "_last_seen": r["days"][-1], "_page": r["page"],
            "_section": r["section"][:120], "_evidence": r["line"][:300],
        })
    rows.sort(key=lambda x: (x["known_by"], x["_state"]))
    _write_csv(path, cols, rows)
    return len(rows)


def write_status(status_log, page_days, path: Path) -> int:
    """One row per CHANGE in a state's redistricting status.

    This is the closest thing to a finished event table the archive can
    produce on its own: the status is a short controlled phrase somebody
    maintained by hand, and the date is the revision it changed in. What it
    still cannot do is decide which of our `status_after` values a phrase
    maps onto, or attach the change to a named plan, so both are left empty.
    """
    cols = ["state", "known_by", "status_raw", "previous_status_raw",
            "plan_id", "event_type", "status_after", "in_effect_for_2026",
            "date_basis", "source_url", "notes",
            "_resolution_days", "_looks_like_rewording", "_first_observation",
            "_page", "_evidence"]
    rows = []
    for e in status_log:
        pd_ = page_days.get(e["page"], [])
        res = resolution(pd_, e["known_by"])
        rows.append({
            "state": e["state"], "known_by": e["known_by"],
            "status_raw": e["status_raw"],
            "previous_status_raw": e["previous_status_raw"],
            "plan_id": "", "event_type": "", "status_after": "",
            "in_effect_for_2026": "", "date_basis": "wiki_first_seen",
            "source_url": f"https://en.wikipedia.org/wiki/"
                          f"{e['page'].replace(' ', '_')}",
            "notes": "",
            "_resolution_days": res,
            # Wording churn is not an event. "Previous districts left in
            # place" became "Districts left in place" with nothing happening
            # in the world, and a reviewer should be able to see that at a
            # glance rather than reading both strings.
            # 0.82, not 0.75: "Pending legislative action" ->
            # "...and referendum" scores 0.78 and IS a real change, while
            # "Previous districts left in place" -> "Districts left in place"
            # scores 0.84 and is not. The gap between those two is the whole
            # tolerance, so the flag is advisory and the row is never dropped.
            "_looks_like_rewording": (
                "yes" if e["previous_status_raw"] and difflib.SequenceMatcher(
                    None, e["previous_status_raw"].lower(),
                    e["status_raw"].lower()).ratio() >= 0.82 else ""),
            # The first row for a state is the status it ALREADY had when we
            # started watching, not a change. Marked, because reading it as an
            # event dated 2025-01-20 would invent a transition.
            "_first_observation": "yes" if not e["previous_status_raw"] else "",
            "_page": e["page"], "_evidence": e["evidence"],
        })
    rows.sort(key=lambda r: (r["state"], r["known_by"]))
    _write_csv(path, cols, rows)
    return len(rows)


def _write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
def survey(cycle: int, section_lines: int, whole_page: bool = False,
           include_prose: bool = False) -> int:
    days = wiki_days(cycle)
    print("=" * 72)
    print(f"survey · cycle {cycle} · {len(days)} day(s) in the wikipedia raw "
          f"tree")
    if days:
        print(f"         {days[0]} .. {days[-1]}")
    print("=" * 72)
    if not days:
        print("  nothing to survey — run wiki_history.py first")
        return 1

    for title, chamber in PAGES.items():
        have = [d for d in days if wikitext(cycle, d, title) is not None]
        print(f"\n-- {title}")
        print(f"   default chamber {chamber}")
        if not have:
            print("   NO REVISIONS HELD")
            continue
        span = ((dt.date.fromisoformat(have[-1])
                 - dt.date.fromisoformat(have[0])).days + 1)
        biggest = max(
            ((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days, a, b)
            for a, b in zip(have, have[1:])) if len(have) > 1 else (0, "", "")
        print(f"   {len(have)} day(s) held, {have[0]} .. {have[-1]} "
              f"({len(have) / span:.0%} of the span)")
        if biggest[0] > 1:
            print(f"   largest gap {biggest[0]} days ({biggest[1]} -> "
                  f"{biggest[2]}) — first-seen dates near there are that loose")

        # Headings, and when each first appeared. A section that shows up in
        # March is itself an event worth knowing about.
        first_heading: dict[str, str] = {}
        for d in have:
            for h, _ in sections(prepare(wikitext(cycle, d, title) or "")):
                first_heading.setdefault(h, d)
        cand = [h for h in first_heading if SEC_CANDIDACY.search(h)]
        red = [h for h in first_heading if SEC_REDISTRICT.search(h)]
        print(f"   {len(first_heading)} distinct heading(s) over the run")
        print(f"   candidacy-ish sections: "
              f"{', '.join(sorted(cand)[:6]) if cand else 'NONE — will scan whole page'}")
        print(f"   redistricting-ish sections: "
              f"{', '.join(sorted(red)[:6]) if red else 'NONE — will scan whole page'}")

        # The SAME code path --extract uses, so what you tune is what you get.
        latest = wikitext(cycle, have[-1], title) or ""
        cand, red, skipped, _stat = scan(latest, chamber, cycle, whole_page,
                                         include_prose)
        named = sum(1 for c in cand if c[3])
        print(f"   {len(cand)} candidacy match(es) on {have[-1]}, "
              f"{named} with a race id, {len(red)} redistricting line(s)")
        if skipped:
            print(f"   {skipped} prose match(es) skipped "
                  f"(summary sentences; --include-prose to keep them)")
        print(f"   sample:")
        for h, kind, person, rid, why, shown_text in cand[:section_lines]:
            print(f"     [{kind:<20}] race={rid or '-':<16} "
                  f"person={person or '-':<24} ({why})")
            print(f"       {shown_text[:150]}")
        if not cand:
            print("     none — the patterns need tuning against this page")
    print("\nTune TOPICS/SEC_* at the top of this file, then re-run --survey.")
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="First-seen event extraction from the Wikipedia revision "
                    "archive. Fetches nothing.")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--survey", action="store_true",
                    help="show what is held and what the patterns match. "
                         "Run this first.")
    ap.add_argument("--extract", action="store_true",
                    help="write the draft tables")
    ap.add_argument("--page", help="restrict to one page title")
    ap.add_argument("--whole-page", action="store_true",
                    help="ignore section filtering and scan everything. "
                         "More recall, much more noise.")
    ap.add_argument("--include-prose", action="store_true",
                    help="keep summary sentences ('five governors have "
                         "announced...'). They are counts, not events, and "
                         "are dropped by default.")
    ap.add_argument("--section-lines", type=int, default=12,
                    help="sample lines to print per page in --survey")
    ap.add_argument("--data-root",
                    help="root holding {cycle}/raw/wikipedia. Defaults to the "
                         "working tree, or the private raw clone when that "
                         "holds more days (see PLSC_RAW_REPO).")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)

    if not a.survey and not a.extract:
        a.survey = True

    global DATA
    DATA = choose_root(a.data_root, a.cycle)

    if a.survey:
        rc = survey(a.cycle, a.section_lines, a.whole_page,
                    a.include_prose)
        if not a.extract:
            return rc

    facts, redis, days, page_days, status_log = extract(
        a.cycle, a.page, a.whole_page, a.include_prose)
    out = Path(a.out)
    n1 = write_candidacy(facts, page_days, out / "candidacy_events_draft.csv")
    n2 = write_redistricting(redis, page_days,
                             out / "redistricting_events_draft.csv")
    n3 = write_status(status_log, page_days,
                      out / "redistricting_status_timeline.csv")

    conf = collections.Counter()
    censored = 0
    for f in facts.values():
        pd_ = union_days(f.pages, page_days)
        res = resolution(pd_, f.first_seen)
        conf[grade(len(f.days), gaps_in(f.days, pd_), res, bool(f.race_id),
                   right_censored(f.days, pd_))] += 1
        censored += 1 if res < 0 else 0

    print("\n" + "=" * 72)
    print("EXTRACT")
    print("=" * 72)
    print(f"  {len(days)} day(s) scanned")
    print(f"  {n1} candidacy fact(s): "
          f"{conf['high']} high · {conf['medium']} medium · {conf['low']} low"
          f" · {conf['new']} new")
    print(f"  {censored} left-censored (already on the page on day one)")
    print(f"  {n2} redistricting line(s)")
    print(f"  {n3} status change(s) across "
          f"{len({e['state'] for e in status_log})} state(s)")
    print(f"  -> {out / 'candidacy_events_draft.csv'}")
    print(f"  -> {out / 'redistricting_events_draft.csv'}")
    print(f"  -> {out / 'redistricting_status_timeline.csv'}")
    print("""
  HOW TO READ THE DRAFT

  Sort by _confidence. `low` means the fact was visible on one day only,
  which is usually an edit that got reverted — expect to delete most of
  those. `_resolution_days` is the error bar on `known_by`: 1 is a tight
  date, 9 means the fact could have appeared any time in a nine-day window
  because that is how far apart the revisions we hold are.

  `_left_censored = yes` is the one case where the date is wrong in a known
  direction: the fact was already on the page in the oldest revision we
  hold, so `known_by` is an upper bound and the real date is earlier —
  possibly before the term started. Those rows need a primary source or
  they need dropping; they must not be read as "announced on 2025-01-20".

  `event_date` is empty everywhere by design. `known_by` is when the page
  said it; `event_date` is when it happened, and only a primary source
  knows that. Fill it in for the rows that matter and change `date_basis`
  to `primary_source` when you do.

  The redistricting draft is deliberately less finished. It reports the
  lines that changed and when, and leaves plan_id, event_type and
  status_after empty, because inferring "this passed the legislature" from
  prose is exactly the kind of guess that would quietly become data.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
