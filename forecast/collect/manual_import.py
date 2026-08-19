#!/usr/bin/env python3
"""
Manual source import — for data a human entered by hand.

WHY THIS EXISTS, AND WHY IT IS NOT A WORKAROUND

Some sources change a few times a cycle and forbid automated collection. Cook
PVI is the type case: it is recomputed once per redistricting, its terms treat
the ratings as proprietary, and cookpolitical.com enforces against crawlers at
the Cloudflare edge. Scraping it would be both rude and pointless.

A person opening a page they are entitled to view and copying a table is a
different act from a robot fetching it on a schedule. This script gives that
act the same provenance discipline as an automated capture: the raw text you
pasted is stored verbatim, hashed, and recorded in the manifest with who
entered it, when, and from what URL.

  # 1. paste the table into a text file, then:
  python3 forecast/collect/manual_import.py --source cook_pvi --file ~/Desktop/pvi.txt

  # 2. check what it understood before trusting it:
  python3 forecast/collect/manual_import.py --source cook_pvi --file ~/Desktop/pvi.txt --preview

Accepts a pasted text table (tab, comma, pipe or multi-space separated), or a
.xlsx / .csv file directly. Column names are matched loosely, so "pvi_2026",
"PVI 2026" and "2026 PVI" all work.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pip install -r forecast/collect/requirements.txt", file=sys.stderr)
    raise SystemExit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]
FORECAST_DIR = REPO_ROOT / "forecast"
DATA_DIR = FORECAST_DIR / "data"

# "AL-01", "AL 1", "AL01", "AL-AL" (at-large), "Alabama 1"
_DISTRICT = re.compile(r"\b([A-Z]{2})[\s\-–_]?(\d{1,2}|AL)\b")
# "R+15", "D+3", "EVEN", "R 15", "-15"
_PVI = re.compile(r"\b([RD])\s*\+?\s*(\d{1,2}(?:\.\d)?)\b|\b(EVEN)\b", re.I)


def parse_pvi_value(text: str) -> float | None:
    """Cook writes R+15 / D+3 / EVEN. Store signed, negative = Republican."""
    m = _PVI.search(text)
    if not m:
        return None
    if m.group(3):
        return 0.0
    party, mag = m.group(1).upper(), float(m.group(2))
    return -mag if party == "R" else mag


# ---------------------------------------------------------------------------
# Structured input (.xlsx / .csv). Preferred over a paste when available: the
# column names disambiguate fields a regex has to guess at, and a spreadsheet
# usually carries more than one useful column.
# ---------------------------------------------------------------------------

def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


# Loose column matching. First match wins, so order matters.
_COLS = {
    "district":   ["dist", "district", "cd", "seat", "code"],
    "pvi":        ["pvi2026", "2026pvi", "pvinew", "pvicurrent", "pvi"],
    "pvi_prior":  ["pvi2025", "2025pvi", "pviold", "pviprior", "pviprevious"],
    "incumbent":  ["incumbent", "member", "representative"],
    "party":      ["incumbentparty", "party"],
    "rank":       ["rank2026", "rank"],
}


def _map_columns(header: list[str]) -> dict[str, int]:
    norm = [_norm(h) for h in header]
    out: dict[str, int] = {}
    for field, candidates in _COLS.items():
        for cand in candidates:
            for i, h in enumerate(norm):
                if h == cand and i not in out.values():
                    out[field] = i
                    break
            if field in out:
                break
    return out


def parse_structured(path: Path) -> tuple[list[dict], list[str]]:
    """Read .xlsx or .csv into the same row shape as a text paste."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit(
                "Reading .xlsx needs openpyxl:  python3 -m pip install openpyxl"
                "  (or export the sheet to CSV and pass that instead)")
        ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        table = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        import csv as _csv
        with path.open(encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(4096); fh.seek(0)
            try:
                dialect = _csv.Sniffer().sniff(sample, delimiters=",\t;|")
            except _csv.Error:
                dialect = _csv.excel
            table = [r for r in _csv.reader(fh, dialect)]

    table = [r for r in table if r and any(c not in (None, "") for c in r)]
    if not table:
        return [], ["file contained no rows"]

    cols = _map_columns(table[0])
    if "district" not in cols or "pvi" not in cols:
        return [], [f"could not find district and PVI columns. Header seen: "
                    f"{[str(c) for c in table[0]]}"]

    rows, skipped = [], []
    for raw in table[1:]:
        def cell(field):
            i = cols.get(field)
            return raw[i] if i is not None and i < len(raw) else None

        d = _DISTRICT.search(str(cell("district") or "").upper())
        v = parse_pvi_value(str(cell("pvi") or ""))
        if not d or v is None:
            skipped.append(" | ".join(str(c) for c in raw[:4] if c is not None))
            continue
        state, num = d.group(1), d.group(2)
        num = "1" if num == "AL" else num
        rec = {"state": state, "district": f"{int(num):02d}", "pvi": v}
        prior = parse_pvi_value(str(cell("pvi_prior") or ""))
        if prior is not None:
            rec["pvi_prior"] = prior
        for f in ("incumbent", "party", "rank"):
            val = cell(f)
            if val not in (None, ""):
                rec[f] = str(val).strip()
        if rec.get("incumbent", "").upper().startswith("OPEN"):
            rec["open_seat"] = True
        rows.append(rec)
    return rows, skipped


def parse_table(text: str) -> tuple[list[dict], list[str]]:
    rows, skipped = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        d = _DISTRICT.search(line)
        v = parse_pvi_value(line)
        if d and v is not None:
            state, num = d.group(1).upper(), d.group(2).upper()
            num = "1" if num == "AL" else num
            try:
                district = f"{int(num):02d}"
            except ValueError:
                skipped.append(line); continue
            rows.append({"state": state, "district": district, "pvi": v})
        else:
            skipped.append(line)
    return rows, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import a hand-entered source.")
    ap.add_argument("--source", required=True, help="source id in the registry")
    ap.add_argument("--file", required=True, help="text file with the pasted table")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--date", default=None, help="snapshot date (default: today UTC)")
    ap.add_argument("--accessed-url", default=None,
                    help="override the source_url recorded for provenance")
    ap.add_argument("--preview", action="store_true",
                    help="show what was understood, write nothing")
    a = ap.parse_args(argv)

    reg_path = FORECAST_DIR / "sources" / f"{a.cycle}.yaml"
    registry = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    src = next((s for s in registry["sources"] if s["id"] == a.source), None)
    if src is None:
        print(f"ERROR: {a.source!r} is not in {reg_path.name}", file=sys.stderr)
        return 2
    if src.get("method") != "manual":
        print(f"ERROR: {a.source!r} has method {src.get('method')!r}, not 'manual'.",
              file=sys.stderr)
        return 2

    path = Path(a.file).expanduser()
    if path.suffix.lower() in (".xlsx", ".xlsm", ".csv", ".tsv"):
        rows, skipped = parse_structured(path)
        text = f"[structured import from {path.name}]"
    else:
        text = path.read_text(encoding="utf-8")
        rows, skipped = parse_table(text)

    print("=" * 70)
    print(f"manual import · {a.source} · {len(rows)} rows understood")
    print("=" * 70)
    if rows:
        for r in rows[:6]:
            sign = "R+" if r["pvi"] < 0 else ("D+" if r["pvi"] > 0 else "EVEN ")
            mag = "" if r["pvi"] == 0 else f"{abs(r['pvi']):g}"
            print(f"    {r['state']}-{r['district']}   {sign}{mag}")
        if len(rows) > 6:
            print(f"    ... and {len(rows)-6} more")
    if skipped:
        print(f"\n  {len(skipped)} lines not understood (headers and notes are normal):")
        for s in skipped[:5]:
            print(f"    {s[:72]}")

    # Sanity check the shape of what arrived, loudly.
    if rows:
        n_states = len({r["state"] for r in rows})
        print(f"\n  {len(rows)} districts across {n_states} states")
        n_prior = sum(1 for r in rows if "pvi_prior" in r)
        if n_prior:
            moved = sum(1 for r in rows
                        if "pvi_prior" in r and abs(r["pvi"] - r["pvi_prior"]) >= 0.5)
            print(f"  {n_prior} carry a prior-cycle PVI; {moved} districts moved "
                  f"(redistricting)")
        n_open = sum(1 for r in rows if r.get("open_seat"))
        if n_open:
            print(f"  {n_open} open seats flagged")
        if len(rows) < 400:
            print(f"  NOTE: the House has 435 districts — this looks partial.")
        dupes = len(rows) - len({(r["state"], r["district"]) for r in rows})
        if dupes:
            print(f"  WARNING: {dupes} duplicate district(s) in the input")
    else:
        print("\n  Nothing understood. Expected lines containing a district and a PVI,")
        print("  e.g.  'AL-01   R+15'.  Paste the table as plain text and retry.")
        return 1

    if a.preview:
        print("\n  --preview: nothing written")
        return 0

    snapshot = a.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "source_id": a.source,
        "snapshot_date": snapshot,
        "entered_by": getpass.getuser(),
        "entered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "method": "manual entry by a human from a page they are entitled to view",
        "source_url": a.accessed_url or (src.get("config") or {}).get("source_url", ""),
        "row_count": len(rows),
        "rows": rows,
    }
    body = json.dumps(payload, indent=2).encode()

    d = DATA_DIR / str(a.cycle) / "raw" / a.source / snapshot
    d.mkdir(parents=True, exist_ok=True)
    (d / "manual.json").write_bytes(body)
    (d / "manual.meta.json").write_text(json.dumps({
        "url": payload["source_url"], "status": 200,
        "fetched_at": payload["entered_at"], "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "manual_entry": True, "entered_by": payload["entered_by"],
    }, indent=2))
    # Keep the original input too. Same rule as automated capture: store raw
    # before parsed, so a parser fix can be reprocessed against the original.
    if path.suffix.lower() in (".xlsx", ".xlsm", ".csv", ".tsv"):
        import shutil
        shutil.copy2(path, d / f"manual_source{path.suffix.lower()}")
    else:
        (d / "manual_source.txt").write_text(text, encoding="utf-8")

    print(f"\n  wrote {(d/'manual.json').relative_to(REPO_ROOT)}")
    print(f"  tier: {src.get('publication')}  — "
          f"{'NEVER published' if src.get('publication')=='private' else 'see aggregate.py'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
