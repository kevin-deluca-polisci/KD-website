#!/usr/bin/env python3
"""
Join endorsements to election returns. One row per general-election candidate.

    python3 forecast/model/endorsement_join.py --audit \
        --endorsements /tmp/end2022.json /tmp/end2024.json \
        --returns forecast/data/2026/derived/returns.csv

    python3 forecast/model/endorsement_join.py \
        --endorsements ... --returns ... \
        --out forecast/data/2026/derived/endorsement_panel.csv

-----------------------------------------------------------------------------
WHY THE MATCH RATE IS THE RESULT, NOT A DIAGNOSTIC

An unmatched race is not a missing row. It is a race silently dropped from
training, and the drops are not random: names fail to match where they are
unusual, hyphenated, accented, recently changed, or where several people share
a surname. Those cluster in crowded, high-turnover, majority-minority districts
-- which is to say, in exactly the races where candidate quality is most worth
measuring. A model fit on what happened to match would be fit on the tidy half
of American politics.

So every tier of matching is counted separately, every failure is printed with
both sides of the near-miss, and the summary refuses to report a single
headline number without the denominators underneath it.

WHAT MAKES THIS TRACTABLE: THE RACE, NOT THE NAME

Nothing here matches a name against thirty thousand names. Everything matches
WITHIN one race -- one district, one year -- where the candidate pool is two to
ten people. "Garbarino" is hopeless as a global key and unambiguous inside
NY-02. That single constraint is what lets crude tiers work, and it is also
what makes an ambiguous match a real signal rather than noise: two people in
ONE race sharing a surname is worth a human look.

THE ASYMMETRY, WHICH IS DELIBERATE

Two match rates get reported and they answer different questions.

  COVERAGE   of general-election nominees, how many have endorsement data?
             This is the modelling denominator. A nominee with none is a row
             the model cannot use.
  WASTE      of endorsement candidates, how many matched a nominee?
             Low is EXPECTED and fine -- Wikipedia lists endorsements for
             people who lost primaries and never reached the general ballot.
             It is only alarming if it falls where coverage also falls.

Confusing the two would make a healthy join look broken or a broken one look
healthy.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Nicknames common enough in American politics to be worth a table. This is
# deliberately short: every entry is a judgement that two strings are one
# person, and a long table is a long list of chances to be wrong. The surname
# tiers below catch most of what this misses, without asserting anything.
NICK = {
    "bob": "robert", "bobby": "robert", "rob": "robert", "robbie": "robert",
    "bill": "william", "billy": "william", "will": "william", "willie": "william",
    "dick": "richard", "rick": "richard", "ricky": "richard", "rich": "richard",
    "jim": "james", "jimmy": "james", "jamie": "james",
    "joe": "joseph", "joey": "joseph",
    "mike": "michael", "mikey": "michael",
    "tom": "thomas", "tommy": "thomas",
    "dan": "daniel", "danny": "daniel",
    "dave": "david", "davey": "david",
    "steve": "stephen", "steven": "stephen",
    "chris": "christopher", "chuck": "charles", "charlie": "charles",
    "ted": "edward", "ed": "edward", "eddie": "edward", "ned": "edward",
    "tony": "anthony", "nick": "nicholas", "greg": "gregory",
    "jeff": "jeffrey", "ken": "kenneth", "larry": "lawrence",
    "matt": "matthew", "pat": "patrick", "pete": "peter", "phil": "philip",
    "ron": "ronald", "sam": "samuel", "tim": "timothy", "vince": "vincent",
    "andy": "andrew", "ben": "benjamin", "frank": "francis", "hank": "henry",
    "jack": "john", "johnny": "john", "kate": "katherine",
    "kathy": "katherine", "cathy": "catherine", "liz": "elizabeth",
    "beth": "elizabeth", "betty": "elizabeth", "sue": "susan",
    "susie": "susan", "peggy": "margaret", "maggie": "margaret",
    "meg": "margaret", "debbie": "deborah", "cindy": "cynthia",
    "sandy": "sandra", "barb": "barbara", "jen": "jennifer",
    "jenny": "jennifer", "becky": "rebecca", "abby": "abigail",
}
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v|md|phd|esq|dds|dr)\b")


def fold(name: str) -> str:
    """Accent- and punctuation-insensitive lowercase form.

    Diaz-Balart is written "Díaz-Balart" on Wikipedia and "DIAZ-BALART" by the
    Clerk of the House. Without folding the accent those are two people, and
    the district they are in is exactly the sort this join must not lose.
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r'"[^"]*"', " ", n)          # "Chuy", "Buddy"
    n = re.sub(r"\([^)]*\)", " ", n)        # leftover parentheticals
    n = _SUFFIX.sub(" ", n.lower())
    n = re.sub(r"[^a-z ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def parts(name: str) -> list[str]:
    return [p for p in fold(name).split() if len(p) > 1 or True]


def canon_first(tok: str) -> str:
    return NICK.get(tok, tok)


def full_key(name: str) -> str:
    p = parts(name)
    if not p:
        return ""
    return " ".join([canon_first(p[0])] + p[1:])


def core_key(name: str) -> str:
    """First and last only, nickname-canonicalised. Drops middle names."""
    p = parts(name)
    if not p:
        return ""
    if len(p) == 1:
        return p[0]
    return f"{canon_first(p[0])} {p[-1]}"


def surname(name: str) -> str:
    p = parts(name)
    return p[-1] if p else ""


def sur_initial(name: str) -> str:
    p = parts(name)
    if not p:
        return ""
    return f"{p[-1]} {p[0][:1]}" if len(p) > 1 else p[0]


_QUOTED = re.compile("[\"\u201c\u2018']([A-Za-z][A-Za-z.\\- ]{1,18})[\"\u201d\u2019']")


def nick_key(name: str) -> str:
    """The name in quotes, plus the surname: 'buddy carter'.

    The Clerk writes the legal name and parks the name everyone uses in
    quotes -- EARL L "BUDDY" CARTER, ERIC A "RICK" CRAWFORD, HENRY C "HANK"
    JOHNSON. Wikipedia writes "Buddy Carter". Without reading the quotes those
    only meet at the surname tier, which is the loosest one and the one most
    likely to be wrong in a crowded race. With it they meet on two tokens.

    This only became possible after the mojibake repair: the quotes arrived as
    mangled bytes and there was nothing to match on.
    """
    m = _QUOTED.search(name or "")
    if not m:
        return ""
    p = parts(name)
    if not p:
        return ""
    # CANONICALISE THE NICKNAME TOO. The other side of this tier is core_key,
    # which already turns "Rick" into "richard"; leaving the quoted form raw
    # would mean the two halves of the same tier disagree with each other and
    # the tier would only ever fire for nicknames absent from the table.
    return f"{canon_first(fold(m.group(1)))} {p[-1]}"


def any_token_keys(name: str) -> list[str]:
    """Every '<given token> <surname>' this name could be known by.

    A surprising number of members of Congress go by a MIDDLE name and file
    under their legal first: J. French Hill, H. Morgan Griffith, W. Gregory
    Steube, A. Donald McEachin, C.A. Dutch Ruppersberger. Wikipedia uses the
    name people use; the Clerk uses the one on the form.

    Without this they meet only at the bare surname, which is the loosest tier
    and the one that goes wrong in a crowded district. With it they meet on two
    tokens, and a two-token agreement inside a single race is about as safe as
    matching gets. Initials are excluded -- 'j hill' would match half a state.
    """
    p = parts(name)
    if len(p) < 2:
        return []
    sur = p[-1]
    return [f"{canon_first(tok)} {sur}" for tok in p[:-1] if len(tok) > 1]


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# THE MATCH CASCADE
#
# Tiers run in order and STOP at the first that produces exactly one candidate
# on each side. Ambiguity never resolves itself by falling through to a looser
# tier -- a looser tier is more likely to be ambiguous, not less -- so an
# ambiguous tier ends the attempt and the pair is reported for a human.
#
# Every tier records which one fired. A join whose matches are 90% tier 1 is a
# different object from one that leans on tier 4, and the audit says which.

TIERS = ("exact", "core", "nickname", "middle_name",
         "surname_initial", "surname", "fuzzy")


def _unique(d: dict, k):
    v = d.get(k) or []
    return v[0] if len(v) == 1 else None


def match_race(end_cands: list[dict], ret_cands: list[dict],
               fuzzy_floor: float = 0.86) -> tuple[list[tuple], list[dict],
                                                   list[dict], list[dict]]:
    """Pair endorsement candidates with returns candidates inside one race.

    Returns (pairs, unmatched_endorsement, unmatched_returns, ambiguous).
    """
    def index(rows, keyfn):
        d = defaultdict(list)
        for r in rows:
            ks = keyfn(r["candidate"])
            for k in ([ks] if isinstance(ks, str) else ks):
                if k:
                    d[k].append(r)
        return d

    pairs, ambiguous = [], []
    e_left = list(end_cands)
    r_left = list(ret_cands)

    # (tier, key for the endorsement side, key for the returns side). They
    # differ for the nickname tier, where only one side carries the quotes.
    for tier, efn, rfn in (("exact", full_key, full_key),
                           ("core", core_key, core_key),
                           ("nickname", core_key, nick_key),
                           ("middle_name", core_key, any_token_keys),
                           ("surname_initial", sur_initial, sur_initial),
                           ("surname", surname, surname)):
        if not e_left or not r_left:
            break
        ei, ri = index(e_left, efn), index(r_left, rfn)
        took_e, took_r = set(), set()
        for k, es in ei.items():
            rs = ri.get(k) or []
            if not rs:
                continue
            if len(es) == 1 and len(rs) == 1:
                if id(rs[0]) in took_r or id(es[0]) in took_e:
                    continue          # a multi-key tier can offer one row twice
                pairs.append((es[0], rs[0], tier))
                took_e.add(id(es[0]))
                took_r.add(id(rs[0]))
            else:
                # TWO PEOPLE, ONE KEY, ONE RACE. Real and rare: brothers,
                # juniors, or a surname shared by two candidates in a crowded
                # district. Guessing here is how a model ends up attributing
                # one candidate's endorsements to another.
                ambiguous.append({"tier": tier, "key": k,
                                  "endorsement": [x["candidate"] for x in es],
                                  "returns": [x["candidate"] for x in rs]})
        e_left = [x for x in e_left if id(x) not in took_e]
        r_left = [x for x in r_left if id(x) not in took_r]

    # LAST TIER: character similarity, and only when it is decisive. A best
    # score that is not clearly better than the runner-up is not a match; it is
    # a coin flip with a decimal point on it.
    if e_left and r_left:
        took_e, took_r = set(), set()
        for e in list(e_left):
            scored = sorted(((ratio(full_key(e["candidate"]),
                                    full_key(r["candidate"])), r)
                             for r in r_left if id(r) not in took_r),
                            key=lambda t: -t[0])
            if not scored:
                break
            best, r = scored[0]
            runner = scored[1][0] if len(scored) > 1 else 0.0
            if best >= fuzzy_floor and best - runner >= 0.06:
                pairs.append((e, r, "fuzzy"))
                took_e.add(id(e))
                took_r.add(id(r))
        e_left = [x for x in e_left if id(x) not in took_e]
        r_left = [x for x in r_left if id(x) not in took_r]

    return pairs, e_left, r_left, ambiguous


def race_key(chamber, state, district):
    return (chamber, state, district if chamber == "house" else None)


def load_endorsements(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        d = json.loads(p.read_text(encoding="utf-8"))
        rows.extend(d)
    return rows


def load_returns(path: Path) -> list[dict]:
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8")):
        for b in ("special", "runoff", "won", "uncontested", "writein",
                  "fusion", "votes_unreliable"):
            if b in r:
                r[b] = str(r[b]).strip().lower() == "true"
        for f in ("votes", "totalvotes", "share", "margin_D", "two_party_D"):
            if f in r:
                try:
                    r[f] = float(r[f]) if r[f] not in ("", None) else None
                except ValueError:
                    r[f] = None
        r["cycle"] = int(r["cycle"])
        out.append(r)
    return out


def build(end_rows: list[dict], ret_rows: list[dict],
          phase_filter: str = "all") -> dict:
    """Everything the audit and the panel both need, computed once."""
    # --- endorsements, grouped to one record per candidate per race ---------
    ecand: dict[tuple, dict] = {}
    for r in end_rows:
        if r.get("stance") != "endorsed":
            continue
        if r.get("duplicate_key"):
            continue
        if phase_filter == "general" and r.get("phase") != "general":
            continue
        cyc, ch = r.get("cycle"), r.get("chamber")
        if not cyc or ch not in ("house", "senate", "governor"):
            continue
        rk = (cyc,) + race_key(ch, r.get("state"), r.get("race_district"))
        k = rk + (r.get("candidate"),)
        e = ecand.setdefault(k, {
            "cycle": cyc, "chamber": ch, "state": r.get("state"),
            "district": r.get("race_district"),
            "candidate": r.get("candidate"),
            "party_wiki": r.get("candidate_party"),
            "n": 0, "cross": 0, "cats": Counter(), "dates": [], "phases": Counter(),
        })
        e["n"] += 1
        e["cross"] += 1 if r.get("cross_party") else 0
        e["cats"][r.get("category") or "unspecified"] += 1
        e["phases"][r.get("phase") or "unknown"] += 1
        if r.get("ref_date") and not r.get("date_flag"):
            e["dates"].append(r["ref_date"])

    # --- returns, general-election candidates only -------------------------
    rcand: dict[tuple, list[dict]] = defaultdict(list)
    for r in ret_rows:
        if r.get("special") or r.get("runoff"):
            continue
        rk = (r["cycle"],) + race_key(r["chamber"], r["state"],
                                      r.get("district") or None)
        rcand[rk].append(r)

    # WHO IS THE NOMINEE, which is not "everyone carrying a party label".
    #
    # Two things put more than one Democrat or Republican on one general
    # ballot, and they need opposite treatment.
    #
    #   WRITE-INS coded with a major party. Debra Jo Borden took 32 votes in
    #   AZ-05 in 2022 beside Javier Ramos's 120,243. Counting her as a nominee
    #   adds a row nobody could ever have endorsement data for, and quietly
    #   drags the coverage rate down for a reason that has nothing to do with
    #   coverage.
    #
    #   TOP-TWO PRIMARIES. California and Washington advance the two leading
    #   candidates regardless of party, so CA-15 in 2022 is Mullin (108,077)
    #   against Canepa (86,797) and both are Democrats. Both ARE real
    #   nominees. But the race has no D-versus-R difference, so it cannot
    #   enter a model whose regressor is the gap between the two parties'
    #   endorsement shares. Marked and excluded with the count shown, rather
    #   than silently dropped or silently averaged in.
    for _rk, _rs in rcand.items():
        for _party in ("DEMOCRAT", "REPUBLICAN"):
            _same = [x for x in _rs if x["party"] == _party]
            if not _same:
                continue
            _top = max(_same, key=lambda x: x["votes"] or 0)
            for x in _same:
                x["nominee"] = (x is _top)
        _t2 = sorted(_rs, key=lambda x: -(x["votes"] or 0))[:2]
        _sp = (len(_t2) == 2 and _t2[0]["party"] == _t2[1]["party"]
               and _t2[0]["party"] in ("DEMOCRAT", "REPUBLICAN"))
        for x in _rs:
            x["same_party_general"] = _sp

    ebyrace: dict[tuple, list[dict]] = defaultdict(list)
    for k, e in ecand.items():
        ebyrace[k[:4]].append(e)

    pairs, un_e, un_r, ambig = [], [], [], []
    for rk in set(ebyrace) | set(rcand):
        es, rs = ebyrace.get(rk, []), rcand.get(rk, [])
        if not es:
            un_r.extend({"race": rk, **r} for r in rs)
            continue
        if not rs:
            un_e.extend({"race": rk, **e} for e in es)
            continue
        p, le, lr, am = match_race(es, rs)
        pairs.extend((rk, e, r, t) for e, r, t in p)
        un_e.extend({"race": rk, **e} for e in le)
        un_r.extend({"race": rk, **r} for r in lr)
        ambig.extend({"race": rk, **a} for a in am)
    return {"pairs": pairs, "unmatched_endorsement": un_e,
            "unmatched_returns": un_r, "ambiguous": ambig,
            "ecand": ecand, "rcand": rcand}


def panel(res: dict) -> list[dict]:
    """One row per matched general-election candidate, plus race shares."""
    rows = []
    for rk, e, r, tier in res["pairs"]:
        rows.append({
            "cycle": rk[0], "chamber": rk[1], "state": rk[2],
            "district": rk[3] or "",
            "candidate": r["candidate"], "candidate_wiki": e["candidate"],
            "party": r["party"], "match_tier": tier,
            "n_endorsements": e["n"], "n_cross_party": e["cross"],
            "n_orgs": e["cats"].get("organization", 0),
            "n_labor": e["cats"].get("labor", 0),
            "n_newspaper": e["cats"].get("newspaper", 0),
            "first_endorsement": min(e["dates"]) if e["dates"] else "",
            "last_endorsement": max(e["dates"]) if e["dates"] else "",
            "votes": r["votes"], "share": r["share"], "won": r["won"],
            "margin_D": r["margin_D"], "two_party_D": r["two_party_D"],
            "uncontested": r["uncontested"],
            "votes_unreliable": r["votes_unreliable"],
            "nominee": bool(r.get("nominee")),
            "same_party_general": bool(r.get("same_party_general")),
        })
    # ENDORSEMENT SHARE IS WITHIN-RACE, which is the whole point of section 1
    # of the roadmap: a raw count measures how interesting the race is.
    byrace = defaultdict(list)
    for row in rows:
        byrace[(row["cycle"], row["chamber"], row["state"],
                row["district"])].append(row)
    for rs in byrace.values():
        tot = sum(x["n_endorsements"] for x in rs)
        for x in rs:
            x["race_endorsements"] = tot
            x["endorsement_share"] = (100 * x["n_endorsements"] / tot
                                      if tot else None)
            x["n_matched_in_race"] = len(rs)
        d = next((x for x in rs if x["party"] == "DEMOCRAT"), None)
        rp = next((x for x in rs if x["party"] == "REPUBLICAN"), None)
        # THE MODELLING UNIT: a differential needs both sides present.
        both = (d is not None and rp is not None
                and d["nominee"] and rp["nominee"]
                and not rs[0]["same_party_general"])
        diff = (d["endorsement_share"] - rp["endorsement_share"]
                if both and d["endorsement_share"] is not None else None)
        for x in rs:
            x["two_sided"] = both
            x["endorsement_share_diff_D"] = diff
    return rows


def audit(res: dict, rows: list[dict], show: int = 12) -> None:
    pairs = res["pairs"]
    print("=" * 74)
    print(f"  {len(pairs):,} candidate match(es)")
    print("=" * 74)
    print("  by tier: " + "  ".join(
        f"{t} {n:,}" for t, n in Counter(p[3] for p in pairs).most_common()))
    print("    tier 1 'exact' is a folded full-name hit. Anything leaning on")
    print("    'surname' or 'fuzzy' deserves a read of the examples below.")

    # COVERAGE, the denominator that matters.
    print("\n  COVERAGE -- of general-election nominees, who has endorsements?")
    matched_r = {id(p[2]) for p in pairs}
    # Only cycles we HOLD endorsements for. Printing fifty years of 0.0%
    # rows for cycles nobody scraped buries the four lines that matter.
    have = {e["cycle"] for e in res["ecand"].values()}
    for cyc in sorted({int(k[0]) for k in res["rcand"]} & have):
        for ch in ("house", "senate"):
            rs = [r for k, v in res["rcand"].items() if k[0] == cyc
                  and k[1] == ch for r in v]
            if not rs:
                continue
            major = [r for r in rs if r["party"] in ("DEMOCRAT", "REPUBLICAN")
                     and r.get("nominee")]
            got = [r for r in major if id(r) in matched_r]
            sp = {(r["state"], r["district"]) for r in rs
                  if r.get("same_party_general")}
            races = {(r["state"], r["district"]) for r in major} - sp
            twosided = {(x["state"], x["district"]) for x in rows
                        if x["cycle"] == cyc and x["chamber"] == ch
                        and x["two_sided"]}
            print(f"    {cyc} {ch:<8}{len(got):>5}/{len(major):<6} major-party "
                  f"nominees ({100*len(got)/max(len(major),1):>5.1f}%)   "
                  f"{len(twosided):>3}/{len(races):<3} races with BOTH sides "
                  f"({100*len(twosided)/max(len(races),1):>5.1f}%)"
                  + (f"   [{len(sp)} same-party general(s) set aside]"
                     if sp else ""))
    print("    'BOTH sides' is the modelling denominator: a differential needs")
    print("    two nominees, so a race with one is not half usable, it is")
    print("    unusable.")

    print("\n  WASTE -- of endorsement candidates, how many reached a ballot?")
    for cyc in sorted({e["cycle"] for e in res["ecand"].values()}):
        tot = sum(1 for e in res["ecand"].values() if e["cycle"] == cyc)
        mt = sum(1 for p in pairs if p[0][0] == cyc)
        print(f"    {cyc}: {mt:,}/{tot:,} ({100*mt/max(tot,1):.1f}%)")
    print("    Low is EXPECTED -- Wikipedia lists endorsements for people who")
    print("    lost primaries and never reached the general ballot. It only")
    print("    matters where COVERAGE is also low.")

    un_r = [r for r in res["unmatched_returns"]
            if r.get("party") in ("DEMOCRAT", "REPUBLICAN")
            and r.get("nominee") and int(r["race"][0]) >= 2022]
    print(f"\n  NOMINEES WITH NO ENDORSEMENT RECORD: {len(un_r):,}")
    has_e = {k[:4] for k in res["ecand"]}
    lonely = [r for r in un_r if r["race"] in has_e]
    print(f"    of which {len(lonely):,} are in races where the OTHER side has")
    print(f"    endorsements -- those are the real misses, the rest are races")
    print(f"    Wikipedia simply does not cover.")
    for r in lonely[:show]:
        rk = r["race"]
        others = [e["candidate"] for e in res["ecand"].values()
                  if (e["cycle"], e["chamber"], e["state"],
                      e["district"] if e["chamber"] == "house" else None) == rk]
        print(f"      {rk[0]} {rk[2]}-{rk[3] or ''} {r['candidate'][:26]:<28}"
              f"({r['party'][:3]})  page has: {', '.join(others[:3])[:44]}")

    if res["ambiguous"]:
        print(f"\n  AMBIGUOUS -- {len(res['ambiguous'])} case(s) refused rather "
              f"than guessed:")
        for a in res["ambiguous"][:show]:
            print(f"      {a['race'][0]} {a['race'][2]}-{a['race'][3] or ''} "
                  f"key={a['key']!r}")
            print(f"        wiki:    {a['endorsement']}")
            print(f"        returns: {a['returns']}")

    loose = [p for p in pairs if p[3] in ("surname", "fuzzy")]
    if loose:
        print(f"\n  LOOSEST MATCHES -- {len(loose)} made on surname or "
              f"similarity. Read these:")
        for rk, e, r, t in loose[:show]:
            print(f"      [{t:<7}] {rk[0]} {rk[2]}-{rk[3] or ''}  "
                  f"{e['candidate'][:28]:<30}-> {r['candidate'][:28]}")

    print("\n  PANEL")
    usable = [x for x in rows if x["two_sided"] and not x["uncontested"]
              and not x["votes_unreliable"] and x["nominee"]]
    print(f"    {len(rows):,} matched candidate row(s); {len(usable):,} sit in "
          f"two-sided contested races with reliable votes")
    byc = Counter((x["cycle"], x["chamber"]) for x in usable)
    for k, n in sorted(byc.items()):
        print(f"      {k[0]} {k[1]:<9}{n:>6,} usable candidate-rows")
    ds = [x["endorsement_share_diff_D"] for x in usable
          if x["endorsement_share_diff_D"] is not None]
    if ds:
        ds.sort()
        print(f"    endorsement share differential (D-R), the regressor:")
        print(f"      min {ds[0]:+.1f}   median {ds[len(ds)//2]:+.1f}   "
              f"max {ds[-1]:+.1f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--endorsements", nargs="+", required=True)
    ap.add_argument("--returns", required=True)
    ap.add_argument("--phase", choices=["all", "general"], default="all",
                    help="which endorsement phases count. See ROADMAP 3a -- "
                         "this is an open pre-registration question, so both "
                         "are runnable and neither is the default by accident")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--out", help="write the panel as CSV")
    ap.add_argument("--show", type=int, default=12)
    a = ap.parse_args(argv)

    end = load_endorsements([Path(p).expanduser() for p in a.endorsements])
    ret = load_returns(Path(a.returns).expanduser())
    print(f"  {len(end):,} endorsement row(s), {len(ret):,} returns row(s), "
          f"phase={a.phase}")
    res = build(end, ret, a.phase)
    rows = panel(res)
    if a.audit or not a.out:
        audit(res, rows, a.show)
    if a.out and rows:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        cols = list(rows[0])
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(f"\n  wrote {out}  ({len(rows):,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
