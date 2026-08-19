# PLSC 2219 Forecast Archive — collector

A permanent, citable archive of 2026 US midterm election forecasts, captured daily.

**Why:** ABC shut FiveThirtyEight down in March 2025. On **17 May 2026** Disney removed the archived site as well — `fivethirtyeight.com` now redirects to ABC News. Nate Silver offered to buy the remaining IP to restore the archive and was refused. The most prominent forecaster in the field lost its own record of what it said and when. This archive is a response to something that already happened, not a hypothetical.

---

## Quick start

```bash
pip install -r forecast/collect/requirements.txt

python3 forecast/collect/capture.py --self-test    # validate the registry, no network
python3 forecast/collect/capture.py --list         # what will and won't be collected
python3 forecast/collect/capture.py --dry-run      # rehearse a real run
python3 forecast/collect/capture.py                # capture today
```

**First real run should be:**

```bash
python3 forecast/collect/capture.py --backfill
```

The `--backfill` flag walks Wikipedia revision history back to 1 January 2026. Wikipedia is the only source in the registry that recovers the **past**; everything else exists only going forward from the day collection starts. Run it once, then never again.

---

## The one rule

**Phase 1 fetches and writes bytes. It does not parse.**

```
Stage 1  capture.py    →  raw bytes + hash manifest   never parses, never renders
Stage 2  parse.py      →  long-format CSV             never fetches
Stage 3  fundamentals  →  our model's estimate        never fetches
Stage 4  aggregate.py  →  category averages           never fetches  ← privacy boundary
Stage 5  publish.py    →  site.json, commit, push     push only
```

`./forecast/run.sh` chains all five. See **PIPELINE.md** for the full walk-through.

This is the rule most likely to feel like overhead and the most expensive to skip. When a source changes its page structure in mid-October and a parser silently starts returning garbage, raw snapshots let you fix the parser and reprocess every historical snapshot. If only parsed output was kept, that history is gone permanently. For an archive, that is the failure that matters most.

Do not add parsing to `capture.py`.

---

## Layout

```
forecast/
├── sources/2026.yaml       ← the registry. THIS is the cycle.
├── collect/
│   ├── capture.py          phase 1 runner
│   ├── parse.py            stage 2
│   ├── aggregate.py        stage 4 — THE PRIVACY BOUNDARY
│   ├── publish.py          stage 5
│   ├── requirements.txt    PyYAML and nothing else, on purpose
│   └── parsers/            one module per source (4 written, 2 scaffolded)
├── model/fundamentals.py   stage 3 — the class model
├── mirror/mirror_538.sh    mirrors the surviving FiveThirtyEight repos
├── mockup/index.html       design mockup, not published
├── run.sh                  ← the weekly command
└── workflows/              copy to .github/workflows/

forecast/data/2026/          ← NOT the repo-root data/ (see below)
├── raw/<source>/<date>/    exactly as received, never edited
│   └── <name>.json + <name>.meta.json    status, headers, sha256, fetch time
├── raw/<source>/_backfill/ historical captures, kept out of the daily stream
├── parsed/                 PRIVATE — per-forecaster long format
├── derived/                PUBLIC  — category averages, the only published tier
├── submissions/            gitignored; only the class aggregate leaves
├── raw_manifest.csv        PUBLIC  — SHA-256 of every capture, no content
└── manifest.csv            one row per source per run
```

**raw/ and parsed/ are gitignored and pushed to a private repo instead.** Several
sources permit collection but not republication of their individual numbers during
the cycle. `raw_manifest.csv` preserves provenance anyway: hashes are committed
publicly on the day of capture, so the eventual release can be verified as
bit-identical to what was collected. See PIPELINE.md.

**To run 2028:** copy `sources/2026.yaml` to `sources/2028.yaml`, edit it, and run `--cycle 2028`. No code should need to change. That is the entire reusability story, and it is why the registry carries so much metadata.

---

## The registry is the interesting file

Each source declares more than a URL. Three fields do real work:

**`license`** — `permitted` / `permission_pending` / `prohibited`. **The runner refuses to collect anything that is not `permitted`.** This puts the legal position in code rather than in your memory, so a source whose permission is pending cannot be collected by forgetting that permission was pending. The self-test additionally refuses to run at all if a `prohibited` source has been enabled.

**`declared_inputs`** — which other sources this forecaster consumes. This matters more than it looks. The sources are **not independent**:

- DDHQ ingests Polymarket **and** Kalshi, weighted by trading volume
- VoteHub's complete forecast ingests expert ratings **and** market data
- FiftyPlusOne stacks an expert-ratings sub-model
- Grant Williams takes polling input from Silver Bulletin
- 270toWin's consensus is a function of six other forecasters

An archive that treats these as independent draws will understate correlated error badly. Recording the graph is cheap now and impossible to retrofit.

**`publication`** — `individual` / `aggregate_only` / `private`. What may be shown publicly, enforced at render rather than at capture. Store everything; publish per the rule.

---

## What is collected today

| Source | Category | Why it's clean |
|---|---|---|
| Kalshi | market | Public REST, no auth, robots explicitly welcomes AI agents |
| Polymarket | market | robots.txt has no `Disallow` at all |
| Grant Williams | professional | MIT licence; auto-commits forecast JSON daily, so his git history is itself an archive |
| Wikipedia | expert_ordinal | CC BY-SA 4.0, full API, **and revision history that recovers the past** |
| RealClearPolling | polling | Minimal robots, no AI blocks, no crawl-delay |
| Race to the WH | professional | Free, robots-permitted. Raw capture works; parser still to write |

**Blocked, deliberately and visibly:**

