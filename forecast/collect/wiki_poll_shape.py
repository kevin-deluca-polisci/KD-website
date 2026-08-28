#!/usr/bin/env python3
"""Diagnostic: what shape are Wikipedia's per-race polling tables, really?

    python3 forecast/collect/wiki_poll_shape.py
    python3 forecast/collect/wiki_poll_shape.py --states Maine Texas Georgia
    python3 forecast/collect/wiki_poll_shape.py --rows 4

WRITES NOTHING. It exists to answer the one question wiki_race_probe.py could
not: is the markup consistent enough across states that ONE parser handles all
of them, or does every race need its own special case?

WHAT IT PRINTS, AND WHY EACH PIECE MATTERS

    SECTION TREE. Every heading with its level, so the nesting is visible. The
    probe reported "Polling, Polling" for every race, which means there are at
    least two polling sections per article — almost certainly primary polling
    and general-election polling. Only the general election is usable for a
    race-level forecast, and a parser that grabs both would silently mix a
    Democratic primary poll into a D-vs-R margin. This is the single most
    dangerous thing about the job and it has to be visible before anything is
    written.

    TABLE OPENERS. `{| class="wikitable ..."` verbatim, because the class list
    is what a walker keys on and it varies.

    HEADER CELLS. The column names, in order, for each table. If Maine and
    Texas disagree about what column three is, one parser cannot read both
    positionally and has to match on header text instead.

    FIRST DATA ROWS. Verbatim wikitext, so the date format, the citation
    style, the candidate-name convention and the way percentages are written
    are all inspectable rather than guessed at.

    TEMPLATE CENSUS. Which {{templates}} appear inside the polling sections.
    Wikipedia's election tables lean on templates heavily, and a parser that
    handles raw pipes but not {{Party shading}} will read the wrong cell.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import urllib.parse
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "forecast" / "collect"))
import capture  # noqa: E402

API = "https://en.wikipedia.org/w/api.php"
SPECIAL = {"Florida", "Ohio"}
DEFAULT_STATES = ["Maine", "Texas", "Georgia"]

_F = None


def fetcher():
    global _F
    if _F is None:
        reg = yaml.safe_load(open(REPO / "forecast" / "sources" / "2026.yaml",
                                  encoding="utf-8"))
        _F = capture.Fetcher(reg.get("contact") or {}, reg.get("defaults") or {})
    return _F


def wikitext(state: str) -> str | None:
    kind = "special election" if state in SPECIAL else "election"
    title = f"2026 United States Senate {kind} in {state}"
    q = urllib.parse.urlencode({"action": "parse", "page": title,
                                "prop": "wikitext", "format": "json"})
    try:
        body, _ = fetcher().get(f"{API}?{q}")
        d = json.loads(body.decode("utf-8"))
    except Exception as e:
        print(f"    fetch failed: {type(e).__name__}: {e}")
        return None
    if "error" in d:
        print(f"    api error: {d['error'].get('code')}")
        return None
    wt = d["parse"]["wikitext"]
    return wt["*"] if isinstance(wt, dict) else wt


HEAD = re.compile(r"^(={2,5})\s*(.+?)\s*\1\s*$", re.M)
TABLE_OPEN = re.compile(r"^\{\|(.*)$", re.M)


def section_tree(wt: str) -> list[tuple[int, str, int]]:
    """(level, title, offset) for every heading."""
    return [(len(m.group(1)), m.group(2), m.start()) for m in HEAD.finditer(wt)]


def slice_section(wt: str, start: int, level: int) -> str:
    """From a heading to the next heading of the same or higher level."""
    nxt = re.compile(r"^={2," + str(level) + r"}\s*[^=]", re.M)
    m = nxt.search(wt, start + 1)
    return wt[start: m.start() if m else len(wt)]


def show(state: str, nrows: int) -> None:
    print(f"\n{'=' * 78}\n  {state}\n{'=' * 78}")
    wt = wikitext(state)
    if not wt:
        return
    tree = section_tree(wt)
    print(f"  {len(wt)//1024} KB, {len(tree)} headings\n")

    print("  SECTION TREE (polling branches marked)")
    for lvl, title, off in tree:
        mark = "  <<<" if "poll" in title.lower() else ""
        print(f"    {'  ' * (lvl - 2)}{'=' * lvl} {title}{mark}")

    polls = [(l, t, o) for l, t, o in tree if "poll" in t.lower()]
    print(f"\n  {len(polls)} polling section(s)")

    for lvl, title, off in polls:
        seg = slice_section(wt, off, lvl)
        print(f"\n  {'-' * 74}\n  SECTION: {'=' * lvl} {title}   ({len(seg)//1024} KB)")
        # what sits ABOVE this heading in the tree tells us whether these are
        # primary polls or general-election polls
        parents = [t for l, t, o in tree if o < off and l < lvl]
        print(f"    parent chain: {' > '.join(parents[-2:]) or '(top level)'}")

        tmpl = collections.Counter(re.findall(r"\{\{\s*([A-Za-z][\w ]{0,28})", seg))
        print(f"    templates: " + (", ".join(f"{k.strip()}({v})"
              for k, v in tmpl.most_common(8)) or "none"))

        opens = TABLE_OPEN.findall(seg)
        print(f"    {len(opens)} table(s)")
        for i, cls in enumerate(opens[:2]):
            print(f"      table {i+1} opener: {{|{cls.strip()[:90]}")

        # header cells of the first table
        tb = seg.find("{|")
        if tb < 0:
            print("      no table found in this section")
            continue
        body = seg[tb:]
        end = body.find("\n|}")
        body = body[: end if end > 0 else 6000]
        heads = re.findall(r"^!(.+)$", body, re.M)
        if heads:
            cells = []
            for h in heads[:3]:
                cells += [c.strip() for c in re.split(r"!!|\|\|", h) if c.strip()]
            print(f"      header cells ({len(cells)}):")
            for c in cells[:14]:
                print(f"         {c[:78]}")
        rows = re.split(r"^\|-", body, flags=re.M)[1:]
        print(f"      {len(rows)} data row(s); first {min(nrows,len(rows))} verbatim:")
        for r in rows[:nrows]:
            for line in [x for x in r.strip().splitlines() if x.strip()][:9]:
                print(f"         {line[:110]}")
            print("         ---")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", nargs="+", default=DEFAULT_STATES)
    ap.add_argument("--rows", type=int, default=2)
    a = ap.parse_args(argv)
    print("wikipedia per-race polling SHAPE diagnostic — writes nothing")
    for s in a.states:
        show(s, a.rows)
    print("\n\n  Read the header cells and the parent chains across states:")
    print("  same columns in the same order means one positional parser;")
    print("  different order means matching on header text; a 'primary' parent")
    print("  chain means that table must be excluded from general-election polls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
