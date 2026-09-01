#!/usr/bin/env python3
"""
Backfill Wikipedia's own revision history into dated captures.

    python3 forecast/collect/wiki_history.py --cycle 2026 --dry-run
    python3 forecast/collect/wiki_history.py --cycle 2026
    python3 forecast/collect/wiki_history.py --cycle 2026 --since 2025-01-20 --step-days 1

WHY THIS IS THE MOST VALUABLE BACKFILL WE HAVE

Everything else in the archive begins the day capture began. Wikipedia keeps
every revision of every page forever, so for the four pages we read it is the
one source whose past can be recovered exactly as it stood — the ratings table
as it read on 12 March, the poll-aggregation table as it read on 4 May.

And it is recoverable as EVIDENCE rather than as description. A revision is
Wikipedia's own dated record: the page said that, on that day, and we are
merely reading the record afterwards. Under the provenance taxonomy in
collect/parsers/__init__.py that is `archival`, which is the class scoring
counts as real-time. Contrast our reconstructed poll averages, which are
`retrospective` and cannot be scored: we computed them in August from a file
as it stands in August. This module converts history into admissible data;
the poll reconstruction only ever produced illustrative history.

WHAT IT WRITES, AND WHY IT IS SHAPED LIKE THIS

The same artifact the live capture would have written that morning, in that
day's directory, under the same name. `parse.py --all` then reads a recovered
day with the existing parser and no special case, and every row it produces is
dated to the revision's own day. Not one line of parsing code knows this
module exists — the same discipline collect/market_history.py follows, and for
the same reason: a second implementation of the table readers would be a
liability, and the ratings tables are fiddly.

WHAT IT DOES NOT DO. The existing `capture.py --backfill` fetches the revision
LIST — ids, timestamps, users, sizes — and stops there. That records THAT the
page changed and never WHAT it said, so nothing downstream could read it and
nothing ever has. This module fetches the wikitext of the revisions that
matter. The revision list is still worth capturing for provenance; it is
simply not the data.

TWO RULES IT WILL NOT BREAK

1. NEVER OVERWRITE A REAL CAPTURE. If a day already holds an artifact for that
   page, it is left exactly as it is. A captured byte is evidence of what we
   fetched; a recovered revision is evidence of what Wikipedia held. Both are
   real, and they must not be confused or silently swapped.

2. ONE REVISION PER DAY, THE LAST ONE. A busy page changes a dozen times a
   day, and a day's forecast is the state it settled at, not whichever edit we
   happened to land on. Taking the last revision of each day also makes the
   result deterministic: re-running this module writes the same bytes.

POLITENESS. Every fetch goes through the same Fetcher as the daily capture, so
it inherits the project's rate limit, user agent and contact details. A full
run over four pages and nineteen months is a few thousand requests against an
API that explicitly permits them, once. Use --step-days to thin it out if you
would rather not.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture  # noqa: E402  — Fetcher, RawStore naming, registry loading

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"

# Mirrors collect/market_history.py. A recovered revision written by an older
# and wronger version of this module can be withdrawn and rewritten by bumping
# this; a real capture is never touched by that sweep.
SHAPE_VERSION = 1


def _slug(name: str) -> str:
    return capture.RawStore._slugify(name)


def _artifact_name(title: str) -> str:
    """Exactly what the live capture calls this page's artifact."""
    return _slug(f"current-{title}")


def _day_dir(cycle: int, date: str, source_id: str = "wikipedia") -> Path:
    """The directory the LIVE capture of this source writes that day into.

    Keyed on the source id rather than hardcoded, because the race articles are
    captured under `wiki_endorsements` and a revision recovered into the
    `wikipedia` tree would be invisible to the parser that reads them.
    """
    return DATA / str(cycle) / "raw" / source_id / date


