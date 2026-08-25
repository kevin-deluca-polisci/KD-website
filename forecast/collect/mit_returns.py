#!/usr/bin/env python3
"""
Election RESULTS, from Harvard Dataverse. The `y` the endorsement model needs.

    python3 forecast/collect/mit_returns.py --list
    python3 forecast/collect/mit_returns.py --download
    python3 forecast/collect/mit_returns.py --columns

-----------------------------------------------------------------------------
WHY THIS EXISTS

Three cycles of endorsements with no outcomes attached is a description, not a
training set. `endorsement_quality` cannot be fit, cannot be validated, and
cannot be scored until every race in 2022 and 2024 carries who actually won and
by how much.

WHY DATAVERSE RATHER THAN THE PAGES WE ALREADY HOLD

Wikipedia race articles carry results, in {{Election box}} templates, on pages
this project already downloads every day. Parsing those is tempting and wrong.
It is a second extraction project with its own failure modes, on a source that
is not authoritative for vote counts, and the output would have to be
reconciled against a real returns dataset anyway. MEDSL has already done that
reconciliation, publishes it with a DOI, and is citable in a way a wiki page
is not.

WHY --list COMES FIRST, AND WHY IT DOES NOT GUESS

The Wikipedia extractor was written twice because the first version inferred
structure from how a page LOOKED rather than reading the markup. The same
mistake is available here: the column names of these files are widely known,
widely reproduced, and were revised between versions. So this asks the API what
is actually in the dataset -- version, files, columns, licence -- and reports
it, before one line of normalisation gets written against a remembered schema.

THE GOVERNOR SOURCE IS NOT MEDSL. MEDSL publishes House, Senate and President
and does not publish a gubernatorial series. Klarner's Governors Dataset is the
standard substitute and it is a DIFFERENT dataset by a different author with a
different schema and its own coverage end date. It is listed here so the gap is
visible rather than discovered later; nothing merges the two silently.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import unicodedata
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
API = "https://dataverse.harvard.edu/api"

DATASETS = {
    "house": {
        "doi": "doi:10.7910/DVN/IG0UN2",
        "note": "MEDSL, U.S. House. District-level, the core of the House model.",
    },
    "senate": {
        "doi": "doi:10.7910/DVN/PEJ5QU",
        "note": "MEDSL, U.S. Senate, statewide.",
    },
    "president": {
        "doi": "doi:10.7910/DVN/42MVDX",
        "note": "MEDSL, president by state. Not needed for endorsements; "
                "useful as a partisanship baseline cross-check.",
    },
    "governor": {
        "doi": "doi:10.7910/DVN/PQ0Y1N",
        "note": "KLARNER, not MEDSL -- different author, schema and coverage. "
                "MEDSL publishes no gubernatorial series.",
    },
}


def fetcher():
    import capture
    import yaml
    reg = yaml.safe_load((REPO / "forecast" / "sources" / "2026.yaml")
                         .read_text(encoding="utf-8"))
    return capture.Fetcher(reg.get("contact") or {}, reg.get("defaults") or {})


def describe(f, doi: str) -> dict | None:
    url = (f"{API}/datasets/:persistentId/?"
           + urllib.parse.urlencode({"persistentId": doi}))
    body, _ = f.get(url)
    if not body:
        return None
    d = json.loads(body)
    if d.get("status") != "OK":
        return None
    return d["data"]["latestVersion"]


def _license(v: dict) -> str:
    lic = v.get("license")
    if isinstance(lic, dict):
        return f"{lic.get('name')} <{lic.get('uri')}>"
    if isinstance(lic, str):
        return lic
    tou = (v.get("termsOfUse") or "").strip()
    return (tou[:200] + "...") if len(tou) > 200 else (tou or "NOT STATED")


def _citation(v: dict) -> str:
    for fld in (v.get("metadataBlocks", {}).get("citation", {})
                .get("fields", [])):
        if fld.get("typeName") == "title":
            return str(fld.get("value"))
    return "?"


def cmd_list(f, only: list[str] | None) -> int:
    for name, spec in DATASETS.items():
        if only and name not in only:
            continue
        print("=" * 72)
        print(f"  {name.upper()}   {spec['doi']}")
        print(f"  {spec['note']}")
        print("=" * 72)
        v = describe(f, spec["doi"])
        if v is None:
            print("  could not read dataset metadata\n")
            continue
        print(f"  title    : {_citation(v)}")
        print(f"  version  : {v.get('versionNumber')}."
              f"{v.get('versionMinorNumber')}   released "
              f"{str(v.get('releaseTime'))[:10]}")
        print(f"  licence  : {_license(v)}")
        print(f"  {len(v.get('files', []))} file(s):")
        for fl in v.get("files", []):
            df = fl.get("dataFile", {})
            print(f"    id {df.get('id'):<10} {df.get('filename','?'):<44}"
                  f"{(df.get('filesize') or 0)/1e6:>8.1f} MB  "
                  f"{df.get('contentType','')}")
        print()
    return 0


def raw_dir(cycle: int) -> Path:
    return REPO / "forecast" / "data" / str(cycle) / "raw" / "mit_returns"


def cmd_download(f, only: list[str] | None, cycle: int, snapshot: str) -> int:
    import capture
    store = capture.RawStore(cycle, snapshot)
    total = 0
    manual: list[tuple] = []
    for name, spec in DATASETS.items():
        if only and name not in only:
            continue
        v = describe(f, spec["doi"])
        if v is None:
            print(f"  {name}: could not read metadata")
            continue
        for fl in v.get("files", []):
            df = fl.get("dataFile", {})
            fn = df.get("filename", "")
            # The .tab is Dataverse's ingested copy; `format=original` gets the
            # file as deposited, which is what the codebook describes.
            if not fn.lower().endswith((".tab", ".csv", ".txt", ".dta")):
                continue
            # TWO URLS, IN ORDER, AND THE SECOND IS NOT A FALLBACK FOR
            # POLITENESS -- it is the correct one for half these files.
            #
            # `format=original` returns the file as deposited, which is what
            # the codebook describes, but it is only valid for files Dataverse
            # INGESTED into its own tabular format. Ask for the original of a
            # file that was never ingested and the API answers 400. That is
            # exactly the split seen on the first run: the senate .tab was
            # ingested and worked, the president .csv was not and did not.
            urls = [
                f"{API}/access/datafile/{df['id']}?"
                + urllib.parse.urlencode({"format": "original"}),
                f"{API}/access/datafile/{df['id']}",
            ]
            body = meta = None
            errs = []
            for u in urls:
                try:
                    body, meta = f.get(u)
                    break
                except Exception as e:
                    errs.append(str(e))
                    body = None
            if body is None:
                print(f"  {name}/{fn}: FAILED  {' | '.join(errs)}")
                manual.append((name, fn, spec["doi"], df["id"]))
                continue
            meta = dict(meta)
            meta["dataverse"] = {
                "doi": spec["doi"], "file_id": df["id"], "filename": fn,
                "version": f"{v.get('versionNumber')}."
                           f"{v.get('versionMinorNumber')}",
                "licence": _license(v),
            }
            n = store.write("mit_returns", f"{name}-{fn}", body, meta)
            total += n
            print(f"  ok  {name:<10}{fn:<44}{n/1e6:>8.2f} MB")
    print(f"\n  {total/1e6:.2f} MB into "
          f"{raw_dir(cycle) / snapshot}")
    if manual:
        print("\n  COULD NOT FETCH THESE. Download them in a browser and file")
        print("  them with --import, which records the same provenance a")
        print("  fetched copy would have had:")
        for name, fn, doi, fid in manual:
            print(f"\n    {name}: {fn}")
            print(f"      https://dataverse.harvard.edu/dataset.xhtml"
                  f"?persistentId={doi}")
            print(f"      or direct: https://dataverse.harvard.edu/api/access/"
                  f"datafile/{fid}")
            print(f"      then: python3 forecast/collect/mit_returns.py "
                  f"--import ~/Downloads/{fn} --as {name}")
    return 0


def cmd_import(path: Path, name: str, cycle: int, snapshot: str) -> int:
    """File a hand-downloaded copy with the provenance a fetch would have left.

    A file that arrives by hand and lands in the archive unmarked is worse than
    one that never arrives: every other byte here records where it came from
    and when, and one that does not is indistinguishable from something
    somebody made up. This writes the same meta a fetch would, plus an explicit
    note that a human moved it.
    """
    import capture
    import hashlib
    import datetime as dt
    if not path.exists():
        raise SystemExit(f"no such file: {path}")
    body = path.read_bytes()
    spec = DATASETS[name]
    meta = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "status": None,
        "final_url": None,
        "manual_import": {
            "by": "human, in a browser",
            "reason": "Dataverse API refused the programmatic request",
            "local_filename": path.name,
            "doi": spec["doi"],
        },
    }
    store = capture.RawStore(cycle, snapshot)
    n = store.write("mit_returns", f"{name}-{path.name}", body, meta)
    print(f"  filed {path.name} as mit_returns/{name}  {n/1e6:.2f} MB")
    print(f"  sha256 {meta['sha256'][:16]}...")
    return 0


def cmd_columns(cycle: int) -> int:
    """What is ACTUALLY in the files, read from the files."""
    import csv
    base = raw_dir(cycle)
    if not base.is_dir():
        raise SystemExit(f"nothing downloaded yet under {base}")
    dates = sorted(p.name for p in base.iterdir() if p.is_dir())
    d = base / dates[-1]
    for p in sorted(d.iterdir()):
        if p.name.endswith(".meta.json"):
            continue
        print("=" * 72)
        print(f"  {p.name}   {p.stat().st_size/1e6:.1f} MB")
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  unreadable: {e}")
            continue
        lines = txt.splitlines()
        delim = "\t" if lines and lines[0].count("\t") > lines[0].count(",") \
            else ","
        rdr = csv.reader(lines[:4000], delimiter=delim)
        rows = list(rdr)
        if not rows:
            print("  empty")
            continue
        hdr = rows[0]
        print(f"  {len(lines):,} lines, delimiter {delim!r}, "
              f"{len(hdr)} columns:")
        print("    " + ", ".join(hdr))
        yi = next((i for i, c in enumerate(hdr)
                   if c.strip().lower() in ("year", "yr", "elecyear")), None)
        if yi is not None:
            yrs = sorted({r[yi] for r in rows[1:] if len(r) > yi and r[yi]})
            print(f"    year column {hdr[yi]!r}: {yrs[0]} .. {yrs[-1]} "
                  f"(from the first {len(rows)-1:,} rows only)")
        print("  first data row:")
        for k, val in zip(hdr, rows[1] if len(rows) > 1 else []):
            print(f"    {k:<24}{str(val)[:56]}")
        print()
    print("  NOTE: the year range above is read from the head of the file, so")
    print("  it is a lower bound on coverage, not the real maximum. The")
    print("  normaliser will report the true range once it reads all of it.")
    return 0


# ---------------------------------------------------------------------------
# NORMALISATION
#
# THE TWO SCHEMAS ARE NOT THE SAME, which is the first thing that breaks a
# normaliser written against one of them. The Senate file carries
# `party_simplified`; the House file does not, and carries `party` (raw Clerk
# strings), `runoff` and `fusion_ticket` instead. Read the columns, do not
# assume them.
#
# FOUR TRAPS, THREE OF THEM DOCUMENTED IN THE CODEBOOK AND ALL FOUR SILENT.

MEDSL_MIN = ("year", "state_po", "office", "district", "candidate",
             "candidatevotes", "totalvotes")

_OFFICE = {"US HOUSE": "house", "US SENATE": "senate",
           "US PRESIDENT": "president", "PRESIDENT": "president",
           "GOVERNOR": "governor"}
# THE FIFTY STATES, AND NOTHING ELSE, FOR THE HOUSE.
#
# MEDSL carries the District of Columbia's delegate election in its House file:
# Eleanor Holmes Norton, 2020 and 2024. A delegate has no seat and no floor
# vote, so counting one makes the House 436 members and every "435 races, 435
# winners" check quietly wrong by one. Territories (AS GU MP PR VI) would do
# the same if they appeared.
#
# This is the same call already recorded for the conditions sheet -- delegates
# out, at-large districts numbered 00 -- applied to the returns so the two
# sides of the join agree about what a House race is.
STATE_CODES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY".split())
NON_VOTING = {"DC", "AS", "GU", "MP", "PR", "VI"}

_DEM = re.compile(r"^(democrat|democratic|democratic-farmer-labor|dfl|"
                  r"democratic-npl|democratic farmer labor)$")
_REP = re.compile(r"^(republican|gop)$")


_SUSPECT_MOJI = re.compile(
    "[\u00c3\u00c2\u00e2][\u0080-\u00bf]"
    "|\u00e2\u20ac[\u0098-\u009d\u201c\u201d]"
    "|[\u0080-\u009f]")


def _moji_score(s: str) -> int:
    return len(_SUSPECT_MOJI.findall(s))


def unmojibake(s: str) -> str:
    """Undo double-encoding, but only where it demonstrably improves things.

    MEDSL's House file is valid UTF-8 carrying text that was already mangled
    before they shipped it: a character was encoded to UTF-8, read back as
    latin-1, and encoded again. An acute A arrives as the four bytes
    C3 83 C2 81 and renders as a tilde-A plus an invisible control character.
    55 fields in the 2024 file, and they are not random -- they are every
    accented surname and every quoted nickname, which is to say precisely the
    names the endorsement join has the hardest time matching. Velazquez,
    Carson, Duenas, D'Esposito, and every "Buddy"/"Chuck"/"Hank".

    The round-trip that undoes it is the same operation that DESTROYS correct
    text, so it runs only when the string carries a mojibake signature and only
    when the result scores cleaner than the input. Correctly encoded names pass
    through untouched, which is checked rather than hoped for.
    """
    for _ in range(3):
        if not _SUSPECT_MOJI.search(s):
            break
        try:
            u = s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if _moji_score(u) >= _moji_score(s):
            break
        s = u
    return s


def _clean_name(s: str) -> str:
    """Undo the Clerk file's quoting quirks.

    MEDSL escapes an inner quote the C way -- \\" -- rather than the CSV way,
    where it would be doubled. Python's csv module therefore leaves the
    backslashes in the field, and every name arrives as
    EARL L. \\"BUDDY\\" CARTER. That is invisible in a vote total and fatal to
    a name match: anything looking for a quoted nickname finds an opening quote
    and no closing one, so the whole nickname tier silently never fires.

    Also flattens the curly quote variants to straight ones so downstream has a
    single form to look for.
    """
    s = s.replace("\\", "")
    s = (s.replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2018", "'").replace("\u2019", "'"))
    return re.sub(r"\s+", " ", s).strip().strip('"').strip()


def simplify_party(raw: str) -> str:
    """Raw Clerk party string -> DEMOCRAT / REPUBLICAN / OTHER / UNKNOWN.

    The House file has no `party_simplified` column, so this reconstructs it.
    Minnesota's DFL and North Dakota's Democratic-NPL are the Democratic party
    under local names; missing it files two states' nominees as third parties.
    """
    p = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if _DEM.match(p):
        return "DEMOCRAT"
    if _REP.match(p):
        return "REPUBLICAN"
    if p in ("", "na", "n/a", "none"):
        return "UNKNOWN"
    return "OTHER"


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("true", "t", "1", "yes")


def name_key(name: str) -> str:
    """Fold a candidate name for joining across sources.

    MEDSL writes "NICK LALOTA"; Wikipedia writes "Nick LaLota". Accents folded,
    nicknames in quotes dropped, suffixes removed, punctuation flattened. It
    will not resolve "Bob" to "Robert" and is not meant to -- the join reports
    its own match rate and the misses get looked at rather than assumed away.
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r'"[^"]*"', " ", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv|md|phd|esq)\b", " ", n.lower())
    n = re.sub(r"[^a-z ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def surname_key(name: str) -> str:
    """'lalota n'. The fallback join key when full names disagree."""
    parts = name_key(name).split()
    if not parts:
        return ""
    return f"{parts[-1]} {parts[0][:1]}" if len(parts) > 1 else parts[0]


def normalise(path: Path) -> tuple[list[dict], dict]:
    """One row per CANDIDATE per general-election race.

    TRAP 1 -- FUSION TICKETS. New York, Connecticut, New Jersey and South
    Carolina let a candidate appear on several party lines, one row each. Nick
    LaLota in NY-01 in 2024 is 200,802 REPUBLICAN and 25,483 CONSERVATIVE, and
    he received 226,285 votes. Keying on candidate+party splits him in two,
    understates him by 11%, and in a close race hands the seat to whoever
    actually lost. So the key is the CANDIDATE, votes sum across lines, and the
    reported party is the line they polled best on. 1,219 candidates in the
    file need this.

    TRAP 2 -- FLORIDA'S UNCONTESTED SENTINEL. The codebook says it plainly:
    for uncontested Florida races `candidatevotes` is set to 1. Ninety-two rows.
    Those are not vote counts and any share or margin computed from them is
    fiction, so they are flagged `votes_unreliable` rather than quietly
    averaged into anything.

    TRAP 3 -- MODE. A candidate can appear once per voting mode, and many
    states ALSO report a TOTAL row. If a TOTAL exists it is the number;
    otherwise the parts are summed. Summing blindly doubles those states.

    TRAP 4 -- PRIMARIES. Dropped entirely, and this is a modelling decision
    rather than a data one: see forecast/ROADMAP.md. A primary tells you who
    the strong candidate was IN THAT PRIMARY, against a different opponent set
    than the general. The quality DIFFERENTIAL that a general-election vote
    share responds to is between the two nominees, so primary contests are not
    the same object and are not pooled with them.
    """
    txt = path.read_text(encoding="utf-8", errors="replace")
    head = txt.splitlines()[0] if txt else ""
    delim = "\t" if head.count("\t") > head.count(",") else ","
    rdr = csv.DictReader(io.StringIO(txt), delimiter=delim)
    cols = set(rdr.fieldnames or [])
    missing = [c for c in MEDSL_MIN if c not in cols]
    if missing:
        raise SystemExit(f"{path.name}: not a returns file, missing {missing}")
    party_col = "party_simplified" if "party_simplified" in cols else "party"

    groups: dict[tuple, dict] = {}
    notes = {"primary_rows_dropped": 0, "fl_sentinel": 0,
             "fusion_merged": 0, "party_column": party_col,
             "non_voting_dropped": 0}
    for r in rdr:
        office = _OFFICE.get((r.get("office") or "").strip().upper())
        if office is None:
            continue
        stage = re.sub(r"\s+", " ", (r.get("stage") or "gen").strip().lower())
        # "pre" IS NOT "pri". It is the FIRST ROUND of a general election that
        # went to a runoff -- Coverdell v Fowler in Georgia in 1992, Chambliss
        # v Martin in 2008. Three letters from "pri", and dropping it as a
        # primary would silently delete Georgia's general-election results in
        # every year the race went two rounds. Only stages that actually begin
        # "pri" are primaries; everything else is some flavour of general and
        # is kept, labelled, so a runoff is never pooled with a first round.
        if stage.startswith("pri"):
            notes["primary_rows_dropped"] += 1
            continue
        notes.setdefault("stages", {})
        notes["stages"][stage] = notes["stages"].get(stage, 0) + 1
        st_ = (r.get("state_po") or "").strip().upper()
        if office == "house" and st_ not in STATE_CODES:
            notes["non_voting_dropped"] = notes.get("non_voting_dropped", 0) + 1
            notes.setdefault("non_voting_seen", set()).add(st_)
            continue
        dist = (r.get("district") or "").strip()
        if office == "house":
            try:
                dist = f"{int(float(dist)):02d}"      # at-large is 0 -> "00"
            except (TypeError, ValueError):
                dist = "00"
        else:
            dist = None
        try:
            votes = float(r.get("candidatevotes") or 0)
            total = float(r.get("totalvotes") or 0)
        except ValueError:
            continue
        is_runoff = _truthy(r.get("runoff")) or "runoff" in stage
        race = (int(r["year"]), office, (r.get("state_po") or "").strip(),
                dist, _truthy(r.get("special")), is_runoff)
        cand = _clean_name(unmojibake((r.get("candidate") or "").strip()))
        k = race + (name_key(cand),)
        g = groups.setdefault(k, {"cand": cand, "lines": [], "total_row": None,
                                  "sum": 0.0, "totalvotes": total,
                                  "writein": False, "fusion": False,
                                  "sentinel": False})
        g["lines"].append((simplify_party(r.get(party_col)), votes,
                           (r.get(party_col) or "").strip()))
        if (r.get("mode") or "").strip().lower() in ("total", ""):
            g["total_row"] = (g["total_row"] or 0) + votes
        else:
            g["sum"] += votes
        g["totalvotes"] = max(g["totalvotes"], total)
        g["writein"] |= _truthy(r.get("writein"))
        g["fusion"] |= _truthy(r.get("fusion_ticket"))
        # THE SENTINEL IS NOT ALWAYS 1. The codebook documents Florida's
        # uncontested races as candidatevotes == 1; FL-25 in 2020 records Mario
        # Diaz-Balart, who was unopposed, at MINUS one. A negative vote count
        # is never real, and this one produced a race with no winner at all
        # rather than an error -- 434 winners for 435 seats, which is the kind
        # of off-by-one that survives every glance.
        if votes < 0 or (votes <= 1 and total <= 1):
            g["sentinel"] = True

    rows = []
    for k, g in groups.items():
        year, office, st, dist, special, runoff = k[:6]
        v = g["total_row"] if g["total_row"] is not None else g["sum"]
        if len(g["lines"]) > 1:
            notes["fusion_merged"] += 1
        if g["sentinel"]:
            notes["fl_sentinel"] += 1
        best = max(g["lines"], key=lambda x: x[1])
        rows.append({
            "year": year,
            # THE CYCLE, NOT THE YEAR. Georgia's runoffs are held in January
            # and MEDSL dates them to the year they occur: David Perdue's
            # runoff is filed under 2021 and belongs to the 2020 cycle. Joining
            # endorsements to outcomes on `year` would drop every Georgia
            # runoff from the training set, and Georgia runoffs are exactly the
            # close races the model most needs.
            "cycle": year if year % 2 == 0 else year - 1,
            "chamber": office, "state": st, "district": dist,
            "special": special, "runoff": runoff, "candidate": g["cand"],
            "party": best[0], "party_raw": best[2],
            "candidate_key": k[6], "surname_key": surname_key(g["cand"]),
            "n_party_lines": len(g["lines"]), "fusion": g["fusion"],
            "votes": v, "totalvotes": g["totalvotes"],
            "share": (100 * v / g["totalvotes"]) if g["totalvotes"] else None,
            "writein": g["writein"], "votes_unreliable": g["sentinel"],
        })

    by_race: dict[tuple, list[dict]] = {}
    for r in rows:
        by_race.setdefault((r["year"], r["chamber"], r["state"], r["district"],
                            r["special"], r["runoff"]), []).append(r)
    for rs in by_race.values():
        rs.sort(key=lambda x: -(x["votes"] or 0))
        top = rs[0]["votes"] or 0
        second = rs[1]["votes"] if len(rs) > 1 else 0
        # AN UNOPPOSED CANDIDATE WON, whatever number is in the votes column.
        # Where the counts are sentinels rather than votes, the ballot still
        # had one name on it and that person took the seat.
        sole = len(rs) == 1
        d = next((x["votes"] for x in rs if x["party"] == "DEMOCRAT"), None)
        rp = next((x["votes"] for x in rs if x["party"] == "REPUBLICAN"), None)
        two = (d + rp) if (d is not None and rp is not None) else None
        for i, r in enumerate(rs):
            r["won"] = (i == 0 and (top > 0 or sole))
            r["n_candidates"] = len(rs)
            # NO MAJOR-PARTY OPPONENT is the case the model cannot use: there
            # is no quality differential to measure against.
            r["uncontested"] = (d is None or rp is None)
            r["margin_D"] = (100 * (d - rp) / two) if two else None
            r["two_party_D"] = (100 * d / two) if two else None
            r["race_margin"] = ((100 * (top - (second or 0)) / r["totalvotes"])
                                if r["totalvotes"] else None)
    return rows, notes


def normalise_report(rows: list[dict], notes: dict, label: str) -> None:
    from collections import Counter
    print("=" * 72)
    print(f"  {label}: {len(rows):,} candidate-rows")
    print("=" * 72)
    yrs = sorted({r["year"] for r in rows})
    print(f"  years {yrs[0]} .. {yrs[-1]} ({len(yrs)})   "
          f"party read from {notes['party_column']!r}")
    st_ = notes.get("stages") or {}
    if st_:
        print("  stages kept: " + "  ".join(
            f"{k!r} {v:,}" for k, v in sorted(st_.items(), key=lambda x: -x[1])))
    off = [r for r in rows if r["year"] != r["cycle"]]
    if off:
        print(f"  {len(off)} row(s) held in an odd year, filed to the previous "
              f"cycle (Georgia runoffs)")
    print(f"  {notes['primary_rows_dropped']} primary row(s) dropped   "
          f"{notes['fusion_merged']:,} fusion candidate(s) merged across party "
          f"lines")
    if notes["fl_sentinel"]:
        print(f"  {notes['fl_sentinel']} Florida uncontested row(s) flagged "
              f"votes_unreliable (candidatevotes==1 sentinel)")
    print("  by party : " + "  ".join(
        f"{k} {v:,}" for k, v in Counter(r["party"] for r in rows).most_common(4)))
    for y in [x for x in (2018, 2020, 2022, 2024) if x in yrs]:
        g = [r for r in rows if r["year"] == y and not r["special"]
             and not r["runoff"]]
        rc = {(r["state"], r["district"]) for r in g}
        print(f"    {y}: {len(rc):>3} race(s), {len(g):>5,} candidates, "
              f"{sum(1 for r in g if r['won']):>3} winner(s), "
              f"{sum(1 for r in g if r['uncontested']):>3} with no major-party "
              f"opponent")
    nv = notes.get("non_voting_dropped") or 0
    if nv:
        print(f"  {nv} non-voting delegate row(s) dropped "
              f"({', '.join(sorted(notes.get('non_voting_seen') or []))})")
    # THE CHECK THAT MATTERS: the House has 435 seats. Any other number means
    # a district is double-counted, missing, or is not a district.
    for y in [x for x in yrs if x >= 2016]:
        h = [r for r in rows if r["chamber"] == "house" and r["cycle"] == y
             and not r["special"]]
        if not h:
            continue
        rc = len({(r["state"], r["district"]) for r in h})
        if rc != 435:
            print(f"  !! {y}: {rc} House races, expected 435")
        # EVERY RACE HAS EXACTLY ONE WINNER. Not 'about one'.
        seats = {}
        for r in h:
            seats.setdefault((r["state"], r["district"]), []).append(r)
        odd = {k: sum(1 for x in v if x["won"]) for k, v in seats.items()}
        bad_w = {k: n for k, n in odd.items() if n != 1}
        if bad_w:
            print(f"  !! {y}: {len(bad_w)} race(s) without exactly one "
                  f"winner: " + ", ".join(f"{a}-{b} ({n})"
                                          for (a, b), n in list(bad_w.items())[:5]))
    bad = [r for r in rows if r["share"] is not None and r["share"] > 100.5]
    print(f"  {len(bad)} row(s) with share > 100% "
          f"(nonzero means the mode/fusion collapse double-counted)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--columns", action="store_true")
    ap.add_argument("--normalize", action="store_true",
                    help="turn stored returns into candidate-level rows")
    ap.add_argument("--out", metavar="PATH", help="write normalised CSV")
    ap.add_argument("--import", dest="import_path", metavar="FILE",
                    help="file a hand-downloaded copy into the archive")
    ap.add_argument("--as", dest="as_name", choices=sorted(DATASETS),
                    help="which dataset an --import belongs to")
    ap.add_argument("--only", action="append",
                    choices=sorted(DATASETS), help="restrict to one dataset")
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--snapshot", default=None)
    a = ap.parse_args(argv)

    if a.columns:
        return cmd_columns(a.cycle)
    if a.normalize:
        base = raw_dir(a.cycle)
        dates = sorted(x.name for x in base.iterdir() if x.is_dir())
        d = base / dates[-1]
        allrows = []
        for fp in sorted(d.iterdir()):
            if fp.name.endswith(".meta.json") or "sources-" in fp.name:
                continue
            try:
                rs, nt = normalise(fp)
            except SystemExit as e:
                print(f"  skip {fp.name}: {e}")
                continue
            normalise_report(rs, nt, fp.name)
            allrows += rs
        if a.out and allrows:
            import csv as _csv
            out = Path(a.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", newline="", encoding="utf-8") as fh:
                w = _csv.DictWriter(fh, fieldnames=list(allrows[0]))
                w.writeheader()
                w.writerows(allrows)
            print(f"\n  wrote {out}  ({len(allrows):,} rows)")
        return 0
    if a.import_path:
        if not a.as_name:
            ap.error("--import needs --as house|senate|president|governor")
        import datetime as dt
        return cmd_import(Path(a.import_path).expanduser(), a.as_name,
                          a.cycle, a.snapshot or dt.date.today().isoformat())
    if not (a.list or a.download):
        ap.error("give --list, --download or --columns")
    f = fetcher()
    if a.list:
        return cmd_list(f, a.only)
    import datetime as dt
    snap = a.snapshot or dt.date.today().isoformat()
    return cmd_download(f, a.only, a.cycle, snap)


if __name__ == "__main__":
    sys.exit(main())
