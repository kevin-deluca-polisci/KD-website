#!/usr/bin/env python3
"""endorse_quality.py — candidate quality from general-election endorsements.

Assembles the analysis dataset and, separately, fits it. The two are separate
subcommands on purpose: `--describe` reports COVERAGE and nothing about the
outcome, so the sample can be inspected and the pre-registration written
without anybody having seen a coefficient.

    python3 forecast/endorse_quality/endorse_quality.py --describe
    python3 forecast/endorse_quality/endorse_quality.py --fit

Read forecast/endorse_quality/PREREGISTRATION.md first. Every inclusion rule
below is fixed there, and this file is not the place to change one.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DERIVED = REPO_ROOT / "forecast" / "data" / "2026" / "derived"

# Settled cycles only. 2026 has no outcome, so it is scored, never fit.
FIT_CYCLES = (2018, 2020, 2022, 2024)

# Years between elections for the same seat.
SEAT_PERIOD = {"senate": 6, "house": 2, "governor": 4}


def _rd(p: Path) -> list[dict]:
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))



# Returns rows that are not people. They carry surname_keys ("votes o",
# "votes u") that would otherwise sit in a race's candidate set and, in
# principle, match somebody.
_PSEUDO = ("votes", "other", "writein", "write-in", "blank", "scattering")


def _is_pseudo(surname_key: str) -> bool:
    return surname_key.split()[0] in _PSEUDO if surname_key else True



_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def surnames_of(name: str) -> set[str]:
    """Every string that could be this person's surname.

    DERIVED FROM THE NAME, NOT READ FROM `surname_key`. That column cannot be
    trusted: MEDSL stores most rows "FIRST LAST" but a minority "LAST, FIRST",
    and the key is built as though every row were the former. So 2022 Hawaii
    carries `SCHATZ, BRIAN -> "brian s"` and 2022 Nevada
    `CORTEZ MASTO, CATHERINE -> "catherine c"`, both of which put the FIRST
    name where the surname belongs. It is a small minority -- 38 rows in 2022,
    20 in 2024, none in 2020 -- but it is silent, and a seat whose previous
    winner lands in it would be coded OPEN with a sitting senator on the
    ballot.

    Three shapes are handled, each because the 75-race sample contains one:

        "Bob Casey Jr."          suffix, so the last token is not the surname
        "Lori Chavez-DeRemer"    compound; MEDSL keeps "deremer" alone
        "SCHATZ, BRIAN"          comma form; the surname is before the comma
    """
    raw = (name or "").lower().replace(".", "").strip()
    if not raw:
        return set()
    if "," in raw:
        head = raw.split(",", 1)[0].strip()
        cands = {head, head.split()[-1] if head.split() else head}
    else:
        parts = [w for w in raw.split() if w]
        parts = [w for w in parts if w not in _SUFFIX] or parts
        cands = {parts[-1]} if parts else set()
    out = set()
    for c in cands:
        if not c:
            continue
        out.add(c)
        if "-" in c:
            out |= {seg for seg in c.split("-") if seg}
        if " " in c:                      # "cortez masto"
            out |= set(c.split())
    return out


def _norm_district(d) -> str | None:
    d = (d or "")
    d = str(d).strip().lstrip("0")
    return d or None


# ---------------------------------------------------------------------------
# Endorsements
# ---------------------------------------------------------------------------

def endorsement_rows(cycles=FIT_CYCLES, include_primary: bool = False) -> list[dict]:
    """General-phase endorsement rows, deduplicated.

    `include_primary` exists only to run the robustness check the
    pre-registration promises. It is never the default and never the reported
    specification.
    """
    keep = {"general"} if not include_primary else {"general", "primary", "runoff"}
    out = []
    for cy in cycles:
        f = DERIVED / f"endorsements_{cy}.json"
        if not f.exists():
            continue
        for r in json.loads(f.read_text()):
            if r.get("phase") not in keep:
                continue
            if r.get("duplicate_key"):
                continue
            r["_cycle"] = cy
            out.append(r)
    return out


def race_differentials(rows: list[dict]) -> dict[tuple, dict]:
    """Per race: the two nominees, their endorsement counts, and the two
    differentials the pre-registration fixes.

    CROSS-PARTY IS TAKEN FROM THE SOURCE, NOT RECOMPUTED. Wikipedia annotates
    these by hand -- an endorsement whose endorser's usual side is the other
    one. Deriving it instead from each endorser's modal endorsement elsewhere
    would need an endorser to appear in several races, and 78% appear once.
    """
    per = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"n": 0, "cross": 0}))
    for r in rows:
        name, party = r.get("candidate"), r.get("candidate_party")
        if not name or party not in ("D", "R"):
            continue
        key = (r["_cycle"], r.get("chamber"), r.get("state"),
               _norm_district(r.get("race_district")))
        c = per[key][(name, party)]
        c["n"] += 1
        if r.get("cross_party"):
            c["cross"] += 1

    races = {}
    for key, cands in per.items():
        D = sorted([c for c in cands if c[1] == "D"], key=lambda c: -cands[c]["n"])
        R = sorted([c for c in cands if c[1] == "R"], key=lambda c: -cands[c]["n"])
        if not D or not R:
            continue
        d, r_ = cands[D[0]], cands[R[0]]
        total = d["n"] + r_["n"]
        races[key] = {
            "cycle": key[0], "chamber": key[1], "state": key[2], "district": key[3],
            "cand_D": D[0][0], "cand_R": R[0][0],
            "n_D": d["n"], "n_R": r_["n"], "n_total": total,
            # (a) share, centred at zero
            "share_D": (d["n"] / total) - 0.5 if total else None,
            # (b) cross-party differential
            "cross_D": d["cross"], "cross_R": r_["cross"],
            "cross_diff": d["cross"] - r_["cross"],
            # a contested primary showing through into the general phase
            "multi_D": len(D) > 1, "multi_R": len(R) > 1,
        }
    return races


# ---------------------------------------------------------------------------
# Outcomes and controls
# ---------------------------------------------------------------------------

def outcomes() -> tuple[dict, dict]:
    """Race-level two-party margins, and each race's winner key (for incumbency)."""
    margins, winners = {}, {}
    for r in _rd(DERIVED / "returns.csv"):
        try:
            y = int(r["year"])
        except (TypeError, ValueError, KeyError):
            continue
        if str(r.get("special")).lower() == "true":
            continue
        key = (y, r.get("chamber"), r.get("state"), _norm_district(r.get("district")))
        try:
            margins[key] = float(r["margin_D"])
        except (TypeError, ValueError, KeyError):
            pass
        if str(r.get("won")).lower() == "true":
            # surname_key, NOT candidate_key. candidate_key is not stable
            # across cycles: Susan Collins is "susan m collins" in 1996-2014
            # and "susan margaret collins" in 2020, so keying on it loses a
            # five-term incumbent. surname_key is "collins s" in all five --
            # surname plus first initial, which also discriminates two
            # same-surname candidates in one race where a bare surname could
            # not.
            nm = (r.get("candidate") or "").strip()
            sn = surnames_of(nm)
            if sn and not _is_pseudo(nm.lower()):
                winners[key] = sn
    return margins, winners


