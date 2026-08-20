#!/usr/bin/env python3
"""
PLSC 2219 forecast archive — phase 1: capture.

This script FETCHES AND WRITES DOWN BYTES. It does not parse, aggregate, or
render. That separation is the single most important property of the design:
when a source changes its layout in October and a parser silently starts
returning garbage, the raw captures let you fix the parser and reprocess every
historical snapshot. If only parsed output were kept, that history is gone
permanently, and for an archive that is the failure that matters most.

Corollary: DO NOT ADD PARSING TO THIS FILE. Parsers live in collect/parsers/
and read from data/<cycle>/raw/.

Usage
-----
    python3 capture.py                          # capture everything enabled
    python3 capture.py --dry-run                # show what would happen
    python3 capture.py --only kalshi            # one source
    python3 capture.py --only kalshi,polymarket # several
    python3 capture.py --backfill               # include historical backfill
    python3 capture.py --self-test              # run offline logic checks
    python3 capture.py --list                   # show registry + licence gate

Exit codes
----------
    0  every attempted source succeeded
    1  at least one source failed (the others still ran and were written)
    2  fatal: could not start at all (bad registry, missing deps)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except ImportError:  # pragma: no cover
    print("ERROR: missing PyYAML.  pip install -r collect/requirements.txt", file=sys.stderr)
    raise SystemExit(2)


def _ssl_context() -> ssl.SSLContext:
    """
    Build a TLS context with a CA bundle that actually exists.

    Python builds from python.org do NOT use the macOS system trust store. They
    ship their own bundle and expect you to have run "Install Certificates.command"
    once, which many people never do — the symptom is CERTIFICATE_VERIFY_FAILED on
    every https call, including github.com. Preferring certifi's bundle when it is
    installed makes the collector work on a fresh machine without that ritual, and
    keeps it working identically in CI and on a cluster.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()


# --------------------------------------------------------------------------
# Paths. Everything is resolved relative to the repo root so the script runs
# identically from a laptop and from CI. Debugging inside a GitHub Action is
# miserable; keeping one entry point that works locally is worth the fuss.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FORECAST_DIR = REPO_ROOT / "forecast"

# NOTE: the archive lives at forecast/data/, NOT at the repo-root data/.
# That is deliberate and load-bearing. Hugo treats a root-level data/ directory
# as site data and parses every file in it into .Site.Data on every build. The
# root data/ already holds publications.csv and friends for exactly that reason.
# Dropping tens of thousands of raw snapshot files there would make each build
# read the entire archive. Hugo ignores unknown root directories, so anything
# under forecast/ is invisible to it.
DATA_DIR = FORECAST_DIR / "data"


# --------------------------------------------------------------------------
# Result accounting
# --------------------------------------------------------------------------

# A source that returns byte-identical content for this many days is probably
# not being maintained. This is the answer to "what if a forecaster quietly
# stops updating?" — you find out because the archive tells you, rather than
# by noticing months later that a line went flat.
STALE_AFTER_DAYS = 10


def _days_between(a: str | None, b: str | None) -> int | None:
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except Exception:
        return None


@dataclass
class SourceResult:
    source_id: str
    ok: bool = False
    artifacts: int = 0
    bytes_written: int = 0
    requests_made: int = 0
    skipped_reason: str | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# HTTP with manners
# --------------------------------------------------------------------------

