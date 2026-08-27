"""
Race to the WH. Publication: aggregate_only (free site, no licence grant).

WHY THIS PARSER LOOKS NOTHING LIKE THE OTHERS

racetothewh.com's own pages carry no numbers at all — every figure on the site
is an Infogram embed, so an HTML selector finds nothing however well written.
The real payload is a JavaScript assignment inside the embed page:

    window.infographicData = {...600KB of JSON...}

That JSON is a *document model for a drawing*, not a data feed. Infogram nests
the actual sheet several levels down inside a chart entity, and the exact path
depends on the chart type, the Infogram editor version, and whether the author
used a chart, a table, or a map. A hard-coded path like

    blob["elements"]["content"]["content"]["entities"][uid]["props"]["chartData"]

is exactly the kind of thing that works today and silently breaks in October.
We got a taste of that already: an inspection walk limited to depth 4 returned
absolutely nothing, because the sheet sits deeper than that.

So this parser does not navigate. It WALKS the entire object and picks out
every 2-D array that looks like a spreadsheet, then works out what each one is
by reading its own header row. If Infogram reshuffles its document model, the
sheet moves but is still a sheet, and this keeps working.

THE TRADE
    Structure-agnostic parsing means we cannot assert "the data is where we
    expect". So the assertions move to the CONTENT instead: a table only
    produces rows if its headers name a party and a quantity we recognise, and
    a race label has to parse as a real seat. Anything else is ignored.

WHAT THE CAPTURE ACTUALLY CONTAINS — READ THIS BEFORE DEBUGGING

    Nothing. As of the 2026-08-19 capture, the embeds carry NO forecast values
    at all. This was established, not assumed:

      · 53 CHART entities in the Senate deck, and every one of their sheets is
        empty — `data: [[[]]]`
      · the map charts hold a 62x62 identity matrix, every state's own column
        set to a constant 10. That is geometry for colouring a map, not a
        rating: all 57 values are identical
      · exactly 19 strings in the whole 636KB blob contain a "%", and every one
        is legend copy ("Safe R: Democrats have an under 5% Chance...")
      · the 84 TEXT entities are labels — "Chance to Win a Senate Majority",
        "Projected Seats Won" — with no numbers attached

    The numbers arrive at render time. Each chart carries a pointer instead:

        chartData.custom.live = {"enabled": true, "provider": "atlas_google_drive",
                                 "key": "f6b57856-...",
                                 "sheetNames": ["Projected Lead",
                                                "Leads the Polling",
                                                "Chance to Win"],
                                 "title": "26 Sen - NH - Box"}

    38 such pointers in the Senate deck, 51 in the House deck — one per race.
    The titles are a complete inventory of what the deck covers; the keys are
    handles into Infogram's live-data service.

    WHY THIS MATTERS MORE THAN A NORMAL PARSER GAP: the two-phase design's
    whole promise is that a broken parser can be fixed later and re-run over
    every stored date. That promise does not hold here. No future parser can
    recover numbers that were never in the bytes. Until the live-data URL is in
    the registry and capture is fetching it, each day's capture is permanently
    empty — not "unparsed yet", but empty.

    UNRESOLVED: the live-data endpoint. infogram.com/robots.txt permits us
    (only /app/ and /oembed are disallowed), so this is a discovery problem,
    not a permission one. Guessing the URL failed; reading it out of the
    minified viewer bundle failed. The reliable way is 30 seconds of DevTools —
    open the embed, Network tab, filter XHR, find the request carrying the
    live key. See the registry entry.

    So: this parser is written, tested, and correct for the day the data shows
    up, and returns [] quietly until then — see the tail of parse() for why
    that is [] and not an exception.

WHAT IT EMITS
    win_prob_D   0-1     per race
    margin_D     points  per race, D minus R
    seats_D/R    count   chamber topline, when the deck carries one

WHAT IT DOES NOT EMIT
    Nothing is stamped `individual`. The site is free to read and robots
    permits us, but there is no licence grant, so these are inputs to an
    average and not ours to republish race-by-race. Same posture as
    silver_bulletin.
"""
from __future__ import annotations

import json
import re

from . import (Context, LoadedArtifact, NATIONAL_HOUSE, NATIONAL_SENATE,
               RATING_LEVEL as LEVEL, Row, race_id, state_from_text)

# --------------------------------------------------------------------------
# Finding the blob
# --------------------------------------------------------------------------

_ASSIGN = re.compile(r"window\.infographicData\s*=\s*")


