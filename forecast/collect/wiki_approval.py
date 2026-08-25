#!/usr/bin/env python3
"""
Presidential approval, read out of Wikipedia's poll tables.

WHY THIS IS A MODULE AND NOT JUST A PARSER. A parsed Row carries a
`snapshot_date` and nothing else datelike, so a poll's own field period has
nowhere to live in it. Every approval poll read today would be stamped today,
and the one property that makes approval worth capturing — that a past value is
a COMPUTATION over the polls published by then, not a guess — would be lost on
the way into the archive.

academic.py already solved this once for the generic ballot: it reads Silver's
raw CSV rather than parsed rows, because the poll's own end date is in the file
and not in the schema. This module is the same answer for approval, so there is
one pattern rather than two.

WHAT THE PAGE ACTUALLY CONTAINS, measured rather than assumed:

    Aggregate polls > Approval      11 aggregators, updated daily
    2026 > Jul/Jun/May/Feb           9 individual polls in total
    2025 > Jul..Jan                ~135 individual polls

The 2025 tables are dense and the 2026 ones are nearly empty — the page has
largely stopped carrying monthly nationwide tables for the election year. So
this source gives us a good 2025 history, today's level from the aggregator
table, and NOT a 2026 series. See docstring on polls() for what that means for
backfilling.

TWO KINDS OF NUMBER, KEPT APART. An individual poll and an aggregator's average
are different objects, and averaging them together would count the same polls
twice — once raw and once inside somebody's model. They go out under separate
quantities for the same reason the market parsers keep a price apart from a
probability.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}

# The section that holds nationwide job approval, and the two things inside it
# that are NOT nationwide job approval polls.
NATIONWIDE = "Nationwide job approval ratings"
STOP = "Statewide job approval ratings"
SKIP_PATHS = ("Approval of transition as president-elect",)

# Wikipedia writes a minus as U+2212 in these tables; int() does not read it.
_MINUS = "−"


def _cells(line: str) -> list[str]:
    """One wikitable row line -> its cell texts, styles stripped.

    A cell may be written `| 34%` or `| style="background:#8B8B54" | 34%`. The
    value is what follows the LAST pipe, which is also correct for the plain
    form. Anything else — a nested template with a pipe in it — would break
    this, and none of these tables use one.
    """
    out = []
    for raw in line.split("\n"):
        raw = raw.strip()
        if not raw.startswith("|") and not raw.startswith("!"):
            continue
        body = raw[1:]
        if "|" in body:
            body = body.rsplit("|", 1)[1]
        out.append(body.strip())
    return out


def _pct(s: str) -> float | None:
    m = re.search(r"(-?" + _MINUS + r"?\d+(?:\.\d+)?)\s*%", s)
    if not m:
        return None
    return float(m.group(1).replace(_MINUS, "-"))


def _end_date(cell: str, year: int, month_hint: int | None) -> str | None:
    """'July 23-27' -> that July's 27th. Returns the END of the field period.

    THE END, NOT THE START, because a poll is knowable on the day its field
    period closes and not before. Using the start date would let a poll into an
    average up to a week before it existed, which is the exact leak this whole
    module is built to avoid.

    Handles 'July 27', 'July 23-27', 'July 23–27', and 'July 30 – August 2',
    where the second month wins. Returns None rather than guessing.
    """
    txt = cell.replace("&ndash;", "-").replace("–", "-").replace(_MINUS, "-")
    txt = re.sub(r"<[^>]+>", " ", txt)
    found = list(re.finditer(r"([A-Z][a-z]+)?\s*(\d{1,2})", txt))
    if not found:
        return None
    mon, day = None, None
    for m in found:
        if m.group(1) and m.group(1) in MONTHS:
            mon = MONTHS[m.group(1)]
        if m.group(2):
            day = int(m.group(2))
    if mon is None:
        mon = month_hint
    if mon is None or day is None or not (1 <= day <= 31):
        return None
    # A December field period listed under a January section belongs to the
    # previous year. Only fires on the year boundary.
    y = year
    if month_hint is not None and mon == 12 and month_hint == 1:
        y -= 1
    try:
        return dt.date(y, mon, day).isoformat()
    except ValueError:
        return None


def _tables(seg: str) -> list[str]:
    """Every wikitable in a wikitext segment, as raw '{| ... |}' blocks."""
    out, depth, start = [], 0, None
    i = 0
    while i < len(seg):
        if seg.startswith("{|", i):
            if depth == 0:
                start = i
            depth += 1
            i += 2
            continue
        if seg.startswith("|}", i) and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(seg[start:i + 2])
                start = None
            i += 2
            continue
        i += 1
    return out


def _rows(table: str) -> tuple[list[str], list[list[str]]]:
    """(header cells, data rows). Header is whatever the '!' line declares."""
    parts = re.split(r"^\|-.*$", table, flags=re.M)
    header: list[str] = []
    data: list[list[str]] = []
    for chunk in parts:
        if not header and "!" in chunk:
            hd = _cells("\n".join(l for l in chunk.split("\n")
                                  if l.strip().startswith("!")))
            hd = [re.sub(r"\{\{[^}]*\}\}|<[^>]+>", " ", h).strip() for h in hd]
            if hd:
                header = hd
                continue
        cs = _cells("\n".join(l for l in chunk.split("\n")
                              if l.strip().startswith("|")
                              and not l.strip().startswith("|}")
                              and not l.strip().startswith("|+")))
        if cs:
            data.append(cs)
    return header, data


def _col(header: list[str], *names: str) -> int | None:
    """Index of the first header matching any name. Never a fixed position.

    THE HEADER IS READ, NOT ASSUMED, because these tables are hand-maintained
    and their column order is not guaranteed across months. A parser that
    hardcodes 'approve is column five' produces plausible numbers from the
    wrong column the first time somebody inserts one.
    """
    low = [h.lower() for h in header]
    for n in names:
        for i, h in enumerate(low):
            if h.startswith(n):
                return i
    return None


def _sections(wikitext: str):
    """Yield (heading_path, body) for every section under the nationwide head."""
    a = wikitext.find("==" + NATIONWIDE + "==")
    if a < 0:
        a = wikitext.find(NATIONWIDE)
    b = wikitext.find(STOP, a + 1)
    seg = wikitext[a:b if b > 0 else len(wikitext)]
    heads = [(m.start(), len(m.group(1)), m.group(2).strip())
             for m in re.finditer(r"^(=+)\s*(.+?)\s*=+\s*$", seg, re.M)]
    for i, (pos, lvl, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(seg)
        path = [name]
        cur = lvl
        for j in range(i - 1, -1, -1):
            if heads[j][1] < cur:
                cur = heads[j][1]
                path.insert(0, heads[j][2])
        yield path, seg[pos:end]


def read_capture(path: Path) -> str:
    doc = json.loads(Path(path).read_text())
    wt = (doc.get("parse") or {}).get("wikitext")
    if isinstance(wt, dict):
        wt = wt.get("*") or ""
    return wt or ""


def extract(wikitext: str) -> dict:
    """{'polls': [...], 'aggregators': [...]} from one page capture."""
    polls, aggs = [], []
    for path, body in _sections(wikitext):
        if any(s in path for s in SKIP_PATHS):
            continue
        joined = " > ".join(path)
        year = None
        for p in path:
            if re.fullmatch(r"20\d\d", p):
                year = int(p)
        month_hint = None
        m = re.match(r"([A-Z][a-z]+)\s+20\d\d$", path[-1])
        if m and m.group(1) in MONTHS:
            month_hint = MONTHS[m.group(1)]
            if year is None:
                year = int(path[-1].split()[-1])

        for tbl in _tables(body):
            header, data = _rows(tbl)
            if not header:
                continue
            ia = _col(header, "approve", "approval")
            idis = _col(header, "disapprove", "disapproval")
            if ia is None:
                continue

            is_agg = "Aggregate polls" in path
            idate = _col(header, "date", "updated", "dates")
            iname = _col(header, "aggregator", "poll source", "pollster", "poll")
            for cs in data:
                if len(cs) <= ia:
                    continue
                ap = _pct(cs[ia])
                dis = _pct(cs[idis]) if idis is not None and len(cs) > idis else None
                if ap is None:
                    continue
                who = cs[iname] if iname is not None and len(cs) > iname else ""
                who = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", who)
                who = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", who).strip()
                dcell = cs[idate] if idate is not None and len(cs) > idate else ""
                if is_agg:
                    d = None
                    md = re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(20\d\d)", dcell)
                    if md and md.group(1) in MONTHS:
                        try:
                            d = dt.date(int(md.group(3)), MONTHS[md.group(1)],
                                        int(md.group(2))).isoformat()
                        except ValueError:
                            d = None
                    aggs.append({"aggregator": who, "date": d,
                                 "approve": ap, "disapprove": dis,
                                 "section": joined})
                else:
                    if year is None:
                        continue
                    d = _end_date(dcell, year, month_hint)
                    if d is None:
                        continue
                    polls.append({"pollster": who, "date": d,
                                  "approve": ap, "disapprove": dis,
                                  "section": joined})
    # The same poll can appear in two month tables at a month boundary.
    seen, uniq = set(), []
    for p in sorted(polls, key=lambda r: (r["date"], r["pollster"])):
        k = (p["date"], p["pollster"], p["approve"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return {"polls": uniq, "aggregators": aggs}


def load_history(cycle: int = 2026) -> list[dict]:
    """Every dated approval poll across every capture, deduplicated.

    WHAT THIS CAN AND CANNOT SUPPORT. Wikipedia's nationwide monthly tables are
    dense through 2025 and nearly empty for 2026 — nine polls for the whole
    election year at the time of writing. So this is enough to reconstruct
    approval through 2025 and NOT enough to reconstruct it through 2026, which
    is the half the fundamentals backfill actually needs. Silver Bulletin
    publishes the same polls densely and is the obvious second source; this
    module's shape is deliberately the one his file would also fit.
    """
    out, seen = [], set()
    for f in sorted(glob.glob(str(DATA / str(cycle) / "raw" / "wiki_approval"
                                  / "*" / "*.json"))):
        if f.endswith(".meta.json"):
            continue
        try:
            got = extract(read_capture(Path(f)))
        except Exception:
            continue
        for p in got["polls"]:
            k = (p["date"], p["pollster"], p["approve"])
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
    return sorted(out, key=lambda r: r["date"])


def _self_test() -> int:
    fails = 0

    def ck(name, got, want):
        nonlocal fails
        if got != want:
            fails += 1
            print(f"  FAIL {name}: got {got!r} want {want!r}")

    ck("end of a range", _end_date("July 23-27", 2026, 7), "2026-07-27")
    ck("en dash", _end_date("July 23–27", 2026, 7), "2026-07-27")
    ck("single day", _end_date("July 27", 2026, 7), "2026-07-27")
    ck("crosses months", _end_date("July 30 – August 2", 2026, 7),
       "2026-08-02")
    ck("december under january", _end_date("December 28-30", 2026, 1),
       "2025-12-30")
    ck("nonsense", _end_date("n/a", 2026, 7), None)
    ck("pct plain", _pct("34%"), 34.0)
    ck("pct styled", _pct('style="background:#8B8B54" | 66%'), 66.0)
    ck("pct unicode minus", _pct("−32%"), -32.0)
    ck("pct absent", _pct("Adults"), None)

    tbl = ('{| class="wikitable"\n!Poll source\n! Date\n! Approve\n! Disapprove\n'
           '|-\n| [https://x.example CNN/SSRS]\n| July 23-27\n| 34%\n'
           '| style="background:#8B8B54" | 66%\n|-\n|}')
    hd, rows = _rows(tbl)
    ck("header read", hd[:4], ["Poll source", "Date", "Approve", "Disapprove"])
    ck("approve column found by name", _col(hd, "approve"), 2)
    ck("one data row", len(rows), 1)

    print("  self-test:", "PASS" if not fails else f"{fails} FAILURE(S)")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dump", action="store_true",
                    help="print what was read from the newest capture")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    files = sorted(f for f in glob.glob(
        str(DATA / str(a.cycle) / "raw" / "wiki_approval" / "*" / "*.json"))
        if not f.endswith(".meta.json"))
    if not files:
        raise SystemExit("no wiki_approval capture — run capture.py first")
    got = extract(read_capture(Path(files[-1])))
    print(f"  newest capture: {Path(files[-1]).parent.name}")
    print(f"  {len(got['polls'])} individual poll(s), "
          f"{len(got['aggregators'])} aggregator row(s)")
    if got["aggregators"]:
        vals = [g["approve"] for g in got["aggregators"] if g["approve"]]
        print(f"  aggregator approval: mean {sum(vals)/len(vals):.2f} "
              f"over {len(vals)}  [{min(vals)} .. {max(vals)}]")
        for g in got["aggregators"][:4]:
            print(f"      {g['aggregator'][:28]:<30} {g['date']}  {g['approve']}")
    hist = load_history(a.cycle)
    print(f"  {len(hist)} dated poll(s) across all captures")
    if hist:
        print(f"      {hist[0]['date']} .. {hist[-1]['date']}")
        by_year: dict[str, int] = {}
        for p in hist:
            by_year[p["date"][:4]] = by_year.get(p["date"][:4], 0) + 1
        print("      by year:", by_year)
    if a.dump:
        for p in hist[:20]:
            print(f"      {p['date']}  {p['approve']:>5}  {p['pollster'][:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
