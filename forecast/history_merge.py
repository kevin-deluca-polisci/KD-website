"""Combine every copy of the seat-projection history we hold, losing nothing.

WHY THIS EXISTS
    `seat_projections_history.json` is one monolithic file rewritten by every
    run. That has two consequences, and we are living with both.

    It grows the archive by a whole fresh copy of every prior date each day.
    Thirteen commits of this one path are already about half the pack in
    plsc2219-raw.

    Worse, a degraded run silently REPLACES the whole file. A capture that
    fails, a parser whose output shape changed, a laptop run without the
    private tree — any of those write a thinner history over a fatter one, and
    the fat one survives only in git. Measured on the real archive: the working
    copy in plsc2219-raw holds 543 model-days, the working copy in KD-website
    holds 806, and the union across those two plus one old commit is 894. No
    single copy has everything.

WHAT IT DOES
    Unions every copy at the (date, model) level, not the date level, because
    that is the level at which things went missing. Nothing is deleted: the
    monolith stays exactly where it is, and the merged result is written
    alongside it as one file per date.

    Every choice is recorded. `history_manifest.json` says, for each
    (date, model), which copy it came from and how many alternatives existed;
    `history_conflicts.csv` lists every case where copies disagreed. The merge
    is reproducible and, because the inputs are untouched, reversible.

WHY SHARDING FIXES THE APPEND PROBLEM TOO
    Once history is one file per date, a daily run writes one file. A bad run
    can damage at most the day it ran, and `--verify` catches even that by
    asserting no shard ever drops a (date, model) the manifest already knows
    about.

USAGE
    python3 forecast/history_merge.py --report            # inventory, writes nothing
    python3 forecast/history_merge.py --report --from OTHER.json --git ../plsc2219-raw
    python3 forecast/history_merge.py --write             # shard + manifest
    python3 forecast/history_merge.py --write --prune     # ...and drop stale shards
    python3 forecast/history_merge.py --verify            # nothing lost since last write
    python3 forecast/history_merge.py --unshard           # shards -> monolith, fresh clone
    python3 forecast/history_merge.py --fill-from-shards DIR   # add missing dates only

    CAREFUL WITH --from AND --git. They are for recovering a history that lost
    something. Because the merge unions and never deletes, pointing them at an
    older copy also resurrects rows that were removed ON PURPOSE — the five
    class_polling model-days dropped on 2026-08-27, for instance, which would
    put cook_pvi back into the baseline audit. Run bare unless you are actually
    recovering.

WHY THIS FILE MOVED OUT OF THE REPO ROOT
    It was `_history_merge.py`, which `.gitignore` matches as `_*.py`. That was
    right while it was a one-off rescue and wrong once run.sh started calling
    it every night: an untracked file that the pipeline depends on exists on
    one laptop, which is the exact failure this whole archive arrangement is
    built to prevent.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

def _find_data() -> Path:
    """Walk up from this file until a forecast/data tree appears, so the
    script runs the same from forecast/model/ or from the repo root."""
    for p in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        cand = p / "forecast" / "data"
        if cand.is_dir():
            return cand
    return Path(__file__).resolve().parents[2] / "forecast" / "data"


DATA = _find_data()
HIST_NAME = "seat_projections_history.json"


def digest(o) -> str:
    return hashlib.sha256(
        json.dumps(o, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def read_json(text: str, label: str):
    try:
        h = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  {label:22s} UNPARSEABLE ({e})")
        return None
    return h if isinstance(h, dict) else None


def git_blobs(repo: Path, rel: str) -> list[tuple[str, float, str]]:
    """Every version of `rel` ever committed in `repo`, with its commit time.

    The commit time is what orders a blob against the working files: this
    merge lets the NEWEST run win a value, and an older copy contributes only
    what the newer ones no longer carry."""
    def g(*a):
        return subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *a],
                              capture_output=True, text=True).stdout
    revs = g("rev-list", "--all", "--", rel).split()
    out = []
    for r in revs:
        txt = g("show", f"{r}:{rel}")
        if not txt.strip():
            continue
        ts = g("show", "-s", "--format=%ct", r).strip()
        out.append((f"git:{repo.name}@{r[:8]}", float(ts or 0), txt))
    return out


def gather(cycle: int, extra: list[Path], gits: list[Path]):
    """Every copy we can lay hands on, sorted OLDEST FIRST.

    Sort order is the whole merge rule. Iterating oldest to newest and
    letting each copy overwrite the last means the newest run wins any value
    two copies disagree about, while an entry only an older copy still has
    survives because nothing later overwrites it."""
    cands: list[tuple[str, float, str]] = []
    live = DATA / str(cycle) / "model_private" / HIST_NAME
    for p in [*extra, live]:
        p = Path(p)
        if p.exists():
            cands.append((f"file:{p}", p.stat().st_mtime,
                          p.read_text(encoding="utf-8")))
        elif p in extra:
            print(f"  file:{p} — not found, skipping")
    for r in gits:
        rel = f"{cycle}/model_private/{HIST_NAME}"
        cands.extend(git_blobs(Path(r), rel))
        cands.extend(git_blobs(Path(r), f"forecast/data/{rel}"))
    out = []
    for label, when, txt in cands:
        h = read_json(txt, label)
        if h:
            out.append((label, when, h))
    out.sort(key=lambda t: t[1])
    return out


# Day-level keys that describe the run rather than one model.
DAY_KEYS = ("holdover_D", "majority_at", "note", "publication", "sigma",
            "snapshot_date")


def score(model: dict) -> tuple:
    """Tiebreak ONLY between copies with the same timestamp.

    Recency decides a disagreement, because these models are revised on
    purpose and an older value is superseded, not lost. Only when two copies
    are equally recent does richness decide: a projection carrying district
    arrays is a complete Monte Carlo, one without is a run that stopped
    early."""
    return (len(model.get("districts") or []),
            len(model.get("races") or {}),
            len(model),
            len(json.dumps(model, separators=(",", ":"))))


def merge(copies: list[tuple[str, dict]]):
    days: dict[str, dict] = collections.defaultdict(dict)
    prov: dict[str, dict] = collections.defaultdict(dict)
    conflicts: list[dict] = []
    day_meta: dict[str, dict] = collections.defaultdict(dict)

    newest = copies[-1][0] if copies else None
    for label, when, h in copies:
        for d, day in h.items():
            if not isinstance(day, dict):
                continue
            for k in DAY_KEYS:
                if day.get(k) is not None and k not in day_meta[d]:
                    day_meta[d][k] = day[k]
            for mid, m in (day.get("projections") or {}).items():
                if not isinstance(m, dict):
                    continue
                cur = days[d].get(mid)
                if cur is None:
                    days[d][mid] = m
                    prov[d][mid] = {"from": label, "when": when, "alts": 0,
                                    "sha": digest(m)}
                    continue
                if digest(cur) == digest(m):
                    # Same content, newer copy. Advance the attribution so
                    # "recovered from an older copy" means what it says: the
                    # newest run genuinely no longer carries this entry, not
                    # merely that an older copy happened to be read first.
                    prov[d][mid]["alts"] += 1
                    if when >= prov[d][mid]["when"]:
                        prov[d][mid]["from"], prov[d][mid]["when"] = label, when
                    continue
                # NEWEST WINS. Equal timestamps fall back to richer.
                prev_when = prov[d][mid]["when"]
                keep_new = (when > prev_when
                            or (when == prev_when and score(m) > score(cur)))
                conflicts.append({
                    "date": d, "model": mid,
                    "kept_from": label if keep_new else prov[d][mid]["from"],
                    "other_from": prov[d][mid]["from"] if keep_new else label,
                    "kept_sha": digest(m if keep_new else cur),
                    "other_sha": digest(cur if keep_new else m),
                    "kept_tide": (m if keep_new else cur).get("tide_D"),
                    "other_tide": (cur if keep_new else m).get("tide_D"),
                    "kept_districts": len((m if keep_new else cur).get("districts") or []),
                    "other_districts": len((cur if keep_new else m).get("districts") or []),
                })
                if keep_new:
                    days[d][mid] = m
                    prov[d][mid] = {"from": label, "when": when,
                                    "alts": prov[d][mid]["alts"] + 1,
                                    "sha": digest(m)}
                else:
                    prov[d][mid]["alts"] += 1
    return days, prov, conflicts, day_meta, newest


def short(label: str) -> str:
    return label if not label.startswith("file:") else "file:" + Path(label[5:]).parts[-4] + "/…/" + Path(label[5:]).name


def report(copies, days, prov, conflicts, newest):
    import datetime as dt
    print(f"\n  {len(copies)} readable copies, oldest first "
          f"(the last one is the authority on any value)\n")
    print(f"  {'copy':44s} {'when':12s} {'dates':>6s} {'m-days':>7s}  span")
    for label, when, h in copies:
        md = sum(len((v or {}).get("projections") or {}) for v in h.values())
        ds = sorted(h)
        stamp = dt.datetime.fromtimestamp(when).strftime("%Y-%m-%d")
        print(f"  {short(label)[:44]:44s} {stamp:12s} {len(h):6d} {md:7d}  "
              f"{ds[0]} .. {ds[-1]}")

    total = sum(len(v) for v in days.values())
    newest_md = sum(len((v or {}).get("projections") or {})
                    for v in dict(((l, h) for l, _, h in copies))[newest].values())
    recovered = collections.Counter()
    for d in prov:
        for mid, p in prov[d].items():
            if p["from"] != newest:
                recovered[p["from"]] += 1
    print(f"\n  MERGED: {len(days)} dates, {total} model-days")
    print(f"    newest copy alone had {newest_md}; "
          f"{total - newest_md} model-day(s) recovered from older copies:")
    for label, n in recovered.most_common():
        print(f"      {n:4d} from {short(label)[:56]}")

    print(f"\n  disagreements (same date+model, different content): {len(conflicts)}")
    tide = [c for c in conflicts if c["kept_tide"] != c["other_tide"]]
    print(f"    of which the national tide differs: {len(tide)} — "
          f"in every one the NEWER value was kept")
    for c in tide[:6]:
        print(f"      {c['date']} {c['model']:28s} kept {c['kept_tide']} "
              f"over {c['other_tide']}")
    per_model = collections.Counter(mid for d in days for mid in days[d])
    print("\n  model-days in the merged history, per model:")
    for mid, n in per_model.most_common():
        print(f"      {mid:32s} {n:4d}")


def write(cycle, days, prov, conflicts, day_meta, prune=False):
    base = DATA / str(cycle) / "model_private"
    out = base / "history"
    out.mkdir(parents=True, exist_ok=True)

    # ONLY REWRITE A SHARD WHOSE CONTENT CHANGED, and this is the difference
    # between an archive that stays manageable and one that does not.
    #
    # A daily run changes exactly one date. Rewriting all 585 shards anyway
    # would leave 585 fresh mtimes, and `rsync -a` compares size and mtime, so
    # the nightly sync would copy 334 MB across to the archive to deliver one
    # changed day. Git would still commit only the one file, but the copy is
    # wasted every night and the whole point of sharding is that a day costs a
    # day.
    written = skipped = 0
    for d, models in sorted(days.items()):
        payload = {**day_meta.get(d, {}), "snapshot_date": d,
                   "projections": models}
        text = json.dumps(payload, indent=1)
        p = out / f"{d}.json"
        if p.exists():
            try:
                if p.read_text(encoding="utf-8") == text:
                    skipped += 1
                    continue
            except OSError:
                pass
        p.write_text(text, encoding="utf-8")
        written += 1

    (base / "history_manifest.json").write_text(
        json.dumps({d: prov[d] for d in sorted(prov)}, indent=1), encoding="utf-8")
    if conflicts:
        with (base / "history_conflicts.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(conflicts[0]))
            w.writeheader()
            w.writerows(conflicts)
    print(f"\n  {written} shard(s) written, {skipped} unchanged, in {out}")
    print(f"  wrote history_manifest.json ({sum(len(v) for v in prov.values())} entries)")
    print(f"  wrote history_conflicts.csv ({len(conflicts)} row(s))")

    # STALE SHARDS: a file for a date the merged history no longer has. They
    # arise when a date is deliberately removed — two empty 2025 dates were,
    # on 2026-08-27 — and they are not harmless bookkeeping: gather() never
    # reads them so they cannot come back through the merge, but they DO get
    # copied to the archive, where they sit as a committed record contradicting
    # the history beside them.
    #
    # Reported and not deleted by default, because "lose nothing" is this
    # script's contract and silently removing a date's only surviving copy
    # would break it. --prune is the deliberate version.
    stale = sorted(p for p in out.glob("*.json") if p.stem not in days)
    if stale:
        if prune:
            for p in stale:
                p.unlink()
            print(f"  pruned {len(stale)} stale shard(s): "
                  f"{[p.stem for p in stale]}")
        else:
            print(f"\n  {len(stale)} STALE shard(s) — dates the merged history "
                  f"no longer has:")
            for p in stale:
                print(f"      {p.name}")
            print("  They will be copied to the archive as-is. Remove them with"
                  " --prune once you are sure those dates are meant to be gone.")

    print(f"\n  NOTHING WAS DELETED. {HIST_NAME} is untouched; delete it only "
          f"once you have pushed the shards and confirmed --verify passes.")


def fill_from_shards(cycle: int, shard_dir: Path) -> int:
    """Add dates the local history is MISSING from a shard directory.

    FILL-ONLY, AND THE ASYMMETRY IS THE WHOLE DESIGN.

        A date the archive has and we do not is added. A date we already have
        is never touched, whatever the archive says about it. So this can
        recover a day and can never revise one.

    WHY IT IS NEEDED

        run.sh's sync_raw_down deliberately never mirrored model_private down,
        because an older archived copy overwriting a newer local one is the
        accident this whole arrangement exists to prevent. That was right while
        one laptop was the only writer.

        It stopped being right when the daily Action started writing too. Now
        the archive can hold a date the laptop has never seen — any day the
        Action runs and the laptop does not — and the old rule says to ignore
        it. On 2026-08-28 the archive held 586 dates and the working tree 585.
        That day it self-healed, because the missing date was today and the
        next local run recomputed it. The day it does not self-heal is the day
        the laptop's history acquires a permanent hole, and everything
        published from that laptop afterwards is built from the copy with the
        hole in it.

        Fill-only closes that without reopening the original accident: the
        archive may hand us a day we lack, and may never change a day we hold.

    Shards with no projections are skipped: an empty day is not a recovered
    day, and two of them were deliberately deleted on 2026-08-27.
    """
    base = DATA / str(cycle) / "model_private"
    p = base / HIST_NAME
    if not shard_dir.is_dir():
        print(f"  no shard directory at {shard_dir} — nothing to fill from")
        return 0
    if not p.exists():
        print(f"  no {HIST_NAME} to fill. This is a fresh tree — use --unshard,"
              f" which builds it from the shards outright.")
        return 0
    try:
        hist = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  cannot read {HIST_NAME} ({type(e).__name__}) — refusing to "
              f"touch it")
        return 0

    added, empty = [], 0
    for s in sorted(shard_dir.glob("*.json")):
        d0 = s.stem
        if d0 in hist:
            continue
        try:
            day = json.loads(s.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"  unreadable shard, skipped: {s.name}")
            continue
        if not (day.get("projections") or {}):
            empty += 1
            continue
        day.setdefault("snapshot_date", d0)
        hist[d0] = day
        added.append(d0)

    if not added:
        print(f"  history already holds every date in the archive "
              f"({len(hist)} date(s))")
        return 0
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({k: hist[k] for k in sorted(hist)}))
    tmp.replace(p)
    show = added if len(added) <= 6 else added[:5] + [f"... +{len(added)-5} more"]
    print(f"  filled {len(added)} date(s) from the archive: {', '.join(show)}")
    print(f"  {HIST_NAME} now holds {len(hist)} date(s)")
    if empty:
        print(f"  ({empty} empty shard(s) skipped)")
    return len(added)


def unshard(cycle: int) -> int:
    """Rebuild the monolith from the shards.

    The inverse of --write, and what makes it safe for the archive to stop
    carrying seat_projections_history.json at all.

    The archive used to hold the monolith gzipped. That cleared GitHub's 100 MB
    per-file ceiling but not the growth: a new date changes the compressed
    bytes throughout, gzip does not delta-compress, so every nightly sync
    committed a fresh ~33 MB blob — about 2.2 GB between August and November.
    Shards cost one ~600 KB file per day instead, roughly 40 MB over the same
    stretch, and this is the function that lets a fresh clone turn them back
    into the file every other script reads.
    """
    base = DATA / str(cycle) / "model_private"
    src = base / "history"
    dest = base / HIST_NAME
    if not src.is_dir():
        print(f"  no shard directory at {src}")
        return 1
    shards = sorted(src.glob("*.json"))
    if not shards:
        print(f"  {src} holds no shards")
        return 1
    # Refuse rather than overwrite, for the same reason run.sh's
    # --restore-history refuses: which of two copies is newer is a question for
    # a person. Rebuilding is for a clone that has no monolith yet.
    if dest.exists():
        print(f"  {dest} already exists — refusing to overwrite it.")
        print("  Move it aside first if you really mean to rebuild from shards.")
        return 1
    hist, md = {}, 0
    for p in shards:
        day = json.loads(p.read_text(encoding="utf-8"))
        day.setdefault("snapshot_date", p.stem)
        hist[p.stem] = day
        md += len(day.get("projections") or {})
    dest.write_text(json.dumps({d: hist[d] for d in sorted(hist)}),
                    encoding="utf-8")
    print(f"  rebuilt {dest}")
    print(f"    {len(hist)} date(s), {md} model-day(s), "
          f"{dest.stat().st_size / 1e6:.0f} MB")
    return 0


def verify(cycle):
    base = DATA / str(cycle) / "model_private"
    man_p = base / "history_manifest.json"
    if not man_p.exists():
        print("  no manifest — run --write first")
        return 2
    man = json.loads(man_p.read_text())
    bad = 0
    for d, models in man.items():
        p = base / "history" / f"{d}.json"
        if not p.exists():
            print(f"  MISSING shard {d}")
            bad += 1
            continue
        have = set((json.loads(p.read_text()).get("projections") or {}))
        gone = set(models) - have
        if gone:
            print(f"  {d} lost {sorted(gone)}")
            bad += 1
    print(f"\n  {'FAIL' if bad else 'PASS'}: {len(man)} date(s) checked, "
          f"{bad} with losses")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--from", dest="extra", action="append", default=[],
                    help="another copy of the history file (repeatable)")
    ap.add_argument("--git", action="append", default=[],
                    help="a repo whose whole history of this path to sweep")
    ap.add_argument("--report", action="store_true", help="inventory only (default)")
    ap.add_argument("--write", action="store_true", help="write shards + manifest")
    ap.add_argument("--prune", action="store_true",
                    help="with --write: delete shards for dates the merged "
                         "history no longer has")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--unshard", action="store_true",
                    help="rebuild seat_projections_history.json from the "
                         "shards (for a fresh clone; refuses to overwrite)")
    ap.add_argument("--fill-from-shards", dest="fill", default=None,
                    metavar="DIR",
                    help="add dates the local history lacks from a shard "
                         "directory. Fill-only: never revises a date we "
                         "already hold")
    a = ap.parse_args(argv)

    if a.verify:
        return verify(a.cycle)
    if a.unshard:
        return unshard(a.cycle)
    if a.fill:
        fill_from_shards(a.cycle, Path(a.fill))
        return 0
    if a.prune and not a.write:
        print("  --prune only means anything with --write.")
        return 2

    print("=" * 74)
    print(f"history merge · cycle {a.cycle}")
    print("=" * 74)
    copies = gather(a.cycle, [Path(p) for p in a.extra], [Path(p) for p in a.git])
    if not copies:
        print("  no readable copies found")
        return 2
    days, prov, conflicts, day_meta, newest = merge(copies)
    report(copies, days, prov, conflicts, newest)
    if a.write:
        write(a.cycle, days, prov, conflicts, day_meta, prune=a.prune)
    else:
        print("\n  --report only. Nothing written. Add --write to shard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