def _balanced(text: str, start: int) -> str | None:
    """
    Slice the JSON literal beginning at `start`, respecting strings.

    Counting braces without tracking string state is the classic way to lose:
    Infogram's blob is full of text entities containing braces, and one stray
    "{" inside a caption throws the depth count off by one for the remaining
    600KB.
    """
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if closer is None:
        return None
    depth, i, n = 0, start, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def _blob(text: str):
    """Return the parsed infographicData object, or None if this isn't an embed."""
    m = _ASSIGN.search(text)
    if not m:
        return None
    i = m.end()
    while i < len(text) and text[i] not in "{[":
        if text[i] not in " \t\r\n":
            return None
        i += 1
    raw = _balanced(text, i) if i < len(text) else None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Infogram occasionally emits a trailing ";" or wraps in a call; retry
        # after trimming anything past the last closing bracket.
        try:
            return json.loads(raw.rstrip().rstrip(";"))
        except json.JSONDecodeError:
            return None


# --------------------------------------------------------------------------
# Finding sheets inside the blob
# --------------------------------------------------------------------------

_SCALAR = (str, int, float, bool, type(None))


def _cell(v) -> str:
    """Normalise one cell to plain text. Infogram cells may be scalars or dicts."""
    if isinstance(v, dict):
        for k in ("value", "text", "label", "v"):
            if k in v:
                v = v[k]
                break
        else:
            return ""
    if v is None or isinstance(v, bool):
        return ""
    s = str(v)
    s = re.sub(r"<[^>]+>", " ", s)          # cells carry <b>, <br/>, spans
    s = s.replace("−", "-")            # unicode minus
    return re.sub(r"\s+", " ", s).strip()


def _is_row(x) -> bool:
    return isinstance(x, list) and bool(x) and all(
        isinstance(c, _SCALAR) or (isinstance(c, dict) and len(c) < 12) for c in x)


def _sheets(obj, path="", out=None, depth=0):
    """
    Yield every (path, table) in the object where table is a list of rows.

    No depth cap and no key-name filter — the two assumptions that made the
    first inspection pass come back empty.
    """
    if out is None:
        out = []
    if depth > 30:
        return out
    if isinstance(obj, list):
        if len(obj) >= 2 and all(_is_row(r) for r in obj):
            widths = {len(r) for r in obj}
            if max(widths) >= 2:
                out.append((path, [[_cell(c) for c in r] for r in obj]))
                # A sheet's rows are terminal; don't recurse into them.
                return out
        for i, v in enumerate(obj):
            _sheets(v, f"{path}[{i}]", out, depth + 1)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _sheets(v, f"{path}.{k}" if path else str(k), out, depth + 1)
    return out


# --------------------------------------------------------------------------
# Reading a sheet
# --------------------------------------------------------------------------

_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}
_ABBR = set(_STATES.values())

