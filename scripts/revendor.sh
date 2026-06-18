#!/usr/bin/env bash
# Re-vendor a bundled tool from a pinned upstream commit.
#
# This repo vendors arxiv-latex-cleaner and FactReview as plain source (no git
# submodules). Use this to refresh a subtree to a newer upstream revision.
#
# Usage:
#   scripts/revendor.sh <tool> [<commit-ish>]
#
#   <tool>        arxiv-latex-cleaner | FactReview
#   <commit-ish>  upstream ref to vendor (default: the tool's default branch HEAD)
#
# After it finishes:
#   - update the pinned commit recorded in NOTICE (and README), and
#   - if you re-vendored FactReview, RE-APPLY and RE-DOCUMENT any local
#     modifications, then update FactReview/CHANGES.md (AGPL §5a).
#
# NOTE: this OVERWRITES the subtree to match upstream (rsync --delete). Local
# code changes in the subtree are lost — that is the point of a clean re-vendor.
# FactReview/CHANGES.md is preserved so the change log survives.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

case "${1:-}" in
  arxiv-latex-cleaner) URL="https://github.com/google-research/arxiv-latex-cleaner" ;;
  FactReview)          URL="https://github.com/DEFENSE-SEU/FactReview" ;;
  *)
    echo "Usage: scripts/revendor.sh <arxiv-latex-cleaner|FactReview> [<commit-ish>]" >&2
    exit 2
    ;;
esac
TOOL="$1"
REF="${2:-}"

command -v rsync >/dev/null 2>&1 || { echo "ERROR: rsync is required." >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo ">>> cloning $URL"
git clone --quiet "$URL" "$TMP/src"
if [[ -n "$REF" ]]; then
  echo ">>> checking out $REF"
  git -C "$TMP/src" checkout --quiet "$REF"
fi
NEW_SHA="$(git -C "$TMP/src" rev-parse HEAD)"

echo
echo "About to overwrite ./$TOOL with upstream @ ${NEW_SHA}."
echo "Local modifications in ./$TOOL (except CHANGES.md) will be discarded."
read -r -p "Continue? [y/N]: " ANS
[[ "${ANS:-n}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

rsync -a --delete \
  --exclude='.git' \
  --exclude='CHANGES.md' \
  "$TMP/src/" "./$TOOL/"

echo
echo ">>> re-vendored $TOOL @ $NEW_SHA"
echo "    next steps:"
echo "      1. update the pinned commit for $TOOL in NOTICE (and README)"
if [[ "$TOOL" == "FactReview" ]]; then
  echo "      2. re-apply local modifications and update FactReview/CHANGES.md (AGPL §5a)"
fi
echo "      3. git add $TOOL && git commit"
