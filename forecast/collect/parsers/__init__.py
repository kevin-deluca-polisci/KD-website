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
}

# Chamber-level pseudo-races. Same ID convention as real races so the class
# submission CSV merges without translation.
NATIONAL_HOUSE = "NATL_HOUSE_2026"
NATIONAL_SENATE = "NATL_SENATE_2026"

_STATE = re.compile(r"^[A-Z]{2}$")


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

def race_id(chamber: str, state: str = "", district: str = "",
            cycle: int = 2026, special: bool = False) -> str:
    ch = {"house": "HOU", "senate": "SEN", "governor": "GOV"}.get(chamber.lower())
    if ch is None:
        raise ValueError(f"unknown chamber {chamber!r}")
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


def get(source_id: str):
    """Return the parser module for a source, or None if none is written yet."""
    try:
        return importlib.import_module(f"{__name__}.{source_id}")
    except ModuleNotFoundError:
        return None