def incumbency(race_key: tuple, cand_D: str, cand_R: str, winners: dict) -> int:
    """+1 if the Democrat holds the seat, -1 if the Republican does, 0 if open.

    Derived, per the pre-registration: no source here carries incumbency, so it
    is the previous same-seat winner matched by name.
    """
    cy, chamber, state, district = race_key
    back = SEAT_PERIOD.get(chamber)
    if not back:
        return 0
    prev = winners.get((cy - back, chamber, state, district))
    if not prev:
        return 0
    # SURNAME MATCHES; THE INITIAL IS ONLY A TIEBREAK. Measured, not assumed.
    #
    # Requiring the first initial to agree as well looks stricter and is simply
    # wrong for anyone who goes by a middle name or a nickname, which bit twice
    # in a 75-race sample: MD-2 "Dutch Ruppersberger" against C.A.
    # Ruppersberger, and VA-5 "Bob Good" against Robert Good. Both are
    # long-sitting incumbents and both were coded OPEN by an initial-sensitive
    # match. Surname alone gets both right.
    #
    # The risk surname-alone carries is two candidates in one race sharing one.
    # Across all 936 House and Senate races in 2022 and 2024 that never happens
    # once the returns' pseudo-rows (OVER VOTES, UNDER VOTES) are excluded, so
    # the initial breaks a tie and never rejects an otherwise unique match.
    hits = [w for w, nm in (("D", cand_D), ("R", cand_R))
            if prev & surnames_of(nm)]
    if len(hits) == 1:
        return 1 if hits[0] == "D" else -1
    if len(hits) == 2:
        def initial(n):
            ps = [x for x in (n or "").lower().replace(".", "").split() if x]
            return ps[0][0] if ps else ""
        for w, nm in (("D", cand_D), ("R", cand_R)):
            if w in hits and initial(nm) in {s[0] for s in prev if s}:
                return 1 if w == "D" else -1
    return 0


