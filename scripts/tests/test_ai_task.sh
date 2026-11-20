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
if [[ -n "${FAKE_CODEX_WAIT:-}" ]]; then  # block until the test releases it (bounded)
    for _ in $(seq 100); do [[ -e "$FAKE_CODEX_WAIT" ]] && break; sleep 0.1; done
fi
[[ -z "${FAKE_CODEX_FAIL:-}" ]] || exit 1
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
run f || code=$?
check "refuses a branch that is not the task's own" test "${code:-0}" -eq 2
check "no agent runs on the wrong branch" test ! -s "$FAKE_CALLS"
unset code

setup feature/lpk-7-fix
run f LPK7 || code=$?
check "rejects a malformed Jira key" test "${code:-0}" -eq 2
unset code

setup feature/lpk-7-fix
codex_only="codex exec --sandbox read-only review --base origin/main"
run n
check "declined findings: Codex reviewed, Claude not called" test "$(cat "$FAKE_CALLS")" = "$codex_only"
check "declined findings still count an attempt" test "$(cat "$state/attempts")" = 1
check "declining does not suggest pushing" bash -c '! grep -q "gh pr create" "$1"' _ "$work/out"

setup feature/lpk-7-fix
run c
check "clean review: Claude not called" test "$(cat "$FAKE_CALLS")" = "$codex_only"
check "clean review prints the manual draft PR command" grep -q "gh pr create --draft" "$work/out"
check "clean review spends no Claude budget" test ! -e "$state/spent_usd"

setup feature/lpk-7-fix
run x || code=$?
check "review marked failed stops with an error" test "${code:-0}" -eq 5
check "review marked failed: Claude not called" test "$(cat "$FAKE_CALLS")" = "$codex_only"
check "review marked failed is not reported clean" bash -c '! grep -q "gh pr create" "$1"' _ "$work/out"
unset code

setup feature/lpk-7-fix
FAKE_CODEX_FAIL=1 run c || code=$?
check "Codex error stops even if the operator says clean" test "${code:-0}" -eq 5
check "Codex error: Claude not called" test "$(cat "$FAKE_CALLS")" = "$codex_only"
check "Codex error is not reported clean" bash -c '! grep -q "gh pr create" "$1"' _ "$work/out"
unset code
run f
check "the lock is released after a failed run" grep -q "^claude" "$FAKE_CALLS"

setup feature/lpk-7-fix
FAKE_CODEX_WAIT="$work/go" run f &
first=$!
for _ in $(seq 100); do [[ -s "$FAKE_CALLS" ]] && break; sleep 0.1; done
echo f | scripts/ai-task.sh LPK-7 >"$work/out2" 2>&1 || code=$?
check "a concurrent run for the same task is refused" test "${code:-0}" -eq 4
check "the concurrent run says why" grep -q "in progress" "$work/out2"
check "the concurrent run calls no agent" test "$(cat "$FAKE_CALLS")" = "$codex_only"
check "the concurrent run leaves the counter alone" test "$(cat "$state/attempts")" = 1
touch "$work/go"
wait "$first" && first_code=0 || first_code=$?
check "the first run completes" test "$first_code" -eq 0
check "only the first run spent budget" test "$(cat "$state/spent_usd")" = 1.25
check "only one Claude call" test "$(grep -c "^claude" "$FAKE_CALLS")" -eq 1
unset code

setup feature/lpk-7-fix
run f
check "Claude gets the findings" grep -q "a finding" "$FAKE_CALLS"
check "Claude gets the full budget first" grep -q "<--max-budget-usd> <5.00>" "$FAKE_CALLS"
check "Claude may not push or commit" grep -q "<Bash(git push:\*)> <Bash(git commit:\*)>" "$FAKE_CALLS"
check "Claude may not read .env or SSH keys" grep -q "<Read(\*\*/.env\*)> <Read(~/.ssh/\*\*)>" "$FAKE_CALLS"
check "Claude never bypasses permissions" bash -c '! grep -q "dangerously\|bypassPermissions" "$1"' _ "$FAKE_CALLS"
check "spend is recorded" test "$(cat "$state/spent_usd")" = 1.25
run f
check "the next attempt gets only the remaining budget" grep -q "<--max-budget-usd> <3.75>" "$FAKE_CALLS"

setup feature/lpk-7-fix
echo 5 >"$state/attempts"
run f || code=$?
check "a sixth attempt is refused" test "${code:-0}" -eq 3
check "no agent runs after five attempts" test ! -s "$FAKE_CALLS"
unset code

setup feature/lpk-7-fix
echo 5.00 >"$state/spent_usd"
run f || code=$?
check "a spent budget stops the task" test "${code:-0}" -eq 3
check "no agent runs over budget" test ! -s "$FAKE_CALLS"
unset code

setup feature/lpk-7-fix
FAKE_COST='"oops"' run f
check "an unknown cost counts as the whole budget" test "$(cat "$state/spent_usd")" = 5.00

(( failures == 0 )) && echo "All ai-task tests passed." || { echo "$failures failed"; exit 1; }
