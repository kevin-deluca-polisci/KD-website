#!/usr/bin/env python3
"""Build the public release of the U.S. Election Forecast Archive.

    python3 forecast/collect/release.py --cycle 2026

Reads the pipeline's published files in forecast/data/<cycle>/derived/ and
writes a clean copy to forecast/archive/<cycle>/, the folder that is shared
with other people. derived/ stays the pipeline's working folder.

What changes on the way:
  - `category` becomes `line`, the source line the row belongs to (polling,
    market, professional, academic). The `facet` column is dropped.
  - Nothing from the class model (old or new) is included.
  - forecasts_by_source.csv gets the same `line` column, and a model that was
    listed under two old categories appears once.
  - approval.csv is the daily approval average as a flat table.
  - files.csv lists every file with its row count and SHA-256.

Run after publish.py. Writes nothing if a required input is missing.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import facets  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LINES = [s for s in facets.SOURCE_ORDER if s != "class"]

# Fields in the per-line tables, in order. `facet` is gone and `category` is
# renamed `line`.
AVG_FIELDS = ["snapshot_date", "line", "race_id", "chamber", "state", "district",
              "quantity", "unit", "n_sources", "n_gated", "n_retrospective",
              "partial", "n_withheld", "mean", "min", "max", "sd", "tier",
              "display", "sole_source", "oldest_as_of", "n_carried"]
BY_SOURCE_FIELDS = ["snapshot_date", "source_id", "line", "race_id", "chamber",
                    "state", "district", "quantity", "value", "unit", "as_of",
                    "provenance"]
TIMELINE_FIELDS = ["date", "line", "quantity", "unit", "value", "low", "high",
                   "band_kind", "n_sources"]

DESCRIPTIONS = {
    "timeline.csv": "The daily national series drawn on the site's chart, one row per date, line and quantity.",
    "category_averages.csv": "Every published average: date, line, race and quantity.",
    "suppressed.csv": "Averages that exist but are withheld (fewer than three forecasts whose terms restrict republication).",
    "forecasts_by_source.csv": "Individual forecasts from sources whose terms allow republication by name.",
    "approval.csv": "Daily average of the published presidential approval aggregators (an input, not a forecast).",
}


def rd(p: Path) -> list[dict]:
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def wr(p: Path, rows: list[dict], fields: list[str]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def line_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r.get("facet", "source") != "source":
            continue
        if r.get("category") not in LINES:
            continue
        out.append({**r, "line": r["category"]})
    return out


def by_source(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        sid = r["source_id"]
        if not facets.in_archive(sid):
            continue
        # on_line: a race row from a source that only carries the national
        # polling average to each race is not that race's polling.
        got = facets.on_line(sid, r["category"], r.get("race_id", ""), r.get("provenance", ""))
        if got is None:
            continue
        line = "input" if got[0] == "reference" else got[1]
        if line == "class":
            continue
        rec = {**r, "line": line}
        key = tuple(rec.get(k, "") for k in BY_SOURCE_FIELDS)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def timeline(rows: list[dict]) -> list[dict]:
    return [{"date": r["snapshot_date"], "line": r["series"],
             "quantity": r["panel"], **{k: r.get(k, "") for k in
                                        ("unit", "value", "low", "high",
                                         "band_kind", "n_sources")}}
            for r in rows if r.get("series") in LINES]


def approval(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    pts = ((d.get("series") or {}).get("aggregate") or {}).get("points") or []
    return [{"date": p["date"], "approve": p.get("approve"),
             "n_aggregators": p.get("n"), "low": p.get("low"),
             "high": p.get("high")} for p in pts]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the public archive release.")
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)

    src = REPO / "forecast" / "data" / str(a.cycle) / "derived"
    dst = REPO / "forecast" / "archive" / str(a.cycle)
    need = ["category_averages.csv", "suppressed.csv", "by_source_open.csv",
            "timeline.csv", "approval.json"]
    missing = [n for n in need if not (src / n).exists()]
    if missing:
        print(f"  release: missing {', '.join(missing)} in {src}; nothing written")
        return 1

    outputs = {
        "timeline.csv": (timeline(rd(src / "timeline.csv")), TIMELINE_FIELDS),
        "category_averages.csv": (line_rows(rd(src / "category_averages.csv")), AVG_FIELDS),
        "suppressed.csv": (line_rows(rd(src / "suppressed.csv")), AVG_FIELDS + ["reason"]),
        "forecasts_by_source.csv": (by_source(rd(src / "by_source_open.csv")), BY_SOURCE_FIELDS),
        "approval.csv": (approval(src / "approval.json"),
                         ["date", "approve", "n_aggregators", "low", "high"]),
    }
    listing = []
    for name, (rows, fields) in outputs.items():
        wr(dst / name, rows, fields)
        listing.append({"file": name, "rows": len(rows),
                        "bytes": (dst / name).stat().st_size,
                        "sha256": sha(dst / name),
                        "description": DESCRIPTIONS[name]})
        print(f"  release: {name:26s} {len(rows):8d} rows")
    wr(dst / "files.csv", listing, ["file", "rows", "bytes", "sha256", "description"])

    # A last check on what is about to be shared: no class model rows, no
    # dropped sources, and no source named that is not individual-tier.
    bad = [r for r in outputs["forecasts_by_source.csv"][0]
           if not facets.in_archive(r["source_id"])]
    bad += [r for r in outputs["category_averages.csv"][0] if r["line"] == "class"]
    if bad:
        print(f"  release: {len(bad)} row(s) that must not be shared; stopping")
        return 1
    print(f"  release: wrote {dst.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
