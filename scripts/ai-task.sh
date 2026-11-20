#!/usr/bin/env bash
# Supervised review-and-fix loop for one Jira task (LPK-251). One run = one correction attempt:
#   1. Codex reviews the committed branch (scripts/ai-review.sh: read-only, clean worktree, no .env);
#   2. a human reads the findings and decides whether Claude should fix them;
#   3. Claude edits the working tree within the remaining budget; it never commits or pushes.
# The human reviews the diff, commits, and decides when to stop. Pushing and opening the draft PR stay manual.
# Usage: scripts/ai-task.sh LPK-123   (on a branch feature/lpk-123-...)
set -euo pipefail

MAX_ATTEMPTS=5
BUDGET_USD="${AI_TASK_BUDGET_USD:-5.00}"  # Claude spend only; Codex usage is not measured (see docs)

key="${1:-}"
if [[ ! "$key" =~ ^LPK-[0-9]+$ ]]; then
    echo "Usage: scripts/ai-task.sh LPK-<number>"
    exit 2
fi

cd "$(git rev-parse --show-toplevel)"
branch="$(git branch --show-current)"
if [[ "$branch" != "feature/${key,,}-"* ]]; then
    echo "STOP: run on the task's own branch, feature/${key,,}-<topic>."
    exit 2
fi

state="$(git rev-parse --absolute-git-dir)/ai-tasks/$key"
mkdir -p "$state"
chmod 700 "$state"
attempts="$(cat "$state/attempts" 2>/dev/null || echo 0)"
spent="$(cat "$state/spent_usd" 2>/dev/null || echo 0)"
remaining="$(python3 -c 'import sys; from decimal import Decimal as D; print(max(D(sys.argv[1]) - D(sys.argv[2]), D(0)))' \
    "$BUDGET_USD" "$spent")"

echo "$key: attempt $((attempts + 1)) of $MAX_ATTEMPTS, Claude spend so far \$$spent of \$$BUDGET_USD"
if (( attempts >= MAX_ATTEMPTS )); then
    echo "STOP: $MAX_ATTEMPTS correction attempts used. Finish by hand or reset $state deliberately."
    exit 3
fi
if python3 -c 'import sys; from decimal import Decimal as D; sys.exit(0 if D(sys.argv[1]) <= 0 else 1)' "$remaining"; then
    echo "STOP: the \$$BUDGET_USD Claude budget for $key is used."
    exit 3
fi

attempts=$((attempts + 1))
echo "$attempts" >"$state/attempts"  # counted before any paid call, so a crash cannot reset it
review="$state/review-$attempts.log"
scripts/ai-review.sh | tee "$review"

read -r -p "Send these findings to Claude for a fix? [y/N] " answer
if [[ "$answer" != [yY] ]]; then
    echo "No fix requested. When ready: git push -u origin $branch && gh pr create --draft --base main"
    exit 0
fi

result="$state/claude-$attempts.json"
prompt="Jira $key, correction attempt $attempts of $MAX_ATTEMPTS. A Codex review of this branch found the issues
below. Fix only the valid ones, following CLAUDE.md; say which you reject and why. Run the targeted tests with
backend/venv/bin/python backend/manage.py test <labels> --settings=config.e2e_settings. Do not commit or push.

$(cat "$review")"
# Edits only; the shell is limited to tests and git inspection. Secrets and pushes are denied explicitly.
claude -p "$prompt" --output-format json --no-session-persistence --max-budget-usd "$remaining" \
    --permission-mode acceptEdits \
    --allowedTools "Bash(backend/venv/bin/python backend/manage.py test:*)" \
        "Bash(backend/venv/bin/python backend/manage.py check:*)" "Bash(git diff:*)" "Bash(git status:*)" \
    --disallowedTools "Bash(git push:*)" "Bash(git commit:*)" "Read(**/.env*)" "Read(~/.ssh/**)" \
        "Read(~/.codex/**)" "Read(~/.claude/**)" "Edit(**/.env*)" "Edit(.github/**)" "Edit(scripts/**)" \
    >"$result" || echo "Claude exited with an error; see $result"

# An unreported or unreadable cost counts as the whole budget: stopping beats guessing.
python3 - "$result" "$spent" "$BUDGET_USD" >"$state/spent_usd" <<'EOF'
import json, sys
from decimal import Decimal
try:
    cost = Decimal(str(json.load(open(sys.argv[1]))["total_cost_usd"]))
    assert cost.is_finite() and cost >= 0
    print(Decimal(sys.argv[2]) + cost)
except Exception:
    print(sys.argv[3])
EOF
echo "Claude spend for $key: \$$(cat "$state/spent_usd") of \$$BUDGET_USD"
python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("result", ""))' "$result" 2>/dev/null || true
echo "Review the changes (git diff), commit them yourself, then run this script again for the next review."
