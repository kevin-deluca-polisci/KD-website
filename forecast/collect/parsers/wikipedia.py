"""
Wikipedia race ratings. Publication: individual (CC BY-SA 4.0, attribution required).

The most valuable source in the registry for one reason: revision history means
it is the only source that recovers the PAST. Everything else exists only going
forward from the day capture started.

Ordinal ratings are collected but deliberately kept OUT of the dispersion
figure downstream — "Lean R" does not average with a vote share, and the
crosswalk is a judgment call better spent as a class discussion.

Caveat worth remembering: the tables lag the forecasters by weeks (June dates
were still showing in August). The "as of" date in the table is authoritative,
not the revision timestamp.
"""
from __future__ import annotations
import re
from . import Context, LoadedArtifact, Row, race_id

RATINGS = {
    "solid d": 0, "safe d": 0, "likely d": 1.5, "lean d": 3, "leans d": 3,
    "tilt d": 4, "tilts d": 4, "toss-up": 5, "tossup": 5, "toss up": 5,
    "tilt r": 6, "tilts r": 6, "lean r": 7, "leans r": 7, "likely r": 8.5,
    "solid r": 10, "safe r": 10,
}
_FORECASTERS = {
    "cook": "cook", "inside elections": "inside_elections", "sabato": "sabato",
    "crystal ball": "sabato", "race to the wh": "race_to_the_wh",
    "the economist": "economist", "economist": "economist", "votehub": "votehub",
    "split ticket": "split_ticket", "the argument": "split_ticket",
    "dднq": "ddhq", "ddhq": "ddhq", "decision desk": "ddhq",
}
_ROW = re.compile(r"^\|\s*(.+)$")
_SEAT = re.compile(r"\b([A-Z]{2})[‑–-](\d{1,2}|AL)\b")
_STATE_SEN = re.compile(r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|"
                        r"Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|"
                        r"Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
                        r"Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|"
                        r"New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|"
                        r"Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|"
                        r"Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b")
_ABBR = {"Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
 "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA","Hawaii":"HI",
 "Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS","Kentucky":"KY",
 "Louisiana":"LA","Maine":"ME","Maryland":"MD","Massachusetts":"MA","Michigan":"MI",
 "Minnesota":"MN","Mississippi":"MS","Missouri":"MO","Montana":"MT","Nebraska":"NE",
 "Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM","New York":"NY",
 "North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK","Oregon":"OR",
 "Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD",
 "Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA",
 "West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY"}


def _clean(cell: str) -> str:
    cell = re.sub(r"\{\{[^}]*\|([^|}]+)\}\}", r"\1", cell)   # templates
    cell = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", cell)  # wikilinks
    cell = re.sub(r"<[^>]+>", " ", cell)                     # html
    cell = re.sub(r"'{2,}", "", cell)                        # bold/italic
    return cell.strip(" |")


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    rows: list[Row] = []
    pages = 0
    for art in artifacts.values():
        payload = art.json()
        text = (payload.get("parse", {}) or {}).get("wikitext", "")
        if isinstance(text, dict):
            text = text.get("*", "")
        if not text:
            continue
        pages += 1
        header: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("!"):
                cells = [_clean(c) for c in re.split(r"!!|\|\|", line.lstrip("!"))]
                mapped = [_FORECASTERS.get(c.strip().lower(), "") for c in cells]
                if sum(1 for m in mapped if m) >= 2:
                    header = mapped
                continue
            m = _ROW.match(line)
            if not m or not header:
                continue
            cells = [_clean(c) for c in re.split(r"\|\|", m.group(1))]
            if len(cells) < 2:
                continue
            label = cells[0]
            rid = ch = st = dist = None
            if (sm := _SEAT.search(label)):
                st, d = sm.group(1), sm.group(2)
                d = "1" if d == "AL" else d
                try:
                    rid, ch, dist = race_id("house", st, d), "house", f"{int(d):02d}"
                except ValueError:
                    continue
            elif (sm := _STATE_SEN.search(label)):
                st = _ABBR[sm.group(1)]
                gov = re.search(r"govern", label, re.I)
                rid = race_id("governor" if gov else "senate", st)
                ch, dist = ("governor" if gov else "senate"), ""
            if rid is None:
                continue
            for i, cell in enumerate(cells):
                if i >= len(header) or not header[i] or not cell:
                    continue
                key = cell.strip().lower()
                if key not in RATINGS:
                    continue
                # Attribute to the forecaster named in the column, but keep the
                # source_id as wikipedia — this is Wikipedia's transcription of
                # a rating, not a direct capture from that forecaster.
                rows.append(ctx.row(art, race_id=rid, chamber=ch, state=st,
                                    district=dist or "", quantity="rating_ordinal",
                                    value=f"{header[i]}:{cell.strip()}", unit="ordinal"))
                rows.append(ctx.row(art, race_id=rid, chamber=ch, state=st,
                                    district=dist or "", quantity="rating_numeric",
                                    value=RATINGS[key], unit="ordinal"))
    if pages and not rows:
        raise ValueError(
            "read wikitext but matched no ratings rows — the table markup has "
            "probably changed. Check the header-detection heuristic.")
    return rows