class Fetcher:
    """
    Minimal HTTP client with a descriptive user agent, a polite per-host
    floor, and self-timed exponential backoff with jitter.

    Backoff is self-timed rather than Retry-After driven on purpose: Kalshi
    returns no Retry-After header on 429, so anything that trusts the header
    stalls or hammers.
    """

    def __init__(self, contact: dict, defaults: dict, dry_run: bool = False):
        project = contact.get("project", "forecast archive")
        url = contact.get("url", "")
        email = contact.get("email", "")
        self.user_agent = f"{project} (+{url}; {email})"
        self.timeout = defaults.get("timeout_seconds", 30)
        self.max_retries = defaults.get("max_retries", 4)
        self.backoff_base = defaults.get("backoff_base_seconds", 2)
        self.min_interval = defaults.get("min_interval_seconds", 1.0)
        self.dry_run = dry_run
        self._last_request_at: dict[str, float] = {}
        self.request_count = 0

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            wait = self.min_interval - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.time()

    def get(self, url: str, headers: dict | None = None) -> tuple[bytes, dict]:
        """Return (body_bytes, meta_dict). Raises on unrecoverable failure."""
        if self.dry_run:
            return b"", {"dry_run": True, "url": url, "status": None}

        attempt = 0
        last_error: Exception | None = None

        while attempt <= self.max_retries:
            self._throttle(url)
            req = urllib.request.Request(url)
            req.add_header("User-Agent", self.user_agent)
            req.add_header("Accept", "application/json, text/html;q=0.9, */*;q=0.8")
            for k, v in (headers or {}).items():
                req.add_header(k, v)

            try:
                self.request_count += 1
                with urllib.request.urlopen(req, timeout=self.timeout,
                                            context=SSL_CONTEXT) as resp:
                    body = resp.read()
                    meta = {
                        "url": url,
                        "final_url": resp.geturl(),
                        "status": resp.status,
                        "headers": dict(resp.headers),
                        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "attempt": attempt + 1,
                    }
                    return body, meta

            except urllib.error.HTTPError as e:
                last_error = e
                # 4xx other than 429 will not improve with retrying.
                if e.code != 429 and 400 <= e.code < 500:
                    raise
            except urllib.error.URLError as e:
                # A TLS verification failure is deterministic. Retrying it four
                # times with exponential backoff just burns 30 seconds before
                # reporting the same thing, so fail fast and say what to do.
                if isinstance(e.reason, ssl.SSLError):
                    raise RuntimeError(
                        f"TLS verification failed for {url}: {e.reason}\n"
                        f"    This is almost always a machine setup issue, not a "
                        f"problem with the source.\n"
                        f"    Fix (macOS, python.org build):  "
                        f"/Applications/Python\\ 3.x/Install\\ Certificates.command\n"
                        f"    Or:  python3 -m pip install --upgrade certifi"
                    ) from e
                last_error = e
            except Exception as e:  # timeouts, DNS, connection resets
                last_error = e

            attempt += 1
            if attempt <= self.max_retries:
                delay = self.backoff_base ** attempt + random.uniform(0, 1.0)
                time.sleep(delay)

        raise RuntimeError(f"exhausted {self.max_retries} retries for {url}: {last_error}")


# --------------------------------------------------------------------------
# Raw store
# --------------------------------------------------------------------------

