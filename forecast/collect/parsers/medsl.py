"""
MEDSL official returns. Publication: individual (CC0 public domain dedication).

Statewide two-party presidential vote, which is the statewide analogue of the
district PVI we hold from Cook — the baseline the Senate and Governor models
need.

The only genuinely unencumbered source in the registry: CC0 means no attribution
obligation and no share-alike. We cite it anyway, as scholarly courtesy.

MODELLING CAUTION, recorded here because it is easy to forget once the numbers
are in a CSV: Senate and especially Governor races are far less nationalised
than House races. A presidential-derived baseline needs a wider error band for
Governor than Senate, and wider for Senate than House. Vermont, Massachusetts,
Maryland, Kansas and Kentucky have all repeatedly elected governors against a
20-30 point presidential lean. Do not apply one uniform swing to all offices.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict

from . import Context, LoadedArtifact, Row, race_id

_ABBR_FIX = {"DISTRICT OF COLUMBIA": "DC"}


def _two_party(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """state_po -> (dem_votes, rep_votes), summed across modes and candidates."""
    tally: dict[str, dict[str, float]] = defaultdict(lambda: {"D": 0.0, "R": 0.0})
    for r in rows:
        po = (r.get("state_po") or "").strip().upper()
        if len(po) != 2:
            continue
        party = (r.get("party_simplified") or r.get("party_detailed") or "").strip().upper()
        try:
            v = float(r.get("votes") or r.get("candidatevotes") or 0)
        except (TypeError, ValueError):
            continue
        if party == "DEMOCRAT":
            tally[po]["D"] += v
        elif party == "REPUBLICAN":
            tally[po]["R"] += v
    return {k: (v["D"], v["R"]) for k, v in tally.items()}


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    rows: list[Row] = []

    for art_name, chamber in (("president_state_2024", None),
                              ("senate_state_2024", "senate")):
        art = artifacts.get(art_name)
        if art is None:
            continue
        table = list(csv.DictReader(io.StringIO(art.text())))
        if not table:
            raise ValueError(f"{art_name}: no rows")
        tp = _two_party(table)
        if not tp:
            raise ValueError(
                f"{art_name}: parsed {len(table)} rows but found no D/R votes. "
                f"Columns: {list(table[0])[:18]}")

        for po, (d, r) in sorted(tp.items()):
            total = d + r
            if total <= 0:
                continue
            margin = round((d - r) / total * 100, 3)
            if chamber == "senate":
                # Senate baselines are per-state, but only where a Senate race
                # actually ran in 2024 — that is what this file contains.
                rows.append(ctx.row(art, race_id=race_id("senate", po),
                                    chamber="senate", state=po,
                                    quantity="margin_D_prior_senate",
                                    value=margin, unit="pct"))
            else:
                # Statewide presidential baseline, used for BOTH the Senate and
                # Governor models. Stored once, against a state-level pseudo-race
                # so it is not mistaken for a race-specific forecast.
                rows.append(ctx.row(art, race_id=f"STATE_{po}_BASELINE",
                                    chamber="national", state=po,
                                    quantity="margin_D_pres_2024",
                                    value=margin, unit="pct"))
    if not rows:
        raise ValueError("no MEDSL artifacts stored for this date")
    return rows