# "AL-01", "AL-1", "AL-AL", "AL01", "Alabama 1", "Alabama's 1st"
_DIST = re.compile(r"\b([A-Z]{2})\s*[-–]?\s*(\d{1,2}|AL)\b", re.I)
_NAMED_DIST = re.compile(r"^(.*?)(?:'s)?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)


def _seat(label: str, prefer: str) -> tuple[str, str, str, str] | None:
    """label -> (race_id, chamber, state, district), or None if it isn't a seat."""
    t = label.strip()
    if not t or len(t) > 60:
        return None
    up = t.upper()

    if prefer == "house":
        m = _DIST.search(up)
        if m and m.group(1) in _ABBR:
            st = m.group(1)
            d = 1 if m.group(2) == "AL" else int(m.group(2))
            # Race to the WH numbers at-large seats 0 ("AK - 0", "WY - 0");
            # Wikipedia writes them "AL". Both mean district 1, and a race_id
            # that disagreed with itself across sources would quietly split
            # every at-large seat into two races that never average together.
            if d == 0:
                d = 1
            if 1 <= d <= 53:
                return race_id("house", st, d), "house", st, f"{d:02d}"
        m = _NAMED_DIST.match(t)
        if m:
            st = _STATES.get(m.group(1).strip().upper())
            if st:
                d = int(m.group(2))
                if 1 <= d <= 53:
                    return race_id("house", st, d), "house", st, f"{d:02d}"
        return None

    # Senate. Check the full state name first and longest-first, or "West
    # Virginia" gets read as Virginia — the same bug that put DC in Washington.
    for name in sorted(_STATES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", up):
            st = _STATES[name]
            return race_id("senate", st), "senate", st, ""
    if up in _ABBR:
        return race_id("senate", up), "senate", up, ""
    return None


_D = re.compile(r"\bDEM|\bD\b|DEMOCRAT|\bBLUE\b", re.I)
_R = re.compile(r"\bREP\b|\bR\b|REPUBLIC|\bGOP\b|\bRED\b", re.I)
_PROB = re.compile(r"WIN|CHANCE|PROB|ODDS|LIKELIHOOD|FAVOU?RED|%", re.I)
_MARGIN = re.compile(r"MARGIN|LEAN|SPREAD|\bNET\b|FORECAST|PROJECT", re.I)
_SEATS = re.compile(r"\bSEATS?\b", re.I)


def _classify(header: str) -> tuple[str, int] | None:
    """
    header text -> (kind, sign) where kind is prob | margin | seats.

    sign 0 on a seats column means "the party is named in the ROW, not the
    column" — the shape a topline sheet takes:

        Party        | Seats
        Democrats    | 221
        Republicans  | 214

    Requiring the header to name the party missed that entirely on the first
    pass, and a topline is the one number from this source most likely to end
    up in a slide.
    """
    h = header.strip()
    if not h:
        return None
    d, r = bool(_D.search(h)), bool(_R.search(h))
    if d == r:                       # names both parties or neither
        party = 0
    else:
        party = 1 if d else -1
    if _SEATS.search(h):
        return "seats", party
    if _PROB.search(h) and party:
        return "prob", party
    if _MARGIN.search(h):
        # An unlabelled margin column is D-positive by our convention only if
        # it says so; otherwise treat a party word as the sign.
        return "margin", party or 1
    return None


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _number(cell: str) -> float | None:
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group()) if m else None


def _prob(cell: str) -> float | None:
    v = _number(cell)
    if v is None:
        return None
    if "%" in cell or v > 1.0:
        v /= 100.0
    if not (0.0 <= v <= 1.0):
        return None
    if v == 0.0 and not _NUM.search(cell.replace(",", "")):
        return None
    return v


def _margin(cell: str) -> float | None:
    """'D+3.2' / 'R+1.5' / '+3.2' / '-1.5' -> signed D-minus-R points."""
    t = cell.strip().upper()
    v = _number(t)
    if v is None:
        return None
    m = re.match(r"^\s*([DR])\s*\+?\s*", t)
    if m:
        v = abs(v) * (1 if m.group(1) == "D" else -1)
    elif "%" in t:
        # A bare percentage under a loosely-named column ("Forecast") is far
        # more likely a probability than a margin. Refuse rather than record a
        # 65-point Democratic lead.
        return None
    if not (-100.0 <= v <= 100.0):
        return None
    return v


def _live_charts(blob) -> list[dict]:
    """
    Every chart in the deck that is backed by Infogram's LIVE DATA service.

    This is the crux of the source. See the module docstring — a live chart
    ships with an empty sheet and a pointer:

        chartData.custom.live = {
            "enabled": true,
            "key": "f6b57856-...",              <- the data handle
            "provider": "atlas_google_drive",
            "sheetNames": ["Projected Lead", "Leads the Polling", "Chance to Win"],
            "title": "26 Sen - NH - Box",       <- which race
        }

    The pointers are worth surfacing even though we cannot yet dereference
    them: they are how we would notice a key rotation, and the titles are a
    complete inventory of which races the deck covers.
    """
    out = []
    def walk(o):
        if isinstance(o, dict):
            cd = o.get("chartData")
            if isinstance(cd, dict):
                lv = (cd.get("custom") or {}).get("live")
                if isinstance(lv, dict) and lv.get("enabled") and lv.get("key"):
                    out.append({**lv, "_filled": _filled(cd.get("data"))})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(blob)
    return out


def _filled(data) -> int:
    """
    Non-empty data cells in a chart's own sheets, excluding the header row and
    the label column.

    Deliberately narrow. A whole-blob cell count would be useless here: the
    deck's map charts carry a 62x62 identity matrix (every state's own column
    set to a constant 10, purely to give the map something to colour), so a
    naive count reports hundreds of "populated" cells in a deck that contains
    no forecast at all.
    """
    n = 0
    if not isinstance(data, list):
        return 0
    for sheet in data:
        if not isinstance(sheet, list):
            continue
        for row in sheet[1:]:
            if not isinstance(row, list):
                continue
            for c in row[1:]:
                if isinstance(c, dict):
                    c = c.get("value")
                if c not in (None, "", False):
                    n += 1
    return n


# --------------------------------------------------------------------------
# The live-data payload — where the numbers actually are
#
# capture.py's handle_infogram dereferences each chart's live pointer and
# stores the result. The payload is a workbook:
#
#   {"data": [ [...sheet0 rows...], [...sheet1 rows...] ],
#    "sheetNames": ["Chance to Win", "Projected Seats", "Win", "Margin", ...],
#    "refreshed": "2026-08-19T23:43:38.665Z"}
#
# These sheets are parsed by NAME rather than by sniffing their headers, and
# that is a deliberate departure from the embed-HTML path above. Header
# sniffing cannot work here: the "Win" sheet's columns are headed "D", "R",
# "Ind" and its cells read "Ossoff: 92.3%", so nothing in the header says
# "probability". The sheet name is the only thing that does — and unlike the
# headers, it is carried in the pointer, so we know it before we fetch.
# --------------------------------------------------------------------------

_SHEET_PROB = re.compile(r"^\s*(win|chance)", re.I)
# NOT "lean". The House ratings workbook has sheets named "Lean D" and
# "Lean R" — those are rating BUCKETS listing which seats fall in each
# category, not margins. Matching them produced 23 district "margins" that
# were really bucket membership, all positive, all plausible-looking, and all
# meaningless. "Margin" is the only sheet name that means a margin.
_SHEET_MARGIN = re.compile(r"margin", re.I)
_SHEET_RATING = re.compile(r"^\s*rating", re.I)
_SHEET_SEATS = re.compile(r"seat", re.I)
_SHEET_LIST = re.compile(r"^\s*list\s*$", re.I)

# "Ossoff: 92.3%" / "*Markey: 99.3%". The asterisk marks a presumed nominee.
_NAMED_PCT = re.compile(r"([-\d.]+)\s*%")
_RATING_TEXT = re.compile(
    r"(?:race\s+rating\s*:\s*)?\b(safe|solid|likely|lean[s]?|tilt[s]?|toss\s*-?\s*up|tossup)\b"
    r"(?:\s+([DR])\b)?", re.I)


def _rating_value(cell: str) -> tuple[str, float] | None:
    """'Safe R' / 'Race Rating: Tossup' / 'Tilt D' -> (label, 0..10)."""
    m = _RATING_TEXT.search(cell or "")
    if not m:
        return None
    level = re.sub(r"\s+", "", m.group(1)).upper().rstrip("S")
    if level in {"TOSSUP", "TOSS-UP"}:
        return "Toss-up", 5.0
    party = (m.group(2) or "").upper()
    base = LEVEL.get(level)
    if base is None or party not in ("D", "R"):
        return None
    return f"{m.group(1).title()} {party}", base if party == "R" else 10.0 - base


def _live_payload(art: LoadedArtifact) -> tuple[list[str], list] | None:
    """(sheetNames, sheets) for a live-data artifact, or None if it isn't one."""
    try:
        obj = art.json()
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    data = obj.get("data")
    names = obj.get("sheetNames") or art.meta.get("live_sheet_names") or []
    if not isinstance(data, list) or not isinstance(names, list):
        return None
    return [str(x) for x in names], data


# Which live workbooks carry LEVELS we can use, as opposed to derived views of
# them. Default-deny, and the reason is a bug this exact list exists to stop.
#
# The deck ships 63 live workbooks and many reuse the same sheet names for
# different quantities. "26 Sen - Biggest Shifts" has a sheet called "Margin"
# holding the CHANGE in margin since the last update. Sheet-name matching
# cannot tell it from the real thing, and because artifacts are read in name
# order it sorted ahead of "Sen 26 - Main Graphics" and claimed every Senate
# race first. The archive duly recorded Idaho at D+11.6 and Alabama at D+3.9 —
# both are R+20-plus seats, and both numbers were real, just answers to a
# different question.
#
# The same failure mode produced House seats_D and seats_R of 24 apiece, from
# map workbooks whose sheets are named "Seats Dems can Flip", and 23 bogus
# district margins from a ratings table with columns headed "Lean D"/"Lean R".
#
# An allowlist rots if Race to the WH renames a chart. That is the cheaper
# failure: a rename shows up as missing rows and raises, whereas a denylist
# fails by silently admitting the next "Biggest Movers" chart at full
# confidence. parse() reports what it skipped so a rename is visible.
_AUTHORITATIVE = re.compile(
    r"main\s+graphics|race\s+rating|detailed\s+list|table\s+summarizing", re.I)


def _parse_live(art: LoadedArtifact, ctx: Context, seen: set) -> list[Row]:
    got = _live_payload(art)
    if got is None:
        return []
    names, sheets = got
    if not _AUTHORITATIVE.search(str(art.meta.get("live_title") or "")):
        return []
    title = str(art.meta.get("live_title") or "")
    prefer = "house" if re.search(r"\bhouse\b", title, re.I) else "senate"
    rows: list[Row] = []

    def add(rid, chamber, st, dist, q, v, unit):
        if (rid, q) in seen:
            return
        seen.add((rid, q))
        rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=st,
                            district=dist, quantity=q, value=v, unit=unit))

    natl = "NATL_HOUSE_2026" if prefer == "house" else "NATL_SENATE_2026"

    for i, sheet in enumerate(sheets):
        name = names[i] if i < len(names) else ""
        if not isinstance(sheet, list) or not sheet:
            continue
        grid = [[_cell(c) for c in r] if isinstance(r, list) else [] for r in sheet]
        header = grid[0] if grid else []
        # Which columns belong to which party? On these sheets the party is in
        # the header ("", "D", "R", "Ind" / "", "Democrats", "Republicans").
        side = {}
        for j, h in enumerate(header[1:], start=1):
            d, r = bool(_D.search(h)), bool(_R.search(h))
            if d and not r:
                side[j] = 1
            elif r and not d:
                side[j] = -1

        # -- chamber toplines: party in the header, quantity in the row label
        if len(grid) >= 2 and side and not _seat(grid[1][0] if grid[1] else "", prefer):
            for r in grid[1:]:
                if not r:
                    continue
                lab = r[0]
                for j, sgn in side.items():
                    if j >= len(r) or not r[j]:
                        continue
                    if _SHEET_SEATS.search(name) or _SHEET_SEATS.search(lab):
                        v = _number(r[j])
                        if v is not None and 0 <= v <= 535:
                            add(natl, "national", "", "",
                                "seats_D" if sgn > 0 else "seats_R", v, "seats")
                    elif _SHEET_PROB.search(name) or _PROB.search(lab):
                        v = _prob(r[j])
                        if v is not None:
                            add(natl, "national", "", "", "win_prob_D",
                                round(v if sgn > 0 else 1.0 - v, 4), "prob")
            continue

        # -- per-race sheets
        for r in grid[1:]:
            if not r:
                continue
            seat = _seat(r[0], prefer)
            if not seat:
                # The House workbooks put the race in a later column
                # ("", "", "331", "PA - 7", "Incumbent: ...", "Rating: Tossup").
                for cell in r[1:5]:
                    seat = _seat(cell, prefer)
                    if seat:
                        break
            if not seat:
                continue
            rid, chamber, st, dist = seat

            if _SHEET_RATING.search(name) or _SHEET_LIST.search(name):
                for cell in r[1:]:
                    got_r = _rating_value(cell)
                    if got_r:
                        add(rid, chamber, st, dist, "rating_ordinal",
                            f"race_to_the_wh:{got_r[0]}", "ordinal")
                        add(rid, chamber, st, dist, "rating_numeric",
                            got_r[1], "ordinal")
                        break
                continue

            # Is a candidate outside the two major parties on this ballot?
            # Nebraska 2026 is Ricketts (R) v Osborn (I) with no Democrat, and
            # the sheet says so: the D cell is blank and the "Ind" column
            # carries a number. Complementing the R probability there would
            # publish Osborn's 15.6% as the DEMOCRAT'S chance — a real number
            # attached to a candidate who does not exist. Better to emit
            # nothing than something confidently wrong.
            has_other = any(bool(c) for j, c in enumerate(r)
                            if j > 0 and j not in side)

            for j, cell in enumerate(r[1:], start=1):
                if not cell:
                    continue
                sgn = side.get(j, 0)
                if _SHEET_MARGIN.search(name):
                    v = _margin(cell)
                    if v is not None:
                        add(rid, chamber, st, dist, "margin_D", round(v, 2), "pct")
                elif _SHEET_PROB.search(name):
                    if not sgn:
                        continue
                    if sgn < 0 and has_other:
                        continue          # see above — not a two-way race
                    m = _NAMED_PCT.search(cell)   # "Ossoff: 92.3%"
                    if not m:
                        continue
                    v = float(m.group(1)) / 100.0
                    if not (0.0 <= v <= 1.0):
                        continue
                    add(rid, chamber, st, dist, "win_prob_D",
                        round(v if sgn > 0 else 1.0 - v, 4), "prob")
    return rows


