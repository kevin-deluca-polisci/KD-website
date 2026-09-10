#!/usr/bin/env python3
"""fill_endorser_party_wiki.py — party for the non-congressional endorsers.

    python3 forecast/endorse_quality/fill_endorser_party_wiki.py --plan
    python3 forecast/endorse_quality/fill_endorser_party_wiki.py --fetch
    python3 forecast/endorse_quality/fill_endorser_party_wiki.py --merge

MUST RUN WHERE WIKIPEDIA IS REACHABLE. Kevin's machine, or a CI step beside
capture.py. It is deliberately a separate script from the roster fill so that
the half needing no network stays runnable anywhere.

WHY THIS EXISTS, AND WHY IT IS NOT ABOUT FINDING MORE DEFECTIONS.

`endorser_party` in the source is an ANNOTATION, not a record. Measured on
2026-09-07, within congressional endorsers -- same people, same categories:

    party annotated by Wikipedia      29 cross of  29   = 100%
    party filled from the roster      25 cross of 495   =   5%

Editors write the party down when the endorsement is noteworthy, and what makes
it noteworthy is that it crosses party lines. So the pre-fill flag measured
"defection OR somebody flagged it", and a coefficient fit on it would estimate
annotation propensity.

This script exists to make the remaining population REPRESENTATIVE. It attempts
every party-bearing endorser that still lacks a party, never a selected subset,
and it records failures as failures rather than dropping them -- because a fill
that quietly skipped the hard cases would rebuild the same bias in a new shape.

WHY CATEGORIES AND NOT THE INFOBOX. A page's categories are structured, come
back 50 titles at a time, and say the party in a fixed form: "Democratic Party
members of the Arizona House of Representatives", "Republican Party state
senators". Infobox parsing means fetching and parsing wikitext per page, is
brittle across templates, and buys nothing here.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DERIVED = REPO_ROOT / "forecast" / "data" / "2026" / "derived"
SIDECAR = HERE / "endorser_party.csv"
CACHE = HERE / "_wiki_categories_cache.json"

API = "https://en.wikipedia.org/w/api.php"
UA = "PLSC2219-forecast/1.0 (https://kevinmdeluca.com/forecast/; academic research)"
BATCH = 50
SLEEP = 0.5

NOPARTY = {"Organizations", "Labor unions", "Newspapers"}
CYCLES = (2022, 2024, 2026)

# Category text -> party. Ordered: the first match wins, so the explicit
# party-membership forms are tested before the looser ones.
PATTERNS = [
    (re.compile(r"\bDemocratic Party\b", re.I), "D"),
    (re.compile(r"\bRepublican Party\b", re.I), "R"),
    (re.compile(r"\bDemocratic-Farmer-Labor\b", re.I), "D"),
    (re.compile(r"\bIndependent politicians\b", re.I), "I"),
    (re.compile(r"\bIndependent (?:members|senators|governors)\b", re.I), "I"),
    (re.compile(r"\bLibertarian Party\b", re.I), "L"),
    (re.compile(r"\bGreen Party\b", re.I), "G"),
    (re.compile(r"\bWorking Families Party\b", re.I), "WF"),
]

# Categories that name a party but are NOT a statement about this person's
# membership. "Candidates in the 2020 Democratic Party presidential primaries"
# is about an election; "Democratic Party (United States) presidential
# nominees" is about the person. Without this, anyone who ever ran against a
# party gets that party's letter.
EXCLUDE = re.compile(
    r"\b(primaries|primary elections?|presidential candidates|"
    r"candidates in the|opponents|critics|defectors from)\b", re.I)



def _ssl_context() -> ssl.SSLContext:
    """A context that actually has root certificates on macOS.

    Python installed from python.org ships its own OpenSSL and does NOT read
    the system keychain, so every HTTPS call fails with
    CERTIFICATE_VERIFY_FAILED until somebody runs the bundled
    "Install Certificates.command". certifi carries the same root bundle as a
    package, so preferring it here makes the script work on a fresh machine
    instead of failing 141 times in a row and looking like a network outage.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_CERT_HELP = """
CERTIFICATE VERIFICATION FAILED — this is a local Python setup problem, not
Wikipedia. Python from python.org does not use the macOS keychain. Either:

    /Applications/Python\ 3.12/Install\ Certificates.command

or, if that file is not there:

    python3 -m pip install --upgrade certifi

Then re-run --fetch. Nothing was written, and the cache is unchanged.
"""


def cat(r) -> str:
    return (r.get("category_label") or r.get("category") or "")


def load_sidecar() -> dict:
    out = {}
    if SIDECAR.exists():
        with SIDECAR.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out[r["endorser_key"]] = (r["party"], r["method"], r["endorser"])
    return out


def needed() -> dict:
    """endorser_key -> (display name, wiki title). Everyone still unfilled."""
    have = load_sidecar()
    want = {}
    for cy in CYCLES:
        f = DERIVED / f"endorsements_{cy}.json"
        if not f.exists():
            continue
        for r in json.loads(f.read_text()):
            if cat(r) in NOPARTY:
                continue
            if r.get("endorser_party"):
                continue
            k = r.get("endorser_key")
            if not k or k in have or k in want:
                continue
            title = r.get("endorser_link") or r.get("endorser")
            if not title:
                continue
            want[k] = (r.get("endorser") or k, title)
    return want


