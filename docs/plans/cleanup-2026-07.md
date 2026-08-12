# Cleanup plan — repo hygiene, stale docs, fixture sanitization (2026-07-23)

**Goal:** make the repo tidy and everything still functional. Remove real junk,
fix stale/misleading docs, and scrub one leaked payment reference — **without
touching the test fakes**, which are load-bearing infrastructure, not dummies.

**Scope:** hygiene + correctness of docs/fixtures only. No feature work, no
behavior changes to `domain/` or `analytics/`. Every phase ends with
`uv run pytest` green and `uv run journal rebuild` succeeding.

**Decisions (confirmed with owner):**
- `graphify-out/` → **track it in git** (keep `.gitattributes` merge driver;
  keep `graphify-out/cache/` ignored).
- Deleted `docs/plans/M5.1-*.md` and `M6-*.md` → **commit the deletion**
  (full text remains in git history).
- This is **plan only** — no edits performed yet.

---

## CRITICAL: what is NOT junk (do not remove)

The request mentioned "fake login or other temporary/dummy functions." An audit
(graphify + grep) found **no stub/placeholder code that should be replaced.**
Every match was legitimate. The following are mandated by `CLAUDE.md` and must
survive cleanup:

| Item | Why it exists | Rule |
|------|---------------|------|
| `src/journal/adapter/fake.py` (`FakeMT5Client`) | The fixture-backed adapter that lets the whole codebase be tested with no MT5 running. | Hard Rule 1, 7 |
| `tests/fixtures/*.json` | Real recorded data, sanitized. The test suite depends on them. | Hard Rule 7, 10 |
| `one_account_login()` in `store/db.py` | Deliberate de-dup seam enforcing the "exactly one account" invariant. High fan-in is by design, not accidental coupling. | CLAUDE.md "This account" |
| `scripts/record_fixtures.py` | The tool that regenerates fixtures from live MT5. Needed, not junk. | — |
| `raise NotImplementedError` in `reconstruct.py:264,269` | Deliberate guards for `INOUT`/`OUT_BY` deal entries that must never occur on this account. | Hard Rule, CLAUDE.md |
| `...` bodies in `adapter/base.py` | Python `Protocol` method signatures — correct idiom, not empty stubs. | Rule 1 |
| "never hardcode 0" comments (`chart.py`, `deals.py`, `live.py`) | Anti-hardcode guidance for the measured server offset (Trap 7). | docs/mt5-deal-model.md |

**Anti-pattern guard for the executor:** if a step ever proposes deleting or
"replacing with a real one" anything in the table above, STOP — that is a
misread of the request.

---

## Phase 0 — Baseline (prove green before touching anything)

**Do:**
1. `uv run pytest` — capture full output. This is the reference bar.
2. `uv run journal rebuild` — confirm it succeeds.
3. `git status` — snapshot the starting working tree.

**Verify:** all tests pass; rebuild exits 0. If anything is already red, record
it — later phases must not be blamed for a pre-existing failure.

**Do not proceed if baseline is red** without first noting the failure.

---

## Phase 1 — On-disk junk (zero risk, no git-tracked files touched)

Everything here is already gitignored; this only cleans the working directory.

**Do:**
1. Remove macOS cruft: `find . -name .DS_Store -not -path './.git/*' -delete`
   (present in `./`, `docs/`, `src/`, `src/journal/`, `tests/`).
