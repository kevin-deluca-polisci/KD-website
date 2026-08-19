# The weekly pipeline

One command, five stages. Only stage 1 touches anyone else's server.

```bash
./forecast/run.sh                 # everything, then commit + push the public tier
./forecast/run.sh --from parse    # skip fetching; reprocess what's already stored
./forecast/run.sh --dry-run       # show what would change, commit nothing
./forecast/run.sh --no-push       # do the work, commit locally
```

| Stage | Reads | Writes | Tier | Network |
|---|---|---|---|---|
| 1 · capture | the web | `raw/` + `raw_manifest.csv` | private / **hashes public** | yes |
| 2 · parse | `raw/` | `parsed/<date>.csv` | private | never |
| 3 · model | history + inputs | `derived/fundamentals_model.json` | public | no |
| 4 · aggregate | `parsed/` | `derived/*.csv` | **public** | never |
| 5 · publish | `derived/` | `site.json`, git commit, push | public | push only |

---

## Why the stages are separated

**Parsers read from storage, not the network.** When Race to the WH changes its layout in mid-October and the parser starts returning garbage, you fix the parser and run `--from parse`. Every date ever captured reprocesses and the public series corrects itself retroactively. That is the entire payoff for capture refusing to parse, and it is why writing parsers isn't urgent — bytes are already banking, and a parser written in September works on August's captures.

It also means you can iterate on a chart forty times in an afternoon without hitting Kalshi once.

---

## The three tiers

```
raw/            every byte as received          PRIVATE    → private repo
parsed/         per-forecaster long format      PRIVATE    → private repo
derived/        category averages + N           PUBLIC     → KD-website
raw_manifest    SHA-256 hashes, no content      PUBLIC     → KD-website
```

Several sources permit collection but not republication of their individual numbers while the election is running. Publishing category averages is both safer and the better analytical unit, and it is what the scope doc committed to from the start.

### Provenance survives going private

Committing raw to git used to be what made the archive trustworthy: append-only, timestamped, diffable. Gitignoring it would have reduced that to "trust me, it's on my laptop."

`raw_manifest.csv` restores it. Every capture's SHA-256 is committed publicly on the day of capture, with no content. When the full archive is released after the election, anyone can hash the released files and verify they are bit-identical to what was captured on the dates claimed. Git's own commit timestamps do the dating.

Same logic as preregistration: commit the hash now, reveal the content later. For the eventual paper it is a strong methods paragraph, and it forecloses any suspicion the archive was tidied before release.

---

## The publication rule, enforced rather than trusted

`aggregate.py` is the privacy boundary. The rule lives in code for the same reason the licence gate does: a rule you have to remember is one you will forget at 11pm in late October.

**Tier** comes from the registry and rides on every parsed row:

| Tier | Meaning |
|---|---|
| `individual` | may be published per-forecaster (permissive licence) |
| `aggregate_only` | only the category mean may leave |
| `private` | never published in any form during the cycle |

**Minimum N.** If a category has two contributing sources and you publish the mean, anyone who knows one value recovers the other by subtraction. So any average containing a gated source publishes only at **N ≥ 3**; below that the cell is suppressed and `suppressed.csv` records why.

Categories made entirely of `individual` sources are exempt — Kalshi and Polymarket prices are a public order book in real time, so there is nothing to protect.

**Ordinal ratings are never averaged.** "Lean R" does not combine with a vote share. They get their own panel; the crosswalk is worth more as a class discussion than as a silent assumption in a script.

**The audit re-derives the guarantee from the output**, rather than trusting the code path that produced it. Tested by deliberately sabotaging the tier logic: the audit catches it, exits non-zero, and writes nothing. A future refactor that breaks the boundary fails loudly instead of leaking.

---

## Writing the remaining parsers

Four are written (Kalshi, Polymarket, Grant Williams, Wikipedia). Two are scaffolds, because RCP and Race to the WH are HTML-only and should be written against real bytes rather than a guess:

```bash
python3 forecast/collect/parse.py --inspect rcp
```

prints the stored artifacts with a structural sketch — JSON key tree, or HTML table/heading outline, plus a flag if the real data is hiding in a `__NEXT_DATA__` blob. Copy `parsers/_scaffold.py.txt` to `parsers/rcp.py` and implement `parse()`.

**Parsers must fail loudly.** An empty list means "this capture genuinely contained no forecasts"; an exception means "something changed, look at it." Silent degradation to zero rows is the worst failure mode here, because it is indistinguishable from a quiet week. Every written parser raises with a diagnostic when it finds nothing.

The four JSON parsers were written without access to a live response, so treat their field-name guesses as provisional. Each raises with the keys it actually saw, so the first real run tells you exactly what to fix.

---

## Setup

**Private archive repo**, once:

1. Create a private repo, e.g. `kevin-deluca-polisci/plsc2219-raw`
2. Fine-grained PAT, `Contents: write`, scoped to that repo only
3. Add it to KD-website as the `RAW_ARCHIVE_TOKEN` secret
4. Copy `forecast/workflows/forecast-capture.yml` to `.github/workflows/`
5. Copy `forecast/data_gitignore.txt` to `forecast/data/.gitignore`

The daily Action captures and pushes raw to the private repo, then commits only hashes and run provenance to the public one. It does not parse, aggregate, or publish — you do that weekly, where you can look at the numbers before they go live.

**First run:**

```bash
python3 forecast/collect/capture.py --dry-run          # verify endpoints
./forecast/run.sh --backfill --no-push                 # includes Wikipedia history
```

`--backfill` walks Wikipedia revision history to 1 January 2026. It is the only source that recovers the past. Run it once.

---

## Release, after the election

The private archive becomes a documented dataset with a Dataverse DOI. `raw_manifest.csv` is what lets anyone verify the release matches what was committed during the cycle — flip the private repo public, or export it with a codebook, and the hashes line up.

Worth doing alongside a short data paper. It would also be the natural place to publish the dependency graph, since `declared_inputs` records which forecasters consume which others, and nobody has documented that for a full cycle.