def _revisions(fetcher, api: str, title: str, since: str,
               cap: int) -> list[tuple[str, int]]:
    """[(YYYY-MM-DD, revid)] oldest first, every revision since `since`."""
    out: list[tuple[str, int]] = []
    rvcontinue = None
    while len(out) < cap:
        params = {
            "action": "query", "prop": "revisions", "titles": title,
            "rvlimit": "500", "rvdir": "newer",
            "rvstart": f"{since}T00:00:00Z",
            "rvprop": "ids|timestamp", "format": "json", "formatversion": "2",
        }
        if rvcontinue:
            params["rvcontinue"] = rvcontinue
        body, _meta = fetcher.get(f"{api}?{urllib.parse.urlencode(params)}")
        if not body:
            break
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            print(f"    {title}: revision list was not JSON")
            break
        pages = (payload.get("query") or {}).get("pages") or []
        revs = (pages[0].get("revisions") or []) if pages else []
        for r in revs:
            ts, rid = r.get("timestamp"), r.get("revid")
            if ts and rid:
                out.append((str(ts)[:10], int(rid)))
        rvcontinue = (payload.get("continue") or {}).get("rvcontinue")
        # THE CAP TRUNCATES THE RECENT END, NOT THE OLD ONE, and that is the
        # opposite of harmless. Listing walks `rvdir=newer` from `since`, so a
        # page that exceeds the cap keeps its oldest revisions and silently
        # loses its newest -- the page would stop having history exactly where
        # the campaign gets interesting, and the summary would still say
        # COMPLETE. Say so loudly instead; it is one line and it is the
        # difference between a known limit and a hole nobody looks for.
        if len(out) >= cap and rvcontinue:
            print(f"    {title}: HIT THE {cap}-REVISION CAP. The list stops at "
                  f"{out[-1][0]} and anything edited after that is NOT "
                  f"recoverable until max_revisions_per_page is raised.")
        if not rvcontinue or not revs:
            break
    return out


def _pick(revs: list[tuple[str, int]], step_days: int) -> dict[str, int]:
    """One revision id per target day: the LAST edit of that day.

    With --step-days N the grid thins to every Nth day, and each grid day takes
    the newest revision at or before it — so a week with no edits still gets
    the state the page was actually in, rather than a hole.
    """
    last_of_day: dict[str, int] = {}
    for d0, rid in revs:                      # oldest first, so later wins
        last_of_day[d0] = rid
    if step_days <= 1:
        return last_of_day
    if not last_of_day:
        return {}
    days = sorted(last_of_day)
    start, end = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
    out: dict[str, int] = {}
    cur = start
    while cur <= end:
        target = cur.isoformat()
        got = [d0 for d0 in days if d0 <= target]
        if got:
            out[max(got)] = last_of_day[max(got)]
        cur += dt.timedelta(days=step_days)
    return out


