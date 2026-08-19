# Setting up the private archive repo

Fifteen minutes, once. After this the daily capture runs itself in GitHub's cloud
and your laptop can be shut.

**What it's for:** raw captures and per-forecaster parsed values can't go in the
public repo during the cycle, but they also shouldn't live only on one disk —
the past can't be re-fetched. A private repo gives offsite backup, git history,
and a one-click flip to public when you release the archive.

---

## Part 1 · Create the repo (github.com)

1. Go to **github.com** → the **+** menu, top right → **New repository**
2. **Owner:** `kevin-deluca-polisci`
3. **Repository name:** `plsc2219-raw`
4. **Visibility: Private** ← the whole point; double-check this
5. Tick **Add a README file** (an empty repo has no branch, and the workflow
   expects one to exist)
6. **Create repository**

Note whether the default branch is `main` or `master`. GitHub defaults to `main`;
the workflow assumes `main`. If yours says `master`, change the last line of the
"Push raw to the private archive" step in the workflow from `HEAD:main` to
`HEAD:master`.

---

## Part 2 · Create the access token

The Action lives in your **public** repo but has to write to the **private** one,
so it needs a token. Use a fine-grained token scoped to that one repository —
not a classic token, which would grant access to everything you own.

1. **github.com** → your avatar (top right) → **Settings**
2. Bottom of the left sidebar → **Developer settings**
3. **Personal access tokens** → **Fine-grained tokens** → **Generate new token**
4. Fill in:
   - **Token name:** `plsc2219-raw-write`
   - **Expiration:** pick a date past the election. **Set a calendar reminder for
     a week before it expires** — an expired token means silent capture failures,
     and silent is the bad kind.
   - **Resource owner:** `kevin-deluca-polisci`
   - **Repository access:** *Only select repositories* → choose **`plsc2219-raw`**
   - **Permissions** → *Repository permissions* → find **Contents** → set to
     **Read and write**. Leave everything else alone.
5. **Generate token**
6. **Copy it now.** GitHub shows it exactly once.

---

## Part 3 · Give the token to the public repo

1. Go to **github.com/kevin-deluca-polisci/KD-website**
2. **Settings** tab → left sidebar → **Secrets and variables** → **Actions**
3. **New repository secret**
   - **Name:** `RAW_ARCHIVE_TOKEN` (exactly this; the workflow looks for it)
   - **Secret:** paste the token
4. **Add secret**

The value is write-only from here on — you can replace it but never read it back.

---

## Part 4 · Install the workflow

The file is already in your repo, just not in the place GitHub looks:

```bash
cd ~/Library/CloudStorage/Dropbox/Claude/website/KD-website
mkdir -p .github/workflows
cp forecast/workflows/forecast-capture.yml .github/workflows/
```

Check the top of `.github/workflows/forecast-capture.yml` and confirm:

```yaml
env:
  RAW_ARCHIVE_REPO: kevin-deluca-polisci/plsc2219-raw
```

---

## Part 5 · Commit and push (GitHub Desktop)

1. Open **GitHub Desktop**, select the **KD-website** repository
2. You should see the whole `forecast/` directory as new files, plus
   `.github/workflows/forecast-capture.yml`
3. **Before committing, check the changed-files list.** You should see
   `forecast/data/2026/derived/…` but **NOT** anything under
   `forecast/data/2026/raw/` or `parsed/`. If raw files appear, stop — the
   gitignore isn't being picked up, and nothing should be pushed until it is.
4. Summary: `forecast: collector, fundamentals model, daily capture workflow`
5. **Commit to main** → **Push origin**

---

## Part 6 · Test it before trusting it

1. **github.com/kevin-deluca-polisci/KD-website** → **Actions** tab
2. Left sidebar → **Forecast capture (daily)**
3. **Run workflow** ▾ → tick **dry_run** → **Run workflow**

A dry run fetches nothing and writes nothing; it only proves the workflow parses
and the registry validates. Watch it go green.

Then run it again with **dry_run unticked**. That one does the real thing. When
it finishes, check:

- **github.com/kevin-deluca-polisci/plsc2219-raw** now has a `2026/raw/…` tree
- **KD-website** has a new bot commit touching only `raw_manifest.csv` and
  `manifest.csv` — hashes, not content

If the second one fails at the push step, it's almost always the token: wrong
scope, wrong repo, or the branch is `master` not `main`.

---

## What you end up with

| | Runs | Does | Where it writes |
|---|---|---|---|
| **Daily** | GitHub Action, 11:00 UTC | capture only | raw → private repo; hashes → public repo |
| **Weekly** | you, one command | parse, model, aggregate, publish | derived → public repo |

`./forecast/run.sh --from parse` becomes your weekly command once the Action is
running, because capture already happened. Stage 1 is only there for when you
want a snapshot right now.

---

## Two things to remember

**Set the token-expiry reminder.** An expired token fails the push step while
capture still "succeeds", so the Action can look healthy while quietly archiving
nothing. The manifest will show it; nothing else will.

**Check the Actions tab occasionally.** GitHub disables scheduled workflows in
repositories with no activity for 60 days. You'll be committing weekly, so this
shouldn't bite, but it is the classic way a cron job dies unnoticed.