2. Optionally clear regenerable caches: `__pycache__/`, `.pytest_cache/`.
   (Cosmetic — they're ignored. Skip if you prefer.)
3. **Do NOT** touch `data/`, `cache/`, `.venv/` — ignored and either real
   account data (`data/journal.db`) or reproducible render cache. Rule 6/10.

**Verify:** `git status` shows no newly-changed *tracked* files from this phase.
`uv run pytest` still green.

---

## Phase 2 — .gitignore + tracking decisions

**Do:**
1. Extend `.gitignore` (currently only `data/ cache/ __pycache__/ *.pyc .venv/
   venv/ .DS_Store`). Add:
   ```
   .pytest_cache/
   *.egg-info/
   .claude/settings.local.json
   graphify-out/cache/
   ```
   - `.claude/settings.local.json` is personal (a local permission allowlist) —
     ignore it.
   - `graphify-out/cache/` is the graph's internal cache — ignore even though we
     track the rest of `graphify-out/`.
2. **Track** the shared project config and generated graph (owner decision):
   - `git add .gitattributes` — defines the `graphify` merge driver; belongs in
     the repo.
   - `git add .claude/settings.json` — the graphify hook-guard config; shared,
     useful to teammates/future sessions.
   - `git add graphify-out/` — the knowledge graph (minus the now-ignored
     `cache/` subdir).

**Verify:** `git status` — `settings.local.json` no longer appears; `.gitignore`,
`.gitattributes`, `.claude/settings.json`, and `graphify-out/` are staged.
`git check-ignore graphify-out/cache` prints the path (confirmed ignored).

---

## Phase 3 — Resolve uncommitted working-tree changes

**Do:**
1. **`CLAUDE.md`** is modified in two ways:
   - It adds a `## graphify` section — **keep** (matches the hooks now in the
     repo).
   - It still reads `Currently on: **M0**` at line ~112. This is badly stale —
     the project has shipped through **M7 (web dashboard)**. Fix it to reflect
     reality, e.g. `Currently on: **M7 shipped — see docs/HANDOFF.md**`, and add
     `M7 web dashboard` to the milestone list on the line above.
2. **Deleted plan docs** (`docs/plans/M5.1-sessions-ea-breakdown.md`,
   `docs/plans/M6-annotations-weekly-report.md`): stage the deletions
   (`git rm`), per owner decision — the content lives in git history.

**Verify:** `git diff CLAUDE.md` shows only the graphify section + the milestone
fix. `git status` shows the two plan docs as staged deletions. No unintended
edits.

---

## Phase 4 — Stale docstrings/comments (functional but misleading)

**Do:**
1. `src/journal/adapter/fake.py:7` — docstring says fixtures are
   "all valid empty placeholders for M0; populated in later" milestones. The
   fixtures are populated now (real recorded data). Reword so it doesn't imply
   the fixtures are empty stubs. Copy the tone of the surrounding module
   docstring; do not change any code.
2. Skim `docs/HANDOFF.md` for any other "Currently on M0/Mx" style staleness and
   align it with the M7-shipped reality. (HANDOFF was updated through M6.1 per
   git log — confirm M7 is reflected.)

**Verify:** grep for `placeholder|empty placeholder|Currently on` across
`src/` and `docs/` returns only intentional matches (HTML input placeholders in
`web/templates/` are fine). `uv run pytest` green — this phase changes only
comments/docs.

**Anti-pattern guard:** touch comments/docstrings only. If a "fix" requires
editing executable lines, it does not belong in this phase.

---

## Phase 5 — Fixture sanitization (the one real leak) ⚠️

A payment/deposit reference leaked into a committed fixture.

**Finding:** `tests/fixtures/deals.json:3` — the balance/deposit deal
(`type: 2`, `symbol: ""`) carries
a `comment` holding a real funding-transaction reference — a direction letter,
a scheme name, the account currency and the transaction digits — not
trade-structural data. Rule 10 says fixtures must be sanitized. (This matches
the prior "funding payment references committed to test fixtures" flag.)

> **2026-08-12 — the reference itself was redacted out of this paragraph.**
> It was quoted here in full, and this repository is public. The shape is
> described instead; `tests/test_repo_hygiene.py` now fails if any tracked file
> carries one again.

**Do:**
1. Audit ALL fixtures for the same class of leak before editing:
   scan `tests/fixtures/*.json` for `comment`/`external_id` values on
   balance-type deals (`type: 2`) and any `D-…` / long-digit references.
2. Scrub the reference in the committed fixture — replace the deposit `comment`
   with a neutral sanitized token (e.g. `"DEPOSIT-REDACTED"`), keeping the JSON
   shape and the deal itself intact. **Keep** genuine trade comments and
   `external_id` on real trade deals — those are structurally useful and were
   deliberately preserved (see the `record-fixtures-comment-oversanitized`
   note); only the funding/deposit reference is the problem.
3. Close the hole at the source: in `scripts/record_fixtures.py`, extend the
   sanitizer so that on balance/credit deals (`type == 2`, empty `symbol`) the
   `comment` is redacted on future recordings — while still preserving trade
   comments. Add/adjust a test asserting no raw deposit reference survives a
   record pass.
4. After editing fixtures, confirm the test suite still reconstructs correctly —
   the deposit deal's `profit` (balance line) must be unchanged; only the
   `comment` text changes.

**Verify:**
- `grep -rE 'D-[A-Z]+-USC-[0-9]{6,}' tests/fixtures/` returns nothing.
- `uv run pytest` green (balance invariant / reconstruct tests unaffected —
  only a comment string changed).
- `uv run journal rebuild` succeeds.

---

## Phase 6 — Final verification & commit

**Do:**
1. `uv run pytest` — paste the FULL output. Definition-of-done requires the
   actual pytest text, not "looks right."
2. `uv run journal rebuild` — confirm success.
3. `git status` review — the working tree should now contain only intentional
   changes: `.gitignore`, `.gitattributes`, `.claude/settings.json`,
   `graphify-out/`, `CLAUDE.md`, the two staged plan-doc deletions, the
   `fake.py` docstring, the scrubbed fixture, and `record_fixtures.py` +
   its test.
4. Commit in logical chunks (suggested):
   - `chore: repo hygiene — gitignore, track graphify config + graph`
   - `docs: fix stale M0 milestone marker and fake.py fixture docstring;
     remove completed plan docs`
   - `fix(fixtures): redact leaked deposit reference; harden record_fixtures
     sanitizer`
   (Commit only when the owner asks — this plan does not auto-commit.)

**Verify (definition of done):** tests pass with pasted output; `journal
rebuild` succeeds; `git status` clean except intended changes; the "NOT junk"
table is fully intact — no test fake, fixture-loading path, or `one_account_login`
was removed.

---

## Out of scope (noted, not part of this cleanup)

These came up in the earlier M8 improvement review and are **improvements, not
cleanup** — leave for a separate milestone unless the owner asks:
- Dependency version pins in `pyproject.toml` (currently unpinned).
- A schema migration system (Rule: schema changes need migration files).
- `scripts/probe_enums.py` / `probe_rates.py` — one-off live diagnostics; keep
  as documented tools unless you want them archived under `scripts/probe/`.