- **VoteHub — `prohibited`.** Explicit terms: *"Any scraping, automated collection, or reproduction of our data without prior written permission is strictly prohibited."* Do not enable without written permission.
- **Cook, Inside Elections, Silver Bulletin, FiftyPlusOne, Split Ticket, Economist — `permission_pending`.** Paid, gated, or robots-blocked. Most are available second-hand through Wikipedia's ratings tables under CC BY-SA.
- **Sabato, DDHQ — `permission_pending` pending a robots check.** Both are probably fine; their `robots.txt` simply could not be retrieved during the audit. Two manual checks away from being enabled. See the `notes` in the registry.

A source you decided not to collect is a decision that should be visible and dated, not an absence. That is why the blocked sources stay in the registry rather than being deleted.

---

## Design properties, and why each exists

**Idempotent.** Keyed on `(source, date, slug)`. Running twice in a day overwrites rather than appends, so a re-run after a partial failure is always safe.

**Failure isolation.** Each source is wrapped. A timeout or a layout change logs the failure, records whatever it did retrieve, and lets every other source proceed. A pipeline that refuses to update because one API was briefly down is a pipeline that stops getting run.

**Self-timed backoff with jitter.** Kalshi returns no `Retry-After` header on 429, so anything trusting that header either stalls or hammers.

**Descriptive user agent** carrying the project name, URL, and your email. This matters more than it sounds: it is the difference between looking like an academic archive and looking like an anonymous scraper, if anyone checks.

**Discovery rather than hardcoded tickers** for Kalshi. Kalshi renames series mid-cycle, so a hardcoded list guarantees silent data loss. The discovery response is stored too, so you can always audit what the filter saw on a given day.

**Manifest row per source per run** — timestamp, sources attempted and succeeded, artifact and byte counts, error text, notes. This is the provenance record. It is what gets cited in the writeup, and it is how you diagnose "the site looked wrong on the 14th" three weeks after the fact.

**Runs identically locally and in CI.** Debugging inside a GitHub Action is miserable.

---

## Taxonomy

After the 19 August 2026 membership audit, counting only free, publicly accessible, non-derivative sources:

| Category | Members | In the dispersion figure |
|---|---|---|
| **Fundamentals** | our class model, Ray Fair | Yes |
| **Polling** | RCP, Silver Bulletin free, NYT, VoteHub averages | Yes |
| **Professional** | DDHQ, Economist, Race to the WH, Election Statsheet, Grant Williams | Yes |
| **Markets** | Kalshi, Polymarket | Yes |
| **Expert ratings** | Cook, Sabato, Inside Elections, Fox, RCP map | **No — own panel** |

The August audit found that fundamentals-only and polling-only each had exactly
one free public member and failed the three-member rule. Two things resolved that:

**Building our own fundamentals model** solved the membership problem rather than
papering over it. Fundamentals now has two members, and one of them is ours and
cannot stop updating.

**Prediction markets are kept at n=2 by exception.** They are methodologically
distinct, have the best data access of anything in the audit, and are the only
continuously-priced sources. Merging them into anything else would destroy real
information.

Worth teaching rather than hiding: essentially every serious 2026 forecaster is a
hybrid, and pure fundamentals models survive only as academic artifacts (Ray Fair's
Yale model) or as tools that say on the page that they are not forecasts. **A
taxonomy describing a distinction the field has stopped making is itself a finding.**

Ordinal ratings stay out of the dispersion figure. "Lean R" does not average with a
vote share, and the crosswalk is a judgment call worth more as a class discussion
than as a silent assumption in a script.

Full detail: `PLSC2219_source_audit.md`.

---

## Why the archive is at `forecast/data/` and not `data/`

Hugo treats a **root-level `data/` directory as site data** and parses every file in it into `.Site.Data` on every build. That is what `data/publications.csv` and `data/teaching.csv` are already doing on this site. Dropping tens of thousands of raw snapshot files there would make every single build read the entire archive, and the build would slow to a crawl and then fail.

Hugo ignores unknown root directories, so everything under `forecast/` is invisible to it. Do not move the archive to `data/`.

---

## Ordinal ratings do not average with vote shares

Inside Elections publishes `rating_numeric` (0 = Solid D → 10 = Solid R) and it is tempting to average that against vote shares and probabilities. Don't. Give ratings their own panel and keep them out of the dispersion calculation. The crosswalk is a judgment call you would have to defend, and it is more valuable as a class discussion than as a silent assumption buried in a script.

---

## Automation

Copy `forecast/workflows/forecast-capture.yml` to `.github/workflows/`. It runs daily at 11:00 UTC and has a manual trigger with `only`, `dry_run`, and `backfill` inputs, so you can fire a targeted run from a phone.

It validates the registry before capturing and **refuses to run if a prohibited source has been enabled**. Capture is allowed to fail partially so that successful sources still commit, with the failure surfaced as a warning and a non-zero exit rather than a false green.

The workflow does not build or deploy. `deploy.yml` triggers on push and picks the data commit up on its own.

---

## Next

1. **Run `mirror/mirror_538.sh`.** The surviving 538 repos persist at GitHub's and their owners' discretion, which is exactly the fragility this project is about.
2. **Run once with `--backfill`** to recover Wikipedia's revision history.
3. **Check two robots.txt by hand** — `votes.decisiondeskhq.com` and `centerforpolitics.org` — and flip those sources on if clear. That is two more sources for about ten minutes of work.
4. **Write parsers** in `collect/parsers/`, against captures that will by then already exist. Nothing is lost by doing this in September.
5. **Freeze the focus slate** by 1 September.
