# Supervised AI review-and-fix loop (LPK-251)

`scripts/ai-task.sh LPK-<n>` runs one correction attempt for one Jira task on its own branch
`feature/lpk-<n>-<topic>`:

1. Codex reviews the committed branch against `origin/main` (`scripts/ai-review.sh`: read-only sandbox, a clean
   worktree of `HEAD`, so untracked files such as `.env` are not in it).
2. A human reads the review and classifies it: `c` clean (stop, Claude is not called), `f` findings to send to
   Claude, `x` failed or unclear (stop with an error). Anything else stops without Claude. Codex's review output
   is free text, so the script does not guess; a non-zero Codex exit always stops as a failure.
3. Claude (`claude -p`) edits the working tree. It may run only the targeted Django tests, `check`, `git diff` and
   `git status`; commits, pushes, reading `.env*` and agent or SSH credential folders, and editing `.github/` or
   `scripts/` are denied. It never commits.
4. The human reviews `git diff`, commits, and runs the script again for the next review.

When the review is clean, the human pushes the branch and opens a **draft** PR (`gh pr create --draft --base main`).
CI runs on it as usual. Nothing merges or deploys automatically.

## Limits the script enforces

- **One run per task at a time.** A `flock` on `.git/ai-tasks/LPK-<n>/lock` is taken before the counters are read
  and held until the script exits; a second run stops at once (exit 4) without calling an agent or changing a counter.
- At most **5 attempts** per task. The count is stored in `.git/ai-tasks/LPK-<n>/attempts` before any paid call.
- A **$5 Claude budget** per task (`AI_TASK_BUDGET_USD` to change it). Each Claude call gets the remaining amount as
  `--max-budget-usd`, and its reported `total_cost_usd` is added to `.git/ai-tasks/LPK-<n>/spent_usd`. An unreported
  cost counts as the whole budget.

## What remains supervised, and why

- **$5 is a target, not a hard limit.** Codex does not report a cost, so Codex reviews are not counted. Claude
  checks `--max-budget-usd` between steps, so one call can overshoot slightly.
- **Not safe to run unattended.** Both agents run as the developer's Linux user, so a tool that executes code (tests,
  Codex's read-only sandbox) could read any file that user can read. Deny rules narrow this but cannot close it;
  only a separate user or a container with no credentials would. Hence a human approves every attempt and every
  commit.
- **No AI in GitHub Actions.** It would need an API key stored as a repository secret and would bill every run with
  no per-task cap. CI only runs `scripts/tests/test_ai_task.sh`, which uses fake `codex` and `claude` binaries.
- Jira is not read or updated by the script; the human picks the task.
