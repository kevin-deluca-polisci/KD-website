#!/usr/bin/env python3
"""
Endorsements out of Wikipedia race articles.

    python3 forecast/collect/wiki_endorsements.py --self-test
    python3 forecast/collect/wiki_endorsements.py --plan --cycle 2026
    python3 forecast/collect/wiki_endorsements.py --probe "2026 United States Senate election in Texas"
    python3 forecast/collect/wiki_endorsements.py --probe-plan --cycle 2026 --limit 8

-----------------------------------------------------------------------------
WHAT IS ACTUALLY THERE

Every serious race article carries an endorsements block, and it is far larger
and far more regular than it looks. One 2026 gubernatorial article lists on the
order of 400 endorsers; a 2026 Senate article a few hundred; the per-state House
articles carry them district by district. Completed cycles keep theirs -- the
2024 Michigan Senate article still lists its ~150 -- so this is not only a 2026
instrument.

THE SHAPE, READ FROM THE MARKUP RATHER THAN FROM THE RENDERED PAGE

The first version of this parser inferred structure from how the article LOOKS
and returned zero rows from every real page while passing every synthetic test.
What renders as a heading is not a heading:

    ==== Endorsements ====
    {{Endorsements box
    | title=John Cornyn
    | colwidth=60
    | list=
    '''Executive branch officials'''
    * [[Susan Combs]], former U.S. assistant secretary of the interior<ref>..</ref>
    '''U.S. senators'''
    * [[John Barrasso]], Senate majority whip from Wyoming<ref>..</ref>
    }}

The candidate is a TEMPLATE PARAMETER and the endorser category is BOLD TEXT
inside another parameter. One 2026 Senate article carries thirteen of these.

This is better than the heading structure I guessed at, for two reasons. The
candidate name arrives as a named field rather than being inferred from
position, so there is nothing to get wrong; and confining the parser to the
inside of an `Endorsements box` makes a whole class of false positive
impossible. Those articles carry "==== Withdrawn ====" and "==== Declined ===="
headings that are about candidates who left or never entered the RACE, and any
parser keying on those words in headings would file a list of non-candidates as
a list of non-endorsers. Inside the box, '''Declined to endorse''' means what
it says; outside it, the same word means something else entirely.

Headings still matter, for context rather than content: which primary a box
sits under, whether it is the first round or the runoff or post-primary, and on
House articles which district. That comes from the heading path at the box's
own offset in the page.

WHY THE ENDORSER LINK MATTERS MORE THAN THE ENDORSER NAME

85-90% of entries are wikilinks. `[[John Barrasso|Barrasso]]` and
`[[John Barrasso]]` are one entity with one stable key -- the link TARGET -- and
that key is the same in 2020, 2024 and 2026 and on every page. Keying on
display text instead would fragment a single endorser across articles and make
any panel of "groups that endorse across cycles" mostly an artefact of how
editors happened to write the link. The unlinked 10-15% fall back to a folded
name key and are marked so they can be audited separately.

DATES, AND WHAT THIS FILE DOES NOT PROMISE

Endorsement entries carry no inline date. Two recoverable sources, in order:

  1. the citation. `<ref>{{cite news |date=March 4, 2026 |...}}</ref>` is the
     publication date of the story announcing it, which is within a day or two
     of the endorsement itself. Cheap, and it is the date a reader would give.
  2. revision first-seen. wiki_history.py already reconstructs one revision per
     day for the pages in the registry; the day an entry first appears bounds
     the endorsement from above. Expensive, exact, and it is what makes a row
     `archival` rather than `retrospective` under score/RULES.md.

This module extracts (1) and is shaped so that (2) needs no new parser: run it
over the dated captures wiki_history writes and diff the row sets by day. That
is deliberately NOT done here -- get the extraction right against live pages
first, then turn the crank over history.

NOTHING HERE FETCHES ON IMPORT. --probe uses the project's own Fetcher, so it
inherits the rate limit, user agent and contact details of the daily capture.
Wikipedia is CC BY-SA: the derived table may be published with attribution,
which is a licence this archive does not have for most of what it holds.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wiki_firstseen as wf          # noqa: E402  prepare/sections/display/clean

REPO = HERE.parents[1]
# Plausibility needs "now", and it has to be injectable so the self-test is not
# a function of the day it runs on.
_TODAY = __import__("datetime").date.today().isoformat()

# ---------------------------------------------------------------------------
# The closed vocabulary. Everything downstream hangs off this, so it is data
# and not a regex. Collected by reading Senate, House and gubernatorial
# articles across states; add to it rather than loosening the matcher.
CATEGORIES = {
    "executive branch officials": "executive",
    "federal officials": "executive",
    "u.s. senators": "us_senator",
    "us senators": "us_senator",
    "u.s. representatives": "us_representative",
    "us representatives": "us_representative",
    "statewide officials": "statewide",
    "state officials": "statewide",
    "governors": "statewide",
    "state legislators": "state_legislator",
    "local officials": "local",
    "county officials": "local",
    "municipal officials": "local",
    "mayors": "local",
    "party officials": "party_official",
    "party chapters": "party",
    "political parties": "party",
    "parties": "party",
    "state parties": "party",
    "state party chapters": "party",
    "tribal nations": "tribal",
    "tribal governments": "tribal",
    "labor unions": "labor",
    "unions": "labor",
    "organizations": "organization",
    "interest groups": "organization",
    "political action committees": "organization",
    "businesses": "organization",
    "newspapers": "newspaper",
    "newspapers and media": "newspaper",
    "media": "newspaper",
    "publications": "newspaper",
    "individuals": "individual",
    "activists": "individual",
    "celebrities": "individual",
    "athletes": "individual",
    "academics": "individual",
    "former officials": "executive",
}
# Same vocabulary level, opposite meaning. These are the reason a naive count
# of bullets under a candidate is wrong rather than merely noisy.
STANCES = {
    # Bare, which the 2024 sweep found inside boxes. Safe ONLY because both
    # extraction paths are already scoped to an endorsements context: the
    # template path reads nothing outside an Endorsements box, and the heading
    # path reads nothing outside a section with "endors" in its heading path.
    # In a candidate list these same two words mean something else entirely,
    # and neither path can see a candidate list.
    "withdrawn": "withdrawn",
    "declined": "declined",
    "declined to endorse": "declined",
    "declined to endorse a candidate": "declined",
    "withdrawn endorsement": "withdrawn",
    "withdrawn endorsements": "withdrawn",
    "rescinded endorsement": "withdrawn",
    "former endorsements": "withdrawn",
}
# Headings that are structure, never a candidate, even when they sit directly
# above a category block.
NOT_A_CANDIDATE = {
    "endorsements", "general election", "primary election", "campaign",
    "candidates", "declared", "nominee", "results", "polling", "see also",
    "references", "notes", "external links", "background",
}

# Labels whose bullets are CANDIDATES, not endorsers. The 2024 sweep found
# "Eliminated in primary" used as a bold line inside an endorsements section --
# a candidate roster smuggled into the one place the guards do not protect.
# Three rows, and each one a person filed as endorsing themselves.
NOT_ENDORSERS = {"eliminated in primary", "eliminated in runoff", "nominee",
                 "nominees", "candidates", "declared", "advanced to runoff"}

_REF_BLOCK = re.compile(r"<ref[^>/]*>(.*?)</ref>", re.S | re.I)
_COMMENT_TAG = re.compile(r"<!--.*?-->", re.S)
_DATE_PARAM = re.compile(r"\|\s*date\s*=\s*([^|}\n]+)", re.I)
_ACCESS_PARAM = re.compile(r"\|\s*access-?date\s*=\s*([^|}\n]+)", re.I)
_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
_BULLET = re.compile(r"^(\*+)\s*(.+)$")
_DISTRICT = re.compile(
    r"(?:district\s+(\d{1,2})\b"
    r"|(\d{1,2})(?:st|nd|rd|th)\s+congressional district"
    r"|\bat-large\b)", re.I)
_PARTY_SUFFIX = re.compile(r"\s*\((D|R|I|L|G|DFL|Dem|Rep)\)\s*$", re.I)
_ENDORSER_PARTY = re.compile(
    r"\((Democratic|Republican|Independent|Libertarian|Green)\)\s*$", re.I)

# A state with one at-large seat has no district heading on its House article,
# because there is no district to head. Read as "no district", every one of
# those rows is unjoinable to a race -- 135 of them in the first full sweep,
# which is Alaska 58, Wyoming 31, Delaware 21, Vermont 16, South Dakota 6 and
# North Dakota 3, exactly. They are district 00, the same convention the
# conditions sheet settled on.
AT_LARGE = {"Alaska", "Delaware", "North Dakota", "South Dakota",
            "Vermont", "Wyoming"}

STATES = {
 "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
 "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA",
 "Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA",
 "Kansas":"KS","Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD",
 "Massachusetts":"MA","Michigan":"MI","Minnesota":"MN","Mississippi":"MS",
 "Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV",
 "New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM","New York":"NY",
 "North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK",
 "Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC",
 "South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT",
 "Virginia":"VA","Washington":"WA","West Virginia":"WV","Wisconsin":"WI",
 "Wyoming":"WY",
}


# ---------------------------------------------------------------------------
def entity_key(target: str | None, shown: str) -> tuple[str, bool]:
    """Stable key for one endorser, and whether it came from a link.

    The link target is preferred for the reason in the module docstring: it is
    the same string on every page in every cycle. Unlinked entries get a folded
    name key and are flagged, because they are the ones that will fragment.
    """
    raw = target or shown or ""
    raw = raw.split("#")[0].strip()
    n = unicodedata.normalize("NFKD", raw)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9 ]", " ", n.lower())
    return re.sub(r"\s+", " ", n).strip(), target is not None


def ref_dates(line: str) -> tuple[str | None, str | None]:
    """(published, accessed) out of the citations on ONE line, before stripping.

    Deliberately reads the raw line, because wf.prepare() removes every <ref>
    from the whole text -- correctly, for its own purposes -- and the dates go
    with them. Multi-line refs are handled by the caller joining first.
    """
    pub = acc = None
    # An editor can leave an HTML comment where the date goes:
    #   |date=<!-- not stated -->
    # Read raw, that becomes the date string. wf.prepare() removes comments,
    # but this runs BEFORE prepare, deliberately, because prepare also removes
    # the refs these dates live in.
    line = _COMMENT_TAG.sub(" ", line)
    for body in _REF_BLOCK.findall(line):
        # `|date=` with nothing after it is not a date. Stripping the comment
        # above leaves exactly that, and an empty string is truthy enough
        # downstream to be reported as an unparseable date forever.
        if pub is None:
            m = _DATE_PARAM.search(body)
            if m and m.group(1).strip():
                pub = m.group(1).strip()
        if acc is None:
            m = _ACCESS_PARAM.search(body)
            if m and m.group(1).strip():
                acc = m.group(1).strip()
    return pub, acc


def date_flag(iso: str | None, cycle: int | None, today: str) -> str | None:
    """Is this citation date possible for this cycle? Flag, never silently drop.

    A full 2026 sweep produced a date range of 2019-11-08 .. 2026-12-30. Both
    ends are impossible as endorsement dates: the first is a citation used for
    background, the second is four months in the future, i.e. a typo on the
    page. Two rows out of eleven thousand, which is exactly the size of problem
    that survives every eyeball and then decides a first-seen comparison.

    Flagged rather than nulled, because the raw string is evidence and the
    right response differs by use -- a scoring run should exclude them, a page
    listing endorsements should probably still show them.
    """
    if not iso:
        return None
    if iso > today:
        return "future"
    # THREE YEARS, NOT TWO. The first sweep flagged 36 rows as impossible and
    # they were not: Eleni Kounalakis and Toni Atkins both declared for
    # governor of California in 2023, so Hillary Clinton endorsing Kounalakis
    # in May 2023 is a real 2026-cycle endorsement. An open-seat governor's
    # race runs three years and the window has to admit that. What remains
    # flagged is genuinely wrong -- a 2019 citation to background material.
    if cycle and iso < f"{cycle - 3}-01-01":
        return "pre_cycle"
    # AND NOT AFTER THE ELECTION EITHER. Checking only against "today" is right
    # for a live cycle and useless for a finished one: the 2024 sweep returned
    # citation dates in 2026, which are real dates for real pages and cannot
    # possibly be 2024 endorsements. Early November of the cycle year is close
    # enough; nothing legitimate sits in the gap.
    if cycle and iso > f"{cycle}-11-30":
        return "post_election"
    return None


_TRAILING_PAREN = re.compile(r"\s*\(([^()]{1,24})\)\s*$")
_PARTY_WORD = re.compile(r"^(D|R|I|L|G|DFL|Dem|Rep|Democratic|Republican|"
                         r"Independent|Libertarian|Green)$", re.I)


def split_heading(h: str) -> tuple[str, str | None]:
    """'Xavier Becerra (D)' -> ('Xavier Becerra', 'D').

    Titles carry MORE than a party, and stripping only the party left the rest
    glued to the name: the first full sweep produced candidates literally named
    "Toni Atkins (D) (withdrew)" and "Will Ainsworth (declined)", which are
    three different keys for two people and would never join to anything.
    So peel every trailing parenthetical, and keep whichever one is a party.
    """
    party, name = None, h.strip()
    while True:
        m = _TRAILING_PAREN.search(name)
        if not m:
            break
        inner = m.group(1).strip()
        if _PARTY_WORD.match(inner):
            party = inner.upper()[:1]
        name = name[:m.start()].strip()
    return name, party


def _brace_blocks(text: str, name: str):
    """Yield (offset, full wikitext) of every {{name ...}} call.

    Brace-matched rather than regex-bounded, because an Endorsements box holds
    a few hundred nested {{cite web}}, {{ushr}} and {{efn}} calls and a
    non-greedy match stops at the first of them.
    """
    pat = re.compile(r"\{\{\s*" + re.escape(name) + r"\s*[|\n]", re.I)
    for m in pat.finditer(text):
        i, j, depth = m.start(), m.start(), 0
        while j < len(text):
            if text.startswith("{{", j):
                depth += 1
                j += 2
                continue
            if text.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
                continue
            j += 1
        yield i, text[i:j]


def _params(block: str) -> dict[str, str]:
    """Top-level named parameters of one template call.

    Splits on pipes at depth zero only. Wikilinks contain pipes
    ([[Texas Attorney General|attorney general]]) and so does every cite
    template, so a naive split shreds the list into fragments -- the same
    pipe-counting mistake the conditions extractor made on wikitext.
    """
    body = block[2:-2] if block.endswith("}}") else block[2:]
    parts, buf, curly, square = [], [], 0, 0
    i = 0
    while i < len(body):
        two = body[i:i + 2]
        if two == "{{":
            curly += 1; buf.append(two); i += 2; continue
        if two == "}}":
            curly -= 1; buf.append(two); i += 2; continue
        if two == "[[":
            square += 1; buf.append(two); i += 2; continue
        if two == "]]":
            square -= 1; buf.append(two); i += 2; continue
        if body[i] == "|" and curly == 0 and square == 0:
            parts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(body[i]); i += 1
    parts.append("".join(buf))
    out = {}
    for p in parts[1:]:                      # parts[0] is the template name
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        out[k.strip().lower()] = v.strip()
    return out


# HOW A CATEGORY IS MARKED INSIDE THE BOX, WHICH IS NOT ONE THING.
#
# The Texas article uses a bold line on its own. Alabama, Alaska and Arizona do
# not, and the first version of this parser filed every one of their rows as
# `unspecified` -- 537 rows across eight pages with not a single category
# between them, while Texas came back with eleven. That is the shape of a
# matcher that is too narrow, and it was silent: an uncategorised row still
# looks like data.
#
# It was not merely cosmetic. "Declined to endorse" is marked exactly the way a
# category is, so a page whose categories go unrecognised is a page whose
# NON-endorsements are counted as endorsements. Zero declined rows across eight
# articles was the tell.
#
# So: accept every form editors actually use for the same visual result.
_CAT_HEADING = re.compile(r"^\s*(={2,6})\s*(.+?)\s*\1\s*$")
_CAT_DEFLIST = re.compile(r"^\s*;\s*(.+?)\s*:?\s*$")            # ;Term
_CAT_BOLD = re.compile(
    r"^\s*(?:\*+\s*)?"                    # sometimes bulleted, sometimes not
    r"(?:'''|<b>)\s*(.+?)\s*(?:'''|</b>)" # bold, wiki markup or html
    r"\s*:?\s*$")                         # sometimes with a trailing colon


def category_line(raw: str) -> str | None:
    """The category this line declares, or None if it is not one.

    Order matters. A heading inside a template parameter is unambiguous, a
    definition-list term is unambiguous, and only then is a wholly-bold line
    considered -- because a bullet whose ENTIRE content is bold is a category
    label, while a bullet with bold inside it is an entry.
    """
    for pat, grp in ((_CAT_HEADING, 2), (_CAT_DEFLIST, 1), (_CAT_BOLD, 1)):
        m = pat.match(raw)
        if m:
            lab = m.group(grp).strip().strip("'").strip()
            # A category never contains a link. Without this, an entry written
            # as * '''[[Donald Trump]]''' would be read as a category and would
            # both vanish as a row and mislabel every row after it.
            if "[[" in lab or not lab:
                return None
            return lab
    return None


def _walk_list(body: str):
    """Yield (category_label, bullet_depth, entry) down one `list=` parameter.

    A category applies to every bullet after it until the next one. An entry
    before any category is emitted with label None rather than dropped; some
    boxes open straight into a list.
    """
    label = None
    for raw in body.splitlines():
        cat = category_line(raw)
        if cat is not None:
            label = cat
            continue
        m = _BULLET.match(raw.strip())
        if m:
            yield label, len(m.group(1)), m.group(2).strip()


_MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November",
                "December"]
_MONTHS = {m.lower(): i for i, m in enumerate(_MONTH_NAMES, 1)}
# "Mar 1, 2026" and "Dec 5, 2025" both appeared in the first sweep. Three
# letters is unambiguous for every month, so accept the abbreviation rather
# than throwing the date away.
_MONTHS.update({m[:3].lower(): i for i, m in enumerate(_MONTH_NAMES, 1)})
_MONTHS["sept"] = 9
_MY = re.compile(r"^([A-Za-z]+)\s+(\d{4})$")
_DMY = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")
_MDY = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$")
_ISO_D = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def iso_date(s: str | None) -> str | None:
    """Citation dates arrive in at least three formats. Normalise, or give up.

    Seen in one nine-page probe: "March 28, 2026", "12 January 2026", and ISO.
    Left mixed, any sort or any first-seen comparison is quietly wrong, so the
    raw string is kept alongside and THIS is the field anything compares.
    """
    if not s:
        return None
    s = re.sub(r"\s+", " ", s.strip().strip(","))
    if _ISO_D.match(s):
        return s
    m = _DMY.match(s)
    if m and m.group(2).lower() in _MONTHS:
        return (f"{m.group(3)}-{_MONTHS[m.group(2).lower()]:02d}"
                f"-{int(m.group(1)):02d}")
    m = _MDY.match(s)
    if m and m.group(1).lower() in _MONTHS:
        return (f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}"
                f"-{int(m.group(2)):02d}")
    # A MONTH WITH NO DAY IS STILL WORTH MORE THAN NOTHING, but it must never
    # be mistaken for a precise date. It resolves to the first of the month and
    # the row records precision="month", so anything comparing dates can widen
    # its tolerance instead of silently believing the 1st.
    m = _MY.match(s)
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]:02d}-01"
    return None


def date_precision(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"\s+", " ", raw.strip().strip(","))
    if _MY.match(s):
        return "month"
    return "day" if iso_date(s) else None


def heading_index(text: str) -> list[tuple[int, str]]:
    """[(offset, heading path)] so a template can be located in the article."""
    out, stack = [], []
    for m in re.finditer(r"^[ \t]*(={2,6})[ \t]*(.+?)[ \t]*\1[ \t]*$",
                         text, re.M):
        level, name = len(m.group(1)), m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, name))
        out.append((m.end(), " > ".join(h for _, h in stack)))
    return out


def path_at(index: list[tuple[int, str]], offset: int) -> str:
    """The heading path in force at a byte offset."""
    best = ""
    for off, p in index:
        if off <= offset:
            best = p
        else:
            break
    return best


# NEAR-MISSES, WHICH ARE MOST OF WHAT IS LEFT.
#
# A full 2026 sweep left 47 rows uncategorised across 11,234, and not one of
# them was a category the vocabulary had never heard of. They were
# "Labor union" (singular), "U.S senators" (a missing full stop),
# "Statewide elected officials", "State representatives", "Sheriffs",
# "Party branches" and "Organizaitons" (an outright typo on the page).
#
# Adding twelve more exact strings would fix those twelve and fail on the next
# twelve. The exact table stays as the authority -- it is what makes the common
# cases auditable -- and this runs only when the table misses.
#
# ORDER IS THE WHOLE THING HERE. "State representatives" contains
# "representative", so the state-legislature rules have to be tried before the
# congressional ones or every state house member becomes a member of Congress.
# Likewise "party officials" contains "party".
_FUZZY = [
    (("tribal", "tribe"), "tribal"),
    (("judic", "judge", "justice", "district attorney", "prosecutor"),
     "judicial"),
    (("party official", "party chair", "party leader"), "party_official"),
    (("state senator", "state legislat", "state representative",
      "state house", "state assembly", "legislator", "delegate",
      "state committee"), "state_legislator"),
    (("sheriff", "mayor", "county", "municipal", "city council",
      "alder", "alderman", "selectman", "local"), "local"),
    (("labor", "union"), "labor"),
    (("newspaper", "media", "publication", "editorial"), "newspaper"),
    (("senator",), "us_senator"),
    (("representative", "congress"), "us_representative"),
    (("statewide", "state official", "state elected", "governor",
      "attorney general", "lieutenant"), "statewide"),
    (("executive", "cabinet", "federal official", "secretary",
      "ambassador", "president", "white house", "administration"),
     "executive"),
    (("part", "caucus"), "party"),
    (("organiz", "interest group", "advocacy", "pac", "committee",
      "association", "business"), "organization"),
    (("individual", "activist", "celebrit", "athlete", "academic",
      "author", "actor", "musician"), "individual"),
]


def fuzzy_category(key: str) -> str | None:
    for needles, kind in _FUZZY:
        if any(nd in key for nd in needles):
            return kind
    return None


def classify_label(label: str | None) -> tuple[str, str]:
    """A bold line inside the box -> (category, stance)."""
    if not label:
        return "unspecified", "endorsed"
    key = re.sub(r"\s+", " ", label.lower()).strip().rstrip(":")
    if key in STANCES:
        return "unspecified", STANCES[key]
    for k, v in STANCES.items():
        if key.startswith(k):
            return "unspecified", v
    hit = CATEGORIES.get(key)
    if hit:
        return hit, "endorsed"
    # The exact table is the authority. This only runs when it misses, and it
    # is reported separately so the table can absorb whatever recurs.
    return fuzzy_category(key) or "other", "endorsed"


def context(path: str) -> dict:
    """District, primary and phase out of the heading path around a box."""
    parts = [p.strip() for p in path.split(" > ") if p.strip()]
    district = None
    for p in parts:
        m = _DISTRICT.search(p)
        if m:
            if "at-large" in p.lower():
                district = "00"
            elif m.group(1) or m.group(2):
                district = f"{int(m.group(1) or m.group(2)):02d}"
            break
    primary = None
    for p in parts:
        pl = p.lower()
        if "primary" in pl and "post-primary" not in pl:
            primary = ("D" if "democrat" in pl else
                       "R" if "republican" in pl else "other")
            break
    low = path.lower()
    # WHEN, roughly. A runoff endorsement and a post-primary endorsement are
    # different events from a first-round one and should never be pooled with
    # them silently -- the candidate set is different in each.
    phase = ("runoff" if "runoff" in low else
             "general" if "post-primary" in low or "general" in low else
             "primary" if primary else "unknown")
    return {"district": district, "primary": primary, "phase": phase}


def page_meta(title: str) -> dict:
    """Chamber, state and cycle out of the article title alone."""
    t = title.strip()
    year = None
    m = re.match(r"(\d{4})\b", t)
    if m:
        year = int(m.group(1))
    chamber = None
    if "House of Representatives" in t:
        chamber = "house"
    elif "Senate election" in t or "Senate elections" in t:
        chamber = "senate"
    elif "gubernatorial" in t:
        chamber = "governor"
    elif "presidential" in t:
        chamber = "president"
    state = None
    for name, ab in STATES.items():
        if re.search(rf"\b{re.escape(name)}\b", t):
            state = ab
            break
    at_large = any(re.search(rf"\b{re.escape(n)}\b", t) for n in AT_LARGE)
    return {"cycle": year, "chamber": chamber, "state": state,
            "at_large": at_large and chamber == "house",
            "special": "special" in t.lower()}


def _row(entry: str, title: str, meta: dict, ctx: dict, cand: str,
         party: str | None, kind: str, label: str | None, stance: str,
         depth: int, dated: dict, seen_keys: list, via: str) -> dict | None:
    """One bullet -> one row. Shared by both extraction paths on purpose.

    Two implementations of this would drift, and the fields it fills -- the
    entity key, the date join, the duplicate flag -- are exactly the ones whose
    subtle disagreement would be invisible in the output.
    """
    link = _LINK.search(entry)
    target = link.group(1).strip() if link else None
    shown = wf.display(entry)
    if not shown or len(shown) < 2:
        return None
    name = shown.split(",")[0].strip()
    if link and (link.group(2) or link.group(1)):
        name = wf.display(link.group(2) or link.group(1)).strip()
    key, linked = entity_key(target, name)
    if not key:
        return None
    ep = _ENDORSER_PARTY.search(shown)
    endorser_party = ep.group(1)[0].upper() if ep else None
    q = dated.get(key)
    pub, acc = q.pop(0) if q else (None, None)
    dup = (cand, ctx["phase"], stance, key)
    # HOW MANY TIMES THIS EXACT KEY HAS ALREADY APPEARED for this candidate.
    # The extractor deliberately keeps both of Alabama's Associated Builders
    # and Contractors chapters, which share one link target; the archive walk
    # then deduped them back out again, because its row key had no way to tell
    # them apart. 145 rows silently vanished between 11,236 extracted and
    # 11,091 archived. The occurrence index is what makes the two agree.
    occurrence = sum(1 for k in seen_keys if k == dup)
    is_dup = dup in seen_keys
    seen_keys.append(dup)
    return {
        "page": title, **meta,
        "duplicate_key": is_dup,
        "occurrence": occurrence,
        "race_district": ctx["district"],
        "primary": ctx["primary"],
        "phase": ctx["phase"],
        "candidate": cand,
        "candidate_party": party,
        "endorser": name,
        "endorser_key": key,
        "endorser_link": target,
        "linked": linked,
        "endorser_party": endorser_party,
        "cross_party": (endorser_party is not None and party is not None
                        and endorser_party != party),
        "category": kind,
        "category_label": label,
        "stance": stance,
        "nested": depth > 1,
        "ref_date": iso_date(pub),
        "ref_date_raw": pub,
        "date_flag": date_flag(iso_date(pub), meta.get("cycle"), _TODAY),
        "date_precision": date_precision(pub),
        "access_date": iso_date(acc),
        "access_date_raw": acc,
        "descriptor": shown[len(name):].lstrip(" ,").strip() or None,
        "heading_path": ctx.get("path", ""),
        "via": via,
    }


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    """Same string with those character ranges replaced by whitespace.

    Offsets and line structure are preserved, so a heading index built on the
    original still lines up. Used to hand the fallback parser everything the
    template parser did NOT consume, which is what makes double-counting
    impossible rather than merely unlikely.
    """
    if not spans:
        return text
    buf = list(text)
    for a, b in spans:
        for i in range(a, min(b, len(buf))):
            if buf[i] != "\n":
                buf[i] = " "
    return "".join(buf)


def _heading_rows(residual: str, title: str, meta: dict, dated: dict,
                  seen_keys: list) -> list[dict]:
    """Endorsements written as plain headings rather than as a template.

    THE SECOND PATH, AND WHY IT IS GUARDED THE WAY IT IS.

    An earlier version of this module read endorsements out of the heading
    structure alone and had to be thrown away: race articles carry
    "==== Withdrawn ====" and "==== Declined ====" headings that list
    CANDIDATES who left or never entered, and a parser keying on those words
    filed a candidate roster as a roster of non-endorsers.

    The guard that makes this safe is narrow and absolute: a section is only
    considered when the word "endors" appears somewhere in its heading PATH.
    Inside a section already known to be about endorsements, a "Declined to
    endorse" subheading means what it says. Outside one, the same word does
    not, and outside one is where this never looks.

    It runs only over text the template parser did not consume, so a page that
    uses both styles is read once by each and never twice by either.
    """
    rows: list[dict] = []
    for path, lines in wf.sections(wf.prepare(residual)):
        if "endors" not in path.lower():
            continue
        parts = [p.strip() for p in path.split(" > ") if p.strip()]
        leaf = parts[-1] if parts else ""
        kind, stance = classify_label(leaf)
        # Leaf is a category -> the candidate is the heading above it.
        # Leaf is NOT a category -> the leaf itself is the candidate and the
        # bullets under it are uncategorised.
        is_cat = leaf.lower().strip() in CATEGORIES or kind not in (
            "other", "unspecified")
        if not is_cat and stance == "endorsed":
            cand_raw, kind = leaf, "unspecified"
        elif len(parts) >= 2:
            cand_raw = parts[-2]
        else:
            continue
        if cand_raw.lower().strip() in NOT_A_CANDIDATE or "endors" in cand_raw.lower():
            continue
        cand, party = split_heading(cand_raw)
        if not cand:
            continue
        # THE SAME GUARD THE TEMPLATE PATH HAS. The 2024 sweep filed three
        # candidates as their own endorsers because "Eliminated in primary"
        # sat beneath an Endorsements heading, and the heading guard passed it:
        # the path really does contain "endors". All three heading-path rows in
        # that sweep were this. A roster is a roster wherever it is nested.
        if re.sub(r"\s+", " ", (leaf or "").lower()).strip(": ") in NOT_ENDORSERS:
            continue
        ctx = context(path)
        ctx["path"] = path
        if ctx["district"] is None and meta.get("at_large"):
            ctx["district"] = "00"
        for ln in lines:
            m = _BULLET.match(ln.strip())
            if not m:
                continue
            entry = m.group(2).strip()
            row = _row(entry, title, meta, ctx, cand, party, kind, leaf, stance,
                       len(m.group(1)), dated, seen_keys, "headings")
            if row:
                rows.append(row)
    return rows


def extract(wikitext: str, title: str) -> list[dict]:
    """Every endorsement row on one article."""
    meta = page_meta(title)
    rows: list[dict] = []

    # DATES FIRST, from the raw text, keyed on the endorser. Stripping refs is
    # what makes everything downstream tractable and it is also what destroys
    # the only dates on the page, so they are harvested before that happens.
    # The join key is the endorser rather than the line, because the parsed
    # line and the raw line differ in whitespace and markup in ways not worth
    # chasing, while entity_key is computed identically from both. Values are a
    # queue: one organisation can appear twice on a page, and both passes walk
    # it top to bottom.
    raw_lines = wikitext.splitlines()
    dated: dict[str, list[tuple[str | None, str | None]]] = {}
    for i, ln in enumerate(raw_lines):
        if "<ref" not in ln or not _BULLET.match(ln.strip()):
            continue
        pub, acc = ref_dates("\n".join(raw_lines[i:i + 8]))
        if pub is None and acc is None:
            continue
        # Strip refs BEFORE looking for the endorser link. A cite template
        # carries its own wikilinks (|work=[[The Texas Tribune]]) and the first
        # link on a raw entry line is otherwise the newspaper, not the endorser.
        bare = re.sub(r"<ref[^>]*/>", " ", _REF_BLOCK.sub(" ", ln))
        lk = _LINK.search(bare)
        nm = ((lk.group(2) or lk.group(1)) if lk
              else wf.display(bare).lstrip("* ").split(",")[0])
        k, _ = entity_key(lk.group(1).strip() if lk else None, nm.strip())
        if k:
            dated.setdefault(k, []).append((pub, acc))

    # Heading offsets come from the ORIGINAL text so they line up with the
    # template offsets found in the same string.
    # A LIST, not a set: the occurrence index needs the count.
    seen_keys: list[tuple] = []
    index = heading_index(wikitext)
    boxes = list(_brace_blocks(wikitext, "Endorsements box"))
    for off, block in boxes:
        block = re.sub(r"<ref[^>]*/>", " ", _REF_BLOCK.sub(" ", block))
        par = _params(block)
        cand_raw = wf.display((par.get("title") or par.get("name") or "").strip())
        if not cand_raw:
            continue
        cand, party = split_heading(cand_raw)
        if not cand:
            continue
        path = path_at(index, off)
        ctx = context(path)
        ctx["path"] = path
        if ctx["district"] is None and meta.get("at_large"):
            ctx["district"] = "00"
        body = par.get("list") or par.get("content") or ""
        for label, depth, entry in _walk_list(body):
            if label and re.sub(r"\s+", " ", label.lower()).strip(": ") \
                    in NOT_ENDORSERS:
                continue
            kind, stance = classify_label(label)
            row = _row(entry, title, meta, ctx, cand, party, kind, label,
                       stance, depth, dated, seen_keys, "template")
            if row:
                rows.append(row)

    # WHATEVER THE TEMPLATE PARSER DID NOT CONSUME gets a second look, in case
    # this article writes its endorsements as headings instead. Blanking the
    # box spans rather than re-scanning the whole page is what makes it
    # impossible for one entry to be counted by both paths.
    residual = _blank(wikitext, [(o, o + len(b)) for o, b in boxes])
    rows.extend(_heading_rows(residual, title, meta, dated, seen_keys))
    return rows


def archive_rows(cycle: int, source_id: str = "wiki_endorsements"):
    """Walk the dated raw captures and date every row by when it first appeared.

    THIS IS WHAT THE DAILY CAPTURE IS FOR. A citation date is the day a story
    ran and is missing on ~30% of entries. First-seen in our own archive is
    complete, is `captured` rather than `archival` provenance, and cannot be
    revised by a later editor.

    CARRY-FORWARD IS THE WHOLE TRICK. `dedup: true` stores a page's bytes only
    on the days they change, so most days most pages have metadata and no body.
    A day with no body is not a day with no data -- it is a day the page was
    identical to the last time it was stored. So the walk holds the last parsed
    row set per page and rolls it forward, and a row's first_seen is the first
    date it is PRESENT, not the first date its page happened to be written.

    Getting that backwards would date every endorsement to whenever its page
    next changed for an unrelated reason, which would look entirely plausible
    and be wrong by weeks.

    A row that stops appearing is kept, with last_seen behind the final date.
    Endorsements do get removed from pages, sometimes because they were wrong
    and sometimes because they were withdrawn, and either way silently dropping
    them would make the archive disagree with itself over time.
    """
    root = REPO / "forecast" / "data" / str(cycle) / "raw" / source_id
    if not root.is_dir():
        raise SystemExit(f"no captures at {root}")
    dates = sorted(p.name for p in root.iterdir()
                   if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
    if not dates:
        raise SystemExit(f"no dated captures under {root}")

    state: dict[str, list[dict]] = {}
    first: dict[tuple, str] = {}
    last: dict[tuple, str] = {}
    days: dict[tuple, int] = {}
    row_of: dict[tuple, dict] = {}
    per_date: list[tuple[str, int, int]] = []

    def key(r: dict) -> tuple:
        return (r["page"], r["candidate"], r["endorser_key"], r["phase"],
                r["stance"], r.get("occurrence", 0))

    for d in dates:
        parsed_today = 0
        for meta in sorted((root / d).glob("*.meta.json")):
            slug = meta.name[: -len(".meta.json")]
            bodies = [p for p in (root / d).glob(f"{slug}.*")
                      if not p.name.endswith(".meta.json")]
            if bodies:
                try:
                    doc = json.loads(bodies[0].read_text(encoding="utf-8"))
                    title = doc["parse"]["title"]
                    state[slug] = extract(doc["parse"]["wikitext"], title)
                    parsed_today += 1
                except Exception:
                    state.setdefault(slug, [])
            elif slug not in state:
                # Metadata with no body and nothing carried forward means the
                # first capture of this page was itself deduped against
                # something outside the walk. Nothing to roll forward.
                state[slug] = []
        n = 0
        for rows in state.values():
            for r in rows:
                k = key(r)
                first.setdefault(k, d)
                last[k] = d
                days[k] = days.get(k, 0) + 1
                row_of[k] = r
                n += 1
        per_date.append((d, parsed_today, n))

    out = []
    for k, r in row_of.items():
        out.append({**r, "first_seen": first[k], "last_seen": last[k],
                    "days_present": days[k],
                    "still_present": last[k] == dates[-1]})
    return out, per_date, dates


def archive_report(rows: list[dict], per_date, dates) -> None:
    from collections import Counter
    print("=" * 72)
    print(f"  {len(rows):,} distinct endorsement rows over {len(dates)} "
          f"capture date(s)")
    print("=" * 72)
    print(f"  {'date':<12}{'pages parsed':>14}{'rows present':>14}")
    for d, p, n in per_date[-10:]:
        print(f"  {d:<12}{p:>14}{n:>14,}")
    new_after = [r for r in rows if r["first_seen"] != dates[0]]
    print(f"\n  {len(new_after):,} row(s) first appeared AFTER the first "
          f"capture -- these are the ones with a real captured date")
    gone = [r for r in rows if not r["still_present"]]
    if gone:
        print(f"  {len(gone):,} row(s) have disappeared from their page "
              f"(kept, last_seen recorded)")
    if len(dates) == 1:
        print("\n  Only one capture date so far, so every row's first_seen is")
        print("  today and means 'already there', not 'added today'. The")
        print("  dating becomes real from the second capture onward.")
    both = sum(1 for r in rows if r["ref_date"] and r["first_seen"])
    print(f"\n  {both:,} row(s) have BOTH a citation date and a first_seen,")
    print("  which is the pair that tells us how far behind the page runs.")
    if new_after:
        lag = [(r["first_seen"], r["ref_date"]) for r in new_after
               if r["ref_date"] and not r.get("date_flag")]
        if lag:
            import datetime as _dt
            ds = [(_dt.date.fromisoformat(a) - _dt.date.fromisoformat(b)).days
                  for a, b in lag]
            ds.sort()
            print(f"  median lag between citation date and first seen: "
                  f"{ds[len(ds)//2]} day(s), n={len(ds)}")


# ---------------------------------------------------------------------------
def titles(cycle: int) -> list[str]:
    """Every article worth asking for, built from the naming convention.

    Wikipedia's election titles are mechanical, so the page list needs no
    category crawl and no search -- construct it, then ask the API in one
    batched existence check which of them are real. Special elections and
    individual high-profile district articles are the exceptions and have to
    be discovered; --probe-plan reports what 404s so they can be added.
    """
    # A state with one at-large seat holds an ELECTION, not electionS, and the
    # article title follows. Found by probing: Alaska 404ed while every
    # multi-district state resolved.
    out = []
    for name in STATES:
        out.append(f"{cycle} United States Senate election in {name}")
        noun = "election" if name in AT_LARGE else "elections"
        out.append(f"{cycle} United States House of Representatives "
                   f"{noun} in {name}")
        out.append(f"{cycle} {name} gubernatorial election")
    return out


def fetch(title: str) -> str | None:
    """Wikitext for one page, through the project's own Fetcher."""
    import urllib.parse
    sys.path.insert(0, str(HERE))
    import capture
    import yaml
    reg = yaml.safe_load((REPO / "forecast" / "sources" / "2026.yaml")
                         .read_text(encoding="utf-8"))
    f = capture.Fetcher(reg.get("contact") or {}, reg.get("defaults") or {})
    p = {"action": "parse", "page": title, "prop": "wikitext",
         "format": "json", "formatversion": "2"}
    body, _ = f.get("https://en.wikipedia.org/w/api.php?"
                    + urllib.parse.urlencode(p))
    if not body:
        return None
    d = json.loads(body)
    if "error" in d:
        return None
    return d["parse"]["wikitext"]


