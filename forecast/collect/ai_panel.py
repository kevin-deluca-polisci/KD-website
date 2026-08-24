#!/usr/bin/env python3
"""
The AI panel: ask several models, one race at a time, and store what they said.

    python3 forecast/collect/ai_panel.py --dry-run
    python3 forecast/collect/ai_panel.py --dry-run --show-prompt
    python3 forecast/collect/ai_panel.py --wave competitive
    python3 forecast/collect/ai_panel.py --wave all --limit 20

CAPTURE ONLY. This stores raw responses and never parses them into rows, the
same split every other source in this archive follows: capture fetches and
never interprets, parse reads storage and never fetches. The parser is a
separate file written later, against bytes that already exist.

Read forecast/ai/PREREGISTRATION.md first. It is pinned by hash below and this
script refuses to run if it has changed, for the same reason score.py refuses
against an unrecognised RULES.md: a design decided after the answers are in is
not a design.

-----------------------------------------------------------------------------
WHY THIS IS URGENT IN A WAY THE REST OF THE ARCHIVE IS NOT

Everything else here can be rebuilt from stored bytes. This cannot. A model
version is deprecated and gone, and a model retrained after November knows the
result. What these models say today is recoverable only today.

-----------------------------------------------------------------------------
KEYS

Read from the environment, never from a file in the repo:

    ANTHROPIC_API_KEY   OPENAI_API_KEY   GOOGLE_API_KEY

A provider with no key is skipped and reported as skipped, so a partial panel
is visible rather than silent.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REPO = HERE.parents[1]
DATA = REPO / "forecast" / "data"
PREREG = REPO / "forecast" / "ai" / "PREREGISTRATION.md"

# Pinned on first run: run once, read the printed hash, put it here, commit.
PREREG_SHA256 = "7b7280e88f8b42df77059a8a704651290fc009a6a781d0f916ec7506af0311dd"

# ---------------------------------------------------------------------------
# THE PROMPT IS FROZEN. Changing one character makes a different question and
# therefore a different source; bump to PROMPT_V2 and a new source id rather
# than editing this, and record it in PREREGISTRATION.md §10.
# ---------------------------------------------------------------------------
PROMPT_V1 = """You are forecasting a single 2026 United States election.

Race: {race_label}

Give the probability that the Democratic candidate wins this race.

Answer with JSON and nothing else, in exactly this form:
{{"prob_D": 0.00, "reasoning": "", "confidence": ""}}

- prob_D: a decimal between 0 and 1
- reasoning: at most two sentences
- confidence: one of "low", "medium", "high"