def _header_row(sheet: list[list[str]], prefer: str) -> int:
    """
    Index of the header row. Infogram sheets often carry a title row above it.

    Taking the first row with any classifiable cell was wrong and the synthetic
    fixture caught it: a title like "2026 Senate forecast" contains the word
    "forecast", which reads as a margin keyword, so the title won and the real
    header became the first data row.

    Two signals instead, in order of strength:
      1. the rows BELOW it parse as seats — decisive, and independent of how
         the columns happen to be worded
      2. how many of its own cells classify — the tie-breaker
    """
    best, best_score = 0, (-1, -1)
    for i, row in enumerate(sheet[:3]):
        if i + 1 >= len(sheet):
            break
        named = sum(1 for r in sheet[i + 1:i + 6] if r and _seat(r[0], prefer))
        cls = sum(1 for c in row if _classify(c))
        if cls == 0 and named == 0:
            continue
        score = (named, cls)
        if score > best_score:
            best, best_score = i, score
    return best


# --------------------------------------------------------------------------

def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    rows: list[Row] = []
    seen: set[tuple[str, str]] = set()
    embeds = 0
    sheets_seen = 0
    live_total = 0
    live_payloads = 0
    populated_cells = 0
    live_titles: list[str] = []
    used_books: list[str] = []
    skipped_books: list[str] = []
    header_samples: list[str] = []

    # PREFERRED ARTIFACT — WHY THE TREND BOOKS ARE READ FIRST.
    #
    # Race to the WH publishes the same numbers in more than one Infogram, and
    # before this ordering existed both reached the archive. Every Senate race
    # got two margin rows on 39 races across 7 dates, and aggregate.py averaged
    # them within the source, so the published margin was the mean of two
    # readings that were not the same reading.
    #
    # They are not two roundings. On 2026-08-26 the state-trend workbook was
    # refreshed at 11:34Z with a row dated Aug 26, while Sen-26 Main Graphics
    # was refreshed two minutes later carrying its own "Last Updated on Aug 25"
    # stamp. Main Graphics is a day behind. That is SEN_IA at -1.15 against
    # -0.90: a lag, not a rounding.
    #
    # So the time series wins any cell it covers on the current date, and the
    # snapshot books fill in what only they have — the 435 per-district
    # probabilities, the Senate per-race chance-to-win, the seat ratings. The
    # mechanism is the `seen` claim set both paths already share; ordering is
    # all that had to change.
    #
    # SELECT BY EMBED ID, NOT TITLE, if this ever needs tightening further.
    # `Sen-26 - Main Graphics` exists under two different embed ids
    # (6911ecb8 and d6de4ea5) and only one of them yields rows, so a title is
    # not a unique name for a workbook here.
    trend_books: list[tuple[str, LoadedArtifact, str | None]] = []
    other_books: list[tuple[str, LoadedArtifact, str | None]] = []
    for name, art in artifacts.items():
        is_live = name.startswith("live__") or _live_payload(art) is not None
        kind = trend_kind(str(art.meta.get("live_title") or name)) if is_live else None
        (trend_books if kind else other_books).append((name, art, kind))

    for name, art, kind in trend_books + other_books:
        # The live-data workbooks first — since handle_infogram was added these
        # are where every actual number comes from.
        if name.startswith("live__") or _live_payload(art) is not None:
            live_payloads += 1
            title = str(art.meta.get("live_title") or name)
            if kind:
                # A time series, not a snapshot. Every row is backdated to its
                # own observation date; see the BACKFILL section at the bottom.
                # Rows landing on TODAY also claim their cell, so the snapshot
                # books below do not emit it again.
                got = _parse_trend(art, ctx, kind, seen)
                (used_books if got else skipped_books).append(f"{title} [trend]")
                rows.extend(got)
                continue
            if _AUTHORITATIVE.search(title):
                used_books.append(title)
            else:
                skipped_books.append(title)
            rows.extend(_parse_live(art, ctx, seen))
            continue

        blob = _blob(art.text())
        if blob is None:
            continue                      # the Squarespace pages, as expected
        embeds += 1
        live = _live_charts(blob)
        live_total += len(live)
        populated_cells += sum(l["_filled"] for l in live)
        live_titles += [str(l.get("title") or "?") for l in live]

        # The embed's own name tells us the chamber; the sheets rarely do.
        prefer = "house" if "house" in name.lower() else (
            "senate" if "senate" in name.lower() else "")
        if not prefer:
            title = str(blob.get("title", "")).lower()
            prefer = "house" if "house" in title else "senate"

        for path, sheet in _sheets(blob):
            hi = _header_row(sheet, prefer)
            headers = sheet[hi]
            cols = {i: k for i, h in enumerate(headers) if (k := _classify(h))}
            if not cols:
                continue
            sheets_seen += 1
            if len(header_samples) < 6:
                header_samples.append(f"{path}: {' | '.join(headers[:8])}")

            for cells in sheet[hi + 1:]:
                if not cells:
                    continue
                label = cells[0]

                # Chamber topline: a row keyed by party rather than by seat.
                party_row = 0
                if not _seat(label, prefer):
                    if _D.search(label) and not _R.search(label):
                        party_row = 1
                    elif _R.search(label):
                        party_row = -1

                seat = _seat(label, prefer)
                if not seat and not party_row:
                    continue

                for i, (kind, sign) in cols.items():
                    if i >= len(cells):
                        continue
                    cell = cells[i]
                    if not cell:
                        continue

                    if kind == "seats":
                        # Topline only; a per-seat "seats" column is meaningless.
                        if seat:
                            continue
                        v = _number(cell)
                        if v is None or not (0 <= v <= 535):
                            continue
                        side = sign or party_row
                        if not side:
                            continue      # neither header nor row names a party
                        q = "seats_D" if side > 0 else "seats_R"
                        rid = "NATL_HOUSE_2026" if prefer == "house" else "NATL_SENATE_2026"
                        key = (rid, q)
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append(ctx.row(art, race_id=rid, chamber="national",
                                            quantity=q, value=v, unit="seats"))
                        continue

                    if not seat:
                        continue
                    rid, chamber, st, dist = seat

                    if kind == "prob":
                        v = _prob(cell)
                        if v is None:
                            continue
                        # Store everything as the D probability; an R column is
                        # the same information, complemented.
                        if sign < 0:
                            v = 1.0 - v
                        q = "win_prob_D"
                    else:
                        v = _margin(cell)
                        if v is None:
                            continue
                        if sign < 0:
                            v = -v
                        q = "margin_D"

                    key = (rid, q)
                    if key in seen:
                        continue          # infogram_house and _alt overlap
                    seen.add(key)
                    rows.append(ctx.row(art, race_id=rid, chamber=chamber,
                                        state=st, district=dist, quantity=q,
                                        value=v,
                                        unit="prob" if q == "win_prob_D" else "pct"))

    if rows:
        if live_payloads and not used_books:
            raise ValueError(
                f"{live_payloads} live workbook(s) captured but NONE matched the "
                f"authoritative allowlist — Race to the WH has probably renamed "
                f"its master charts. Titles seen: "
                f"{sorted(set(skipped_books))[:8]}")
        return rows

    # ---------------------------------------------------------------------
    # Zero rows. Which of the two very different reasons is it?
    #
    # [] means "this capture genuinely contained no forecasts"; an exception
    # means "something changed, a human should look". Getting that distinction
    # right matters more here than anywhere else in the pipeline, because for
    # this source the empty case is the NORMAL case and will be until the
    # live-data endpoint is added to the registry. A parser that raised every
    # day would train us to ignore its output — and this is the one source
    # where we most need to notice the day the deck starts carrying numbers.
    # ---------------------------------------------------------------------
    if embeds and live_total and populated_cells == 0 and live_payloads == 0:
        # Shells captured, but their live pointers were never dereferenced —
        # i.e. this date predates handle_infogram, or capture ran with the old
        # `http` method. Genuinely empty, and no parser can ever fix it.
        return []

    detail = "\n        ".join(header_samples) or "(no sheet had a recognisable header)"
    raise ValueError(
        f"read {len(artifacts)} artifact(s); {embeds} embed shell(s), "
        f"{live_payloads} live-data workbook(s), {live_total} live pointer(s) "
        f"with {populated_cells} filled inline cell(s), {sheets_seen} "
        f"classifiable sheet(s) — but no row parsed as a seat.\n"
        f"      This is NOT the usual empty-shell case (which returns no rows "
        f"quietly): either the deck now carries inline data we failed to read, "
        f"or the live pointers are gone.\n"
        f"      Headers seen:\n        {detail}\n"
        f"      Live chart titles: {', '.join(live_titles[:6])}\n"
        f"      Dump every sheet with:\n"
        f"        python3 -m forecast.collect.parsers.race_to_the_wh "
        f"forecast/data/2026/raw/race_to_the_wh/<date>/infogram_senate.html")


