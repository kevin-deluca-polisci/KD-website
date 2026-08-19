#!/usr/bin/env bash
#
# The weekly command. One entry point, five stages, each independently runnable.
#
#   ./forecast/run.sh                  everything, then commit + push the public tier
#   ./forecast/run.sh --from parse     skip fetching; reprocess what is already stored
#   ./forecast/run.sh --dry-run        show what would change, commit nothing
#   ./forecast/run.sh --no-push        do the work, commit locally, do not push
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)     FROM="$2"; shift 2 ;;
    --cycle)    CYCLE="$2"; shift 2 ;;
    --dry-run)  DRY=1; shift ;;
    --no-push)  PUSH=0; shift ;;
    --backfill) BACKFILL="--backfill"; shift ;;
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

# ---- 5. publish -------------------------------------------------------------
if run_stage publish && [[ $DRY -eq 0 ]]; then
  banner "5/5  publish"
  python3 forecast/collect/publish.py --cycle "$CYCLE" || FAILED+=("publish")

  # Commit ONLY the public tier. raw/ and parsed/ are gitignored; this add list
  # is the second line of defence in case a .gitignore edit ever slips.
  git add forecast/data/"$CYCLE"/derived \
          forecast/data/"$CYCLE"/manifest.csv \
          forecast/data/"$CYCLE"/raw_manifest.csv \
          forecast/data/"$CYCLE"/site.json 2>/dev/null || true

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
