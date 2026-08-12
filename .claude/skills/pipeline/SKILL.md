---
name: pipeline
description: The mt5-journal development pipeline — which superpowers skills to run, in which order, for a new feature or a bug fix. Use when starting any change touching more than one file, when fixing a bug, or when asked how work gets shipped in this repo.
---

# mt5-journal pipeline

Run the superpowers skills, in order. They carry the how; this lists the when.

## New feature (anything touching more than one file)

1. `brainstorming` → spec in `docs/specs/<name>.md`. No code yet. You can ask
   plenty of questions to ensure the implementation aligns with what is desired.
2. `writing-plans` → numbered tasks, one task = one commit-able unit.
3. `using-git-worktrees` → branch + worktree per spec. Never build on `main`.
4. `executing-plans`, and inside each task `test-driven-development` (hard rule 7).
5. `requesting-code-review` on the whole branch → `receiving-code-review` →
   fix wave → re-review until clean.
6. `verification-before-completion`, then `finishing-a-development-branch`.
   Fast-forward merge, no force-push.

## Bug fix

1. `systematic-debugging` first — before proposing any fix.
2. `test-driven-development`: reproduce as a failing test. No repro → no fix.
3. Root cause, not symptom: grep every caller of the function you are about to
   touch. The fix goes at the shared choke point, not in each caller.
4. `verification-before-completion`: full `uv run pytest`, not just the new
   test — the fix moved a shared path.
5. Single-file, single-cause → straight to `main`. Otherwise branch.

Skip the ceremony for typos, comments, and one-line constants.