def dump(wikitext: str, title: str, lines: int = 70) -> None:
    """What is ACTUALLY in the page, before any assumption about its shape.

    Written because the first version of this parser returned zero rows from
    every real article while passing every synthetic test -- which is the
    signature of a structure guessed from the RENDERED page rather than read
    from the wikitext. A template can render as a heading. This prints the
    markup and lets the markup decide.
    """
    import collections
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)
    txt = wikitext or ""
    print(f"  {len(txt):,} bytes, {len(txt.splitlines()):,} lines")
    if txt.lstrip()[:9].upper().startswith("#REDIRECT"):
        print(f"  REDIRECT -> {txt.strip()[:120]}")
        return

    heads = re.findall(r"^\s*(={2,6})\s*(.+?)\s*\1\s*$", txt, re.M)
    print(f"\n  {len(heads)} headings. Those mentioning endorse/decline/withdraw:")
    hits = [(len(a), b) for a, b in heads
            if re.search(r"endors|declin|withdraw", b, re.I)]
    for lv, h in hits[:25]:
        print(f"    {'=' * lv} {h}")
    if not hits:
        print("    NONE -- the categories are not headings.")
    print(f"\n  heading levels in use: "
          f"{sorted(collections.Counter(len(a) for a, _ in heads).items())}")
    print("  first 30 headings:")
    for a, b in heads[:30]:
        print(f"    {'=' * len(a)} {b}")

    tpl = collections.Counter(
        m.strip().split("|")[0].split("\n")[0].strip()[:44]
        for m in re.findall(r"\{\{\s*([^{}|]{1,60})", txt))
    print(f"\n  {sum(tpl.values())} template calls, most common:")
    for k, n in tpl.most_common(18):
        print(f"    {n:>5}  {k}")

    print(f"\n  bullet lines (*): "
          f"{sum(1 for l in txt.splitlines() if l.lstrip().startswith('*'))}")
    print(f"  the word 'ndorse' appears {len(re.findall('ndorse', txt))} times")

    i = txt.lower().find("endors")
    if i < 0:
        print("\n  'endors' does not appear at all.")
        return
    start = txt.count("\n", 0, i)
    print(f"\n  --- RAW, {lines} lines from the first 'endors' "
          f"(line {start+1}) ---")
    for n, l in enumerate(txt.splitlines()[max(0, start - 4):start + lines],
                          start=max(1, start - 3)):
        print(f"  {n:>5}| {l[:160]}")