# --------------------------------------------------------------------------
# Standalone dump. Not part of the parser contract — a debugging aid, because
# the whole difficulty of this source is that you cannot see the data.
#
#   python3 forecast/collect/parsers/race_to_the_wh.py path/to/infogram_house.html
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for p in sys.argv[1:]:
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        blob = _blob(text)
        print("=" * 72)
        print(f"{p}  ({len(text):,} chars)")
        if blob is None:
            print("  no window.infographicData found")
            continue
        print(f"  title: {blob.get('title')!r}   updatedAt: {blob.get('updatedAt')}")
        live = _live_charts(blob)
        filled = sum(l["_filled"] for l in live)
        print(f"  {len(live)} live-data chart(s), {filled} filled cell(s) among them"
              + ("   <- EMPTY SHELL: the numbers are fetched at render time"
                 if live and not filled else ""))
        for l in live[:8]:
            print(f"     {str(l.get('title'))[:34]:34s} key={l.get('key')} "
                  f"sheets={l.get('sheetNames')}")
        if len(live) > 8:
            print(f"     ... {len(live) - 8} more")
        found = _sheets(blob)
        print(f"\n  {len(found)} candidate sheet(s)\n")
        for path, sheet in found:
            w = max(len(r) for r in sheet)
            print(f"  ── {path}   {len(sheet)} rows x {w} cols")
            for r in sheet[:6]:
                print("       " + " | ".join(c[:22] for c in r[:8]))
            if len(sheet) > 6:
                print(f"       ... {len(sheet) - 6} more rows")
            print()


