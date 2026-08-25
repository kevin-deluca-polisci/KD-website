#!/usr/bin/env python3
"""
Read a folder of Dave's Redistricting exports into one district baseline table.

    python3 forecast/collect/dra_import.py --dir ~/Downloads/dra --dry-run
    python3 forecast/collect/dra_import.py --dir ~/Downloads/dra \
        --out forecast/data/2026/derived/district_baseline.csv

-----------------------------------------------------------------------------
WHY THIS REPLACES COOK, AND WHAT THAT CHANGES

Cook's PVI is proprietary. Every number computed from it inherits that, which
is why no House district margin has ever been published by this project: the
national tide is public, so publishing a district margin beside it returns the
index exactly, PVI = (margin - tide) / 2. A public-domain baseline is not a
tidiness exercise. It is the only route by which the House side of this archive
is ever released.

DRA's Election Composite is also, on the argument in ROADMAP section 3a, the
better instrument. Cook's index is presidential-only, so in places where the
presidential candidate ran far from their party it measures the presidential
candidate rather than the district. South Texas is the clearest case: a
2020/2024 presidential index there records the strongest Republican on the
ballot, not how the district votes in a normal statewide race. The composite
blends downballot statewide contests where the candidates are ordinary, which
is closer to what a House race is.

THE ARITHMETIC THAT NO LONGER APPLIES

`margin = tide + 2 x PVI` is an identity ONLY because Cook's PVI is a
deviation from the national presidential two-party share. The composite has no
national counterpart -- there is no nationwide "Texas composite" -- so the 2 is
not derived from anything and must not be carried over by habit. What replaces
it is an estimate: with House returns now in the archive back to 1976, the
relationship between a district's baseline and its actual margin is a
regression, not an assumption. See model/dra_check.py.

WHAT THE EXPORT LOOKS LIKE, read from a real file rather than assumed:

    ID,Total Pop,Deviation,Dem,Rep,Oth,Total VAP,White,Minority,Hispanic,...
    "Un",0,0,0,0,0,0,...              <- unassigned territory, all zeros
    "1",766987,0,0.2567,0.7288,...    <- a district
    "",29145505,1,0.4526,0.5257,...   <- BLANK id is the statewide total

Three shapes that bite: a trailing comma on every line gives csv a phantom
column, the "Un" row is not a district, and the blank-id row is the state.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import hashlib
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

STATES = {
 "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
 "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
 "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA",
 "kansas":"KS","kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD",
 "massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS",
 "missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV",
 "newhampshire":"NH","newjersey":"NJ","newmexico":"NM","newyork":"NY",
 "northcarolina":"NC","northdakota":"ND","ohio":"OH","oklahoma":"OK",
 "oregon":"OR","pennsylvania":"PA","rhodeisland":"RI","southcarolina":"SC",
 "southdakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT",
 "virginia":"VA","washington":"WA","westvirginia":"WV","wisconsin":"WI",
 "wyoming":"WY",
}
CODES = set(STATES.values())
# The ten states that redrew for 2026. Only these need a second, older map;
# everywhere else the 2022 lines and the 2026 lines are the same lines.
REDREW = {"TX", "CA", "FL", "OH", "NC", "MO", "TN", "LA", "AL", "UT"}


def infer(name: str) -> dict:
    """State, map version and election, from the filename.

    Filenames are whatever the browser called them, so this reports what it
    inferred and refuses to guess silently. `--dry-run` exists so a folder of
    sixty files can be checked before any of it is trusted.
    """
    stem = Path(name).stem
    flat = re.sub(r"[^a-z0-9]", "", stem.lower())

    st = None
    m = re.search(r"(?:^|[^a-z])([a-z]{2})(?:[^a-z]|$)", stem.lower())
    if m and m.group(1).upper() in CODES:
        st = m.group(1).upper()
    if st is None:
        for long, code in sorted(STATES.items(), key=lambda kv: -len(kv[0])):
            if long in flat:
                st = code
                break

    # A year in the name decides the map. 2025 and 2026 mean the new lines;
    # anything from the last cycle means the old ones.
    ver = None
    if re.search(r"prior|old|previous|former", flat):
        ver = "prior"
    elif re.search(r"current|new", flat):
        ver = "current"
    elif re.search(r"202[56]", flat):
        ver = "current"
    elif re.search(r"202[0-4]|201\d", flat):
        ver = "prior"

    elec = "composite"
    if re.search(r"pres.*2024|2024.*pres", flat):
        elec = "pres2024"
    elif re.search(r"pres.*2020|2020.*pres", flat):
        elec = "pres2020"
    elif re.search(r"pres.*2016|2016.*pres", flat):
        elec = "pres2016"
    return {"state": st, "map_version": ver, "election": elec}


def read_export(path: Path) -> tuple[list[dict], dict | None]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if not rows:
        raise ValueError("empty file")
    for need in ("ID", "Dem", "Rep"):
        if need not in rows[0]:
            raise ValueError(f"no {need!r} column -- is this a DRA district "
                             f"statistics export?")
    out, state_row = [], None
    for r in rows:
        rid = (r.get("ID") or "").strip().strip('"')
        try:
            d, rp = float(r["Dem"]), float(r["Rep"])
        except (TypeError, ValueError):
            continue
        if d + rp <= 0:
            continue                    # "Un", and any empty district
        rec = {"two_party_D": 100 * d / (d + rp),
               "dem": d, "rep": rp, "oth": float(r.get("Oth") or 0)}
        for k_in, k_out in (("Total Pop", "total_pop"), ("Total VAP", "vap"),
                            ("White", "white"), ("Minority", "minority"),
                            ("Hispanic", "hispanic"), ("Black", "black"),
                            ("Asian", "asian"), ("Native", "native"),
                            ("Pacific", "pacific")):
            try:
                rec[k_out] = float(r[k_in]) if r.get(k_in) not in (None, "") \
                    else None
            except (TypeError, ValueError):
                rec[k_out] = None
        if rid == "":
            state_row = rec
        elif rid.isdigit():
            rec["district"] = f"{int(rid):02d}"
            out.append(rec)
    return out, state_row


def expected_seats() -> dict[str, int]:
    """Districts per state, taken from the 2024 returns we already hold.

    Apportionment is fixed for the decade, so 2022, 2024 and 2026 have the same
    counts. Checking against real returns beats a hardcoded table that would
    quietly go stale after the next census.
    """
    p = REPO / "forecast" / "data" / "2026" / "derived" / "returns.csv"
    if not p.exists():
        return {}
    seats = defaultdict(set)
    for r in csv.DictReader(p.open(encoding="utf-8")):
        if r["chamber"] == "house" and r["cycle"] == "2024" \
                and r["special"] == "False":
            seats[r["state"]].add(r["district"])
    return {k: len(v) for k, v in seats.items()}


def snapshot(folder: Path, cycle: int, dry_run: bool = False) -> int:
    """Copy the DRA exports into the raw store, verbatim, with hashes.

    WHY THE BYTES AND NOT THE PARSE. This project's whole architecture is that
    capture stores what a source said and never interprets it, and parse reads
    storage and never fetches. dra_import.py was written before there was a
    `dra` source in the registry, so it read the download folder directly and
    handed rows to a model. That works exactly until someone re-exports a state
    and the earlier numbers are gone with no record that they were ever
    different -- which for a redistricting archive is the one thing that must
    not happen, because the whole point is that a district's index carries the
    date on which it was knowable.

    So the CSVs go into raw/dra/<date>/ unchanged, one .meta.json each, and the
    parser reads them from there like every other source. The download folder
    becomes an inbox rather than the archive.

    NAMING. DRA names an export after the map's VINTAGE, so Arkansas's current
    lines arrive in a file called "AR-2022-...". The stored name is
    <state>-<current|prior>.csv, taken from the containing directory, because
    the directory is the only unambiguous statement of which map a file
    describes. See main() for the same reasoning applied to `infer`.
    """
    files = sorted(f for f in folder.rglob("*")
                   if f.suffix.lower() in (".csv", ".tsv", ".txt"))
    if not files:
        raise SystemExit(f"no CSVs in {folder}")
    date = _dt.date.today().isoformat()
    out = REPO / "forecast" / "data" / str(cycle) / "raw" / "dra" / date
    who = getpass.getuser()
    written = skipped = 0
    for f in files:
        meta_in = infer(f.name)
        fold = re.sub(r"[^a-z]", "", f.parent.name.lower())
        ver = None
        if "current" in fold or "new" in fold:
            ver = "current"
        elif "prior" in fold or "old" in fold or "previous" in fold:
            ver = "prior"
        st = meta_in["state"]
        if not st or not ver:
            print(f"    SKIP {f.name}  (state={st} version={ver}) — a file the "
                  f"importer cannot place is not stored, because a raw file "
                  f"with no state is worse than no file")
            skipped += 1
            continue
        body = f.read_bytes()
        name = f"{st}-{ver}"
        meta = {"source_url": "https://davesredistricting.org/",
                "fetched_at": _dt.datetime.now().astimezone().isoformat(),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "manual_entry": True, "entered_by": who,
                "original_filename": f.name,
                "original_folder": f.parent.name,
                "state": st, "map_version": ver,
                "election": meta_in.get("election"),
                "map_version_from": "containing directory, not the filename"}
        if not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{name}.csv").write_bytes(body)
            (out / f"{name}.meta.json").write_text(
                __import__("json").dumps(meta, indent=2, sort_keys=True))
        written += 1
    print(f"\n  {'would write' if dry_run else 'wrote'} {written} file(s) to "
          f"forecast/data/{cycle}/raw/dra/{date}/"
          + (f"   ({skipped} skipped)" if skipped else ""))
    return 0 if written else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--snapshot", action="store_true",
                    help="copy the exports into raw/dra/<today>/ verbatim "
                         "with hashes, so parse.py can read them like any "
                         "other source. Does not model anything.")
    ap.add_argument("--cycle", type=int, default=2026)
    a = ap.parse_args(argv)

    d = Path(a.dir).expanduser()
    if a.snapshot:
        return snapshot(d, a.cycle, a.dry_run)
    # WALK SUBDIRECTORIES, because the folder is the answer to a question the
    # filename gets wrong. DRA names an export after the map's VINTAGE --
    # "AR-2022-Congressional-district-statistics.csv" -- and Arkansas's 2022
    # lines are its CURRENT lines, because Arkansas never redrew. Read the year
    # as the version and 31 states get filed as prior maps that do not exist.
    # A directory called "Current Map" or "Prior Map" is unambiguous and wins.
    files = sorted(f for f in d.rglob("*")
                   if f.suffix.lower() in (".csv", ".tsv", ".txt"))
    if not files:
        raise SystemExit(f"no CSVs in {d}")
    seats = expected_seats()

    rows, problems, seen = [], [], defaultdict(list)
    statewide: dict[tuple, float] = {}
    print(f"  {len(files)} file(s) in {d}\n")
    print(f"  {'file':<40}{'st':<4}{'map':<9}{'election':<11}"
          f"{'n':>4}  check")
    for f in files:
        meta = infer(f.name)
        folder = re.sub(r"[^a-z]", "", f.parent.name.lower())
        if "current" in folder or "new" in folder:
            meta["map_version"] = "current"
        elif "prior" in folder or "old" in folder or "previous" in folder:
            meta["map_version"] = "prior"
        try:
            got, state_row = read_export(f)
        except Exception as e:
            problems.append(f"{f.name}: {e}")
            print(f"  {f.name[:38]:<40}{'--':<4}{'--':<9}{'--':<11}"
                  f"{'--':>4}  UNREADABLE: {e}")
            continue
        st = meta["state"]
        note = ""
        if st is None:
            note = "NO STATE IN FILENAME"
            problems.append(f"{f.name}: could not read a state from the name")
        elif meta["map_version"] is None:
            note = "NO MAP VERSION IN FILENAME"
            problems.append(f"{f.name}: cannot tell current from prior")
        elif st in seats and len(got) != seats[st]:
            note = f"!! expected {seats[st]} districts, got {len(got)}"
            problems.append(f"{f.name}: {note}")
        else:
            note = "ok"
            if state_row:
                note += f"   statewide 2pD {state_row['two_party_D']:.2f}%"
        print(f"  {f.name[:38]:<40}{st or '?':<4}{meta['map_version'] or '?':<9}"
              f"{meta['election']:<11}{len(got):>4}  {note}")
        if st and meta["map_version"]:
            seen[(st, meta["map_version"], meta["election"])].append(f.name)
            if state_row:
                statewide[(st, meta["map_version"], meta["election"])] = \
                    state_row["two_party_D"]
        sw = state_row["two_party_D"] if state_row else None
        for g in got:
            rows.append({"state": st, "map_version": meta["map_version"],
                         "election": meta["election"], "source_file": f.name,
                         "statewide_two_party_D": sw,
                         # HOW THIS DISTRICT LEANS RELATIVE TO ITS OWN STATE.
                         # This is what a composite measures well and what
                         # redistricting actually changes -- a redraw
                         # reshuffles voters within a state and leaves the
                         # state's own lean untouched. It is also robust to
                         # the dataset mismatch above, since a level shift
                         # common to every district in a state cancels.
                         "dev_from_state": (g["two_party_D"] - sw
                                            if sw is not None else None),
                         **g})

    # ------------------------------------------------------------------
    # THE CHECK THAT MATTERS MOST, and it is an identity rather than a
    # heuristic: redistricting moves voters BETWEEN districts and cannot
    # change how a state votes in total. A state's statewide two-party share
    # must therefore be the same on its old lines and its new lines. Where it
    # is not, the two exports were computed on DIFFERENT ELECTION DATASETS --
    # DRA lets a plan carry its own dataset selection, and a superseded plan
    # often kept an older one.
    #
    # This is not a rounding quibble. The first full folder disagreed by 5.2
    # points in Florida and 5.0 in Alabama, which is larger than almost any
    # redistricting effect. A seats-versus-tide curve built on that would be
    # measuring the instrument, not the map, and would look entirely
    # plausible while doing it.
    print("\n  SELF-CONSISTENCY  (statewide share must not move with the map)")
    state_wide: dict[tuple, float] = {}
    for (st, ver, elec), _names in seen.items():
        pass
    incons = []
    for st in sorted({k[0] for k in seen}):
        c = statewide.get((st, "current", "composite"))
        pr = statewide.get((st, "prior", "composite"))
        if c is None or pr is None:
            continue
        d = c - pr
        if abs(d) >= 0.05:
            incons.append((st, c, pr, d))
    if not incons:
        print("    every state with two maps agrees with itself")
    else:
        print(f"    !! {len(incons)} state(s) disagree. Re-export with the SAME")
        print(f"    !! election dataset selected for both maps.")
        print(f"       {'st':<4}{'current':>9}{'prior':>9}{'diff':>8}")
        for st, c, pr, d in sorted(incons, key=lambda r: -abs(r[3])):
            print(f"       {st:<4}{c:>9.2f}{pr:>9.2f}{d:>+8.2f}")
        problems.append(f"{len(incons)} state(s) fail the statewide identity")

    print("\n  COMPLETENESS")
    have_cur = {k[0] for k in seen if k[1] == "current" and k[2] == "composite"}
    missing_cur = sorted(CODES - have_cur)
    print(f"    current-map composite: {len(have_cur)}/50 states")
    if missing_cur:
        print(f"      missing: {' '.join(missing_cur)}")
    have_pri = {k[0] for k in seen if k[1] == "prior" and k[2] == "composite"}
    missing_pri = sorted(REDREW - have_pri)
    print(f"    prior-map composite:   {len(have_pri & REDREW)}/10 redrawn states")
    if missing_pri:
        print(f"      missing: {' '.join(missing_pri)}")
    extra = sorted(have_pri - REDREW)
    if extra:
        print(f"      note: prior maps supplied for {' '.join(extra)}, which "
              f"did not redraw -- harmless, unused")
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        print(f"    !! {len(dupes)} duplicate(s):")
        for k, v in list(dupes.items())[:5]:
            print(f"       {k}: {', '.join(v)}")

    if problems:
        print(f"\n  {len(problems)} PROBLEM(S):")
        for p_ in problems[:20]:
            print(f"    {p_}")

    print(f"\n  {len(rows):,} district row(s) read")
    if a.out and rows and not a.dry_run:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        cols = ["state", "district", "map_version", "election", "two_party_D",
                "statewide_two_party_D", "dev_from_state",
                "dem", "rep", "oth", "total_pop", "vap", "white", "minority",
                "hispanic", "black", "asian", "native", "pacific",
                "source_file"]
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"  wrote {out}")
    elif a.dry_run:
        print("  dry run, nothing written")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