def race_key(r: dict) -> tuple:
    """One contest. District matters for the House and nowhere else."""
    return (r.get("chamber"), r.get("state"),
            r.get("race_district") if r.get("chamber") == "house" else None)


def audit(rows: list[dict]) -> None:
    """The questions that decide whether this dataset can carry the model.

    Not a summary. Each block below answers something the pre-registration has
    to commit to, and two of them can come back "no":

      COVERAGE     is the instrument present everywhere, or only where the
                   race is interesting? Selection on salience is the failure
                   mode section 1 of the roadmap is about, and it would arrive
                   here as a coverage rate that tracks competitiveness.
      COMPARABLE   share-within-race needs at least two candidates in a race
                   to have endorsements. A race with one endorsed candidate
                   gives a share of 100% that means nothing.
      RECURRENCE   a group's lean can only be estimated from a group seen in
                   several races. If most endorsers appear once, the residual
                   specification is not identified and the pre-registration
                   must say so BEFORE anyone tries it and quietly falls back.
    """
    from collections import Counter, defaultdict
    n = len(rows)
    print("=" * 72)
    print(f"  {n:,} rows")
    print("=" * 72)
    end = [r for r in rows if r["stance"] == "endorsed"]
    print(f"  stance      : " + "  ".join(
        f"{k} {v:,}" for k, v in Counter(r["stance"] for r in rows).most_common()))
    if not any(r["stance"] != "endorsed" for r in rows):
        print("    !! no declined/withdrawn rows ANYWHERE. Either Wikipedia")
        print("    !! does not use them on race articles, or they are being")
        print("    !! missed. Confirm on a page known to carry one.")

    races = defaultdict(list)
    for r in rows:
        races[race_key(r)].append(r)
    print(f"\n  COVERAGE  {len(races)} contest(s) with any endorsement")
    for ch, universe in (("house", 435), ("senate", 35), ("governor", 36)):
        got = {k for k in races if k[0] == ch}
        cands = {(race_key(r), r["candidate"]) for r in rows if r["chamber"] == ch}
        print(f"    {ch:<9}{len(got):>4} of ~{universe} contests"
              f"   {len(cands):>5} candidates"
              f"   {sum(1 for r in rows if r['chamber']==ch):>6} rows")
    nodist = sum(1 for r in rows
                 if r["chamber"] == "house" and not r["race_district"])
    if nodist:
        print(f"    !! {nodist} House rows carry no district -- they cannot be"
              f" joined to a race. Worst pages:")
        bypage = Counter(r["page"] for r in rows
                         if r["chamber"] == "house" and not r["race_district"])
        for pg, c in bypage.most_common(6):
            ex = next(r for r in rows if r["page"] == pg
                      and r["chamber"] == "house" and not r["race_district"])
            print(f"      {c:>4}  {pg[-38:]}")
            print(f"            path: {ex['heading_path'][:76]!r}")

    sizes = sorted(len(v) for v in races.values())
    print(f"\n  SALIENCE  endorsements per contest:"
          f" median {sizes[len(sizes)//2]}, "
          f"p90 {sizes[int(len(sizes)*0.9)]}, max {sizes[-1]}")
    print("    the biggest:")
    for k, v in sorted(races.items(), key=lambda kv: -len(kv[1]))[:5]:
        print(f"      {str(k):<34}{len(v):>5}")

    two = 0
    for k, v in races.items():
        if len({r["candidate"] for r in v if r["stance"] == "endorsed"}) >= 2:
            two += 1
    print(f"\n  COMPARABLE  {two} of {len(races)} contests have >=2 endorsed"
          f" candidates ({100*two/max(len(races),1):.0f}%)")
    print("    share-within-race is only defined for those.")

    per = Counter(r["endorser_key"] for r in rows)
    races_per = defaultdict(set)
    for r in rows:
        races_per[r["endorser_key"]].add(race_key(r))
    multi = sum(1 for k, v in races_per.items() if len(v) >= 2)
    five = sum(1 for k, v in races_per.items() if len(v) >= 5)
    print(f"\n  RECURRENCE  {len(per):,} distinct endorsers")
    print(f"    seen in >=2 contests: {multi:,} ({100*multi/len(per):.0f}%)"
          f"   >=5 contests: {five:,} ({100*five/len(per):.0f}%)")
    print("    a group lean is estimable only for the recurring ones.")
    print("    most prolific:")
    for k, c in per.most_common(10):
        nm = next(r["endorser"] for r in rows if r["endorser_key"] == k)
        print(f"      {nm[:34]:<36}{c:>5} rows, "
              f"{len(races_per[k]):>4} contests")

    dat = [r["ref_date"] for r in rows if r["ref_date"]]
    print(f"\n  DATES  {len(dat):,} of {n:,} ({100*len(dat)/n:.0f}%) carry a"
          f" usable citation date")
    if dat:
        print(f"    range {min(dat)} .. {max(dat)}")
        yr = Counter(d[:7] for d in dat)
        print("    by month, most recent 8:")
        for m in sorted(yr)[-8:]:
            print(f"      {m}  {yr[m]:>5}  {'#' * min(60, yr[m]//8)}")
    bad = Counter(r["ref_date_raw"] for r in rows
                  if r["ref_date_raw"] and not r["ref_date"])
    if bad:
        print(f"    {sum(bad.values())} unparseable: "
              + ", ".join(f"{k!r}" for k, _ in bad.most_common(6)))
    flagged = [r for r in rows if r.get("date_flag")]
    if flagged:
        print(f"    {len(flagged)} IMPOSSIBLE date(s), flagged not dropped:")
        for r in flagged[:6]:
            print(f"      {r['date_flag']:<10}{r['ref_date']}  "
                  f"{r['candidate'][:18]:<20}<- {r['endorser'][:26]}")
        good = [r["ref_date"] for r in rows
                if r["ref_date"] and not r.get("date_flag")]
        if good:
            print(f"    plausible range {min(good)} .. {max(good)}")

    print(f"\n  CATEGORIES")
    for k, c in Counter(r["category"] for r in rows).most_common():
        print(f"    {k:<20}{c:>7}  ({100*c/n:>4.1f}%)")
    unk = Counter(r["category_label"] for r in rows
                  if r["category"] in ("other", "unspecified")
                  and r["category_label"])
    if unk:
        print(f"    unrecognised labels ({sum(unk.values())} rows): "
              + ", ".join(f"{k!r}x{v}" for k, v in unk.most_common(12)))

    xp = [r for r in rows if r["cross_party"]]
    print(f"\n  CROSS-PARTY  {len(xp)} endorsement(s) annotated as crossing"
          f" party lines")
    for r in xp[:10]:
        print(f"    {r['state']} {r['chamber'][:3]}  "
              f"{r['candidate'][:22]:<24}({r['candidate_party']})"
              f" <- {r['endorser'][:26]:<28}({r['endorser_party']})")
    dup = sum(1 for r in rows if r["duplicate_key"])
    unl = sum(1 for r in rows if not r["linked"])
    via = Counter(r.get("via") for r in rows)
    print(f"\n  EXTRACTION PATH  " + "  ".join(
        f"{k} {v:,}" for k, v in via.most_common()))
    if via.get("headings"):
        print("    the fallback fired -- some pages write endorsements as")
        print("    headings rather than as an Endorsements box.")
    print(f"\n  HYGIENE  {dup} duplicate link target(s) within a candidate, "
          f"{unl} unlinked ({100*unl/n:.0f}%)")