def classify(categories: list[str]) -> tuple[str | None, str]:
    hits = collections.Counter()
    for c in categories:
        if EXCLUDE.search(c):
            continue
        for pat, letter in PATTERNS:
            if pat.search(c):
                hits[letter] += 1
                break
    if not hits:
        return None, "no party category"
    if len(hits) == 1:
        return hits.most_common(1)[0][0], "wikipedia categories"
    # A switcher, or a page that mentions both. Majority only if it is decisive;
    # otherwise refuse, because a wrong fill is invisible afterwards and a gap
    # is not.
    top, second = hits.most_common(2)
    if top[1] >= 2 * second[1]:
        return top[0], f"wikipedia categories (majority {dict(hits)})"
    return None, f"ambiguous {dict(hits)}"


def fetch(titles: list[str], cache: dict) -> None:
    ctx = _ssl_context()
    todo = [t for t in titles if t not in cache]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        params = {
            "action": "query", "format": "json", "prop": "categories",
            "cllimit": "500", "redirects": "1",
            "titles": "|".join(chunk),
        }
        url = API + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=45, context=ctx) as fh:
                data = json.loads(fh.read().decode("utf-8"))
        except urllib.error.URLError as e:
            # A certificate failure will not fix itself on the next batch, so
            # stop on the first one rather than printing the same error 141
            # times and burying the cause.
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                print(_CERT_HELP, file=sys.stderr)
                raise SystemExit(2)
            print(f"    [batch {i//BATCH}: {type(e).__name__} {e}]", file=sys.stderr)
            time.sleep(3)
            continue
        except Exception as e:                      # noqa: BLE001
            print(f"    [batch {i//BATCH}: {type(e).__name__} {e}]", file=sys.stderr)
            time.sleep(3)
            continue

        # Redirects and normalisation mean the title that comes back is not
        # always the title asked for. Map both directions or half the answers
        # land under a key nothing looks up.
        alias = {}
        for r in data.get("query", {}).get("redirects", []) or []:
            alias[r["to"]] = r["from"]
        for r in data.get("query", {}).get("normalized", []) or []:
            alias[r["to"]] = r["from"]

        for pg in data.get("query", {}).get("pages", {}).values():
            title = pg.get("title")
            cats = [c["title"] for c in pg.get("categories", []) or []]
            for key in {title, alias.get(title)} - {None}:
                cache[key] = cats
        for t in chunk:
            cache.setdefault(t, cache.get(t, []))
        print(f"    fetched {min(i+BATCH, len(todo))}/{len(todo)}", flush=True)
        CACHE.write_text(json.dumps(cache))
        time.sleep(SLEEP)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true", help="how many, no network")
    ap.add_argument("--fetch", action="store_true", help="fetch categories into the cache")
    ap.add_argument("--merge", action="store_true", help="classify the cache into the sidecar")
    ap.add_argument("--limit", type=int, help="stop after N endorsers (a trial run)")
    a = ap.parse_args(argv)

    want = needed()
    if a.limit:
        want = dict(list(want.items())[:a.limit])
    print(f"endorsers still needing a party: {len(want):,}")

    if a.plan:
        by = collections.Counter()
        for cy in CYCLES:
            f = DERIVED / f"endorsements_{cy}.json"
            if not f.exists():
                continue
            for r in json.loads(f.read_text()):
                if cat(r) not in NOPARTY and r.get("endorser_key") in want:
                    by[cat(r)] += 1
        for k, v in by.most_common(12):
            print(f"  {k:<34} {v:>5} rows")
        print(f"\n  ~{(len(want)+BATCH-1)//BATCH} API calls at {BATCH} titles each,"
              f" about {(len(want)/BATCH)*SLEEP/60:.1f} min of polite fetching.")
        print("  Run --fetch where Wikipedia is reachable, then --merge.")
        return 0

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if a.fetch:
        fetch([t for _, t in want.values()], cache)
        print(f"  cache now holds {len(cache):,} pages")

    if a.merge:
        rows = load_sidecar()
        stats = collections.Counter()
        for k, (name, title) in want.items():
            cats = cache.get(title)
            if cats is None:
                stats["not fetched"] += 1
                continue
            party, how = classify(cats)
            if party:
                rows[k] = (party, how, name)
                stats[f"filled: {how.split('(')[0].strip()}"] += 1
            else:
                stats[how.split("{")[0].strip()] += 1
        with SIDECAR.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["endorser_key", "endorser", "party", "method"])
            for k, (party, how, nm) in sorted(rows.items()):
                w.writerow([k, nm, party, how])
        print(f"\n  sidecar now holds {len(rows):,} endorsers")
        for k, v in stats.most_common():
            print(f"    {k:<40} {v:>5}")
        print("\n  Re-run endorse_quality.py --describe to see the effect on the sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
