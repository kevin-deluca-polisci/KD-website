"""
Presidential approval from Wikipedia. Publication: individual (CC BY-SA 4.0).

WHAT GOES INTO PARSED ROWS AND WHAT DOES NOT, because the split is not obvious.

The page carries two different things. An aggregator table, refreshed daily,
whose rows are true AS OF THE SNAPSHOT — those become rows here, one per
aggregator, and daily capture turns them into a dated series. And monthly
tables of individual polls, each with its own field period, which do NOT become
rows: a parsed Row has `snapshot_date` and no other date field, so every poll
read today would be stamped today and the field date — the only reason
poll-level data is worth having — would be thrown away on the way in.

Those are read straight from the raw captures instead, by
collect/wiki_approval.py, exactly as academic.py reads Silver's poll list for
the generic ballot. One pattern for dated poll data, not two.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from . import Context, LoadedArtifact, Row  # noqa: E402


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    import wiki_approval as wa

    rows: list[Row] = []
    for name, art in sorted(artifacts.items()):
        try:
            got = wa.extract(wa.read_capture(art.path))
        except Exception as e:
            raise ValueError(f"{name}: could not read the approval tables: {e}")

        for g in got["aggregators"]:
            who = (g.get("aggregator") or "").strip()
            if not who or g.get("approve") is None:
                continue
            rows.append(ctx.row(art, race_id="", chamber="national", state="",
                                district="", quantity="approval_pct_aggregate",
                                value=float(g["approve"]), unit="pct"))
    if not rows:
        raise ValueError(
            "no aggregator rows read from the approval page — the table shape "
            "has probably changed. Inspect it with:  python3 "
            "forecast/collect/wiki_approval.py --dump")
    return rows
