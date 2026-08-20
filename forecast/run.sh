#!/usr/bin/env bash
#
# The weekly command. One entry point, five stages, each independently runnable.
#
#   ./forecast/run.sh                  everything, then commit + push the public tier
#   ./forecast/run.sh --from parse     skip fetching; reprocess what is already stored
#   ./forecast/run.sh --dry-run        show what would change, commit nothing
#   ./forecast/run.sh --no-push        do the work, commit locally, do not push
#   ./forecast/run.sh --no-sync        skip the private-archive pull and push
#
# The private archive lives outside Dropbox. Clone it once before the first run:
#   git clone https://github.com/kevin-deluca-polisci/plsc2219-raw.git \
#     "$HOME/Documents/Claude/nondropbox data/plsc2219-raw"
#
# Only stage 1 touches anyone else's server. That is why you can re-run stages
# 2-5 as often as you like while iterating on a chart or fixing a parser
# without hitting Kalshi forty times in an afternoon.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

CYCLE=2026
FROM=capture
DRY=0
PUSH=1
BACKFILL=""
SYNC=1

# The private archive: raw captures and per-forecaster parsed values, which
# cannot be public during the cycle. Deliberately OUTSIDE Dropbox — Dropbox
# syncs .git file-by-file with no consistent snapshot and can corrupt an index
# mid-write. Override with PLSC_RAW_REPO if you move it.
RAW_REPO="${PLSC_RAW_REPO:-$HOME/Documents/Claude/nondropbox data/plsc2219-raw}"
RAW_REMOTE="https://github.com/kevin-deluca-polisci/plsc2219-raw.git"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)     FROM="$2"; shift 2 ;;
    --cycle)    CYCLE="$2"; shift 2 ;;
    --dry-run)  DRY=1; shift ;;
    --no-push)  PUSH=0; shift ;;
    --backfill) BACKFILL="--backfill"; shift ;;
    --no-sync)  SYNC=0; shift ;;
    -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

stage_num() { case "$1" in capture) echo 1;; parse) echo 2;; model) echo 3;;
                           aggregate) echo 4;; publish) echo 5;; *) echo 0;; esac; }
FROM_N=$(stage_num "$FROM")
run_stage() { [[ $(stage_num "$1") -ge $FROM_N ]]; }

banner() { printf '\n\033[1m━━━ %s ━━━\033[0m\n' "$1"; }
FAILED=()

# ---- private archive sync -------------------------------------------------
# Two-way on purpose. Down: the daily GitHub Action captures into the private
# repo, so the newest bytes are there, not here. Up: some sources are collected
# only locally — Cook PVI is hand-entered, and any source that blocks datacentre
# IPs has to run from this machine — and those would otherwise exist on exactly
# one disk, which is the thing this whole arrangement is meant to avoid.

# Copy a directory tree, additively. rsync ships with macOS, but falling back to
# cp keeps this working on a bare container or a stripped-down Linux box — the
# archive should never fail to sync because a utility is missing.
copy_tree() {   # copy_tree SRC/ DEST/
  local src="$1" dest="$2"
  mkdir -p "$dest"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$src" "$dest"
  else
    ( shopt -s dotglob nullglob; cp -R "$src"* "$dest" 2>/dev/null || true )
  fi
}

raw_repo_ready() {
  # GitHub Desktop appends the repository name to whatever local path you give
  # it, so pointing it at ".../plsc2219-raw" produces ".../plsc2219-raw/plsc2219-raw".
  # Detect that rather than making anyone move directories around.
  if [[ ! -d "$RAW_REPO/.git" && -d "$RAW_REPO/$(basename "$RAW_REPO")/.git" ]]; then
    RAW_REPO="$RAW_REPO/$(basename "$RAW_REPO")"
    echo "  (found the clone nested one level deeper — using $RAW_REPO)"
  fi
  if [[ ! -d "$RAW_REPO/.git" ]]; then
    echo "  private archive not found at:"
    echo "      $RAW_REPO"
    echo
    echo "  Clone it once, either in GitHub Desktop (File > Clone repository >"
    echo "  URL > kevin-deluca-polisci/plsc2219-raw, and set the local path to"
    echo "  the folder above), or on the command line:"
    echo
    echo "      git clone $RAW_REMOTE \\"
    echo "        \"$RAW_REPO\""
    echo
    return 1
  fi
  return 0
}

sync_raw_down() {
  raw_repo_ready || return 1
  echo "  pulling $RAW_REPO"
  # A failed pull is a warning, not a stop. Being offline, or having a conflict
  # to resolve, must not prevent parsing bytes that are already on disk — the
  # whole point of the phase split is that stages 2-5 never need the network.
  #
  # GIT_TERMINAL_PROMPT=0 is the important part. Without it, git BLOCKS on
  # "Username for 'https://github.com':" when no credential helper is set —
  # which hangs the script here, and would hang a cron job forever.
  if ! GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/true \
       git -C "$RAW_REPO" pull --ff-only --quiet 2>/dev/null; then
    echo "  WARNING: pull failed. Continuing with the local clone as it stands."
    echo "           If this says nothing changed but you expect new captures,"
    echo "           git has no stored credential for that repo. Fix once with:"
    echo "               git config --global credential.helper osxkeychain"
    echo "               git -C \"$RAW_REPO\" pull      # username + a PAT as the password"
    echo "           after which it is cached and this stops happening."
  fi
  if [[ -d "$RAW_REPO/$CYCLE/raw" ]]; then
    mkdir -p "forecast/data/$CYCLE/raw"
    # NO --delete: local-only captures must survive a sync from the remote.
    copy_tree "$RAW_REPO/$CYCLE/raw/" "forecast/data/$CYCLE/raw/"
    echo "  raw/ now has $(find "forecast/data/$CYCLE/raw" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') sources"
  else
    echo "  (private repo has no $CYCLE/raw yet — first run?)"
  fi
}