# ==========================================================================
# BACKFILL — the trend workbooks, which carry their own history.
#
# Race to the WH publishes several charts whose x-axis is TIME: the national
# Senate and House trends, and one sheet per state of Senate margins. Between
# them they run from September 2025 to yesterday, at roughly four-day
# intervals. That is six months of a professional forecast that no daily
# capture could ever recover, sitting inside bytes we already store.
#
# These workbooks are deliberately OUTSIDE _AUTHORITATIVE. Read as current
# values they would be wrong — every row is a past observation, and the last
# row is yesterday rather than today. They are parsed here instead, and every
# row they produce is BACKDATED to the observation's own date via the
# snapshot_date override in Context.row().
#
# THE YEAR PROBLEM. Cells read "Jan 1", "Aug 19", "Sep 1" with no year, and
# the House series begins in September of the PREVIOUS year. There is no way
# to read a year off a single cell, so the series is walked backwards from its
# final row — which is dated at or just before the capture — and the year is
# decremented whenever the calendar goes forwards as we go backwards.
# ==========================================================================

_TREND_BOOKS = (
    (re.compile(r"sen.*publish\s*trend.*nat", re.I), "senate_national"),
    (re.compile(r"house.*national\s+line\s+graph\s+trend", re.I), "house_national"),
    (re.compile(r"sen.*state\s+trend.*margin", re.I), "senate_state_margin"),
)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_CELL = re.compile(r"^\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\s*$")


