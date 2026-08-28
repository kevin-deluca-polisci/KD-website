#!/usr/bin/env bash
#
# The weekly command. One entry point, five stages, each independently runnable.
#
#   ./forecast/run.sh                  everything, then commit + push the public tier
#   ./forecast/run.sh --from parse     skip fetching; reprocess what is already stored
#   ./forecast/run.sh --dry-run        show what would change, commit nothing
#   ./forecast/run.sh --no-push        do the work, commit locally, do not push
#   ./forecast/run.sh --no-sync        skip the private-archive pull and push
#   ./forecast/run.sh --restore-history  rebuild the seat-projection history
#                                      from the archived shards, then exit
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
RESTORE_HISTORY=0

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
    --restore-history) RESTORE_HISTORY=1; shift ;;
    -h|--help)  sed -n '2,18p' "$0"; exit 0 ;;
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
#
# The optional third argument is a basename PREFIX to skip. It exists for one
# family of files — see HISTORY_JSON below — and it is a prefix rather than an
# exact name because of what happened the first time it was exact: it excluded
# seat_projections_history.json and then cheerfully copied
# seat_projections_history.json.bak (20 MB) and a one-off
# seat_projections_history.json.pre-classpolling-cleanup.gz (38 MB) into the
# archive, where they were committed. Anything sitting beside the monolith
# under that name is a working copy of the same data and belongs in the archive
# no more than the monolith does.
copy_tree() {   # copy_tree SRC/ DEST/ [SKIP_PREFIX]
  local src="$1" dest="$2" skip="${3:-}"
  mkdir -p "$dest"
  if command -v rsync >/dev/null 2>&1; then
    if [[ -n "$skip" ]]; then
      rsync -a --exclude="${skip}*" "$src" "$dest"
    else
      rsync -a "$src" "$dest"
    fi
  else
    ( shopt -s dotglob nullglob
      for f in "$src"*; do
        [[ -n "$skip" && "$(basename "$f")" == "$skip"* ]] && continue
        cp -R "$f" "$dest" 2>/dev/null || true
      done )
  fi
}

# ---- the history travels as shards, not as one file -----------------------
# seat_projections_history.json is one JSON object holding every model-day we
# have. Daily backfill to 2025-01-20 took it from 59 MB to 348 MB.
#
# TWO SEPARATE PROBLEMS, AND GZIP ONLY SOLVED THE FIRST.
#
# Size: GitHub warns at 50 MB and HARD-REJECTS any single file over 100 MB, so
# a 348 MB file could never be pushed at all. Gzip fixed that — 33 MB.
#
# Growth: it did NOT fix this, and this is the one that would have hurt. Each
# day adds a date, which changes the compressed bytes from that point on, and
# gzip does not delta-compress. So every nightly sync committed a fresh ~33 MB
# blob. Sixty-eight nights to the election is about 2.2 GB of pack on top of
# the 225 MB already there.
#
# One file per date costs a day for a day: the nightly run changes one date, so
# one ~600 KB shard changes, roughly 40 MB across the whole stretch. Sharding
# was already in the tree for a different reason — a degraded run can only
# damage the day it ran, and `--verify` proves no shard ever dropped a
# (date, model) the manifest knows about — so the archive simply carries the
# shards and stops carrying the monolith in any form.
#
# The monolith stays LOCAL. Every other script reads it, seats.py rewrites it,
# and it is gitignored here. `--restore-history` rebuilds it on a fresh clone.
HISTORY_JSON="seat_projections_history.json"
MERGE_PY="forecast/history_merge.py"

