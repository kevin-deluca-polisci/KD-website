"""
Race to the WH — professional/hybrid. Publication: aggregate_only.

NOT YET WRITTEN. Squarespace-hosted HTML, robots-permitted, updated daily.
Write against a real capture:  parse.py --inspect race_to_the_wh
"""
from __future__ import annotations
from . import Context, LoadedArtifact, Row, race_id


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    raise NotImplementedError(
        "parser not written yet — run:  python3 forecast/collect/parse.py "
        f"--inspect {ctx.source_id}   to see the stored structure, then copy "
        "_scaffold.py.txt to this source's module and implement parse()."
    )