def _write(cycle: int, date: str, title: str, body: bytes, revid: int,
           api: str, dry: bool, source_id: str = "wikipedia") -> bool:
    day = _day_dir(cycle, date, source_id)
    target = day / f"{_artifact_name(title)}.json"
    if target.exists():
        return False                       # rule 1: never overwrite a capture
    if dry:
        return True
    day.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    meta = {
        "bytes": len(body),
        "provenance": "backfilled",
        "shape_version": SHAPE_VERSION,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "revid": revid,
        "page": title,
        "endpoint": f"{api}?action=parse&oldid={revid}&prop=wikitext",
        "note": ("Wikipedia's own revision of this page as it stood on this "
                 "date, fetched later. Shaped like a live capture so the "
                 "parser needs no special case. Archival, not retrospective: "
                 "the page said this, on this day."),
        "headers": {"Content-Type": "application/json"},
    }
    (day / f"{_artifact_name(title)}.meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True))
    return True


def backfill_page(cycle: int, fetcher, api: str, title: str, since: str,
                  step_days: int, cap: int, dry: bool,
                  deadline: float | None = None,
                  source_id: str = "wikipedia") -> dict:
    stats = collections.Counter()
    revs = _revisions(fetcher, api, title, since, cap)
    stats["revisions_listed"] = len(revs)
    if not revs:
        print(f"    {title}: no revisions since {since}")
        return stats
    targets = _pick(revs, step_days)
    stats["days_targeted"] = len(targets)
    first, last = min(targets), max(targets)

    # THE CEILING IS EDITED DAYS, NOT CALENDAR DAYS, and saying so is the
    # difference between "we are at 44%" and "we are finished". A page nobody
    # edited on a Tuesday has no Tuesday revision to recover, and yesterday's
    # artifact is still what the page said. Reporting the gap against the
    # achievable set is what lets a run tell you whether to run it again.
    have = sum(1 for d in targets
               if (_day_dir(cycle, d, source_id)
                   / f"{_artifact_name(title)}.json").exists())
    stats["already_present"] = have
    stats["missing_before"] = len(targets) - have
    print(f"    {title}: {len(revs)} revision(s), {len(targets)} edited day(s) "
          f"{first} .. {last}")
    print(f"      {have} already in the archive, {len(targets) - have} to fetch")

    for date in sorted(targets):
        if deadline is not None and time.monotonic() > deadline:
            # Stop CLEANLY rather than being killed by the job timeout. Every
            # artifact already written stays written, rule 1 makes the next run
            # skip them, and the summary says how many are left. A backfill that
            # can be resumed by pressing the same button is worth more than one
            # that has to be planned in date windows by hand.
            stats["stopped_on_budget"] = 1
            break
        revid = targets[date]
        if (_day_dir(cycle, date, source_id)
                / f"{_artifact_name(title)}.json").exists():
            stats["skipped_exists"] += 1
            continue
        params = {"action": "parse", "oldid": revid, "prop": "wikitext",
                  "format": "json", "formatversion": "2"}
        try:
            body, _meta = fetcher.get(f"{api}?{urllib.parse.urlencode(params)}")
        except Exception as e:                              # noqa: BLE001
            print(f"      {date} rev {revid}: {type(e).__name__} — skipped")
            stats["fetch_failed"] += 1
            continue
        if dry or not body:
            stats["no_body"] += 1
            continue
        # A deleted or suppressed revision answers with an `error` object and
        # no wikitext. Writing that would put an artifact in the archive that
        # parses to nothing and looks like a day the tables were empty.
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            stats["not_json"] += 1
            continue
        text = ((payload.get("parse") or {}).get("wikitext") or "")
        if isinstance(text, dict):
            text = text.get("*", "")
        if not text:
            stats["no_wikitext"] += 1
            continue
        if _write(cycle, date, title, body, revid, api, dry, source_id):
            stats["written"] += 1
        else:
            stats["skipped_exists"] += 1
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--source", default="wikipedia",
                    help="which registry source's pages to recover. Default "
                         "`wikipedia`, the four summary articles. Pass "
                         "`wiki_endorsements` for the per-race articles, whose "
                         "titles come from a generated plan rather than a "
                         "literal list. Recovered revisions are written into "
                         "that source's own raw tree, so the parser that reads "
                         "the live capture reads these with no special case.")
    ap.add_argument("--since", default=None,
                    help="earliest revision date (default: the registry's "
                         "backfill.since, else 2025-01-20)")
    ap.add_argument("--step-days", type=int, default=1,
                    help="1 = every day with an edit; 7 = weekly grid")
    ap.add_argument("--max-revisions", type=int, default=None,
                    help="cap on revisions listed per page")
    ap.add_argument("--only", default="",
                    help="substring of one page title, for a single page")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be fetched and write nothing")
    ap.add_argument("--time-budget", type=int, default=1200,
                    help="seconds before stopping cleanly (default 1200). The "
                         "run resumes exactly where it left off, so the way to "
                         "finish a large backfill is to press the same button "
                         "again rather than to slice it into date windows.")
    a = ap.parse_args(argv)

    reg = capture.load_registry(a.cycle)
    src = next((s for s in reg.get("sources", []) if s["id"] == a.source), None)
    if src is None:
        print(f"  no source `{a.source}` in the registry")
        return 1
    cfg = src.get("config") or {}
    api = cfg.get("api")
    bf = cfg.get("backfill") or {}
    since = a.since or str(bf.get("since") or "2025-01-20")
    cap = a.max_revisions or int(bf.get("max_revisions_per_page") or 5000)
    # THE PLAN COUNTS AS PAGES. capture.resolve_pages expands a source's
    # literal list AND its generated plan; reading `cfg["pages"]` directly is
    # what made every per-race article invisible to this tool, since they reach
    # the project through wiki_endorsements' plan and never through YAML.
    pages = [p for p in capture.resolve_pages(cfg)
             if not a.only or a.only.lower() in p.lower()]
    if not pages:
        print(f"  source `{a.source}` resolves to no pages"
              + (f" matching --only {a.only!r}" if a.only else ""))
        return 1

    fetcher = capture.Fetcher(reg.get("contact") or {}, reg.get("defaults") or {},
                              dry_run=a.dry_run)

    print("=" * 70)
    print(f"{a.source} history · cycle {a.cycle} · {len(pages)} page(s) · "
          f"since {since} · "
          f"step {a.step_days}d{' · DRY RUN' if a.dry_run else ''}")
    print("=" * 70)

    deadline = (time.monotonic() + a.time_budget) if a.time_budget > 0 else None
    total = collections.Counter()
    failed: list[str] = []
    for title in pages:
        try:
            got = backfill_page(a.cycle, fetcher, api, title, since,
                                max(1, a.step_days), cap, a.dry_run, deadline,
                                a.source)
            total.update(got)
        except Exception:                                   # noqa: BLE001
            # One page's failure must not discard another's work — the same
            # lesson market_history.py learned by throwing away 3,565 files.
            failed.append(title)
            print(f"\n  !! {title} FAILED — traceback follows. Anything the "
                  f"other pages wrote is kept.\n")
            traceback.print_exc()

    print()
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(total.items())))
    if failed:
        print(f"  WARNING: {len(failed)} page(s) failed: {failed}")
    left = total.get("missing_before", 0) - total.get("written", 0)
    if a.dry_run:
        # The shared Fetcher returns nothing in dry-run, so the revision lists
        # come back empty and every count above is zero. Say so, rather than
        # letting a run that fetched nothing report that there is nothing left.
        print("\n  DRY RUN: no requests were made, so the counts above are not "
              "a plan.\n  Run it for real to see the gap; the time budget "
              "stops it before the job does.")
    elif total.get("stopped_on_budget"):
        print(f"\n  STOPPED ON THE TIME BUDGET with {left} edited day(s) still "
              f"missing.\n  Nothing is lost — run this again with the same "
              f"settings to continue from here.")
    elif left > 0:
        print(f"\n  {left} edited day(s) still missing (fetch failures or "
              f"revisions with no wikitext). Re-running is safe.")
    elif failed:
        # NEVER "COMPLETE" OVER A FAILURE. `left` counts the gap on pages that
        # were successfully listed, so a page whose revision list never arrived
        # contributes nothing to it and the old branch reported a clean sweep
        # directly underneath its own WARNING. That is the summary telling you
        # to stop when the honest answer is to run it again.
        print(f"\n  INCOMPLETE: {len(failed)} page(s) could not be listed and "
              f"are not in the archive.\n  Nothing is lost — re-running is "
              f"safe and retries only what is missing.")
    else:
        print("\n  COMPLETE: every edited day in this window is in the archive. "
              "Re-running would write nothing.")
    if total.get("written") and not a.dry_run:
        print(f"\n  next: python3 forecast/collect/parse.py --cycle {a.cycle} --all")
        print("        every recovered day parses with the existing reader, and")
        print("        its rows land dated to the revision, provenance=archival")
    return 0 if total.get("written") or a.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
