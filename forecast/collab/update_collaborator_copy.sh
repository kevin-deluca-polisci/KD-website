#!/bin/bash
# Build the collaborator version of the U.S. Election Forecast Archive in a
# Dropbox folder. Run it on your Mac whenever you want to update the copy:
#
#   bash forecast/collab/update_collaborator_copy.sh
#   bash forecast/collab/update_collaborator_copy.sh "/path/to/shared folder"
#
# It downloads only the files it needs (the public archive tables and the
# private collaborator tables), writes them to <folder>/current/, and saves a
# dated zip of the same files in <folder>/snapshots/.
#
# Needs read access to the private repo kevin-deluca-polisci/plsc2219-raw with
# the GitHub token your Terminal already uses.
set -euo pipefail

DEST="${1:-$HOME/Library/CloudStorage/Dropbox/Yale/ElectionData/ForecastDataArchive}"
CACHE="${CACHE:-$HOME/.cache/forecast-archive}"
PUBLIC_REPO="${PUBLIC_REPO:-https://github.com/kevin-deluca-polisci/KD-website.git}"
PRIVATE_REPO="${PRIVATE_REPO:-https://github.com/kevin-deluca-polisci/plsc2219-raw.git}"
CYCLE=2026

# Download (or refresh) just one folder of a repo.
sparse_fetch() {   # url dir path...
  local url="$1" dir="$2"; shift 2
  if [ ! -d "$dir/.git" ]; then
    git clone --quiet --depth 1 --filter=blob:none --sparse "$url" "$dir"
    git -C "$dir" sparse-checkout set "$@"
  else
    git -C "$dir" sparse-checkout set "$@"
    git -C "$dir" fetch --quiet --depth 1 origin
    git -C "$dir" reset --quiet --hard FETCH_HEAD
  fi
}

mkdir -p "$CACHE"
echo "Downloading the public archive tables..."
sparse_fetch "$PUBLIC_REPO" "$CACHE/public" "forecast/archive/$CYCLE" "forecast/collab"
echo "Downloading the collaborator tables (private repo)..."
sparse_fetch "$PRIVATE_REPO" "$CACHE/private" "$CYCLE/model_private/pending"

PUB="$CACHE/public/forecast/archive/$CYCLE"
PRI="$CACHE/private/$CYCLE/model_private/pending"
DOCS="$CACHE/public/forecast/collab"
for f in "$PRI/forecasts_by_source_all.csv" "$PRI/category_averages_full.csv" \
         "$PUB/timeline.csv" "$PUB/approval.csv"; do
  if [ ! -f "$f" ]; then
    echo "Missing $f"
    echo "The daily job may not have written it yet. Nothing was changed."
    exit 1
  fi
done

DAY=$(date +%Y-%m-%d)
STAGE=$(mktemp -d)
cp "$PRI/forecasts_by_source_all.csv" "$STAGE/forecasts_by_source.csv"
cp "$PRI/category_averages_full.csv"  "$STAGE/category_averages.csv"
[ -f "$PRI/ratings.csv" ] && cp "$PRI/ratings.csv" "$STAGE/ratings.csv"
cp "$PUB/timeline.csv" "$PUB/approval.csv" "$STAGE/"
cp "$DOCS/README_COLLABORATORS.md" "$STAGE/README.md"
cp "$DOCS/DATA_USE.md" "$STAGE/DATA_USE.md"

{
  echo "Built: $DAY"
  echo "Public archive commit:  $(git -C "$CACHE/public" rev-parse --short HEAD)"
  echo "Private archive commit: $(git -C "$CACHE/private" rev-parse --short HEAD)"
  echo "Latest data date: $(tail -n +2 "$STAGE/timeline.csv" | cut -d, -f1 | sort | tail -n 1)"
} > "$STAGE/version.txt"

{
  echo "file,rows,bytes,sha256"
  for f in "$STAGE"/*.csv; do
    n=$(( $(wc -l < "$f") - 1 ))
    b=$(wc -c < "$f" | tr -d ' ')
    h=$(shasum -a 256 "$f" | cut -d' ' -f1)
    echo "$(basename "$f"),$n,$b,$h"
  done
} > "$CACHE/files.csv"
mv "$CACHE/files.csv" "$STAGE/files.csv"

mkdir -p "$DEST/current" "$DEST/snapshots"
rm -f "$DEST/current/"*
cp "$STAGE"/* "$DEST/current/"
( cd "$STAGE" && zip -q -r "$DEST/snapshots/forecast-archive-collaborators-$DAY.zip" . )
rm -rf "$STAGE"

echo
echo "Done. Updated: $DEST/current"
echo "Snapshot:      $DEST/snapshots/forecast-archive-collaborators-$DAY.zip"
cat "$DEST/current/version.txt"