def summarise(rows: list[dict], title: str) -> None:
    from collections import Counter
    print(f"\n  {title}")
    if not rows:
        print("    no endorsement rows")
        return
    end = [r for r in rows if r["stance"] == "endorsed"]
    print(f"    {len(rows)} rows  ({len(end)} endorsed, "
          f"{len(rows)-len(end)} declined/withdrawn)")
    print(f"    {len({r['candidate'] for r in rows})} candidates, "
          f"{len({r['endorser_key'] for r in rows})} distinct endorsers")
    linked = sum(1 for r in rows if r["linked"])
    dated_n = sum(1 for r in rows if r["ref_date"])
    print(f"    {100*linked/len(rows):.0f}% wikilinked   "
          f"{dated_n} dated ({100*dated_n/len(rows):.0f}%)")
    dup = sum(1 for r in rows if r["duplicate_key"])
    xp = [r for r in rows if r["cross_party"]]
    if dup:
        print(f"    {dup} row(s) repeat a link target for the same candidate "
              f"(flagged, not dropped)")
    if xp:
        print(f"    {len(xp)} CROSS-PARTY endorsement(s):")
        for r in xp[:6]:
            print(f"      {r['candidate'][:20]:<22}({r['candidate_party']})"
                  f" <- {r['endorser'][:28]:<30}({r['endorser_party']})")
    bad = [r["ref_date_raw"] for r in rows
           if r["ref_date_raw"] and not r["ref_date"]]
    if bad:
        print(f"    {len(bad)} unparseable date(s): "
              + ", ".join(sorted(set(bad))[:5]))
    cats = Counter(r["category"] for r in rows)
    for k, n in cats.most_common():
        print(f"      {k:<20}{n:>5}")
    # A page whose categories all come back unspecified is a page whose
    # "Declined to endorse" block was not recognised either, which means its
    # non-endorsements are sitting in the endorsed pile. Say so loudly; the
    # first version of this failed exactly here and failed silently.
    unspec = cats.get("unspecified", 0) + cats.get("other", 0)
    if unspec > len(rows) * 0.5:
        print(f"    !! {unspec}/{len(rows)} rows uncategorised -- this page "
              f"marks categories in a form the parser does not know.")
        print(f"    !! Run --dump-box on it. Stance detection is UNRELIABLE "
              f"here.")
    lab = Counter(r["category_label"] for r in rows
                  if r["category"] in ("unspecified", "other")
                  and r["category_label"])
    if lab:
        print("    unrecognised category labels seen: "
              + ", ".join(f"{k!r}x{n}" for k, n in lab.most_common(8)))
    print("    first 5:")
    for r in rows[:5]:
        d = f" [{r['ref_date']}]" if r["ref_date"] else ""
        print(f"      {r['candidate'][:22]:<24}<- {r['endorser'][:34]:<36}"
              f"{r['category']}{d}")


