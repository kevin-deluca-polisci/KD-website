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

# --------------------------------------------------------------------------
# PROVENANCE: is this row a real-time forecast for the date it carries?
#
# The archive holds three-quarters of its rows at dates before daily capture
# began, and they are not all the same kind of thing. Telling them apart is the
# difference between a timeline study and a circular one, so it is a field
# rather than a footnote.
#
#   captured        we fetched it from the publisher THAT DAY.
#   computed        one of our own models produced it that day, from inputs we
#                   held that day. Real-time; simply ours rather than theirs.
#   archival        recovered later from the publisher's OWN DATED RECORD — an
#                   exchange's candlesticks, a Wikipedia revision, Ray Fair's
#                   dated table. The publisher committed to the number on that
#                   date; we merely read the commitment afterwards. Counts as
#                   real-time, because it was.
#   retrospective   WE computed it later for an earlier date, from data as it
#                   stands now. Our reconstructed poll averages and the model
#                   backfills. Not a real-time forecast and must never be
#                   scored as one: the inputs have been revised since, and the
#                   method was chosen in August 2026 with the cycle visible.
#                   Descriptive history, and useful, but not evidence about
#                   what was knowable at the time.
#
# The first three are real-time. Scoring filters on that; the site does not.
PROVENANCE = {"captured", "computed", "archival", "retrospective"}
REALTIME_PROVENANCE = {"captured", "computed", "archival"}

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
    # --- MARKET MICROSTRUCTURE -------------------------------------------
    # Not forecasts. A price a forecast can be TRADED at, which is a different
    # object and must never enter a category average: aggregate.py lists these
    # in NOT_A_FORECAST for the same reason it lists the FRED series.
    #
    # WHY THEY EXIST. The archive stores the bid/ask MIDPOINT as each market's
    # probability, which is the right answer to "what does the market think"
    # and the wrong one to "what would this have cost you". You buy at the ask
    # and sell at the bid, and on a three-cent spread that difference decides
    # the sign of every marginal bet in a portfolio. Depth matters for the same
    # reason: an edge you cannot take size on is not an edge.
    #
    # The side suffix follows win_prob_D/win_prob_R: `price_ask_D` is what one
    # dollar of the Democratic outcome costs to buy.
    "price_bid_D", "price_ask_D",
    "price_bid_R", "price_ask_R",
    # DEPTH CARRIES THE SIDE TOO, and that is not pedantry. On Polymarket a
    # race is often two separate books — one on the Democrat winning, one on
    # the Republican — with their own volume and their own resting depth. The
    # first version of this emitted `market_volume` from each of them, so a
    # race ended up with two rows of the same name and no way to tell which
    # book either belonged to. The question the portfolio work actually asks is
    # "can I take size on the side I want to buy", which is a question about
    # one book.
    "market_volume_D", "market_volume_R",
    "market_open_interest_D", "market_open_interest_R",
    "market_liquidity_D", "market_liquidity_R",
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
    provenance: str = "captured"   # see PROVENANCE above

    def validate(self) -> None:
        if self.quantity not in QUANTITIES:
            raise ValueError(f"unknown quantity {self.quantity!r}")
        if self.provenance not in PROVENANCE:
            raise ValueError(f"unknown provenance {self.provenance!r}")
        # ONE UNIT PER QUANTITY, ENFORCED RATHER THAN HOPED FOR.
        #
        # margin_D was being written with unit "margin" by the market parsers
        # and our own model rows, and with unit "pct" by every parser that
        # reads a poll aggregator. Nothing complained, because aggregate.py
        # groups by (date, category, race, quantity, UNIT) and the two spellings
        # never met — until the BEW model joined the polling family, at which
        # point polling published TWO national margins every day: a four-source
        # average at D+6.19 under "pct" and a one-source row at D+3.86 under
        # "margin". Both were correct arithmetic on a wrongly split group.
        #
        # A quantity is a thing; a unit is how it is measured. If two rows
        # disagree about the unit of the same quantity, one of them is wrong,
        # and it should stop the run rather than quietly fork the group.
        if self.quantity.startswith("margin_D") and self.unit != "pct":
            raise ValueError(
                f"{self.quantity} must be unit 'pct', got {self.unit!r} — a "
                f"second spelling silently splits the category average")
        if self.quantity == "rating_ordinal":
            if not isinstance(self.value, str) or not self.value:
                raise ValueError("rating_ordinal must be a non-empty string")
        else:
            v = float(self.value)
            bounds = {"prob": (0, 1), "pct": (-100, 100), "seats": (0, 535),
                      "ordinal": (0, 10),
                      # Volume and depth are counts of money or contracts, in
                      # whatever units the exchange reports. Bounded only
                      # against a sign error and a parse of the wrong field.
                      "count": (0, 1e15)}.get(self.unit)
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

        # A row may be dated EARLIER than the capture it came from.
        #
        # Some sources publish their own history: Race to the WH ships a trend
        # sheet running back to February, Wikipedia keeps every revision. Those
        # observations are not statements about today, and filing them under
        # today's date would say the professional average moved eight points
        # overnight when in fact six months of it arrived at once.
        #
        # Backdating only. A parser may not date a row into the future, and it
        # may not date one after the capture it was read from — the raw bytes
        # are the evidence, and evidence cannot post-date what it proves.
        asof = kw.pop("snapshot_date", None) or self.snapshot_date
        if asof > self.snapshot_date:
            raise ValueError(
                f"parser dated a row {asof} from a {self.snapshot_date} "
                f"capture; rows may be backdated, never forward-dated")

        # ATTRIBUTION OVERRIDE — one capture, several forecasters.
        #
        # Wikipedia's House article carries a table of generic-ballot averages
        # from six DIFFERENT aggregators. Filed under source_id="wikipedia"
        # they collapse to one contributor, because the aggregator counts
        # sources by id — six independent averages would have counted as one,
        # and the category would have stayed below the disclosure floor while
        # appearing to have six opinions in it.
        #
        # So a parser may attribute a row to the forecaster it actually came
        # from. Two conditions, and the second is the important one:
        #
        #   - it must also declare the CATEGORY, because a source read off
        #     another source's page is rarely in the reading source's category;
        #   - it must also declare the PUBLICATION tier, and that tier is
        #     checked against the same restrict-only rule as any other. A row
        #     attributed to a gated forecaster must carry that forecaster's
        #     gate. Attribution is not a route around a licence: reading Silver
        #     Bulletin's number off Wikipedia does not make it republishable,
        #     it just means we know whose number it is.
        attributed = kw.pop("source_id", None)
        category = kw.pop("category", None) or self.category
        if attributed and attributed != self.source_id:
            if publication is None:
                raise ValueError(
                    f"row attributed to {attributed!r} without an explicit "
                    f"publication tier; attribution must carry the attributed "
                    f"source's licence, not the reading source's")

        # PROVENANCE, DERIVED RATHER THAN DECLARED.
        #
        # The rule is one comparison: if the bytes were obtained AFTER the date
        # the row describes, we are reading a record the publisher made at the
        # time rather than watching them make it — so the row is `archival`.
        # That single test covers every route we have into the past without a
        # per-source setting to keep in sync:
        #
        #   - a Wikipedia revision fetched today for a date in March
        #   - an exchange candlestick synthesised into a past day's directory
        #   - Ray Fair's dated table, back-dated by his parser to the day he
        #     posted each row
        #   - Race to the WH's own trend sheet, likewise
        #
        # A parser that knows better may still pass provenance= explicitly; the
        # only case today is a source that publishes somebody else's ESTIMATE
        # of a past value rather than their own past statement, which nothing
        # in the registry currently does.
        #
        # `retrospective` is deliberately unreachable from here. A parser reads
        # what a publisher said; it never recomputes anything for an earlier
        # date. Only our own models can do that, and they are stamped in
        # aggregate.class_model_rows.
        fetched = (art.meta.get("fetched_at") or art.meta.get("synthesised_at")
                   or "")[:10]
        prov = kw.pop("provenance", None)
        if prov is None:
            later = (fetched > asof) if fetched else (self.snapshot_date > asof)
            prov = "archival" if later else "captured"

        r = Row(
            snapshot_date=asof,
            source_id=attributed or self.source_id,
            category=category,
            publication=tier,
            captured_at=art.meta.get("fetched_at", ""),
            raw_sha256=art.sha256,
            raw_path=str(art.path),
            provenance=prov,
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
    understates the tails on purpose, and the understatement is measurable
    rather than assumed: Polymarket resolves both tails and read D+7.58 on
    2026-08-21 where Kalshi's convention gave D+7.83.

    Returns only the expectation. The mass above zero is P(D wins the popular
    vote), which is NOT the same event as winning the chamber, and win_prob_D
    on a national race already means the chamber. Two questions under one name
    is how a comparison table starts reporting a definition mismatch as
    disagreement.
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