Do not search the web. Answer from what you already know."""

DRAWS = 5
TEMPERATURE = 1.0

PROVIDERS = [
    # (provider, env var, default model alias — the API's returned id is what
    #  gets recorded, never this string)
    ("anthropic", "ANTHROPIC_API_KEY", "claude-opus-5"),
    ("openai", "OPENAI_API_KEY", "gpt-5"),
    ("google", "GOOGLE_API_KEY", "gemini-3-pro"),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_prereg() -> str:
    if not PREREG.exists():
        raise SystemExit(f"REFUSING TO RUN: no pre-registration at {PREREG}")
    got = _sha(PREREG)
    if PREREG_SHA256 == "PLACEHOLDER_FILLED_ON_FIRST_RUN":
        print(f"  PREREGISTRATION.md sha256 = {got}")
        print("  Pin it: set PREREG_SHA256 in ai_panel.py, commit, re-run.")
        print("  Deliberately not self-writing — a program that edits its own")
        print("  integrity check is not an integrity check.")
        raise SystemExit(2)
    if got != PREREG_SHA256:
        raise SystemExit(
            "REFUSING TO RUN: forecast/ai/PREREGISTRATION.md has changed.\n"
            f"  pinned:  {PREREG_SHA256}\n  on disk: {got}\n"
            "  If the change is deliberate, add it to §10, update the pin, "
            "and commit both.")
    return got


# ---------------------------------------------------------------------------
def race_label(race_id: str) -> str:
    """A human-readable race name. The model sees this, so it is part of the
    frozen question and must not drift."""
    parts = race_id.split("_")
    if len(parts) < 3:
        return race_id
    kind, st = parts[0], parts[1]
    if kind == "HOU":
        d = parts[2]
        seat = f"{st} at-large" if d == "00" else f"{st}-{int(d)}"
        return f"United States House of Representatives, {seat}, 2026"
    if kind == "SEN":
        return f"United States Senate, {st}, 2026"
    if kind == "GOV":
        return f"Governor of {st}, 2026"
    return race_id


def select_races(cycle: int, wave: str) -> tuple[list[str], dict]:
    """Which races this wave asks about, and why. See PREREGISTRATION §5.

    THE UNIVERSE IS NOT OBVIOUS AND HAS TO BE BUILT. The published archive
    does not contain a list of all 435 districts: Wikipedia's ratings table
    carries only the races somebody bothered to rate (224 on the newest day,
    258 ever), and our own district projections are unpublished for licence
    reasons. The one complete list is the Cook capture's 435 rows — used here
    ONLY to name the races we ask about, which reproduces no index and leaves
    nothing published.

    COMPETITIVE IS THE NARROW READING. A race is competitive if any rater on
    the newest day calls it anything other than Safe, Solid or Likely, or if
    our own win probability sits in [0.05, 0.95]. An earlier version scanned
    every date rather than the newest, so one "Toss-up" from one rater in
    nineteen months marked a race competitive forever and 245 of 261 races
    qualified. Ratings are a claim about a date, like everything else here.
    """
    derived = DATA / str(cycle) / "derived"
    universe: set[str] = set()
    comp: set[str] = set()

    # --- the 435, from the one source that has them all --------------------
    pvi = sorted((DATA / str(cycle) / "raw" / "cook_pvi").glob("*/manual.json")) \
        if (DATA / str(cycle) / "raw" / "cook_pvi").is_dir() else []
    if pvi:
        for r in (json.loads(pvi[-1].read_text()).get("rows") or []):
            st, d = r.get("state"), r.get("district")
            if st and d is not None:
                universe.add(f"HOU_{st}_{int(d):02d}_{cycle}")

    ca = derived / "category_averages.csv"
    latest_ca = ""
    ca_rows: list[dict] = []
    if ca.exists():
        ca_rows = list(csv.DictReader(ca.open(encoding="utf-8")))
        latest_ca = max((r["snapshot_date"] for r in ca_rows), default="")
        for r in ca_rows:
            rid = r.get("race_id", "")
            if rid.startswith(("SEN_", "GOV_")):
                universe.add(rid)
        for r in ca_rows:
            rid = r.get("race_id", "")
            if (r["snapshot_date"] == latest_ca and rid and
                    not rid.startswith("NATL")
                    and r.get("quantity") == "win_prob_D" and r.get("mean")):
                try:
                    p_ = float(r["mean"])
                except ValueError:
                    continue
                if 0.05 <= p_ <= 0.95:
                    comp.add(rid)

    er = derived / "expert_ratings.csv"
    n_rated = 0
    if er.exists():
        rows = list(csv.DictReader(er.open(encoding="utf-8")))
        latest_er = max((r["snapshot_date"] for r in rows), default="")
        for r in rows:
            if r.get("race_id"):
                universe.add(r["race_id"])
            if r["snapshot_date"] != latest_er:
                continue
            n_rated += 1
            v = (r.get("value") or "")
            rating = v.split(":", 1)[1] if ":" in v else v
            low = rating.lower()
            if low and not any(w in low for w in ("safe", "solid", "likely")):
                comp.add(r["race_id"])

    comp &= universe
    why = {
        "competitive_rule": "newest-day expert rating outside Safe/Solid/"
                            "Likely, OR our win_prob_D in [0.05,0.95]",
        "n_competitive": len(comp), "n_universe": len(universe),
        "n_rated_rows_newest_day": n_rated,
        "universe_from": "cook_pvi districts + Senate/Gov in the archive",
    }
    races = sorted(comp) if wave == "competitive" else sorted(universe)
    return races, why


# ---------------------------------------------------------------------------
def call_model(provider: str, model: str, prompt: str, key: str) -> dict:
    """One draw. Returns the raw response envelope; NEVER parses prob_D.

    Deliberately thin. Each provider's SDK is imported lazily so a missing
    package disables one provider rather than the whole run.
    """
    t0 = time.time()
    if provider == "anthropic":
        import anthropic
        c = anthropic.Anthropic(api_key=key)
        r = c.messages.create(model=model, max_tokens=300,
                              temperature=TEMPERATURE,
                              messages=[{"role": "user", "content": prompt}])
        return {"model_id": r.model, "text": "".join(
            b.text for b in r.content if getattr(b, "type", "") == "text"),
            "raw": r.model_dump() if hasattr(r, "model_dump") else None,
            "latency_ms": int((time.time() - t0) * 1000)}
    if provider == "openai":
        from openai import OpenAI
        c = OpenAI(api_key=key)
        r = c.chat.completions.create(model=model, temperature=TEMPERATURE,
                                      max_tokens=300,
                                      messages=[{"role": "user",
                                                 "content": prompt}])
        return {"model_id": r.model, "text": r.choices[0].message.content,
                "raw": r.model_dump() if hasattr(r, "model_dump") else None,
                "latency_ms": int((time.time() - t0) * 1000)}
    if provider == "google":
        import google.generativeai as genai
        genai.configure(api_key=key)
        m = genai.GenerativeModel(model)
        r = m.generate_content(prompt, generation_config={
            "temperature": TEMPERATURE, "max_output_tokens": 300})
        return {"model_id": model, "text": r.text,
                "raw": None, "latency_ms": int((time.time() - t0) * 1000)}
    raise ValueError(f"unknown provider {provider}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--wave", choices=("competitive", "all"),
                    default="competitive")
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--limit", type=int, help="cap the race count (testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="select races, cost the run, call nothing")
    ap.add_argument("--show-prompt", action="store_true")
    ap.add_argument("--date", help="store under this date (default today UTC)")
    a = ap.parse_args(argv)

    sha = check_prereg()
    date = a.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    races, why = select_races(a.cycle, a.wave)
    if a.limit:
        races = races[:a.limit]

    keys = {p: os.environ.get(env) for p, env, _ in PROVIDERS}
    live = [(p, m) for p, env, m in PROVIDERS if keys[p]]
    skipped = [p for p, env, _ in PROVIDERS if not keys[p]]

    print("=" * 72)
    print(f"AI panel · {date} · wave={a.wave}")
    print("=" * 72)
    print(f"  pre-registration sha256 {sha[:16]}…  (pinned)")
    print(f"  {why['n_competitive']} competitive of {why['n_universe']} "
          f"races  ({why['universe_from']})")
    print(f"  this wave asks {len(races)} race(s) × {len(live)} model(s) "
          f"× {a.draws} draw(s) = {len(races) * len(live) * a.draws} calls")
    if skipped:
        print(f"  SKIPPED, no API key: {', '.join(skipped)}")
    if len(live) < 3:
        print(f"  NOTE: only {len(live)} model(s) available. The MIN_N=3 "
              f"floor means the category cannot publish an average until "
              f"three answer.")
    if a.show_prompt and races:
        print("\n  the frozen prompt, as one model will see it:\n")
        for line in PROMPT_V1.format(race_label=race_label(races[0])).split("\n"):
            print(f"    {line}")

    if a.dry_run:
        print(f"\n  first 8 races: {', '.join(races[:8])}")
        print("  --dry-run: nothing was called and nothing was written.")
        return 0
    if not live:
        print("\n  no API keys in the environment — nothing to do")
        return 1

    out = DATA / str(a.cycle) / "raw" / "ai_panel" / date
    out.mkdir(parents=True, exist_ok=True)
    (out / "_wave.json").write_text(json.dumps({
        "date": date, "wave": a.wave, "races": races, "selection": why,
        "draws": a.draws, "temperature": TEMPERATURE,
        "prompt_sha256": hashlib.sha256(PROMPT_V1.encode()).hexdigest(),
        "prereg_sha256": sha, "providers": [p for p, _ in live],
        "skipped_providers": skipped,
    }, indent=1))

    n_ok = n_err = 0
    for rid in races:
        prompt = PROMPT_V1.format(race_label=race_label(rid))
        for provider, model in live:
            for draw in range(a.draws):
                f = out / f"{provider}-{rid}-{draw}.json"
                if f.exists():           # resume: never re-ask a stored draw
                    continue
                rec = {"race_id": rid, "provider": provider,
                       "model_alias": model, "draw": draw,
                       "prompt": prompt, "temperature": TEMPERATURE,
                       "web_access": False,
                       "requested_at": dt.datetime.now(
                           dt.timezone.utc).isoformat()}
                try:
                    rec.update(call_model(provider, model, prompt,
                                          keys[provider]))
                    n_ok += 1
                except Exception as e:                       # noqa: BLE001
                    rec["error"] = f"{type(e).__name__}: {e}"
                    n_err += 1
                f.write_text(json.dumps(rec, indent=1, default=str))
        if (races.index(rid) + 1) % 20 == 0:
            print(f"    {races.index(rid) + 1}/{len(races)} races")

    print(f"\n  {n_ok} response(s) stored, {n_err} error(s)")
    print(f"  -> {out}")
    print("\n  Nothing was parsed. Rows come later, from these bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
