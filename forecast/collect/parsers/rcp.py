"""
RealClearPolling — poll aggregator. Publication: aggregate_only.

NOT YET WRITTEN. RCP is HTML-only with no API, so it has to be written against
a real capture. Raw bytes are already banking daily, so nothing is lost by
writing this in September — that is exactly what storing raw first buys you.

Note RCP also publishes its own ordinal ratings map. Emit the polling average
only; the map would double-count into expert_ordinal.
"""
from __future__ import annotations
from . import Context, LoadedArtifact, Row, race_id


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    raise NotImplementedError(
        "parser not written yet — run:  python3 forecast/collect/parse.py "
        f"--inspect {ctx.source_id}   to see the stored structure, then copy "
        "_scaffold.py.txt to this source's module and implement parse()."
    )