def trend_kind(title: str) -> str | None:
    """Match on a normalised title so the artifact FILENAME works too.

    live_title comes from the capture metadata and reads "26 Sen - Publish
    State Trend - Margin"; the stored filename for the same workbook is
    "live__26-Sen---Publish-State-Trend---Margin__796ff7b8". Patterns written
    against one silently fail against the other, and the fallback path from
    name to title is exactly where a missing meta file sends you.
    """
    flat = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    for pat, kind in _TREND_BOOKS:
        if pat.search(flat):
            return kind
    return None


def _trend_dates(cells: list[str], last_date: str) -> list[str | None]:
    """Month-day cells -> ISO dates, walking backwards from `last_date`."""
    import datetime as dt
    end = dt.date.fromisoformat(last_date)
    parsed = []
    for c in cells:
        m = _DATE_CELL.match(_cell(c))
        parsed.append((_MONTHS.get(m.group(1).lower()), int(m.group(2)))
                      if m and _MONTHS.get(m.group(1).lower()) else None)

    out: list[str | None] = [None] * len(parsed)
    year = end.year
    nxt: tuple[int, int] | None = None
    for i in range(len(parsed) - 1, -1, -1):
        md = parsed[i]
        if md is None:
            continue
        if nxt is not None and md > nxt:
            year -= 1                    # the calendar went forwards: last year
        nxt = md
        try:
            d = dt.date(year, md[0], md[1])
        except ValueError:               # 29 Feb in a non-leap year, etc.
            continue
        if d > end:
            continue                     # never date a row past its evidence
        out[i] = d.isoformat()
    return out