# ---------------------------------------------------------------------------
FIXTURE = """
== Republican primary ==
=== Candidates ===
==== Nominee ====
* [[Alice Example]], attorney general
==== Withdrawn ====
* Someone Who Quit, realtor
==== Declined ====
* [[Never Ran]], former governor
=== First round ===
==== Endorsements ====
{{Endorsements box
| title=Alice Example (R)
| colwidth=60
| list=
'''Executive branch officials'''
* [[Pat Placeholder]], former [[Department of Nowhere|secretary of nowhere]] (2011-present)<ref>{{cite news |last=Reporter |date=March 4, 2026 |title=Placeholder backs Example |access-date=March 5, 2026}}</ref>
* [[Quincy Sample|Sample]], junior officer<ref name=a>{{cite web
 |date=April 12, 2026
 |title=A citation that wraps across lines
 }}</ref>
* An Unlinked Person, county chair
'''Organizations'''
* [[Fictional Growers Association]]
* [[Made-Up Labor Council]]<ref>{{cite news |date=January 9, 2026 |title=x |work=[[The Invented Herald]]}}</ref>
'''Declined to endorse'''
* [[Wilhelmina Neutral]], former governor
}}
{{Endorsements box
| title=Bob Instance (R)
| list=
'''Newspapers and media'''
* ''[[The Invented Herald]]''
'''Withdrawn endorsement'''
* [[Fictional Growers Association]]
}}
=== Runoff ===
==== Runoff endorsements ====
{{Endorsements box
| title=Alice Example (R)
| list=
'''U.S. senators'''
* [[Dana Fictitious]], [[Nowhere]] (2015-present)
}}
== Democratic primary ==
=== Endorsements ===
{{Endorsements box
| title=Carol Placeholder (D)
| list=
'''Labor unions'''
* [[Invented Teachers Union]]
}}
== General election ==
=== Post-primary endorsements ===
{{Endorsements box
| title=Alice Example (R)
| list=
'''Organizations'''
* [[Sample Advocacy Fund]]
}}
=== Polling ===
* Not an endorsement and must never be read as one
"""

