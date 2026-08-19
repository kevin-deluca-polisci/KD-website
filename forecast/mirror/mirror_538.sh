#!/usr/bin/env bash
#
# Mirror the surviving FiveThirtyEight repositories.
#
# WHY THIS EXISTS
#   ABC shut FiveThirtyEight down in March 2025. On 17 May 2026 Disney removed
#   the archived site as well; fivethirtyeight.com now redirects to ABC News.
#   Nate Silver offered to buy the remaining IP to restore the archive and was
#   refused.
#
#   The DATA survives, but only on GitHub, and only because nobody has decided
#   otherwise yet. That is precisely the fragility this whole project exists to
#   argue about. Mirroring it is both useful and the thesis statement.
#
# WHAT IT DOES
#   `git clone --mirror` of each source repo — full history, every branch, every
#   tag — then optionally pushes each to a repo under your own account. A mirror
#   clone is used rather than a GitHub fork because forks live inside the
#   upstream's fork network, and the whole point is to not depend on the
#   upstream continuing to exist.
#
# USAGE
#   ./mirror_538.sh                          # clone only, into ./538-mirror/
#   ./mirror_538.sh --push kevin-deluca-polisci
#   ./mirror_538.sh --dest ~/data/538-mirror
#
#   With --push you must have `gh` authenticated (`gh auth login`) or the target
#   repos already created and your SSH key working.
#
# AFTERWARDS
#   Consider a Harvard Dataverse deposit of the combined bundle under the
#   original CC BY 4.0 terms with attribution to FiveThirtyEight. No such
#   deposit currently exists, and it is what converts a mirror that depends on
#   your GitHub account into something citable that does not.

set -euo pipefail

DEST="./538-mirror"
PUSH_ORG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --push) PUSH_ORG="$2"; shift 2 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# repo|local name|what it is|licence
REPOS=(
  "fivethirtyeight/data|fivethirtyeight-data|The main 538 data repo. 17.4k stars, 1,475 commits. Still live as of 19 Aug 2026.|CC BY 4.0 (data) / MIT (code)"
  "simonw/fivethirtyeight-polls|fivethirtyeight-polls|Simon Willison's scraper mirror of the LIVE polling data, which 538 never committed to its own repo. Fills the single biggest gap. 281 commits.|CC BY 4.0"
  "Turn-Left-Now/FiveThirtyEight-Archive|fivethirtyeight-archive|Defensive snapshot of 28 538 repos taken right after the March 2025 shutdown announcement.|mixed; per-repo licences preserved"
)

mkdir -p "$DEST"
cd "$DEST"
echo "Mirroring into: $(pwd)"
echo

MANIFEST="MIRROR_MANIFEST.md"
{
  echo "# FiveThirtyEight mirror"
  echo
  echo "Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "By: PLSC 2219 Forecast Archive, Yale University"
  echo
  echo "Mirrored because ABC/Disney removed the FiveThirtyEight archives on"
  echo "17 May 2026. These repositories survive at GitHub's and the owners'"
  echo "discretion. Original attribution and licences are preserved below."
  echo
  echo "| Repo | Local | Licence | Description |"
  echo "|---|---|---|---|"
} > "$MANIFEST"

FAILED=()

for entry in "${REPOS[@]}"; do
  IFS='|' read -r repo name desc lic <<< "$entry"
  echo "─────────────────────────────────────────────────────────────"
  echo "  $repo"
  echo "  $desc"

  # Each repo is isolated. One dead upstream must not abort the whole mirror.
  if [[ -d "${name}.git" ]]; then
    echo "  → already mirrored; fetching updates"
    if ! git --git-dir="${name}.git" remote update --prune; then
      echo "  ✗ update failed" >&2; FAILED+=("$repo"); continue
    fi
  else
    if ! git clone --mirror "https://github.com/${repo}.git" "${name}.git"; then
      echo "  ✗ clone failed — upstream may already be gone" >&2
      FAILED+=("$repo"); continue
    fi
  fi

  SIZE=$(du -sh "${name}.git" | cut -f1)
  COMMITS=$(git --git-dir="${name}.git" rev-list --all --count 2>/dev/null || echo "?")
  HEAD_DATE=$(git --git-dir="${name}.git" log -1 --format=%cI 2>/dev/null || echo "?")
  echo "  ✓ $SIZE, $COMMITS commits, last commit $HEAD_DATE"
  echo "| [$repo](https://github.com/$repo) | \`${name}.git\` | $lic | $desc |" >> "$MANIFEST"

  if [[ -n "$PUSH_ORG" ]]; then
    TARGET="${PUSH_ORG}/${name}"
    echo "  → pushing to $TARGET"
    if command -v gh >/dev/null 2>&1; then
      gh repo view "$TARGET" >/dev/null 2>&1 || \
        gh repo create "$TARGET" --public \
          --description "Mirror of ${repo} — ${lic}. Archived $(date -u +%Y-%m-%d)." \
        || echo "  ! could not create $TARGET; create it by hand and re-run"
    fi
    if ! git --git-dir="${name}.git" push --mirror "git@github.com:${TARGET}.git"; then
      echo "  ✗ push failed for $TARGET" >&2; FAILED+=("$repo push")
    else
      echo "  ✓ pushed"
    fi
  fi
  echo
done

{
  echo
  echo "## Provenance"
  echo
  echo "Each directory is a bare \`git clone --mirror\`, so full history, all"
  echo "branches, and all tags are preserved. Verify any repo with:"
  echo
  echo '```'
  echo "git --git-dir=<name>.git log --oneline | head"
  echo "git --git-dir=<name>.git rev-list --all --count"
  echo '```'
  echo
  echo "## What is NOT here"
  echo
  echo "The interactive forecasts and the editorial archive are gone and cannot"
  echo "be recovered from these repos. fivethirtyeightindex.com indexes roughly"
  echo "38,593 articles via Internet Archive captures, but text and images only."
} >> "$MANIFEST"

echo "═════════════════════════════════════════════════════════════"
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "COMPLETED WITH FAILURES: ${FAILED[*]}"
  echo "Manifest: $(pwd)/$MANIFEST"
  exit 1
fi
echo "ALL MIRRORS OK"
echo "Manifest: $(pwd)/$MANIFEST"
echo
echo "Next: consider depositing the bundle to Harvard Dataverse under the"
echo "original CC BY 4.0 terms. No such deposit currently exists."