def _pct_or_num(cell: str) -> float | None:
    """'54.2%' -> 54.2, '50.8' -> 50.8, '' -> None. The caller scales."""
    return _number(_cell(cell).replace("%", ""))


def _parse_trend(art: LoadedArtifact, ctx: Context, kind: str,
                 seen: set | None = None) -> list[Row]:
    """Parse one time-series workbook.

    `seen` is the same (race_id, quantity) claim set `_parse_live` uses. A
    trend row dated at the CURRENT snapshot registers its claim there, which
    is what stops the snapshot books emitting the same cell a second time —
    see the PREFERRED ARTIFACT note above parse(). Backdated rows never
    claim: they are for dates the snapshot books say nothing about.
    """
    payload = _live_payload(art)
    if payload is None:
        return []
    names, sheets = payload
    rows: list[Row] = []
    # The final observation is at or before the capture; anchoring on the
    # capture date itself would push a series one day into the future when the
    # workbook is refreshed after midnight UTC.
    last_date = ctx.snapshot_date

    for name, sheet in zip(names, sheets):
        if not sheet or len(sheet) < 3 or _SHEET_LIST.match(str(name)):
            continue
        header = [_cell(c) for c in sheet[0]]
        body = sheet[1:]
        dates = _trend_dates([r[0] if r else "" for r in body], last_date)

        if kind == "senate_state_margin":
            st = state_from_text(str(name))
            if not st:
                continue
            col = next((i for i, h in enumerate(header)
                        if _SHEET_MARGIN.search(h)), None)
            if col is None:
                continue
            spec = [(race_id("senate", st), "senate", st, "margin_D", col, "pct", 1.0)]
        elif kind in ("senate_national", "house_national"):
            rid = NATIONAL_SENATE if kind == "senate_national" else NATIONAL_HOUSE
            ch = "national"
            dcol = next((i for i, h in enumerate(header[1:], start=1)
                         if re.search(r"^dem", h, re.I)), None)
            if dcol is None:
                continue
            if _SHEET_PROB.search(str(name)):
                spec = [(rid, ch, "", "win_prob_D", dcol, "prob", 0.01)]
            elif _SHEET_SEATS.search(str(name)):
                spec = [(rid, ch, "", "seats_D", dcol, "seats", 1.0)]
            else:
                continue
        else:
            continue

        for r, asof in zip(body, dates):
            if not asof:
                continue
            for rid, ch, st_, qty, col, unit, scale in spec:
                if col >= len(r):
                    continue
                v = _pct_or_num(r[col])
                if v is None:
                    continue
                if seen is not None and asof == ctx.snapshot_date:
                    seen.add((rid, qty))
                rows.append(ctx.row(art, snapshot_date=asof, race_id=rid,
                                    chamber=ch, state=st_, quantity=qty,
                                    value=round(v * scale, 4), unit=unit))
    return rows
