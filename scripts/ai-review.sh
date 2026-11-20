#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

branch="$(git branch --show-current)"
base="origin/main"

if [[ -z "$branch" || "$branch" == "main" ]]; then
    echo "STOP: review must run on a feature branch."
    exit 2
fi

if ! worktree_status="$(git status --porcelain --untracked-files=normal)"; then
    echo "STOP: Git working-tree check failed."
    exit 2
fi

if [[ -n "$worktree_status" ]]; then
    echo "STOP: commit changes before review."
    exit 2
fi

if ! git merge-base --is-ancestor "$base" HEAD; then
    echo "STOP: branch must include the latest local origin/main."
    exit 2
fi

if [[ "$(git rev-list --count "$base..HEAD")" -eq 0 ]]; then
    echo "STOP: no new commits to review."
    exit 2
fi

logdir="$(git rev-parse --git-path ai-reviews)"
mkdir -p "$logdir"
chmod 700 "$logdir"

log="$(mktemp "$logdir/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX.log")"

echo "Reviewing $branch against $base"
echo "Saving report to $log"

if codex exec --sandbox read-only review --base "$base" 2>&1 | tee "$log"; then
    echo "Review completed. Inspect findings before approval."
else
    echo "Codex failed. Check the saved report."
    exit 1
fi
