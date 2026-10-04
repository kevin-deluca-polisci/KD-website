#!/bin/bash
# Build the collaborator version of the U.S. Election Forecast Archive
# (Version 0.1, pre-release) in a Dropbox folder. Run it on your Mac whenever
# you want to update the copy:
#
#   bash forecast/collab/update_collaborator_copy.sh
#   bash forecast/collab/update_collaborator_copy.sh "/path/to/shared folder"
#
# The folder it writes is a COPY for sharing (read-only for collaborators). It
# is never read by the daily job; the working data stay in the two GitHub
# repositories.
#
# It downloads what it needs into a cache (~/.cache/forecast-archive), then
# writes to <folder>/current/:
#   - the processed tables (public archive + collaborator tables)
#   - raw/    every captured file, as received, except the sources below
# and saves a dated zip of the processed tables in <folder>/snapshots/.
#
# NOT COPIED (private tier; their terms do not allow sharing):
#   cook_pvi, cook_state_pvi, dra, grant_williams
#
# Needs read access to the private repo kevin-deluca-polisci/plsc2219-raw with
# the GitHub token your Terminal already uses. The first run downloads about
# 2 GB; later runs fetch only what changed.
set -euo pipefail

DEST="${1:-$HOME/Library/CloudStorage/Dropbox/Yale/ElectionData/ForecastDataArchive}"
CACHE="${CACHE:-$HOME/.cache/forecast-archive}"
PUBLIC_REPO="${PUBLIC_REPO:-https://github.com/kevin-deluca-polisci/KD-website.git}"
PRIVATE_REPO="${PRIVATE_REPO:-https://github.com/kevin-deluca-polisci/plsc2219-raw.git}"
CYCLE=2026
VERSION="0.1"
EXCLUDE="cook_pvi cook_state_pvi dra grant_williams"

# Download (or refresh) just some folders of a repo.
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
echo "Downloading the collaborator tables and raw files (private repo)..."
sparse_fetch "$PRIVATE_REPO" "$CACHE/private" \
  "$CYCLE/model_private/pending" "$CYCLE/raw"

PUB="$CACHE/public/forecast/archive/$CYCLE"
PRI="$CACHE/private/$CYCLE/model_private/pending"
RAW="$CACHE/private/$CYCLE/raw"
DOCS="$CACHE/public/forecast/collab"
for f in "$PRI/forecasts_by_source_all.csv" "$PRI/category_averages_full.csv" \
         "$PUB/timeline.csv" "$PUB/approval.csv"; do
  if [ ! -f "$f" ]; then
    echo "Missing $f"
    echo "The daily job may not have written it yet. Nothing was changed."
    exit 1
  fi
done
[ -d "$RAW" ] || { echo "Missing $RAW. Nothing was changed."; exit 1; }

DAY=$(date +%Y-%m-%d)
STAGE=$(mktemp -d)
cp "$PRI/forecasts_by_source_all.csv" "$STAGE/forecasts_by_source.csv"
cp "$PRI/category_averages_full.csv"  "$STAGE/category_averages.csv"
[ -f "$PRI/ratings.csv" ] && cp "$PRI/ratings.csv" "$STAGE/ratings.csv"
cp "$PUB/timeline.csv" "$PUB/approval.csv" "$STAGE/"
cp "$DOCS/README_COLLABORATORS.md" "$STAGE/README.md"
cp "$DOCS/DATA_USE.md" "$STAGE/DATA_USE.md"

{
  echo "U.S. Election Forecast Archive: 2026 Midterm Elections"
  echo "Version $VERSION (pre-release, for collaborators only)"
  echo "Built: $DAY"
  echo "Public archive commit:  $(git -C "$CACHE/public" rev-parse --short HEAD)"
  echo "Private archive commit: $(git -C "$CACHE/private" rev-parse --short HEAD)"
  echo "Latest data date: $(tail -n +2 "$STAGE/timeline.csv" | cut -d, -f1 | sort | tail -n 1)"
  echo "Sources not included in raw/: $EXCLUDE"
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

# Processed tables: replace the top-level files only (raw/ is
# updated in place below, so it is not copied twice).
find "$DEST/current" -maxdepth 1 -type f -delete
cp "$STAGE"/* "$DEST/current/"
( cd "$STAGE" && zip -q -r "$DEST/snapshots/forecast-archive-v${VERSION}-$DAY.zip" . )
rm -rf "$STAGE"

# Raw files, as received. rsync copies only what changed since the last run.
echo "Copying raw files..."
RSYNC_EXCL=()
for s in $EXCLUDE; do RSYNC_EXCL+=(--exclude "/$s/"); done
mkdir -p "$DEST/current/raw"
rsync -a --delete "${RSYNC_EXCL[@]}" "$RAW/" "$DEST/current/raw/"

# parsed/ was in the first 0.1 build; the private archive stopped updating it
# on 2026-09-01, so it is no longer copied. The tables above hold every value.
rm -rf "$DEST/current/parsed"

# A list of every raw file with its size and SHA-256.
echo "Listing raw files..."
python3 - "$DEST/current/raw" "$DEST/current/raw_files.csv" <<'PY'
import csv, hashlib, os, sys
root, out = sys.argv[1], sys.argv[2]
with open(out, "w", newline="") as fo:
    w = csv.writer(fo, lineterminator="\n")
    w.writerow(["path", "bytes", "sha256"])
    for d, _dirs, files in sorted(os.walk(root)):
        for name in sorted(files):
            if name.startswith("."):
                continue
            p = os.path.join(d, name)
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            w.writerow([os.path.relpath(p, root), os.path.getsize(p), h.hexdigest()])
PY

echo
echo "Done. Updated: $DEST/current"
echo "Snapshot:      $DEST/snapshots/forecast-archive-v${VERSION}-$DAY.zip"
cat "$DEST/current/version.txt"