HOUSE_FIXTURE = """
== District 1 ==
=== Democratic primary ===
==== Endorsements ====
{{Endorsements box
| title=Carol Placeholder (D)
| list=
'''U.S. representatives'''
* [[Dana Fictitious]], {{ushr|NV|2}}
'''Labor unions'''
* [[Invented Teachers Union]]
}}
== District 12 ==
=== Endorsements ===
{{Endorsements box
| title=Eve Nonexistent (D)
| list=
'''Organizations'''
* [[Sample Advocacy Fund]]
}}
"""


def _self_test() -> int:
    fails = 0

    def check(cond, msg):
        nonlocal fails
        if cond:
            print(f"  ok   {msg}")
        else:
            fails += 1
            print(f"  FAIL {msg}")

    r = extract(FIXTURE, "2026 United States Senate election in Texas")
    by = {(x["candidate"], x["endorser_key"], x["phase"]): x for x in r}
    check(len(r) == 11, f"11 rows from the senate fixture (got {len(r)})")
    check(all(x["state"] == "TX" for x in r), "state read from the title")
    check(all(x["chamber"] == "senate" for x in r), "chamber read from title")
    check(all(x["cycle"] == 2026 for x in r), "cycle read from title")

    # THE FALSE POSITIVE THIS DESIGN EXISTS TO PREVENT. "Withdrawn" and
    # "Declined" are headings in the CANDIDATE list and mean people who left or
    # never entered the race. Nothing outside an Endorsements box is read.
    check(not any(x["endorser"] in ("Someone Who Quit", "Never Ran")
                  for x in r),
          "candidate-list 'Withdrawn'/'Declined' headings are NOT endorsements")
    check(not any("Not an endorsement" in (x["endorser"] or "") for x in r),
          "a bullet outside any box is ignored")

    check({x["candidate"] for x in r} ==
          {"Alice Example", "Bob Instance", "Carol Placeholder"},
          "candidate read from the title= parameter")
    check(by[("Alice Example", "pat placeholder", "primary")]["candidate_party"]
          == "R", "party suffix stripped off the title")

    check(sum(1 for x in r if x["stance"] == "endorsed") == 9,
          "9 endorsed (got %d)" % sum(1 for x in r if x["stance"] == "endorsed"))
    check(any(x["stance"] == "declined" and x["candidate"] == "Alice Example"
              for x in r), "a 'Declined to endorse' bold line becomes declined")
    check(any(x["stance"] == "withdrawn" and x["candidate"] == "Bob Instance"
              for x in r), "a 'Withdrawn endorsement' bold line becomes withdrawn")
    check(by[("Alice Example", "pat placeholder", "primary")]["category"]
          == "executive", "bold line maps to the category vocabulary")

    # Phase, which keeps three different elections from being pooled.
    check({x["phase"] for x in r} == {"primary", "runoff", "general"},
          "phase read from the heading path (got %s)"
          % sorted({x["phase"] for x in r}))
    check(by[("Alice Example", "dana fictitious", "runoff")]["phase"] == "runoff",
          "runoff endorsements marked runoff")
    check(by[("Alice Example", "sample advocacy fund", "general")]["primary"]
          is None, "post-primary block is not filed under a primary")
    check(by[("Carol Placeholder", "invented teachers union", "primary")]
          ["primary"] == "D", "primary party read from the path")

    k = ("Alice Example", "quincy sample", "primary")
    check(k in by, "piped link keys on the TARGET, not the display text")
    check(by[k]["endorser"] == "Sample", "display text still kept as the name")
    unl = [x for x in r if not x["linked"]]
    check(len(unl) == 1 and unl[0]["endorser"] == "An Unlinked Person",
          "unlinked entry falls back to a folded name key and is flagged")

    pp = by[("Alice Example", "pat placeholder", "primary")]
    check(pp["ref_date"] == "2026-03-04", "citation date read")
    check(pp["access_date"] == "2026-03-05", "access-date read")
    check(by[k]["ref_date"] == "2026-04-12",
          "MULTI-LINE citation date read (got %r)" % by[k]["ref_date"])
    # NB the key folds punctuation to spaces, so the hyphen is gone.
    ml = by[("Alice Example", "made up labor council", "primary")]
    check(ml["endorser"] == "Made-Up Labor Council",
          "the newspaper inside a citation is not mistaken for the endorser")
    check(ml["ref_date"] == "2026-01-09", "its date still lands")

    check(pp["descriptor"] == "former secretary of nowhere (2011-present)",
          "descriptor keeps the office and drops the name")

    fg = [x for x in r if x["endorser_key"] == "fictional growers association"]
    check(len(fg) == 2 and {x["stance"] for x in fg} == {"endorsed", "withdrawn"},
          "one endorser can appear for two candidates with two stances")

    # Category markup comes in several forms for the same visual result, and a
    # matcher that knows only one of them fails SILENTLY -- rows still appear,
    # merely uncategorised, and "Declined to endorse" goes with them.
    variants = """
{{Endorsements box
| title=Vera Variant (D)
| list=
;U.S. senators
* [[Dana Fictitious]]
* '''Organizations'''
* [[Sample Advocacy Fund]]
<b>Newspapers</b>
* ''[[The Invented Herald]]''
'''Declined to endorse:'''
* [[Wilhelmina Neutral]]
* '''[[Bold Linked Person]]''', a bulleted entry that is entirely bold
}}
"""
    v = extract(variants, "2026 United States Senate election in Ohio")
    vb = {x["endorser_key"]: x for x in v}
    check(len(v) == 5, f"5 rows from the variants fixture (got {len(v)})")
    check(vb["dana fictitious"]["category"] == "us_senator",
          ";definition-list term read as a category")
    check(vb["sample advocacy fund"]["category"] == "organization",
          "* '''bold''' bulleted line read as a category")
    check(vb["the invented herald"]["category"] == "newspaper",
          "<b>html</b> line read as a category")
    check(vb["wilhelmina neutral"]["stance"] == "declined",
          "'Declined to endorse:' with a trailing colon still detected")
    check(vb["bold linked person"]["stance"] == "declined"
          and vb["bold linked person"]["category"] == "unspecified",
          "a wholly-bold bullet CONTAINING A LINK stays an entry, not a "
          "category")

    # Dates arrive in at least three formats; comparing them mixed is wrong.
    check(iso_date("March 28, 2026") == "2026-03-28", "month-day-year -> ISO")
    check(iso_date("12 January 2026") == "2026-01-12", "day-month-year -> ISO")
    check(iso_date("2026-04-01") == "2026-04-01", "ISO passes through")
    check(iso_date("Spring 2026") is None, "an unparseable date gives up")
    check(pp["ref_date"] == "2026-03-04" and pp["ref_date_raw"] == "March 4, 2026",
          "row carries both the normalised and the raw date")

    # The categories a live probe turned up that the vocabulary did not know.
    check(classify_label("State officials") == ("statewide", "endorsed"),
          "'State officials' recognised")
    check(classify_label("Party chapters") == ("party", "endorsed"),
          "'Party chapters' recognised")
    check(classify_label("Governors") == ("statewide", "endorsed"),
          "'Governors' recognised")

    # Cross-party endorsements, marked by hand on the page.
    xp = """
== Republican primary ==
=== Endorsements ===
{{Endorsements box
| title=Wanda Nominee (R)
| list=
;State officials
*[[Jere Placeholder]], lieutenant governor (1971-1979) ''(Democratic)''
*[[Regular Republican]], attorney general (2019-present)
;Organizations
*[[Associated Widgets]] Alabama
*[[Associated Widgets]] North Alabama
}}
"""
    x = extract(xp, "2026 Alabama gubernatorial election")
    xk = {r["endorser_key"]: r for r in x}
    check(xk["jere placeholder"]["endorser_party"] == "D",
          "the (Democratic) annotation is read off the entry")
    check(xk["jere placeholder"]["cross_party"] is True,
          "a D endorsing in an R primary is flagged cross_party")
    check(xk["regular republican"]["endorser_party"] is None
          and xk["regular republican"]["cross_party"] is False,
          "an unannotated co-partisan is not flagged")
    check(xk["jere placeholder"]["descriptor"]
          == "lieutenant governor (1971-1979) (Democratic)",
          "the annotation stays in the descriptor too")
    aw = [r for r in x if r["endorser_key"] == "associated widgets"]
    check(len(aw) == 2 and [r["duplicate_key"] for r in aw] == [False, True],
          "two chapters sharing one link target: both kept, second flagged")

    # At-large states have no district heading because there is no district.
    al = extract(HOUSE_FIXTURE.replace("== District 1 ==", "== Endorsements ==")
                 .replace("== District 12 ==", "== Also ==")
                 .replace("=== Endorsements ===", "=== More ==="),
                 "2026 United States House of Representatives election in Alaska")
    check(al and all(x["race_district"] == "00" for x in al),
          "at-large House rows get district 00, not None (got %s)"
          % sorted({x["race_district"] for x in al}))
    check(not page_meta("2026 United States House of Representatives "
                        "elections in Nevada")["at_large"],
          "a multi-district state is not marked at-large")

    # Candidate titles carry more than a party.
    check(split_heading("Toni Atkins (D) (withdrew)") == ("Toni Atkins", "D"),
          "party AND status parenthetical both stripped")
    check(split_heading("Will Ainsworth (declined)") == ("Will Ainsworth", None),
          "a status parenthetical alone is stripped, party stays None")
    check(split_heading("Xavier Becerra (D)") == ("Xavier Becerra", "D"),
          "the ordinary case still works")

    # The plausibility window has to admit a real long-runway race.
    check(date_flag("2023-05-25", 2026, "2026-08-25") is None,
          "a May 2023 endorsement in the 2026 cycle is NOT flagged")
    check(date_flag("2019-11-08", 2026, "2026-08-25") == "pre_cycle",
          "a 2019 citation still is")
    check(date_flag("2026-12-30", 2026, "2026-08-25") == "future",
          "a future date is flagged")

    # Dates: abbreviations, and month-only with its precision recorded.
    check(iso_date("Mar 1, 2026") == "2026-03-01", "abbreviated month parsed")
    check(iso_date("Dec 5, 2025") == "2025-12-05", "abbreviated month, 2025")
    check(iso_date("February 2026") == "2026-02-01"
          and date_precision("February 2026") == "month",
          "month-only resolves to the 1st and is marked month-precision")
    check(date_precision("March 4, 2026") == "day", "a full date is day-precision")
    check(iso_date("2026") is None, "a bare year still gives up")
    check(classify_label("White House officials") == ("executive", "endorsed"),
          "'White House officials' recognised")

    check(ref_dates("* [[X]]<ref>{{cite web |date=<!-- not stated --> "
                    "|title=y}}</ref>") == (None, None),
          "an HTML comment in |date= does not become a date")

    # The extractor keeps both chapters; the archive walk must too.
    occ = [r for r in x if r["endorser_key"] == "associated widgets"]
    check([r["occurrence"] for r in occ] == [0, 1],
          "repeated link targets get an occurrence index")
    check(len({(r["page"], r["candidate"], r["endorser_key"], r["phase"],
                r["stance"], r["occurrence"]) for r in x}) == len(x),
          "every extracted row has a distinct archive key")

    # THE SECOND PATH: endorsements written as headings, no template at all.
    # Older cycles may be written this way. The fixture deliberately also
    # carries the candidate-list "Withdrawn"/"Declined" headings that destroyed
    # the first version of this parser, OUTSIDE the endorsements section.
    heads = """
== Republican primary ==
=== Candidates ===
==== Nominee ====
* [[Real Nominee]], attorney general
==== Withdrawn ====
* [[Quit Early]], realtor
==== Declined ====
* [[Never Entered]], former governor
=== Endorsements ===
==== Hilda Heading (R) ====
===== U.S. senators =====
* [[Pat Placeholder]], senior senator<ref>{{cite news |date=March 4, 2024 |title=x}}</ref>
* [[Quincy Sample|Sample]], junior senator
===== Declined to endorse =====
* [[Wilhelmina Neutral]], former governor
==== Ivan Instance (R) ====
===== Organizations =====
* [[Sample Advocacy Fund]]
=== Polling ===
* Not an endorsement
"""
    hr = extract(heads, "2024 United States Senate election in Ohio")
    hb = {x["endorser_key"]: x for x in hr}
    check(len(hr) == 4, f"4 rows from the headings fixture (got {len(hr)})")
    check(all(x["via"] == "headings" for x in hr),
          "they are marked as coming from the fallback path")
    check(not any(x["endorser_key"] in ("quit early", "never entered")
                  for x in hr),
          "GUARD: a candidate list OUTSIDE an endorsements section is ignored")
    check(not any("Not an endorsement" in (x["endorser"] or "") for x in hr),
          "a bullet under Polling is ignored")
    check({x["candidate"] for x in hr} == {"Hilda Heading", "Ivan Instance"},
          "candidates found from the heading above the category")
    check(hb["pat placeholder"]["category"] == "us_senator",
          "category heading read on the fallback path")
    check(hb["wilhelmina neutral"]["stance"] == "declined",
          "'Declined to endorse' INSIDE an endorsements section is a stance")
    check(hb["pat placeholder"]["ref_date"] == "2024-03-04",
          "dates join on the fallback path too")
    check(hb["pat placeholder"]["cycle"] == 2024,
          "cycle read from a 2024 title")

    # A page using BOTH must be read once by each, never twice by either.
    both = FIXTURE + heads.replace("2024", "2026")
    bo = extract(both, "2026 United States Senate election in Texas")
    from collections import Counter as _C
    vias = _C(x["via"] for x in bo)
    check(vias["template"] == 11 and vias["headings"] == 4,
          f"both paths on one page: 11 template + 4 headings (got {dict(vias)})")
    check(len(bo) == 15, f"no double counting (got {len(bo)})")

    h = extract(HOUSE_FIXTURE,
                "2026 United States House of Representatives elections in Nevada")
    check(len(h) == 3, f"3 rows from the house fixture (got {len(h)})")
    check({x["race_district"] for x in h} == {"01", "12"},
          "district read from the heading path and zero-padded")
    check(all(x["chamber"] == "house" and x["state"] == "NV" for x in h),
          "house page meta")

    print("\n  " + ("PASS" if not fails else f"{fails} FAILURE(S)"))
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--plan", action="store_true",
                    help="print the constructed page list and stop")
    ap.add_argument("--probe", metavar="TITLE", action="append",
                    help="fetch one live article and report what came out")
    ap.add_argument("--probe-plan", action="store_true",
                    help="fetch the first --limit pages of the plan")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--dump", metavar="TITLE", action="append",
                    help="print the raw structure of one page and stop")
    ap.add_argument("--dump-lines", type=int, default=70)
    ap.add_argument("--dump-box", metavar="TITLE", action="append",
                    help="print the raw wikitext INSIDE the first two "
                         "Endorsements box calls on a page")
    ap.add_argument("--json", metavar="PATH", help="write rows as JSON")
    ap.add_argument("--archive", action="store_true",
                    help="date every row by when it first appeared in our own "
                         "dated captures, instead of by its citation")
    ap.add_argument("--audit", metavar="PATH",
                    help="audit a JSON file written by an earlier --json run")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    if a.archive:
        rows, per_date, dates = archive_rows(a.cycle)
        archive_report(rows, per_date, dates)
        if a.json:
            Path(a.json).write_text(json.dumps(rows, indent=1),
                                    encoding="utf-8")
            print(f"\n  wrote {a.json}")
        return 0

    if a.audit:
        audit(json.loads(Path(a.audit).read_text(encoding="utf-8")))
        return 0

    plan = titles(a.cycle)
    if a.plan:
        print(f"{len(plan)} candidate titles for {a.cycle}")
        for t in plan[:12]:
            print("  " + t)
        print(f"  ... ({len(plan)-12} more)")
        print("\n  Special elections and standalone district articles are NOT\n"
              "  in this list -- run --probe-plan and add whatever 404s.")
        return 0

    if a.dump_box:
        for tt in a.dump_box:
            wt = fetch(tt)
            if wt is None:
                print(f"  {tt}: no such page")
                continue
            blocks = list(_brace_blocks(wt, "Endorsements box"))
            print("=" * 72)
            print(f"  {tt}\n  {len(blocks)} Endorsements box call(s)")
            if not blocks:
                names = sorted({m.strip().split("|")[0].strip()
                                for m in re.findall(r"\{\{\s*([^{}|]{1,50})", wt)
                                if "endors" in m.lower()})
                print(f"  templates mentioning 'endors': {names}")
            for _off, blk in blocks[:2]:
                par = _params(re.sub(r"<ref[^>]*/>", " ",
                                     _REF_BLOCK.sub("", blk)))
                print("=" * 72)
                print(f"  params: {sorted(par)}")
                body = par.get("list") or par.get("content") or ""
                print(f"  --- first 30 lines of the list, refs stripped ---")
                for i, l in enumerate(body.splitlines()[:30], 1):
                    tag = category_line(l)
                    mark = f"  <== CATEGORY {tag!r}" if tag else ""
                    print(f"  {i:>3}| {l[:120]}{mark}")
        return 0

    if a.dump:
        for tt in a.dump:
            wt = fetch(tt)
            if wt is None:
                print(f"  {tt}: no such page")
            else:
                dump(wt, tt, a.dump_lines)
        return 0

    want = a.probe or (plan[:a.limit] if a.probe_plan else None)
    if not want:
        ap.error("give --self-test, --plan, --dump TITLE, --probe TITLE "
                 "or --probe-plan")

    allrows, missing = [], []
    for t in want:
        wt = fetch(t)
        if wt is None:
            missing.append(t)
            print(f"\n  {t}\n    (no such page)")
            continue
        rows = extract(wt, t)
        allrows.extend(rows)
        summarise(rows, t)

    print(f"\n{'='*68}\n  {len(allrows)} rows from {len(want)-len(missing)} "
          f"page(s); {len(missing)} title(s) did not exist")
    if a.json:
        Path(a.json).write_text(json.dumps(allrows, indent=1), encoding="utf-8")
        print(f"  wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