class RawStore:
    """
    Writes bytes to data/<cycle>/raw/<source_id>/<date>/<slug>.<ext> with a
    .meta.json sidecar carrying status, headers, hash, and fetch time.

    Idempotent by construction: keyed on (source, date, slug), so re-running
    on the same day overwrites rather than appends. Running twice in a day
    must not duplicate snapshots.
    """

    def __init__(self, cycle: int, snapshot_date: str, dry_run: bool = False):
        self.root = DATA_DIR / str(cycle) / "raw"
        self.snapshot_date = snapshot_date
        self.dry_run = dry_run

    @staticmethod
    def _slugify(name: str) -> str:
        s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
        return s[:120] or "artifact"

    @staticmethod
    def _extension(body: bytes, meta: dict) -> str:
        ctype = (meta.get("headers") or {}).get("Content-Type", "")
        if "json" in ctype:
            return "json"
        if "html" in ctype:
            return "html"
        if "xml" in ctype:
            return "xml"
        if "csv" in ctype:
            return "csv"
        head = body.lstrip()[:1]
        if head in (b"{", b"["):
            return "json"
        if head == b"<":
            return "html"
        return "txt"

    def previous_hash(self, source_id: str, name: str) -> tuple[str | None, str | None]:
        """Most recent prior capture's hash and date, for staleness detection."""
        slug = self._slugify(name)
        d = self.root / source_id
        if not d.is_dir():
            return None, None
        dates = sorted((p.name for p in d.iterdir()
                        if p.is_dir() and p.name < self.snapshot_date), reverse=True)
        for dt_ in dates:
            for meta_path in (d / dt_).glob(f"{slug}.meta.json"):
                try:
                    return json.loads(meta_path.read_text()).get("sha256"), dt_
                except Exception:
                    pass
        return None, None

    def write(self, source_id: str, name: str, body: bytes, meta: dict) -> int:
        slug = self._slugify(name)
        if self.dry_run:
            return 0
        d = self.root / source_id / self.snapshot_date
        d.mkdir(parents=True, exist_ok=True)
        ext = self._extension(body, meta)
        (d / f"{slug}.{ext}").write_bytes(body)
        (d / f"{slug}.meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
        return len(body)

    def write_backfill(self, source_id: str, subdir: str, name: str,
                       body: bytes, meta: dict) -> int:
        """Backfill lands under its own dated path, not today's."""
        slug = self._slugify(name)
        if self.dry_run:
            return 0
        d = self.root / source_id / "_backfill" / self._slugify(subdir)
        d.mkdir(parents=True, exist_ok=True)
        ext = self._extension(body, meta)
        (d / f"{slug}.{ext}").write_bytes(body)
        (d / f"{slug}.meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
        return len(body)


# --------------------------------------------------------------------------
# Method handlers. One per `method` value in the registry.
# Each returns (artifact_count, bytes_written, notes).
# --------------------------------------------------------------------------

def handle_http(src: dict, fetcher: Fetcher, store: RawStore, **_) -> tuple[int, int, list[str]]:
    """Plain list of URLs. The common case."""
    urls = (src.get("config") or {}).get("urls") or []
    n = b = 0
    notes: list[str] = []
    if not urls:
        notes.append("no urls configured")
    errors: list[str] = []
    for entry in urls:
        name = entry.get("name") or entry["url"]
        # Isolate per URL. A source listing three files should not lose all three
        # because one of them 404s — capture whatever is reachable and report the
        # rest. Same principle as isolating sources from each other.
        try:
            body, meta = fetcher.get(entry["url"])
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        prev_hash, prev_date = store.previous_hash(src["id"], name)
        if prev_hash and prev_hash == meta.get("sha256"):
            days = _days_between(prev_date, store.snapshot_date)
            meta["unchanged_since"] = prev_date
            if days is not None and days >= STALE_AFTER_DAYS:
                notes.append(f"STALE: {name} byte-identical since {prev_date} "
                             f"({days}d) — the source may have stopped updating")
        b += store.write(src["id"], name, body, meta)
        n += 1

    notes.extend(f"FAILED {e}" for e in errors)
    if errors and n == 0:
        raise RuntimeError("every configured URL failed: " + " | ".join(errors))
    return n, b, notes


def handle_kalshi(src: dict, fetcher: Fetcher, store: RawStore, **_) -> tuple[int, int, list[str]]:
    """
    Discovery-based capture.

    Kalshi renames series mid-cycle, so hardcoding tickers guarantees silent
    data loss. Instead: page the series list, keep tickers matching the
    configured pattern, and capture every market under each match. The
    discovery response itself is stored, so you can always audit what the
    filter saw on a given day.
    """
    cfg = src.get("config") or {}
    base = cfg["api_base"].rstrip("/")
    # Back-compat with the old single-pattern form.
    include = re.compile(cfg.get("series_include") or cfg.get("series_pattern", ".*"),
                         re.IGNORECASE)
    exclude_raw = cfg.get("series_exclude")
    exclude = re.compile(exclude_raw, re.IGNORECASE) if exclude_raw else None
    limit = cfg.get("page_limit", 200)
    max_pages = cfg.get("max_pages", 20)
    category = cfg.get("series_category")

    n = b = 0
    notes: list[str] = []
    matched: list[str] = []
    excluded: list[str] = []
    near_missed: list[str] = []
    cursor = None

    for page in range(max_pages):
        params = {"limit": limit}
        if category:
            params["category"] = category
        if cursor:
            params["cursor"] = cursor
        url = f"{base}/series?{urllib.parse.urlencode(params)}"
        body, meta = fetcher.get(url)
        b += store.write(src["id"], f"series-page-{page:02d}", body, meta)
        n += 1

        if fetcher.dry_run:
            break
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            notes.append(f"series page {page} was not JSON; stored raw and stopped discovery")
            break

        series = payload.get("series") or []
        for s in series:
            ticker = s.get("ticker", "")
            if not ticker:
                continue
            if exclude is not None and exclude.search(ticker):
                excluded.append(ticker)
                continue
            if include.search(ticker):
                matched.append(ticker)
            else:
                near_missed.append(ticker)

        cursor = payload.get("cursor")
        if not cursor or not series:
            break

    # Prune market files for series we no longer collect. Idempotent overwrite
    # replaces same-named artifacts but never removes retired ones, so tightening
    # the filter left 265 stale files behind that the parser then re-read every
    # day forever. Only prune when discovery actually succeeded — a failed run
    # must never be able to empty the directory.
    if matched and not fetcher.dry_run:
        keep = {store._slugify(f"markets-{t}") for t in set(matched)}
        day = store.root / src["id"] / store.snapshot_date
        removed = 0
        if day.is_dir():
            for f in day.iterdir():
                stem = f.name.split(".")[0]
                if stem.startswith("markets-") and stem not in keep:
                    try:
                        f.unlink()
                        removed += 1
                    except OSError:
                        pass
        if removed:
            notes.append(f"pruned {removed} stale artifact(s) from retired series")

    notes.append(f"{len(matched)} series matched, {len(excluded)} explicitly excluded, "
                 f"{len(near_missed)} unmatched")
    # Surface anything that looks election-shaped but did not match, so a series
    # Kalshi renames shows up in the log instead of silently disappearing.
    shaped = [t for t in near_missed
              if re.search(r"(MIDTERM|GENERICBALLOT|GOVERNOR|CONTROL)", t, re.I)]
    if shaped:
        notes.append(f"NEAR MISS — election-shaped but unmatched: {shaped[:8]}")

    # Capture markets for each matched series.
    for ticker in sorted(set(matched)):
        url = f"{base}/markets?{urllib.parse.urlencode({'series_ticker': ticker, 'limit': 1000})}"
        try:
            body, meta = fetcher.get(url)
            b += store.write(src["id"], f"markets-{ticker}", body, meta)
            n += 1
        except Exception as e:
            # Isolate per-ticker failures. One dead series must not abort the run.
            notes.append(f"market fetch failed for {ticker}: {e}")

    return n, b, notes


def handle_polymarket(src: dict, fetcher: Fetcher, store: RawStore, **_) -> tuple[int, int, list[str]]:
    """Named event slugs, plus a keyword sweep for anything missed."""
    cfg = src.get("config") or {}
    base = cfg["gamma_base"].rstrip("/")
    n = b = 0
    notes: list[str] = []

    for slug in cfg.get("slugs", []):
        url = f"{base}/events?{urllib.parse.urlencode({'slug': slug})}"
        try:
            body, meta = fetcher.get(url)
            b += store.write(src["id"], f"event-{slug}", body, meta)
            n += 1
        except Exception as e:
            notes.append(f"slug {slug} failed: {e}")

    # ONE snapshot of the active-event list. The previous version issued one
    # request per "search term" but never actually sent the term as a query, so
    # it stored the same multi-megabyte response several times over.
    if cfg.get("snapshot_active_events"):
        params = {"limit": cfg.get("page_limit", 500),
                  "active": "true", "closed": "false"}
        url = f"{base}/events?{urllib.parse.urlencode(params)}"
        try:
            body, meta = fetcher.get(url)
            b += store.write(src["id"], "active-events", body, meta)
            n += 1
        except Exception as e:
            notes.append(f"active-events snapshot failed: {e}")

    notes.append("per-seat market coverage was unverified in the audit; check the stored events")
    return n, b, notes


def handle_wikipedia(src: dict, fetcher: Fetcher, store: RawStore,
                     backfill: bool = False, **_) -> tuple[int, int, list[str]]:
    """
    Current wikitext for each configured page, plus optional revision backfill.

    The backfill is the single most valuable capture in the whole registry:
    it is the only source in the archive that recovers the PAST. Every other
    source only exists going forward from the day collection starts.
    """
    cfg = src.get("config") or {}
    api = cfg["api"]
    n = b = 0
    notes: list[str] = []

    for title in cfg.get("pages", []):
        params = {
            "action": "parse", "page": title, "prop": "wikitext",
            "format": "json", "formatversion": "2",
        }
        url = f"{api}?{urllib.parse.urlencode(params)}"
        try:
            body, meta = fetcher.get(url)
            b += store.write(src["id"], f"current-{title}", body, meta)
            n += 1
        except Exception as e:
            notes.append(f"current fetch failed for '{title}': {e}")

    bf = cfg.get("backfill") or {}
    if backfill and bf.get("enabled"):
        since = str(bf.get("since", "2026-01-01"))
        cap = bf.get("max_revisions_per_page", 2000)
        for title in cfg.get("pages", []):
            got = 0
            rvcontinue = None
            page_idx = 0
            while got < cap:
                params = {
                    "action": "query", "prop": "revisions", "titles": title,
                    "rvlimit": "500", "rvdir": "newer",
                    "rvstart": f"{since}T00:00:00Z",
                    "rvprop": "ids|timestamp|user|comment|size",
                    "format": "json", "formatversion": "2",
                }
                if rvcontinue:
                    params["rvcontinue"] = rvcontinue
                url = f"{api}?{urllib.parse.urlencode(params)}"
                try:
                    body, meta = fetcher.get(url)
                    b += store.write_backfill(
                        src["id"], title, f"revlist-{page_idx:03d}", body, meta)
                    n += 1
                except Exception as e:
                    notes.append(f"backfill failed for '{title}' page {page_idx}: {e}")
                    break

                if fetcher.dry_run:
                    break
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    notes.append(f"backfill page {page_idx} for '{title}' was not JSON")
                    break

                pages = payload.get("query", {}).get("pages", [])
                revs = pages[0].get("revisions", []) if pages else []
                got += len(revs)
                page_idx += 1
                rvcontinue = payload.get("continue", {}).get("rvcontinue")
                if not rvcontinue:
                    break
            notes.append(f"backfilled {got} revision records for '{title}'")

    return n, b, notes


_LIVE_ASSIGN = re.compile(r'"live"\s*:\s*\{')


def _balanced_json(text: str, start: int) -> str | None:
    """Slice the JSON object starting at `start`, respecting string literals."""
    depth, i, n, in_str, esc = 0, start, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def _discover_live(html: str) -> list[dict]:
    """
    Pull every enabled live-data pointer out of an Infogram embed page.

    This is DISCOVERY, not parsing — the same licence handle_kalshi takes when
    it pages the series list. We read only enough to know what to fetch next;
    what the fetched bytes MEAN is phase 2's problem.
    """
    out, seen = [], set()
    for m in _LIVE_ASSIGN.finditer(html):
        raw = _balanced_json(html, m.end() - 1)
        if not raw:
            continue
        try:
            lv = json.loads(raw)
        except json.JSONDecodeError:
            continue
        key = lv.get("key")
        if not lv.get("enabled") or not key or key in seen:
            continue
        seen.add(key)
        out.append(lv)
    return out


def handle_infogram(src: dict, fetcher: Fetcher, store: RawStore, **_) -> tuple[int, int, list[str]]:
    """
    Two-step capture for Infogram-embedded sources.

    WHY THIS HANDLER EXISTS
        racetothewh.com publishes everything through Infogram. The embed page
        carries no numbers at all — it is a layout shell whose charts hold
        empty sheets and a POINTER to Infogram's live-data service:

            custom.live = {enabled: true, key: "<uuid>",
                           title: "Sen 26 - Main Graphics", sheetNames: [...]}

        The numbers live at  <live_base>/<key>  and are fetched by the viewer
        at render time. Capturing only the embed page therefore stores a
        permanently empty artifact — and unlike a parser bug, that is NOT
        recoverable later, because the bytes never held the data. Hence a
        handler rather than a longer list of URLs.

    WHY DISCOVERY RATHER THAN HARDCODED KEYS
        There are 38 pointers in the Senate deck and 51 in the House deck, and
        they are regenerated when the author rebuilds a chart. A static list
        would rot silently — the exact failure this pipeline is built to avoid.
        Reading the keys out of the shell each day means a rotation is picked
        up automatically, and the shell we store is the audit trail for what
        the discovery saw.
    """
    cfg = src.get("config") or {}
    urls = cfg.get("urls") or []
    live_base = (cfg.get("live_base") or "https://live-data.jifo.co").rstrip("/")
    max_live = int(cfg.get("max_live", 120))

    n = b = 0
    notes: list[str] = []
    errors: list[str] = []
    pointers: dict[str, dict] = {}

    # Step 1 — the embed shells. Same per-URL isolation as handle_http.
    for entry in urls:
        name = entry.get("name") or entry["url"]
        try:
            body, meta = fetcher.get(entry["url"])
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        prev_hash, prev_date = store.previous_hash(src["id"], name)
        if prev_hash and prev_hash == meta.get("sha256"):
            days = _days_between(prev_date, store.snapshot_date)
            meta["unchanged_since"] = prev_date
            if days is not None and days >= STALE_AFTER_DAYS:
                notes.append(f"STALE: {name} byte-identical since {prev_date} "
                             f"({days}d) — the source may have stopped updating")
        b += store.write(src["id"], name, body, meta)
        n += 1
        for lv in _discover_live(body.decode("utf-8", errors="replace")):
            pointers.setdefault(lv["key"], lv)

    if errors and n == 0:
        raise RuntimeError("every configured embed URL failed: " + " | ".join(errors))

    # Step 2 — dereference the pointers.
    if not pointers:
        # Not fatal: the deck may genuinely have no live charts. But say so,
        # because the alternative reading is that discovery broke, and those
        # two look identical from the row count alone.
        notes.append("no live-data pointers found in the embed shells — either "
                     "the deck stopped using live data or the discovery regex "
                     "needs updating")
    ordered = sorted(pointers.values(), key=lambda l: str(l.get("title") or ""))
    if len(ordered) > max_live:
        notes.append(f"CAPPED: {len(ordered)} live pointers discovered, fetching "
                     f"the first {max_live}. Raise config.max_live to take them all.")
        ordered = ordered[:max_live]

    live_ok = 0
    for lv in ordered:
        key = lv["key"]
        title = str(lv.get("title") or "untitled")
        name = f"live__{RawStore._slugify(title)[:60]}__{key[:8]}"
        try:
            body, meta = fetcher.get(f"{live_base}/{key}")
        except Exception as e:
            errors.append(f"live {title} ({key[:8]}): {type(e).__name__}: {e}")
            continue
        # Carry the pointer's own metadata onto the artifact. The sheet names
        # are what phase 2 uses to tell a margin sheet from a rating sheet, and
        # they are not repeated inside every payload.
        meta["live_key"] = key
        meta["live_title"] = title
        meta["live_sheet_names"] = lv.get("sheetNames")
        meta["live_provider"] = lv.get("provider")
        b += store.write(src["id"], name, body, meta)
        n += 1
        live_ok += 1

    if pointers:
        notes.append(f"live data: {live_ok}/{len(ordered)} pointers fetched")
    notes.extend(f"FAILED {e}" for e in errors)
    return n, b, notes


HANDLERS: dict[str, Callable[..., tuple[int, int, list[str]]]] = {
    "http": handle_http,
    "kalshi_discover": handle_kalshi,
    "polymarket_discover": handle_polymarket,
    "wikipedia": handle_wikipedia,
    "infogram_live": handle_infogram,
}


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

RAW_MANIFEST_FIELDS = [
    "snapshot_date", "source_id", "artifact", "sha256", "bytes", "status", "fetched_at",
]


def append_raw_manifest(cycle: int, snapshot_date: str, dry_run: bool) -> Path | None:
    """
    A public, committable record of every raw capture — hashes ONLY, no content.

    This is what buys provenance back after the raw tier went private. Publishing
    the SHA-256 of each capture on the day of capture, in a repo whose commits are
    themselves timestamped, is a cryptographic commitment: when the full archive is
    released after the election, anyone can verify the released bytes are bit-identical
    to what was captured on the day claimed. Nobody has to take our word for it, and
    nobody can suspect the archive was tidied up before release.

    Same logic as preregistration: commit the hash now, reveal the content later.
    """
    if dry_run:
        return None
    raw_root = DATA_DIR / str(cycle) / "raw"
    out = DATA_DIR / str(cycle) / "raw_manifest.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    # RECONCILE, don't skip.
    #
    # This used to key on (date, source, artifact) and skip anything already
    # present. That silently broke the guarantee the file exists to provide.
    # Two captures can happen on one date — the daily Action at 11:00 UTC and a
    # local run.sh afterwards — and the second OVERWRITES the stored bytes,
    # because RawStore names files per date, not per run. Skip-if-present then
    # left the manifest asserting the FIRST run's hash for bytes that had since
    # been replaced. A hash nobody can verify against the released archive is
    # worse than no hash: it looks like proof and isn't.
    #
    # Observed for real on 2026-08-20: of 89 artifacts, the bytes on disk
    # matched the local manifest 89/89 and the Action's 13/89.
    #
    # So the rule is now: exactly one row per (date, source, artifact), and its
    # hash is whatever is ACTUALLY stored. A changed hash rewrites the row and
    # is reported. This also makes the file safe to union-merge in git (see
    # .gitattributes) — duplicates introduced by a merge collapse on next run.
    prior: dict[tuple, dict] = {}
    order: list[tuple] = []
    if out.exists():
        with out.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                key = (r["snapshot_date"], r["source_id"], r["artifact"])
                if key not in prior:
                    order.append(key)
                prior[key] = r          # a later duplicate wins

    current: dict[tuple, dict] = {}
    if raw_root.is_dir():
        for sdir in sorted(raw_root.iterdir()):
            day = sdir / snapshot_date
            if not day.is_dir():
                continue
            for meta_path in sorted(day.glob("*.meta.json")):
                try:
                    meta = json.loads(meta_path.read_text())
                except Exception:
                    continue
                key = (snapshot_date, sdir.name, meta_path.name[:-len(".meta.json")])
                current[key] = {
                    "snapshot_date": snapshot_date, "source_id": sdir.name,
                    "artifact": key[2], "sha256": meta.get("sha256", ""),
                    "bytes": meta.get("bytes", ""), "status": meta.get("status", ""),
                    "fetched_at": meta.get("fetched_at", ""),
                }

    added = sum(1 for k in current if k not in prior)
    changed = [k for k, v in current.items()
               if k in prior and prior[k].get("sha256") != v["sha256"]]

    merged = dict(prior)
    merged.update(current)
    for k in current:
        if k not in order:
            order.append(k)

    if not added and not changed and out.exists() and len(order) == len(prior):
        return out

    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RAW_MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(merged[k] for k in order)
    if changed:
        print(f"  raw_manifest: {len(changed)} artifact(s) re-captured today — "
              f"rows updated to the bytes now stored "
              f"({', '.join('/'.join(k[1:]) for k in changed[:3])}"
              f"{'...' if len(changed) > 3 else ''})")
    return out


MANIFEST_FIELDS = [
    "run_started_at", "snapshot_date", "source_id", "category", "ok",
    "artifacts", "bytes", "requests", "skipped_reason", "error", "notes",
]


def append_manifest(cycle: int, run_started: str, snapshot_date: str,
                    registry: dict, results: list[SourceResult],
                    dry_run: bool) -> Path | None:
    """
    One row per source per run. This is the provenance record: it is what gets
    cited in the writeup, and it is how you diagnose "the site looked wrong on
    the 14th" three weeks after the fact.
    """
    if dry_run:
        return None
    path = DATA_DIR / str(cycle) / "manifest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    by_id = {s["id"]: s for s in registry.get("sources", [])}
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        if not exists:
            w.writeheader()
        for r in results:
            w.writerow({
                "run_started_at": run_started,
                "snapshot_date": snapshot_date,
                "source_id": r.source_id,
                "category": by_id.get(r.source_id, {}).get("category", ""),
                "ok": int(r.ok),
                "artifacts": r.artifacts,
                "bytes": r.bytes_written,
                "requests": r.requests_made,
                "skipped_reason": r.skipped_reason or "",
                "error": (r.error or "").replace("\n", " ")[:500],
                "notes": " | ".join(r.notes)[:800],
            })
    return path


# --------------------------------------------------------------------------
# Licence gate
# --------------------------------------------------------------------------

def gate(src: dict) -> str | None:
    """
    Return a skip reason, or None to proceed.

    The registry enforces the legal position in code rather than in your
    memory. A source whose permission is pending cannot be collected by
    forgetting that permission was pending.
    """
    # Licence is checked BEFORE `enabled` on purpose. A prohibited source that
    # also happens to be disabled should be recorded in the manifest as
    # prohibited, because that is the fact that matters and the one someone
    # will need if they later wonder why it is missing from the archive.
    lic = src.get("license")
    if lic == "prohibited":
        return "LICENCE: prohibited — collection would violate stated terms"
    if lic != "permitted":
        return f"LICENCE: {lic!r} — not yet cleared for collection"
    if not src.get("robots_checked"):
        return "robots.txt has never been verified for this source"
    if not src.get("enabled", False):
        return "disabled in registry"
    return None


# --------------------------------------------------------------------------
# Self-test: offline checks that do not touch the network
# --------------------------------------------------------------------------

def self_test(registry: dict) -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    srcs = registry.get("sources", [])
    check(bool(srcs), "registry has no sources")

    ids = [s.get("id") for s in srcs]
    check(len(ids) == len(set(ids)), f"duplicate source ids: {ids}")

    for s in srcs:
        sid = s.get("id", "<missing id>")
        check(bool(s.get("id")), "a source is missing an id")
        check(s.get("method") in HANDLERS or s.get("method") == "manual",
              f"{sid}: unknown method {s.get('method')!r}")
        check(s.get("category") in
              {"fundamentals", "polling", "professional", "market", "expert_ordinal"},
              f"{sid}: unexpected category {s.get('category')!r}")
        check(s.get("license") in {"permitted", "permission_pending", "prohibited"},
              f"{sid}: unexpected license {s.get('license')!r}")
        check(s.get("publication") in {"individual", "aggregate_only", "private"},
              f"{sid}: unexpected publication {s.get('publication')!r}")
        for dep in s.get("declared_inputs") or []:
            check(dep in ids, f"{sid}: declared_input {dep!r} is not a registered source")

    # Nothing prohibited may ever be enabled.
    for s in srcs:
        if s.get("license") == "prohibited":
            check(not s.get("enabled"),
                  f"{s.get('id')}: prohibited source is ENABLED — refusing")

    # Store logic, exercised without the network.
    check(RawStore._slugify("2026 United States House!! ratings")
          == "2026-United-States-House-ratings", "slugify produced unexpected output")
    check(RawStore._extension(b'{"a":1}', {}) == "json", "extension sniff failed for json")
    check(RawStore._extension(b"<html>", {}) == "html", "extension sniff failed for html")
    check(RawStore._extension(b"x", {"headers": {"Content-Type": "text/csv"}}) == "csv",
          "extension sniff failed for csv header")

    # Gate logic.
    check(gate({"enabled": True, "license": "prohibited"}) is not None,
          "gate let a prohibited source through")
    check(gate({"enabled": True, "license": "permitted", "robots_checked": None}) is not None,
          "gate let an unverified-robots source through")
    check(gate({"enabled": True, "license": "permitted",
                "robots_checked": "2026-08-19"}) is None,
          "gate blocked a fully cleared source")

    if failures:
        print("SELF-TEST FAILED")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    enabled = [s["id"] for s in srcs if not gate(s)]
    blocked = [(s["id"], gate(s)) for s in srcs if gate(s)]
    print("SELF-TEST PASSED")
    print(f"  {len(srcs)} sources registered")
    print(f"  {len(enabled)} will be collected: {', '.join(enabled)}")
    print(f"  {len(blocked)} gated:")
    for sid, reason in blocked:
        print(f"      {sid:20s} {reason}")
    return 0


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_registry(cycle: int) -> dict:
    path = FORECAST_DIR / "sources" / f"{cycle}.yaml"
    if not path.exists():
        print(f"ERROR: no registry at {path}", file=sys.stderr)
        raise SystemExit(2)
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Capture raw forecast snapshots.")
    p.add_argument("--cycle", type=int, default=2026)
    p.add_argument("--date", default=None,
                   help="snapshot date (YYYY-MM-DD); defaults to today UTC")
    p.add_argument("--only", default=None,
                   help="comma-separated source ids to run")
    p.add_argument("--dry-run", action="store_true",
                   help="fetch nothing, write nothing, report what would happen")
    p.add_argument("--backfill", action="store_true",
                   help="also run historical backfill where a source supports it")
    p.add_argument("--self-test", action="store_true",
                   help="offline validation of the registry and helpers")
    p.add_argument("--list", action="store_true",
                   help="print the registry and the licence gate, then exit")
    args = p.parse_args(argv)

    registry = load_registry(args.cycle)

    if args.self_test:
        return self_test(registry)

    if args.list:
        print(f"{'id':22s} {'category':16s} {'lic':20s} status")
        print("-" * 78)
        for s in registry.get("sources", []):
            reason = gate(s)
            status = "COLLECT" if reason is None else f"skip — {reason}"
            print(f"{s['id']:22s} {s.get('category',''):16s} "
                  f"{str(s.get('license','')):20s} {status}")
        return 0

    snapshot_date = args.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    run_started = dt.datetime.now(dt.timezone.utc).isoformat()
    wanted = {s.strip() for s in args.only.split(",")} if args.only else None

    contact = registry.get("contact", {})
    defaults = registry.get("defaults", {})
    store = RawStore(args.cycle, snapshot_date, dry_run=args.dry_run)

    print("=" * 74)
    print(f"forecast capture · cycle {args.cycle} · snapshot {snapshot_date}"
          f"{' · DRY RUN' if args.dry_run else ''}")
    print("=" * 74)

    results: list[SourceResult] = []

    for src in registry.get("sources", []):
        sid = src.get("id", "<unknown>")
        if wanted and sid not in wanted:
            continue

        res = SourceResult(source_id=sid)

        reason = gate(src)
        if reason:
            res.skipped_reason = reason
            results.append(res)
            print(f"  · {sid:22s} SKIP  {reason}")
            continue

        if src.get("method") == "manual":
            # Hand-entered sources are imported by manual_import.py, not fetched.
            res.skipped_reason = "manual entry — use collect/manual_import.py"
            results.append(res)
            print(f"  · {sid:22s} SKIP  manual entry (not fetched)")
            continue

        handler = HANDLERS.get(src.get("method"))
        if handler is None:
            res.error = f"no handler for method {src.get('method')!r}"
            results.append(res)
            print(f"  ✗ {sid:22s} {res.error}")
            continue

        # Each source is wrapped. A timeout or a layout change logs the failure,
        # records whatever it did retrieve, and lets every other source proceed.
        fetcher = Fetcher(contact, defaults, dry_run=args.dry_run)
        try:
            n, b, notes = handler(src, fetcher, store, backfill=args.backfill)
            res.ok, res.artifacts, res.bytes_written = True, n, b
            res.notes = notes
            print(f"  ✓ {sid:22s} {n:4d} artifacts  {b/1024:9.1f} KB")
            for note in notes:
                print(f"      note: {note}")
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            print(f"  ✗ {sid:22s} {res.error}")
            traceback.print_exc(file=sys.stderr)
        finally:
            res.requests_made = fetcher.request_count
            results.append(res)

    manifest = append_manifest(args.cycle, run_started, snapshot_date,
                               registry, results, args.dry_run)
    raw_manifest = append_raw_manifest(args.cycle, snapshot_date, args.dry_run)

    attempted = [r for r in results if not r.skipped_reason]
    failed = [r for r in attempted if not r.ok]
    total_bytes = sum(r.bytes_written for r in results)
    total_artifacts = sum(r.artifacts for r in results)

    print("-" * 74)
    print(f"  {len(attempted)} attempted, {len(failed)} failed, "
          f"{len(results) - len(attempted)} skipped")
    print(f"  {total_artifacts} artifacts, {total_bytes/1024/1024:.2f} MB")
    if manifest:
        print(f"  manifest: {manifest.relative_to(REPO_ROOT)}")
    if raw_manifest:
        print(f"  hashes:   {raw_manifest.relative_to(REPO_ROOT)}  (public; content stays private)")
    if failed:
        print(f"  FAILED: {', '.join(r.source_id for r in failed)}")
    print("=" * 74)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
