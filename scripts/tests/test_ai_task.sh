#!/usr/bin/env bash
# Mocked tests for scripts/ai-task.sh: fake `codex` and `claude` on PATH, no AI call, no network, no deletion.
set -euo pipefail

src="$(cd "$(dirname "$0")/.." && pwd)"
failures=0

check() {  # check <description> <command...>
    if "${@:2}"; then echo "ok   $1"; else echo "FAIL $1"; failures=$((failures + 1)); fi
}

setup() {  # a throwaway repo on $1, one commit ahead of origin/main, with fake agents
    work="$(mktemp -d)"
    mkdir -p "$work/repo/scripts" "$work/bin"
    cp "$src/ai-task.sh" "$src/ai-review.sh" "$work/repo/scripts/"
    cat >"$work/bin/codex" <<'EOF'
#!/usr/bin/env bash
echo "codex $*" >>"$FAKE_CALLS"
echo "[P1] parts/x.py:1 a finding"
EOF
    cat >"$work/bin/claude" <<'EOF'
#!/usr/bin/env bash
printf 'claude' >>"$FAKE_CALLS"; printf ' <%s>' "$@" >>"$FAKE_CALLS"; echo >>"$FAKE_CALLS"
echo "{\"total_cost_usd\": ${FAKE_COST:-1.25}, \"result\": \"fixed\"}"
EOF
    chmod +x "$work/bin/codex" "$work/bin/claude" "$work/repo/scripts/ai-task.sh" "$work/repo/scripts/ai-review.sh"
    export FAKE_CALLS="$work/calls" PATH="$work/bin:$PATH"
    : >"$FAKE_CALLS"
    cd "$work/repo"
    git init -q -b main . && git config user.email t@example.invalid && git config user.name test
    git add scripts && git commit -qm base && git update-ref refs/remotes/origin/main HEAD
    git switch -qc "$1" && git commit -q --allow-empty -m change
    state="$(git rev-parse --absolute-git-dir)/ai-tasks/LPK-7" && mkdir -p "$state"
}

run() { echo "${1:-}" | scripts/ai-task.sh "${2:-LPK-7}" >"$work/out" 2>&1; }

setup feature/other-topic
run y || code=$?
check "refuses a branch that is not the task's own" test "${code:-0}" -eq 2
check "no agent runs on the wrong branch" test ! -s "$FAKE_CALLS"
unset code

setup feature/lpk-7-fix
run y LPK7 || code=$?
check "rejects a malformed Jira key" test "${code:-0}" -eq 2
unset code

setup feature/lpk-7-fix
run n
check "declined findings: Codex reviewed, Claude not called" test "$(cat "$FAKE_CALLS")" = "codex exec --sandbox read-only review --base origin/main"
check "declined findings still count an attempt" test "$(cat "$state/attempts")" = 1
check "prints the manual draft PR command" grep -q "gh pr create --draft" "$work/out"

setup feature/lpk-7-fix
run y
check "Claude gets the findings" grep -q "a finding" "$FAKE_CALLS"
check "Claude gets the full budget first" grep -q "<--max-budget-usd> <5.00>" "$FAKE_CALLS"
check "Claude may not push or commit" grep -q "<Bash(git push:\*)> <Bash(git commit:\*)>" "$FAKE_CALLS"
check "Claude may not read .env or SSH keys" grep -q "<Read(\*\*/.env\*)> <Read(~/.ssh/\*\*)>" "$FAKE_CALLS"
check "Claude never bypasses permissions" bash -c '! grep -q "dangerously\|bypassPermissions" "$1"' _ "$FAKE_CALLS"
check "spend is recorded" test "$(cat "$state/spent_usd")" = 1.25
run y
check "the next attempt gets only the remaining budget" grep -q "<--max-budget-usd> <3.75>" "$FAKE_CALLS"

setup feature/lpk-7-fix
echo 5 >"$state/attempts"
run y || code=$?
check "a sixth attempt is refused" test "${code:-0}" -eq 3
check "no agent runs after five attempts" test ! -s "$FAKE_CALLS"
unset code

setup feature/lpk-7-fix
echo 5.00 >"$state/spent_usd"
run y || code=$?
check "a spent budget stops the task" test "${code:-0}" -eq 3
check "no agent runs over budget" test ! -s "$FAKE_CALLS"
unset code

setup feature/lpk-7-fix
FAKE_COST='"oops"' run y
check "an unknown cost counts as the whole budget" test "$(cat "$state/spent_usd")" = 5.00

(( failures == 0 )) && echo "All ai-task tests passed." || { echo "$failures failed"; exit 1; }
