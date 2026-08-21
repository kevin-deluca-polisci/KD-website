"""
Phase 2 parsers. One module per source.

THE CONTRACT
    Every parser exposes:   parse(artifacts, ctx) -> list[Row]

    `artifacts` is {name: LoadedArtifact} for one source on one date, already
    read off disk. `ctx` carries the source's registry entry and the snapshot
    date. A parser NEVER makes a network request — if you find yourself wanting
    to, the URL belongs in the registry and the bytes belong in raw/.

WHY PARSERS ARE SEPARATE FROM CAPTURE
    Parsers read from storage, so they can be rewritten at any time and re-run
    over every date ever captured. When a source changes its page structure in
    October and a parser silently starts returning garbage, you fix the parser
    and reprocess the whole history. That is only possible because capture
    stored the bytes rather than the interpretation.

FAIL LOUDLY
    A parser that cannot find what it expects must raise, not return []. An
    empty list means "this capture genuinely contained no forecasts"; an
    exception means "something changed and a human needs to look". Silent
    degradation to zero rows is the single worst failure mode here, because it
    looks identical to a quiet week.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# The long format. Every source, every quantity, one shape.
# ---------------------------------------------------------------------------

QUANTITIES = {
    "win_prob_R",     # 0–1 probability the Republican wins
    "win_prob_D",
    "vote_share_R",   # Republican two-party share, 0–100
    "margin_D",       # D minus R, percentage points (national or race)
    "seats_R",        # seat count
    "seats_D",
    "rating_ordinal", # "Lean R" etc. — string, kept out of dispersion
    "rating_numeric", # 0 = Solid D .. 10 = Solid R, where a source publishes it
    "turnout_pct",
    "pvi",            # Cook Partisan Voting Index, signed: negative = R-leaning
    "pvi_prior",      # same, on the PREVIOUS map — the pair shows redistricting
    "margin_D_adjusted",      # house-effect / likely-voter adjusted polling margin
    "margin_D_pres_2024",     # statewide 2024 presidential two-party margin
    "margin_D_prior_senate",  # statewide 2024 Senate two-party margin
    # Economic inputs to the fundamentals model. NOT forecasts — they are
    # right-hand-side variables, and aggregate.py excludes them from every
    # average via NOT_A_FORECAST. Three variants because the election-year
    # annual figure does not exist until the year after the election, so
    # something has to stand in for it and the choice is a judgment call
    # that deserves to be visible. See parsers/fred.py.
    "income_growth_last_full_year",
    "income_growth_ytd",
    "income_growth_yoy_latest_month",
    "income_ytd_months",
}

# Chamber-level pseudo-races. Same ID convention as real races so the class
# submission CSV merges without translation.
NATIONAL_HOUSE = "NATL_HOUSE_2026"
NATIONAL_SENATE = "NATL_SENATE_2026"

# The ordinal rating scale, 0 = Solid D .. 10 = Solid R, matching Inside
# Elections' own numeric scale. Expressed on the R side; mirror for D.
#
# THIS LIVES HERE RATHER THAN IN EACH PARSER ON PURPOSE. Two sources that
# disagreed about whether "Lean" is 7.0 or 7.5 would produce a rating_numeric
# average that is not an average of anything — and the discrepancy would be
# invisible, because both numbers are individually plausible.
RATING_LEVEL = {
    "SOLID": 10.0, "SAFE": 10.0, "LIKELY": 8.5, "LEAN": 7.0, "LEANS": 7.0,
    "TILT": 6.0, "TILTS": 6.0,
}
TOSSUP_LABELS = {"TOSSUP", "TOSS-UP", "TOSS UP"}

_STATE = re.compile(r"^[A-Z]{2}$")

# The real postal codes. Kept here rather than in any one parser because
# race_id() validates against it, which makes "is this a state?" a single
# question with a single answer.
#
# WHY THIS EXISTS: polymarket's title matcher was
#     r"senate.*\b([A-Z]{2})\b|\b([A-Z]{2})\b.*senate"  with re.IGNORECASE
# and [A-Z]{2} under IGNORECASE matches ANY two letters. "Balance of power in
# the Senate" duly yielded state "OF", and every Senate market in the archive
# landed on a race called SEN_OF_2026. Nothing caught it: "OF" is two
# characters, so Row.validate()'s ^[A-Z]{2}$ check passed, the rows looked
# well-formed, and the damage only surfaced when the polling model asked which
# states hold a Senate race and got an answer of one.
POSTAL = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


def is_state(code: str) -> bool:
    return (code or "").strip().upper() in POSTAL


STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI",
    "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}
# Longest first, so "West Virginia" is tested before "Virginia" and "New York"
# before "York". Same ordering bug that once filed Washington DC as Washington
# state.
_NAMES_ORDERED = sorted(STATE_NAMES, key=len, reverse=True)
_NAME_RE = re.compile(r"\b(" + "|".join(re.escape(n) for n in _NAMES_ORDERED)
                      + r")\b", re.I)
# Case-SENSITIVE. See state_from_text().
_ABBR_RE = re.compile(r"\b([A-Z]{2})\b")


def state_from_text(text: str) -> str | None:
    """
    Find the state a market title or table cell refers to.

    NAMES FIRST, AND ABBREVIATIONS ONLY IN CAPITALS. This ordering is the
    whole point of the function.

    Matching a bare two-letter token case-insensitively looks reasonable and
    is quietly catastrophic, because a dozen postal codes are also ordinary
    English words. "Will Republicans win the Senate in 2026?" contains "in",
    and IN is Indiana. So is OR, ME, HI, OK, DE, LA, PA, MA, OH, ID and AL.

    The first version of this guard only checked that the token was a real
    postal code, which killed the obvious "OF" case and left the subtle ones
    untouched — the archive then claimed Senate races in Arizona, California,
    Connecticut, Hawaii, Indiana, Maryland, Nevada, New York, Pennsylvania,
    Utah, Vermont, Washington and Wisconsin, none of which are on the 2026
    map. That is worse than the "OF" bug: it is wrong in a way that looks
    right, and it would have fed a plausible-looking Senate forecast for
    fifteen races that do not exist.
    """
    if not text:
        return None
    m = _NAME_RE.search(text)
    if m:
        return STATE_NAMES[m.group(1).upper()]
    for mm in _ABBR_RE.finditer(text):
        if mm.group(1) in POSTAL:
            return mm.group(1)
    return None


@dataclass
class Row:
    snapshot_date: str
    source_id: str
    category: str
    publication: str        # individual | aggregate_only | private — stamped
                            # here so the aggregator can enforce the tier without
                            # having to re-read the registry
    race_id: str
    chamber: str            # house | senate | governor | national
    state: str              # "" for national
    district: str           # "" unless house
    quantity: str
    value: Any              # float, or str for rating_ordinal
    unit: str               # prob | pct | seats | ordinal
    captured_at: str
    raw_sha256: str
    raw_path: str

    def validate(self) -> None:
        if self.quantity not in QUANTITIES:
            raise ValueError(f"unknown quantity {self.quantity!r}")
        if self.quantity == "rating_ordinal":
            if not isinstance(self.value, str) or not self.value:
                raise ValueError("rating_ordinal must be a non-empty string")
        else:
            v = float(self.value)
            bounds = {"prob": (0, 1), "pct": (-100, 100), "seats": (0, 535),
                      "ordinal": (0, 10)}.get(self.unit)
            if bounds and not (bounds[0] <= v <= bounds[1]):
                raise ValueError(
                    f"{self.quantity}={v} outside {bounds} for unit {self.unit!r}")
        if self.state and not _STATE.match(self.state):
            raise ValueError(f"state {self.state!r} is not a 2-letter code")


FIELDS = [f.name for f in dataclasses.fields(Row)]


@dataclass
class LoadedArtifact:
    """One stored capture: its bytes, its sidecar metadata, and where it came from."""
    name: str
    path: Path
    body: bytes
    meta: dict = field(default_factory=dict)

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except json.JSONDecodeError as e:
            raise ValueError(f"{self.path.name} is not valid JSON: {e}") from e

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def sha256(self) -> str:
        return self.meta.get("sha256", "")


@dataclass
class Context:
    source: dict            # the registry entry
    snapshot_date: str

    @property
    def source_id(self) -> str:
        return self.source["id"]

    @property
    def category(self) -> str:
        return self.source.get("category", "")

    @property
    def publication(self) -> str:
        return self.source.get("publication", "private")

    def row(self, art: LoadedArtifact, publication: str | None = None, **kw) -> Row:
        """
        Build a Row with the provenance fields prefilled from context.

        `publication` overrides the source's default tier for THIS row. Needed
        because a single permitted source can carry a field that is itself
        gated — Grant Williams republishes Cook PVI under MIT, so his forecast
        rows are publishable but the PVI rows are not. Without a per-row
        override, a permitted source would launder a gated field into the
        public tier.

        The override may only ever be MORE restrictive, never less.
        """
        _RANK = {"individual": 0, "aggregate_only": 1, "private": 2}
        tier = self.publication
        if publication is not None:
            if _RANK.get(publication, 2) < _RANK.get(tier, 2):
                raise ValueError(
                    f"parser tried to loosen the tier from {tier!r} to "
                    f"{publication!r}; overrides may only restrict")
            tier = publication
        kw.setdefault("chamber", "national")
        kw.setdefault("state", "")
        kw.setdefault("district", "")
        r = Row(
            snapshot_date=self.snapshot_date,
            source_id=self.source_id,
            category=self.category,
            publication=tier,
            captured_at=art.meta.get("fetched_at", ""),
            raw_sha256=art.sha256,
            raw_path=str(art.path),
            **kw,
        )
        r.validate()
        return r


# ---------------------------------------------------------------------------
# Race IDs. The map vintage matters: 10 states redrew for 2026 and 145
# districts actually changed, so a district number alone is ambiguous across
# cycles. The cycle suffix carries the vintage.
# ---------------------------------------------------------------------------

MARGIN_BUCKET_DEFAULT_WIDTH = 2.0


def margin_ladder_expectation(buckets: list) -> float | None:
    """Expected Democratic margin from a priced ladder of continuous buckets.

    buckets: [((lo, hi), price)], margins in points of D margin, signed, with
    None for an open end. Kalshi and Polymarket both quote a national House
    popular-vote margin as a ladder of this shape, so the arithmetic lives here
    rather than in either parser — two copies of a statistical convention is
    two copies that drift, and the whole point of collecting both is that their
    answers are comparable.

    Prices are bid/ask midpoints across a dozen markets and each carries its
    own spread, so they are normalised by their sum rather than trusted to be
    a distribution.

    An open end is represented as one more bucket of the mean closed width.
    There is no honest way to read a tail off a market: "16% and above" is a
    claim about everything up to a landslide, and Kalshi's "Republicans win" is
    a claim about the entire other half of the line. Widening it to the real
    span would let a thinly traded end bucket dominate the mean. This
    understates the tails on purpose, and the understatement is small — on the
    live book it moves the answer by less than two tenths of a point across any
    plausible assumption — but it IS an assumption and the methods page says so.

    Returns only the expectation. The mass above zero is P(D wins the popular
    vote), which is NOT the same event as winning the chamber, and win_prob_D
    on a national race already means the chamber. Two questions, one name, is
    how a comparison table starts reporting disagreement that is really a
    definition mismatch.
    """
    closed = [(lo, hi) for (lo, hi), _ in buckets
              if lo is not None and hi is not None]
    width = (sum(hi - lo for lo, hi in closed) / len(closed)) if closed \
        else MARGIN_BUCKET_DEFAULT_WIDTH

    num = den = 0.0
    for (lo, hi), p in buckets:
        if p is None or p <= 0 or (lo is None and hi is None):
            continue
        if lo is None:
            rep = hi - width / 2.0
        elif hi is None:
            rep = lo + width / 2.0
        else:
            rep = (lo + hi) / 2.0
        num += p * rep
        den += p
    return (num / den) if den > 0 else None


def race_id(chamber: str, state: str = "", district: str = "",
            cycle: int = 2026, special: bool = False) -> str:
    ch = {"house": "HOU", "senate": "SEN", "governor": "GOV"}.get(chamber.lower())
    if ch is None:
        raise ValueError(f"unknown chamber {chamber!r}")
    if not is_state(state):
        # Refuse at the mint rather than validating downstream. A race_id is
        # the join key for the whole archive: one bogus one does not error, it
        # silently creates a race that no other source can ever match.
        raise ValueError(f"{state!r} is not a US state — refusing to build a race_id")
    parts = [ch, state.upper()]
    if ch == "HOU":
        parts.append(f"{int(district):02d}")
    if special:
        parts.append("SP")
    parts.append(str(cycle))
    return "_".join(parts)


def load(source_id: str, snapshot_date: str, raw_root: Path) -> dict[str, LoadedArtifact]:
    """Read every stored artifact for one source on one date."""
    d = raw_root / source_id / snapshot_date
    if not d.is_dir():
        return {}
    out: dict[str, LoadedArtifact] = {}
    for p in sorted(d.iterdir()):
        if p.suffix == ".json" and p.name.endswith(".meta.json"):
            continue
        if not p.is_file():
            continue
        meta_path = p.with_suffix("").with_suffix(".meta.json")
        if not meta_path.exists():
            meta_path = p.parent / (p.stem + ".meta.json")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                pass
        out[p.stem] = LoadedArtifact(name=p.stem, path=p, body=p.read_bytes(), meta=meta)
    return out


def load_static(source_id: str, snapshot_date: str, raw_root: Path) -> dict[str, LoadedArtifact]:
    """
    Like load(), but for sources whose data does not change.

    Starts from `snapshot_date` and walks BACKWARDS, adding any artifact name
    not already seen. Certified election returns are the case this exists for:
    a file hand-imported on one date would otherwise disappear from the parser's
    view as soon as the next day's capture created a new directory, silently
    taking the 2020 cycle — and therefore the two-cycle state PVI — with it.
    """
    d = raw_root / source_id
    if not d.is_dir():
        return {}
    dates = sorted((p.name for p in d.iterdir()
                    if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)),
                   reverse=True)
    out: dict[str, LoadedArtifact] = {}
    for dt_ in dates:
        if dt_ > snapshot_date:
            continue
        for name, art in load(source_id, dt_, raw_root).items():
            out.setdefault(name, art)
    return out


def get(source_id: str):
    """Return the parser module for a source, or None if none is written yet."""
    try:
        return importlib.import_module(f"{__name__}.{source_id}")
    except ModuleNotFoundError:
        return None