shard_history() {   # refresh the per-date shards from the local monolith
  [[ -f "forecast/data/$CYCLE/model_private/$HISTORY_JSON" ]] || return 0
  [[ -f "$MERGE_PY" ]] || { echo "  WARNING: $MERGE_PY missing — shards not refreshed"; return 1; }
  # BARE. No --from and no --git: those union in older copies, which is a
  # recovery tool and not a nightly one, and it would resurrect rows that were
  # removed deliberately.
  #
  # No --prune either. Deleting a date's only surviving copy is not a thing a
  # nightly job should do unattended; --write reports stale shards and a person
  # decides.
  python3 "$MERGE_PY" --cycle "$CYCLE" --write | sed -n \
    -e 's/^  \([0-9]* shard(s) written.*\)/  \1/p' \
    -e '/STALE shard/,/--prune/p'
  return "${PIPESTATUS[0]}"
}

restore_history() {   # rebuild the monolith from the archive's shards
  raw_repo_ready || return 1
  local src="$RAW_REPO/$CYCLE/model_private/history"
  local dest_dir="forecast/data/$CYCLE/model_private"
  if [[ ! -d "$src" ]]; then
    echo "  no shard directory in the archive at:"
    echo "      $src"
    return 1
  fi
  # Refuse rather than overwrite. The local copy is the one seats.py has been
  # writing all cycle; the archive is a snapshot of some earlier run. Which is
  # newer is a question for a person, not for a default — the same accident
  # sync_raw_down() avoids by not mirroring model_private at all. history_merge
  # --unshard refuses too; this is the earlier and clearer of the two messages.
  if [[ -f "$dest_dir/$HISTORY_JSON" ]]; then
    echo "  $dest_dir/$HISTORY_JSON already exists — refusing to overwrite it."
    echo "  Move it aside first if you really mean to restore from the archive."
    return 1
  fi
  mkdir -p "$dest_dir/history"
  copy_tree "$src/" "$dest_dir/history/"
  echo "  copied $(ls "$dest_dir/history" | wc -l | tr -d ' ') shard(s) from the archive"
  python3 "$MERGE_PY" --cycle "$CYCLE" --unshard
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

  # THE HISTORY COMES DOWN TOO, BUT ONLY THE DAYS WE DO NOT ALREADY HAVE.
  #
  # For most of this project's life model_private was deliberately NOT mirrored
  # down at all, because an older archived copy overwriting a newer local one
  # is the accident the whole arrangement exists to prevent.
  #
  # That reasoning assumed one writer. The daily Action is a second, and it
  # writes dates this laptop never sees — any day the Action runs and nobody
  # runs locally. On 2026-08-28 the archive held 586 dates and the working tree
  # 585. That one self-healed because the missing date was the current day and
  # the next local run recomputed it. The day it does not self-heal, this tree
  # keeps a permanent hole and every later publish is built from the copy with
  # the hole in it.
  #
  # --fill-from-shards is the narrow version of mirroring down: it may ADD a
  # date we lack and may never REVISE one we hold, so the original accident
  # stays impossible. Everything else in model_private/ still does not travel
  # downward.
  if [[ -d "$RAW_REPO/$CYCLE/model_private/history" && -f "$MERGE_PY" ]]; then
    python3 "$MERGE_PY" --cycle "$CYCLE" \
      --fill-from-shards "$RAW_REPO/$CYCLE/model_private/history" \
      || echo "  WARNING: could not fill the history from the archive"
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
  # model_private/ TOO. This used to be a manual step and the manual step was
  # skipped, which is how the archive came to hold 543 model-days of seat
  # projections while the working tree held 806 — 88 of them surviving only
  # inside an old commit, recovered later by _history_merge.py. The tree is
  # gitignored HERE (it must never reach the public repo) and tracked THERE,
  # so nothing but this line moves it across.
  #
  # No --delete, as above. That is not full protection: a thin local run can
  # still overwrite a fatter archived FILE, which is exactly what happened
  # before. What actually protects it now is the shard layout — one file per
  # date under model_private/history/, so a bad run can only damage the day it
  # ran — plus `_history_merge.py --verify`, which fails loudly if a shard has
  # dropped a (date, model) the manifest already knows about. Run it after a
  # sync you have any doubt about.
  #
  # Deliberately NOT mirrored in sync_raw_down(). Copying model_private DOWN
  # would let an older archived copy overwrite a newer local one, which is the
  # same accident pointing the other way.
  #
  # The monolith is EXCLUDED and never travels — see HISTORY_JSON above. What
  # travels is model_private/history/, refreshed FIRST so the shards the
  # archive receives are the ones seats.py just wrote rather than whatever was
  # left there the last time somebody remembered to run the merge by hand.
  # That gap is real: on 2026-08-27 the shard directory was three months stale
  # and --verify was guarding a history that no longer resembled the live one.
  if [[ -d "forecast/data/$CYCLE/model_private" ]]; then
    shard_history || FAILED+=("shard-history")
    copy_tree "forecast/data/$CYCLE/model_private/" \
              "$RAW_REPO/$CYCLE/model_private/" "$HISTORY_JSON"
  fi
  git -C "$RAW_REPO" add -A
  if git -C "$RAW_REPO" diff --staged --quiet; then
    echo "  private archive already up to date"
    return 0
  fi
  # Last line of defence against GitHub's 100 MB per-file ceiling. Checking it
  # HERE, before the commit, turns an oversized file into a message on this
  # machine. Checking it nowhere — which is what happened until 2026-08-27 —
  # turns it into a rejected push with the work already committed, and a blob
  # that then has to be rewritten out of history to unstick the repo.
  #
  # 90 MB, not 100: gzip ratios drift, and a threshold you only hit on the day
  # you exceed the real limit is not a warning.
  local big
  big=$(git -C "$RAW_REPO" diff --staged --name-only -z \
        | while IFS= read -r -d '' f; do
            [[ -f "$RAW_REPO/$f" ]] || continue
            if [[ $(wc -c < "$RAW_REPO/$f") -gt 94371840 ]]; then
              printf '      %s  (%s)\n' "$f" "$(du -h "$RAW_REPO/$f" | cut -f1)"
            fi
          done)
  if [[ -n "$big" ]]; then
    echo "  REFUSING TO COMMIT: file(s) over 90 MB, which GitHub will reject:"
    echo "$big"
    echo "  Nothing was committed. Compress or gitignore these in the archive,"
    echo "  then re-run. (git -C \"\$RAW_REPO\" reset  to unstage.)"
    return 1
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

# ---- restore-only: unpack the archived history and stop ------------------
# Not a stage. A fresh clone has no seat_projections_history.json, because the
# archive carries per-date shards instead of the monolith; this is the one
# command that turns them back into the file every other script reads.
if [[ $RESTORE_HISTORY -eq 1 ]]; then
  banner "restore  <-  private archive   [PRIVATE]"
  restore_history
  exit $?
fi

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
  banner "3/5  models"
  # All of them, in dependency order, every run. This stage used to be
  # fundamentals alone, which quietly meant a local run.sh published a fresh
  # fundamentals number beside a stale polling one.
  #
  # academic.py sits AFTER polling.py and BEFORE seats.py, and the placement is
  # not cosmetic. BEW reads the RAW generic ballot out of polling_model.json,
  # so running it any earlier feeds it yesterday's poll or nothing at all — and
  # "nothing at all" is the quiet failure, because the model simply declines to
  # run and the academic category goes missing from the page without an error.
  #
  # seats.py is last: it reads what every step above just wrote.
  python3 forecast/model/state_pvi.py    --cycle "$CYCLE" || FAILED+=("state_pvi")
  # BEFORE fundamentals, which reads derived/approval.json for the aggregator
  # consensus it prints beside its own input. Ordering the other way round
  # leaves the model file a day behind the panel it quotes.
  python3 forecast/model/approval.py     --cycle "$CYCLE" || FAILED+=("approval")
  python3 forecast/model/fundamentals.py --cycle "$CYCLE" || FAILED+=("fundamentals")
  python3 forecast/model/polling.py      --cycle "$CYCLE" || FAILED+=("polling")
  python3 forecast/model/academic.py     --cycle "$CYCLE" || FAILED+=("academic")
  python3 forecast/model/seats.py        --cycle "$CYCLE" || FAILED+=("seats")
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
