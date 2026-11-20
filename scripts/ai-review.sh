#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

branch="$(git branch --show-current)"
base="origin/main"

if [[ "$branch" != feature/* ]]; then
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

logdir="$(git rev-parse --absolute-git-dir)/ai-reviews"
mkdir -p "$logdir"
chmod 700 "$logdir"

log="$(mktemp "$logdir/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX.log")"

echo "Reviewing $branch against $base"
echo "Saving report to $log"

reviewdir="$(mktemp -d "${TMPDIR:-/tmp}/lpk-review.XXXXXXXX")"

cleanup() {
    git worktree remove --force "$reviewdir" >/dev/null 2>&1 || true
    rmdir "$reviewdir" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git worktree add --detach "$reviewdir" HEAD >/dev/null

if (
    cd "$reviewdir"
    codex exec --sandbox read-only review --base "$base" >"$log" 2>/dev/null
); then
    cat "$log"
    echo "Review completed. Inspect findings before approval."
else
    echo "Codex failed. Review not approved."
    exit 1
fi