sync_raw_up() {
  raw_repo_ready || return 1
  [[ -d "forecast/data/$CYCLE/raw" ]] || return 0
  mkdir -p "$RAW_REPO/$CYCLE"
  # NO --delete here either: never let a local gap erase the remote archive.
  copy_tree "forecast/data/$CYCLE/raw/" "$RAW_REPO/$CYCLE/raw/"
  [[ -d "forecast/data/$CYCLE/parsed" ]] && \
    copy_tree "forecast/data/$CYCLE/parsed/" "$RAW_REPO/$CYCLE/parsed/"
  git -C "$RAW_REPO" add -A
  if git -C "$RAW_REPO" diff --staged --quiet; then
    echo "  private archive already up to date"
    return 0
  fi
  local n
  n=$(git -C "$RAW_REPO" diff --staged --name-only | wc -l | tr -d " ")
  git -C "$RAW_REPO" commit -q -m "raw: local sync $(date -u +%Y-%m-%d) (${n} files)"
  if [[ $PUSH -eq 1 ]]; then
    if GIT_TERMINAL_PROMPT=0 git -C "$RAW_REPO" push --quiet 2>/dev/null; then
      echo "  pushed ${n} files to the private archive"
    else
      echo "  push failed (no stored credential?) — committed locally, not pushed"
    fi
  else
    echo "  committed ${n} files locally (--no-push)"
  fi
}

# ---- 0. sync down from the private archive --------------------------------
if [[ $SYNC -eq 1 && $DRY -eq 0 ]]; then
  banner "0/5  sync  <-  private archive   [PRIVATE]"
  sync_raw_down || FAILED+=("sync-down")
fi

# ---- 1. capture (the only stage that touches the network) -----------------
if run_stage capture; then
  banner "1/5  capture  →  forecast/data/$CYCLE/raw/   [PRIVATE]"
  if [[ $DRY -eq 1 ]]; then
    python3 forecast/collect/capture.py --cycle "$CYCLE" --dry-run $BACKFILL
  else
    python3 forecast/collect/capture.py --cycle "$CYCLE" $BACKFILL || FAILED+=("capture")
  fi
fi

# ---- 2. parse (never the network) -----------------------------------------
if run_stage parse; then
  banner "2/5  parse  →  forecast/data/$CYCLE/parsed/   [PRIVATE]"
  python3 forecast/collect/parse.py --cycle "$CYCLE" --all || FAILED+=("parse")
fi

# ---- 3. our fundamentals model --------------------------------------------
if run_stage model; then
  banner "3/5  fundamentals model"
  python3 forecast/model/fundamentals.py --cycle "$CYCLE" || FAILED+=("model")
fi

# ---- 4. aggregate: the privacy boundary ------------------------------------
if run_stage aggregate; then
  banner "4/5  aggregate  →  forecast/data/$CYCLE/derived/   [PUBLIC]"
  if [[ $DRY -eq 1 ]]; then
    python3 forecast/collect/aggregate.py --cycle "$CYCLE" --check
  else
    # A failed publication audit must stop the run. Publishing something the
    # tier forbids is the one error here that cannot be taken back.
    python3 forecast/collect/aggregate.py --cycle "$CYCLE" || {
      echo "PUBLICATION AUDIT FAILED — refusing to continue." >&2; exit 1; }
  fi
fi

# ---- 4b. sync back up, so local-only captures are not on one disk ---------
if [[ $SYNC -eq 1 && $DRY -eq 0 ]]; then
  banner "4b   sync  ->  private archive   [PRIVATE]"
  sync_raw_up || FAILED+=("sync-up")
fi

# ---- 5. publish -------------------------------------------------------------
if run_stage publish && [[ $DRY -eq 0 ]]; then
  banner "5/5  publish"
  python3 forecast/collect/publish.py --cycle "$CYCLE" || FAILED+=("publish")

  # Commit ONLY the public tier. This stays an explicit allowlist rather than
  # `git add -A` on purpose: gitignore is the first line of defence, not the
  # only one, and a private file that slips past it should still not be swept
  # into a public commit. (It happened — model_private/ was tracked and pushed
  # because the live .gitignore had drifted from data_gitignore.txt.)
  #
  # assets/forecast_<cycle>.json is what Hugo renders the page from. It is
  # produced by publish.py from derived/ only, and without it on this list the
  # site silently freezes at whatever snapshot was last committed.
  #
  # raw/ and parsed/ are gitignored; this add list
  # is the second line of defence in case a .gitignore edit ever slips.
  git add forecast/data/"$CYCLE"/derived \
          forecast/data/"$CYCLE"/manifest.csv \
          forecast/data/"$CYCLE"/raw_manifest.csv \
          forecast/data/"$CYCLE"/site.json \
          assets/forecast_"$CYCLE".json 2>/dev/null || true

  if git diff --staged --quiet; then
    echo "  nothing changed"
  else
    N=$(git diff --staged --name-only | wc -l | tr -d ' ')
    git commit -m "forecast: weekly update $(date -u +%Y-%m-%d) (${N} files)"
    if [[ $PUSH -eq 1 ]]; then
      git pull --rebase --autostash && git push && echo "  pushed"
    else
      echo "  committed locally (--no-push)"
    fi
  fi
fi

printf '\n'
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "COMPLETED WITH FAILURES: ${FAILED[*]}"
  echo "See forecast/data/$CYCLE/manifest.csv"
  exit 1
fi
echo "OK"
