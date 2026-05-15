#!/usr/bin/env bash
# Fetch the upstream public repo for every submodule and show what's new vs the
# pinned commit. Does NOT auto-merge — fast-forwarding to upstream is a
# deliberate choice you make per submodule.
#
# Usage:
#   scripts/sync-upstream.sh                  # show new upstream commits
#   scripts/sync-upstream.sh --ff             # fast-forward each submodule to upstream/main
#
# After --ff, you still need to commit the new submodule pointers from the
# mother repo:
#   git add arxiv-latex-cleaner FactReview && git commit -m "chore: bump submodules"

set -euo pipefail

FF=0
if [[ "${1:-}" == "--ff" ]]; then
  FF=1
fi

# Run from the repo root regardless of where the script was invoked.
cd "$(git rev-parse --show-toplevel)"

git submodule foreach --quiet '
  echo "=== $name ==="
  git fetch --quiet upstream
  default_branch=$(git remote show upstream | sed -n "s/.*HEAD branch: //p")
  base="upstream/${default_branch}"
  pinned=$(git rev-parse HEAD)
  echo "pinned : $pinned"
  echo "upstream ${default_branch}: $(git rev-parse "$base")"
  ahead_behind=$(git rev-list --left-right --count "$pinned"..."$base")
  echo "ahead/behind upstream: $ahead_behind  (left=local-only, right=new-upstream)"
  new_commits=$(git log --oneline "$pinned".."$base" | head -10)
  if [[ -n "$new_commits" ]]; then
    echo "new commits on upstream/${default_branch}:"
    echo "$new_commits" | sed "s/^/  /"
  else
    echo "up to date"
  fi
  if [[ "'"$FF"'" == "1" && -n "$new_commits" ]]; then
    # Detached HEAD is expected for submodules; merge --ff-only is safe.
    git merge --ff-only "$base" >/dev/null && echo "fast-forwarded to $base"
  fi
  echo
'
