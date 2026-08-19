# First run — a graduated test

Do not start with `./forecast/run.sh`. It runs all five stages and ends with a
`git push`. Work up to it.

Everything below runs from the repo root:

```bash
cd ~/Library/CloudStorage/Dropbox/Claude/website/KD-website
```

---

## Step 0 · Three cleanups only you can do

The device bridge can read and write your files but cannot delete them, so these
are yours.

```bash
# 1. A stale git lock (I created this by running `git status` remotely).
#    Until it goes, every git commit will fail with "Unable to create index.lock".
rm -f .git/index.lock

# 2. My three throwaway files from testing the gitignore.
rm -f forecast/data/2026/raw/kalshi/2026-08-19/test.json \
      forecast/data/2026/parsed/2026-08-19.csv \
      forecast/data/2026/raw_manifest.csv

# 3. Check this, then probably delete it:
ls KD-website/
```

That last one is a **complete duplicate of the repo nested inside itself**, with
its own `.git`, 74 files, all predating this session's work. It contains no
`forecast/` directory, so nothing built here would be lost. I don't know what
created it. Look before you delete, then `rm -rf KD-website/` from inside
`KD-website/`.

---

## Step 1 · Offline checks — no network, nothing written

```bash
pip3 install -r forecast/collect/requirements.txt

python3 forecast/collect/capture.py --self-test
python3 forecast/collect/capture.py --list
```

**Good looks like:** `SELF-TEST PASSED`, 15 sources registered, 6 to be collected.
The `--list` output should show `votehub` skipped as `prohibited` and seven others
as `permission_pending`.

---

## Step 2 · Dry run — still writes nothing

```bash
python3 forecast/collect/capture.py --dry-run
```

Confirms the registry parses and the licence gate fires. It does not hit the
network, so it proves nothing about the endpoints. That is the next step.

---

## Step 3 · One source, for real

Start with the simplest thing on the list: three static JSON files from a
GitHub raw URL.

```bash
python3 forecast/collect/capture.py --only grant_williams
```

**Good looks like:** `✓ grant_williams   3 artifacts   ~xx KB`.

**If it fails**, the URL or the branch name is wrong. His default branch is
`master`, not `main` — that is the most likely culprit and it is one line in
`forecast/sources/2026.yaml`.

Then look at what actually landed:

```bash
ls -R forecast/data/2026/raw/grant_williams/
python3 forecast/collect/parse.py --inspect grant_williams
```

`--inspect` prints the real JSON key tree. **This is the most important output of
the whole exercise**, because the parsers were written without ever seeing a live
response.

---

## Step 4 · Parse it, and expect this to fail

```bash
python3 forecast/collect/parse.py --only grant_williams
```

There is a decent chance of:

```
grant_williams: PARSER FAILED — ValueError: parsed 0 rows — the JSON key names
have probably changed. Top-level keys seen: [...]
```

**That is the design working, not a bug.** A parser that silently returns zero
rows is indistinguishable from a quiet week, which is the worst failure an
archive can have, so every parser raises instead. The error names the keys it
actually found; put those into the `_PROB_KEYS` / `_SEAT_KEYS` / `_MARGIN_KEYS`
tuples at the top of `forecast/collect/parsers/grant_williams.py` and re-run.

Re-running costs nothing — parsers read from stored bytes, never the network.

---

## Step 5 · The rest of the sources

```bash
python3 forecast/collect/capture.py
python3 forecast/collect/parse.py --all
```

**Expected:** kalshi, polymarket, grant_williams, wikipedia, rcp and
race_to_the_wh all capture. Parsing will report `rcp` and `race_to_the_wh` as
`parser not written yet` — correct, those are HTML and need real bytes first.
Their captures are banking meanwhile, which is the whole point.

Kalshi is the one to watch: the discovery step reports how many series matched
the pattern. If it says `0 series matched`, widen `series_pattern` in the
registry and re-run.

---

## Step 6 · The Wikipedia backfill — once, ever

```bash
python3 forecast/collect/capture.py --only wikipedia --backfill
```

Walks revision history back to 1 January 2026. Several hundred requests, a few
minutes, rate-limited on purpose. **Wikipedia is the only source that recovers
the past** — everything else exists only from the day capture starts. Run it once
and never again.

---

## Step 7 · Check the privacy boundary before publishing anything

```bash
python3 forecast/collect/aggregate.py --check
```

Writes nothing. Read the tier summary it prints: every source should appear under
`individual`, `aggregate_only`, or `private`, and the last line should say
`publication audit: PASS`.

---

## Step 8 · Full local run, no push

```bash
./forecast/run.sh --from parse --no-push
```

Runs stages 2 through 5, commits locally, does not push. Then inspect what would
go public:

```bash
git show --stat HEAD
cat forecast/data/2026/site.json | head -40
```

**Verify by eye:** no `raw/` or `parsed/` paths in that commit. If you see any,
stop and tell me.

---

## Step 9 · Push, when you're happy

```bash
git push
```

From then on the weekly rhythm is one command:

```bash
./forecast/run.sh
```

---

## Not yet set up, and fine to defer

**The private archive repo.** Until it exists, `raw/` and `parsed/` live only on
your disk, gitignored. That is fine for testing but it is one disk failure from
losing captures that cannot be re-fetched. Worth doing before you rely on it.

**The daily GitHub Action.** Without it you get weekly granularity instead of
daily. Also fine to defer, but every day not captured is gone permanently, so
sooner is better than later.

---

## If something goes wrong

Everything is recorded. `forecast/data/2026/manifest.csv` has one row per source
per run with error text and notes. Start there.

Re-running is always safe: capture is idempotent (keyed on source plus date, so
a re-run overwrites rather than duplicating), and parse and aggregate regenerate
from scratch every time.
