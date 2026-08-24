#!/usr/bin/env python3
"""
Find backfilled seat projections that the backfill can no longer regenerate.

    python3 forecast/model/prune_stale.py            # report only
    python3 forecast/model/prune_stale.py --apply    # remove them (writes a backup first)

-----------------------------------------------------------------------------
THE RULE

`seats.py --backfill-history` rebuilds a past date's projections from the two
model-history files. Any projection stamped `provenance: backfilled` whose key
is NOT one the backfill would write on that date is unmaintainable: no future
run will ever touch it, it cannot be recomputed, and it silently keeps
whatever assumptions were true on the day some earlier version of the code
happened to write it.

That is not a hypothetical. Running the dated-map correction exposed 87 dates
carrying a `class_polling` projection that the current backfill never
regenerates. The comment in seats.py explains why they exist: the
reconstructed polling tide used to be filed under `class_polling` and was
later given its own id, `polling_reconstructed`. The rename left the old rows
behind. They now hold pre-correction seat counts on the old district map, and
because they sit in the polling category beside `polling_reconstructed` — the
same tide under a different name — they also double-count polling on the
seats panel, which is the exact double-count seats.py takes care elsewhere to
avoid.

A stale row is worse than a missing one. A missing date is visible; a stale
one looks like data.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "forecast" / "data"


def regenerable(priv: Path) -> dict[str, set]:
    """{date: {keys the backfill would write}} — mirrors seats.py exactly."""
    out: dict[str, set] = {}
    ah = priv / "academic_models_history.json"
    if ah.exists():
        for d0, day in json.loads(ah.read_text()).items():
            out.setdefault(d0, set()).update((day.get("models") or {}).keys())
    ph = priv / "polling_model_history.json"
    if ph.exists():
        for d0, m in json.loads(ph.read_text()).items():
            if m.get("nowcast_tide_D") is not None:
                out.setdefault(d0, set()).add("polling_reconstructed")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--apply", action="store_true",
                    help="actually remove them. A .bak copy is written first.")
    a = ap.parse_args(argv)

    priv = DATA / str(a.cycle) / "model_private"
    hp = priv / "seat_projections_history.json"
    if not hp.exists():
        print(f"no history at {hp}")
        return 2
    hist = json.loads(hp.read_text(encoding="utf-8"))
    keys = regenerable(priv)

    stale: list[tuple[str, str]] = []
    for date in sorted(hist):
        can = keys.get(date, set())
        for k, v in list((hist[date].get("projections") or {}).items()):
            if v.get("provenance") == "backfilled" and k not in can:
                stale.append((date, k))

    print("=" * 72)
    print(f"stale backfilled projections · {len(hist)} dates")
    print("=" * 72)
    if not stale:
        print("  none — every backfilled projection can be regenerated")
        return 0

    by_key = Counter(k for _, k in stale)
    for k, n in by_key.most_common():
        ds = sorted(d for d, kk in stale if kk == k)
        print(f"  {k:<28}{n:>4} date(s)   {ds[0]} .. {ds[-1]}")

    # Is a still-live twin carrying the same tide? That is the double-count.
    print("\n  where a regenerable twin exists on the same date:")
    twins = 0
    for date, k in stale:
        can = keys.get(date, set())
        mine = (hist[date]["projections"][k] or {}).get("tide_D")
        for other in can:
            o = (hist[date].get("projections") or {}).get(other) or {}
            if o.get("tide_D") is not None and abs(
                    float(o["tide_D"]) - float(mine or 0)) < 1e-9:
                twins += 1
                break
    print(f"    {twins} of {len(stale)} share a tide EXACTLY with a "
          f"regenerable projection on the same date")

    if not a.apply:
        print("\n  report only. Re-run with --apply to remove them.")
        return 0

    bak = hp.with_suffix(".json.bak")
    shutil.copy2(hp, bak)
    for date, k in stale:
        (hist[date].get("projections") or {}).pop(k, None)
    hp.write_text(json.dumps(hist, indent=1, sort_keys=True))
    print(f"\n  removed {len(stale)} projection(s)")
    print(f"  backup: {bak}")
    print("  now re-run:  bash forecast/run.sh --from aggregate --no-push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
