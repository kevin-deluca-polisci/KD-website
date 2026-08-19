"""
Cook PVI, entered by hand. Publication: PRIVATE — never published, in any form.

Cook's index is proprietary. We hold it because the class fundamentals model
needs a district baseline and because Kevin is entitled to read it, not because
we are free to redistribute it. Every row is private, aggregate.py lists `pvi`
in NEVER_PUBLISH, and the audit re-checks both. Three independent locks.

If Cook later grants explicit permission, change `publication` in the registry
and remove `pvi` from NEVER_PUBLISH — in that order, and not before.
"""
from __future__ import annotations

from . import Context, LoadedArtifact, Row, race_id


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    art = artifacts.get("manual")
    if art is None:
        raise ValueError(
            "no manual.json stored — run:  python3 forecast/collect/manual_import.py "
            "--source cook_pvi --file <your pasted table>")
    doc = art.json()
    entries = doc.get("rows") or []
    if not entries:
        raise ValueError("manual.json contains no rows")

    rows: list[Row] = []
    for e in entries:
        state, dist = str(e.get("state", "")).upper(), str(e.get("district", ""))
        try:
            rid = race_id("house", state, dist)
        except (ValueError, TypeError):
            continue
        rows.append(ctx.row(art, race_id=rid, chamber="house", state=state,
                            district=f"{int(dist):02d}", quantity="pvi",
                            value=float(e["pvi"]), unit="pct"))
        # Prior-cycle PVI, where the sheet carried it. The pair is what makes
        # the 2025->2026 redistricting shift measurable per district, which is
        # both a model input and a genuinely good class exercise.
        if e.get("pvi_prior") is not None:
            rows.append(ctx.row(art, race_id=rid, chamber="house", state=state,
                                district=f"{int(dist):02d}", quantity="pvi_prior",
                                value=float(e["pvi_prior"]), unit="pct"))
    if not rows:
        raise ValueError(f"understood 0 of {len(entries)} manual rows")
    return rows