def build(include_primary: bool = False) -> list[dict]:
    races = race_differentials(endorsement_rows(include_primary=include_primary))
    margins, winners = outcomes()
    rows = []
    for key, rec in races.items():
        y = margins.get(key)
        rec = dict(rec)
        rec["margin_D"] = y
        rec["joined"] = y is not None
        rec["incumbent"] = incumbency(key, rec["cand_D"], rec["cand_R"], winners)
        rows.append(rec)
    return rows


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def describe(rows: list[dict]) -> int:
    """Coverage only. Deliberately says NOTHING about the outcome's relationship
    to anything, so it can be run before the pre-registration is finished."""
    joined = [r for r in rows if r["joined"]]
    print("=" * 66)
    print("ENDORSEMENT QUALITY — sample coverage")
    print("=" * 66)
    print(f"  races with >=1 endorsed D and >=1 endorsed R : {len(rows):>4}")
    for ch, n in collections.Counter(r["chamber"] for r in rows).most_common():
        print(f"      {ch:<10} {n:>4}")
    print(f"  joined to a two-party margin                 : {len(joined):>4}")
    lost = collections.Counter(r["chamber"] for r in rows if not r["joined"])
    for ch, n in lost.most_common():
        print(f"      lost: {ch:<10} {n:>4}")
    print()
    print(f"  by cycle : {dict(collections.Counter(r['cycle'] for r in joined))}")
    print(f"  incumbency: {dict(collections.Counter(r['incumbent'] for r in joined))}"
          "   (1 = D holds, -1 = R holds, 0 = open)")
    multi = sum(1 for r in joined if r["multi_D"] or r["multi_R"])
    print(f"  races where a party had >1 endorsed candidate : {multi}")
    tot = [r["n_total"] for r in joined]
    if tot:
        tot.sort()
        print(f"  endorsements per race: min {tot[0]}, median {tot[len(tot)//2]}, max {tot[-1]}")
    cross = sum(abs(r["cross_diff"]) for r in joined)
    nz = sum(1 for r in joined if r["cross_diff"] != 0)
    print(f"  cross-party endorsements, total |diff|        : {cross}")
    print(f"  races with a NON-ZERO cross-party differential: {nz} of {len(joined)}")
    print()
    print("  No outcome relationship is reported here by design. See")
    print("  PREREGISTRATION.md; run --fit once that file is committed.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--describe", action="store_true",
                    help="report sample coverage and exit (no outcome model)")
    ap.add_argument("--fit", action="store_true", help="fit the pre-registered model")
    ap.add_argument("--include-primary", action="store_true",
                    help="robustness check only: fold primary-phase endorsements in")
    ap.add_argument("--out", help="write the assembled dataset to this CSV")
    a = ap.parse_args(argv)

    rows = build(include_primary=a.include_primary)
    if a.out:
        joined = [r for r in rows if r["joined"]]
        if joined:
            with open(a.out, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(joined[0].keys()))
                w.writeheader()
                w.writerows(joined)
            print(f"wrote {len(joined)} rows to {a.out}")
    if a.fit:
        print("--fit is not implemented yet. The pre-registration is committed;\n"
              "the estimator lands in the next change, deliberately separately,\n"
              "so the sample and the specification are reviewable on their own.")
        return 0
    return describe(rows)


if __name__ == "__main__":
    sys.exit(main())
