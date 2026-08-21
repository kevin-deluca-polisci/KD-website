#!/usr/bin/env python3
"""
Development harness: fetch a source live, run its parser, print what came out,
write NOTHING.

    python3 forecast/collect/probe.py --source fair
    python3 forecast/collect/probe.py --source fair --show 40 --dump /tmp/fair

WHY THIS IS A SEPARATE FILE.

The pipeline's central rule is that capture fetches and never parses, and parse
reads storage and never fetches. That separation is what makes a parser bug
recoverable: the original bytes are still on disk, so the parser can be fixed
and re-run against the day it got wrong. Teaching either phase to do the other
one's job would buy convenience today and cost the archive its guarantee.

But writing a parser against bytes you have never seen is the other failure
mode, and this project has already paid for it: three sources were registered
with guessed URLs that all 404'd. What is needed is a way to point the real
fetcher at the real page and run the real parser over the result — WITHOUT
storing anything, so no half-tested parser can put rows into the archive and no
probe run can be mistaken later for a capture.

So this composes the two phases instead of blurring them. It imports the same
Fetcher capture uses (same user agent, same throttle, same backoff, same TLS
context) and the same Context and LoadedArtifact the parsers are written
against, and it touches nothing under data/.

IT RESPECTS THE LICENCE GATE. A probe that ignored the gate would be a way to
fetch a prohibited source by calling it a test, which is exactly the kind of
convenience the gate exists to refuse.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import capture                      # noqa: E402
import parsers as P                 # noqa: E402
from parse import load_registry     # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetch a source live and run its parser. Writes nothing.")
    ap.add_argument("--source", required=True, help="registry source id")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--date", help="snapshot date to parse as (default today UTC)")
    ap.add_argument("--show", type=int, default=25,
                    help="rows to print (0 for all)")
    ap.add_argument("--dump", metavar="DIR",
                    help="also write the fetched bytes here for inspection. "
                         "NOT the archive — use capture.py for that.")
    a = ap.parse_args(argv)

    registry = load_registry(a.cycle)
    src = next((s for s in registry.get("sources", [])
                if s["id"] == a.source), None)
    if src is None:
        print(f"no source {a.source!r} in the {a.cycle} registry")
        return 2

    date = a.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    print("=" * 70)
    print(f"probe · {src['id']} · {src.get('name', '')}")
    print("=" * 70)
    print(f"  category    {src.get('category')}")
    print(f"  tier        {src.get('publication')}")
    print(f"  parse as    {date}")

    # The same gate capture.py applies. A probe is not an exemption.
    reason = capture.gate(src)
    if reason:
        print(f"\n  REFUSED: {reason}")
        print("  The licence gate applies here too — this tool is for testing a "
              "parser, not for reaching a source we may not collect.")
        return 1

    urls = (src.get("config") or {}).get("urls") or []
    if not urls:
        print("\n  no URLs configured for this source")
        return 2

    fetcher = capture.Fetcher(registry.get("contact", {}),
                              registry.get("defaults", {}))

    artifacts: dict[str, P.LoadedArtifact] = {}
    print()
    for u in urls:
        name, url = u.get("name", "?"), u.get("url", "")
        try:
            body, meta = fetcher.get(url)
        except Exception as e:
            print(f"  FETCH FAILED  {name}: {type(e).__name__}: {e}")
            continue
        print(f"  fetched  {name:22} {meta.get('status')} "
              f"{len(body):>8,} bytes  {url}")
        if meta.get("final_url") and meta["final_url"] != url:
            print(f"           redirected to {meta['final_url']}")
        artifacts[name] = P.LoadedArtifact(
            name=name, path=Path(f"<probe>/{name}"), body=body, meta=meta)
        if a.dump:
            d = Path(a.dump)
            d.mkdir(parents=True, exist_ok=True)
            ext = ".json" if body[:1] in (b"{", b"[") else ".html"
            out = d / f"{src['id']}__{name}{ext}"
            out.write_bytes(body)
            print(f"           dumped to {out}")

    if not artifacts:
        print("\n  nothing fetched — cannot test the parser")
        return 1

    mod = P.get(src["id"])
    if mod is None:
        print(f"\n  NO PARSER written for {src['id']} yet. "
              f"The bytes above are what one would have to read.")
        return 1

    ctx = P.Context(source=src, snapshot_date=date)
    try:
        rows = mod.parse(artifacts, ctx)
    except Exception as e:
        print(f"\n  PARSER FAILED — {type(e).__name__}: {e}")
        return 1

    print(f"\n  parsed {len(rows)} row(s)")

    # Validate exactly as the pipeline would, so a row that would be rejected
    # downstream is rejected here instead of at 11pm in late October.
    bad = []
    for r in rows:
        try:
            r.validate()
        except Exception as e:
            bad.append(f"{getattr(r, 'race_id', '?')}/"
                       f"{getattr(r, 'quantity', '?')}: {e}")
    if bad:
        print(f"  {len(bad)} row(s) FAIL validation:")
        for b in bad[:10]:
            print(f"      {b}")
    else:
        print("  all rows pass Row.validate()")

    dates = sorted({getattr(r, "snapshot_date", "") for r in rows})
    if len(dates) > 1:
        print(f"  dates covered: {dates[0]} … {dates[-1]} ({len(dates)} distinct)")

    shown = rows if a.show == 0 else rows[:a.show]
    if shown:
        print()
        print(f"  {'date':12} {'race':20} {'quantity':14} {'value':>10} {'unit':8} tier")
        for r in shown:
            print(f"  {getattr(r, 'snapshot_date', ''):12} "
                  f"{getattr(r, 'race_id', ''):20} "
                  f"{getattr(r, 'quantity', ''):14} "
                  f"{str(getattr(r, 'value', '')):>10} "
                  f"{getattr(r, 'unit', ''):8} "
                  f"{getattr(r, 'publication', '')}")
        if a.show and len(rows) > a.show:
            print(f"  … {len(rows) - a.show} more (use --show 0 for all)")

    print("\n  NOTHING WRITTEN. To store these bytes for real:")
    print(f"      python3 forecast/collect/capture.py --cycle {a.cycle} "
          f"--only {src['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
