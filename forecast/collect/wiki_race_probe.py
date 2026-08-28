#!/usr/bin/env python3
"""Probe: are Wikipedia's per-race articles worth capturing, and how?

    python3 forecast/collect/wiki_race_probe.py
    python3 forecast/collect/wiki_race_probe.py --senate-only --days 120

WRITES NOTHING. It answers three questions that decide whether a per-race
polling capture is a day's work or a fortnight's, and whether it can be
afforded at all.

    1. IS THERE A POLLING TABLE, and is its markup consistent enough across
       states that one parser handles all of them?

    2. WHAT DOES AN ARTICLE WEIGH? The four summary articles already cost
       402 MB across 584 days. Scaling that naively to ~470 race articles is
       about 7 GB by election day, all of it in the private archive whose pack
       is already 367 MB.

    3. HOW OFTEN DOES A RACE ARTICLE ACTUALLY CHANGE? This is the one that
       decides the storage question, because a daily full copy of a page that
       nobody edits is 68 identical copies of the same bytes. If most races
       change rarely, a change log costs a fraction of a daily capture and
       loses nothing.

WHY A CHANGE LOG IS THE RIGHT SHAPE HERE, AND NOT A COMPROMISE

    MediaWiki will return revision ids for up to 50 titles in ONE request. So
    the daily job is ~10 cheap calls to ask "what is each page's current
    revision", a comparison against what we already hold, and a content fetch
    only for the pages that moved. Storage becomes proportional to edits rather
    than to days x races.

    It also improves the timestamps rather than costing them. A daily capture
    can only say "this poll was present when we looked on the 28th". A revision
    carries the exact edit time, so `known_by` becomes the moment the poll
    entered the article, which is a better answer to the question the archive
    actually asks.

    The honest caveat: a revision id changes for a typo fix as much as for a
    new poll, so change-log storage still over-collects relative to polling
    changes specifically. Deduplicating on the polling section's own hash would
    fix that, and it belongs in the parse phase, because capture stores bytes
    and never parses.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "forecast" / "collect"))
import capture  # noqa: E402

API = "https://en.wikipedia.org/w/api.php"

# A deliberately mixed sample: races the field calls competitive, plus two
# safe seats as a control. If the safe ones have no polling table and never
# change, that is the argument for capturing competitive races only.
SENATE = ["Maine", "North Carolina", "Georgia", "Michigan", "New Hampshire",
          "Texas", "Ohio", "Iowa", "Wyoming", "Massachusetts"]


# USE capture.Fetcher, DO NOT REINVENT IT. This file got two things wrong by
# writing its own HTTP, and both were already solved next door:
#
#   The TLS context. python.org builds ignore the macOS trust store, so plain
#   urllib fails CERTIFICATE_VERIFY_FAILED on every call while the daily
#   capture works fine.
#
#   The rate limit. Wikipedia returned 429 on the first request because this
#   opened with ten calls in a tight loop. Fetcher enforces a per-host minimum
#   interval and backs off exponentially with jitter on 429.
#
# It also carries the User-Agent built from the contact block in the registry,
# which names the project, the site and an email. That is what Wikimedia asks
# for, and it is already what the archive presents every day.
_FETCHER = None


def fetcher():
    global _FETCHER
    if _FETCHER is None:
        reg = yaml.safe_load(open(REPO / "forecast" / "sources" / "2026.yaml",
                                  encoding="utf-8"))
        _FETCHER = capture.Fetcher(reg.get("contact") or {},
                                   reg.get("defaults") or {})
    return _FETCHER


def _get(params: dict) -> dict:
    q = urllib.parse.urlencode({**params, "format": "json"})
    body, _meta = fetcher().get(f"{API}?{q}")
    return json.loads(body.decode("utf-8"))


def title_for(state: str) -> str:
    return f"2026 United States Senate election in {state}"


def current_revisions(titles: list[str]) -> dict:
    """{title: revid} for up to 50 titles in ONE request.

    THIS IS THE WHOLE CHANGE-LOG ARGUMENT, demonstrated rather than asserted.
    Asking "what revision is each of 470 races at right now" costs ten calls of
    this shape, and only the pages whose revid moved need their content
    fetched. An earlier draft of this file looped one request per title, which
    is both the slow way and the way that gets a 429.
    """
    out = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        try:
            d = _get({"action": "query", "prop": "revisions",
                      "titles": "|".join(chunk),
                      "rvprop": "ids|timestamp"})
            for pg in (d.get("query", {}).get("pages") or {}).values():
                revs = pg.get("revisions") or []
                out[pg.get("title", "")] = {
                    "missing": "missing" in pg,
                    "revid": revs[0]["revid"] if revs else None,
                    "latest": revs[0]["timestamp"] if revs else None}
        except Exception as e:
            for t in chunk:
                out[t] = {"error": f"{type(e).__name__}: {e}"}
    return out


def revision_count(title: str, days: int) -> dict:
    """How many times this page was edited in the window. One call per page,
    and only run on the probe sample rather than on all 470."""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(time.time() - days * 86400))
    try:
        d = _get({"action": "query", "prop": "revisions", "titles": title,
                  "rvprop": "ids|timestamp", "rvlimit": "max",
                  "rvend": cutoff})
        pg = next(iter(d["query"]["pages"].values()))
        revs = pg.get("revisions") or []
        return {"n": len(revs), "missing": "missing" in pg}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


POLL_HEAD = re.compile(r"==+\s*([^=\n]*[Pp]olling[^=\n]*)\s*==+")
# A poll row in the standard {{Election box}}-free table Wikipedia uses for
# these: a wikitable whose header mentions a pollster column.
POLLSTER_COL = re.compile(r"\|\s*(Poll source|Pollster)\b", re.I)


def probe_article(title: str) -> dict:
    try:
        d = _get({"action": "parse", "page": title, "prop": "wikitext"})
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if "error" in d:
        return {"error": d["error"].get("code", "api error")}
    wt = d["parse"]["wikitext"]["*"] if isinstance(
        d["parse"]["wikitext"], dict) else d["parse"]["wikitext"]
    heads = POLL_HEAD.findall(wt)
    # Count rows that look like a dated poll line inside a polling section.
    npolls = 0
    for m in POLL_HEAD.finditer(wt):
        seg = wt[m.end(): m.end() + 60000]
        nxt = re.search(r"\n==[^=]", seg)
        if nxt:
            seg = seg[: nxt.start()]
        npolls += len(re.findall(r"\n\|\s*\[?\[?https?://|\n\|-\s*\n", seg))
    return {"bytes": len(wt.encode("utf-8")),
            "polling_sections": heads,
            "has_pollster_col": bool(POLLSTER_COL.search(wt)),
            "approx_poll_rows": npolls}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--races", type=int, default=len(SENATE))
    a = ap.parse_args(argv)

    states = SENATE[: a.races]
    titles = [title_for(s) for s in states]

    print("=" * 78)
    print(f"wikipedia per-race probe · {len(titles)} Senate articles · "
          f"revisions over {a.days} days")
    print("=" * 78)
    print("  writes nothing; read-only against the public API\n")

    # One batched call for all ten, which also proves the mechanism works.
    cur = current_revisions(titles)
    live = sum(1 for v in cur.values() if v.get("revid"))
    print(f"  batched revision check: {live}/{len(titles)} pages resolved "
          f"in {(len(titles) + 49) // 50} request(s)\n")

    revs = {t: revision_count(t, a.days) for t in titles}
    rows = []
    print(f"  {'state':16s} {'KB':>7s} {'polls':>6s} {'revs':>5s}  polling section(s)")
    for s, t in zip(states, titles):
        info = probe_article(t)
        r = revs.get(t, {})
        if "error" in info:
            print(f"  {s:16s} {'—':>7s} {'—':>6s} {'—':>5s}  {info['error']}")
            continue
        kb = info["bytes"] / 1024
        secs = ", ".join(info["polling_sections"][:2]) or "NONE FOUND"
        print(f"  {s:16s} {kb:7.0f} {info['approx_poll_rows']:6d} "
              f"{r.get('n', '?'):>5}  {secs[:44]}")
        rows.append({"state": s, "kb": kb, "revs": r.get("n", 0),
                     "polls": info["approx_poll_rows"],
                     "has_table": info["has_pollster_col"]})

    if not rows:
        print("\n  nothing retrieved")
        return 1

    n = len(rows)
    mean_kb = sum(r["kb"] for r in rows) / n
    mean_revs = sum(r["revs"] for r in rows) / n
    with_table = sum(1 for r in rows if r["has_table"])
    print(f"\n  {with_table}/{n} have a pollster column")
    print(f"  mean article {mean_kb:.0f} KB · mean {mean_revs:.1f} revisions "
          f"in {a.days} days ({mean_revs/a.days:.2f}/day)")

    # The comparison that decides the design.
    ALL = 470
    days_left = 66
    daily = ALL * mean_kb * days_left / 1024 / 1024
    changed = ALL * mean_kb * (mean_revs / a.days) * days_left / 1024 / 1024
    comp_races = 80
    comp = comp_races * mean_kb * (mean_revs / a.days) * days_left / 1024 / 1024
    print(f"\n  projected storage to election day, {ALL} races, {days_left} days")
    print(f"    daily full capture      {daily:8.2f} GB")
    print(f"    change log only         {changed:8.2f} GB"
          f"   ({100*changed/daily:.0f}% of daily)")
    print(f"    change log, competitive {comp:8.2f} GB"
          f"   ({100*comp/daily:.1f}% of daily)")
    print("\n  gzip takes wikitext to roughly a fifth of these figures again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
