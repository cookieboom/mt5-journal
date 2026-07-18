# M6 — Annotations, tags, and the weekly report

**Goal.** Add the human layer and the first recurring digest:
1. **Annotations** — per-trade setup/confidence/emotion/plan/notes capture.
2. **Tags** — manual (`journal tag`) plus an auto-tagging pass on `rebuild`.
3. **Weekly report** — one ISO week rendered to a Markdown file in `cache/`.

**Decisions locked (2026-07-18).**
- Weekly report windows over **one selected ISO week** (default = last complete
  week; `--week YYYY-Www` selects another).
- Weekly report is delivered as a **Markdown file** written to `cache/`
  (reproducible from the DB — rule 6), with the path echoed to the terminal.
- Tags = **manual CLI + an auto-tagging pass** run during `rebuild`.

**This milestone is ~3 features (like M5 was ~4).** Ship it in two commits at the
natural boundary: **M6** = annotations + tags (Phases 1–4), **M6.1** = weekly
report (Phases 5–6). Mirrors M5→M5.1. Each phase is independently committable.

---

## Phase 0 — Ground truth (gathered from the repo; verify, don't re-derive)

### The storage already exists — NO schema change, NO migration (verified)
`src/journal/store/schema.sql` already defines everything, and all three
objects are present in the live `data/journal.db` (confirmed by
`SELECT name FROM sqlite_master`):

- **`annotations`** (schema.sql:209-221) — PK `(account_login, position_id,
  segment)`, columns `setup TEXT`, `confidence INTEGER CHECK (BETWEEN 1 AND 5)`,
  `emotion TEXT`, `followed_plan INTEGER` (0/1 nullable), `notes TEXT`,
  `created_at`, `updated_at`. **Keyed on `position_id`, NOT `trades.id`**, with
  the reason in the schema comment: `trades.id` renumbers on every rebuild and
  would orphan notes. This is the "human layer (never rebuilt)" section.
- **`tags`** (schema.sql:223-230) — PK `(account_login, position_id, segment,
  tag)`, `source TEXT CHECK (source IN ('auto','manual'))`. Index `ix_tags_tag`.
- **`v_trades_annotated`** (schema.sql:258-265) — `trades LEFT JOIN annotations`
  on `(account_login, position_id, segment)`, surfacing setup/confidence/
  emotion/followed_plan/notes. Read annotated trades **through this view**.

**Anti-pattern guard:** do NOT edit `schema.sql` to "add" these — they exist.
Do NOT write a migration — `connect()` (db.py:48-64) only applies the schema on
a *fresh* DB and there is no migration runner; the live DB already has the
tables. Touching the schema now would violate "do not edit schema.sql in place
once data exists" (CLAUDE.md "Read before you edit").

### Human-layer write pattern to COPY
`src/journal/ingest/deals.py:408 add_reconciliation(...)` is the existing
human-authored-row writer (it fills `reconciliations`, the other never-rebuilt
table). Copy its shape — a plain function taking `conn` + fields, doing one
INSERT/UPSERT and `conn.commit()`. Annotation/manual-tag writes are user input,
not MT5 ingest, so they get a **new module `src/journal/annotate.py`** rather
than living under `ingest/` (which is for pulling FROM MT5).

### `rebuild()` structure — where the auto-tag pass hooks in
`src/journal/domain/reconstruct.py:519-575`. `rebuild()` calls
`reconstruct()` → `_fill_excursions(conn, trades)` (a POST-reconstruct step,
reconstruct.py:466, that mutates the in-memory `Trade` list) → `DELETE FROM
trades` → per-`Trade` INSERT loop → **single `conn.commit()` at 564**. The M5
excursion pass is the exact precedent: **copy that shape** for auto-tags — a
pure computation over the in-memory `trades` list, applied inside the same
`rebuild` transaction, before the final commit.

`Trade` (reconstruct.py:~123 dataclass) carries what the tag rules need:
`duration_s`, `open_time_msc`, `close_time_msc`, `net_profit`, `magic`,
`status`, `position_id`, `segment`, `account_login`.

### Cache-write + path-echo pattern to COPY (weekly report)
`journal chart` (cli.py `def chart`) writes a file under `cache/` and echoes
`path: {r.path}`. The weekly command copies this: build → render markdown →
write `cache/weekly-YYYY-Www.md` → `typer.echo` the path. `cache/` is
reproducible-from-DB (rule 6); a weekly report qualifies.

### Report aggregation to REUSE (weekly report)
`src/journal/analytics/report.py` already has the per-bucket aggregator
`_bucket_stat(label, rows)` and the constants `_MIN_N = 20`, `_TOL = 1e-9`, plus
`session_of`/`SESSION_ORDER` from `analytics/sessions.py`. The weekly report is
"`journal report`, but scoped to one week", so it MUST reuse these, not
re-derive win/loss classification (two definitions of "a win" would drift).
Phase 5 promotes the shared pieces to importable names.

### Allowed APIs (nothing outside this list is needed)
stdlib `datetime` (`fromtimestamp`, `fromisocalendar`, `timezone.utc`,
`strptime`), `sqlite3`, `typer`, existing `one_account_login`, `now_ms`,
`connect`, `_bucket_stat`, `session_of`. **No new dependencies** (rule 8).
**No `import MetaTrader5`, no MT5 constants** anywhere in this milestone —
it is all DB + human input (rules 1, 12). `domain/tags.py` and
`analytics/weekly.py` are pure/DB and get **tests first** (rule 7).

### Anti-patterns to avoid (whole milestone)
- Keying annotations/tags on `trades.id` (renumbers on rebuild → orphaned).
- Letting the auto-tag pass touch `source='manual'` rows (would delete the
  user's tags on every rebuild).
- Inventing a money-magnitude tag on a tiny sample (see Phase 1 gating).
- Naive datetimes / `utcfromtimestamp` (rule 3) — use
  `datetime.fromtimestamp(ms/1000, tz=timezone.utc)`.
- Editing `schema.sql` or writing a migration (tables already exist).

---

## Phase 1 — `domain/tags.py`: pure auto-tag computation (TDD)

**Write `tests/test_tags.py` first** (rule 7), then the module.

New file `src/journal/domain/tags.py`. One pure function:
```python
def compute_auto_tags(trade, *, big_win_threshold=None, big_loss_threshold=None) -> set[str]:
```
Returns the auto-tags a single closed trade earns. **Structural facts only** —
each is well-defined for every closed trade, no judgement:

| tag             | rule |
|-----------------|------|
| `sub-1min`      | `duration_s is not None and duration_s < 60` |
| `held-overnight`| open and close fall on **different UTC calendar dates** (`fromtimestamp(...,utc).date()` of open ≠ of close) |
| `weekend`       | opened Sat/Sun UTC (`open_dt.weekday() >= 5`) — only BTC can earn this (docs §7) |
| `big-win`       | `big_win_threshold is not None and net_profit >= big_win_threshold` |
| `big-loss`      | `big_loss_threshold is not None and net_profit <= big_loss_threshold` |

**The money-magnitude gate (§9 discipline):** `big-win`/`big-loss` thresholds
are passed IN, not computed here. The caller (Phase 2) supplies them as the
top/bottom-decile `net_profit` of the account's closed trades **only when
`n_closed >= _MIN_N`**, and passes `None` otherwise — so on a sub-20 account no
trade is labelled an outlier against a sample too small to define one. Open/
partial trades earn no tags (skip them in the caller).

**Verification checklist (Phase 1):**
- [ ] `tests/test_tags.py` written before `tags.py`, fails first.
- [ ] `sub-1min`: 59s → tagged; 60s → not; `duration_s=None` → not.
- [ ] `held-overnight`: open 23:30 / close 00:10 next day → tagged; open &
      close same UTC date → not (even if >24h is impossible here, test a
      same-day multi-hour trade).
- [ ] `weekend`: a Saturday open → tagged; a Wednesday open → not.
- [ ] `big-win`/`big-loss`: with thresholds supplied, boundary (`>=`/`<=`)
      correct; with `None` thresholds, never tagged regardless of net_profit.
- [ ] Uses `datetime.fromtimestamp(..., tz=timezone.utc)`, not
      `utcfromtimestamp`.

**Anti-pattern guards:** no threshold computation inside `compute_auto_tags`
(keep it pure and caller-driven); no tag that encodes an opinion the data can't
back; return a `set`, never dupes.

---

## Phase 2 — auto-tag population in `rebuild()` (manual-safe, idempotent)

**Extend `tests/test_reconstruct.py`** (its rebuild-integration tests are the
home for this) — tests first.

In `reconstruct.py`, add `_fill_auto_tags(conn, trades)` mirroring
`_fill_excursions` in placement (called from `rebuild()` inside the same
transaction, before the final `conn.commit()` at :564):

1. Compute the decile thresholds from closed-trade `net_profit` **iff**
   `n_closed >= _MIN_N` (import `_MIN_N` from analytics.report, or define the
   constant once and share) else `None`/`None`.
2. `conn.execute("DELETE FROM tags WHERE account_login = ? AND source = 'auto'",
   (login,))` — **only `source='auto'`. Never manual.**
3. For each closed trade, `compute_auto_tags(t, big_win_threshold=...,
   big_loss_threshold=...)` and `INSERT ... source='auto'` (use
   `INSERT OR IGNORE` against the PK to be safe).

**Verification checklist (Phase 2):**
- [ ] After `rebuild`, a seeded sub-1min / overnight / weekend trade has the
      expected `source='auto'` rows in `tags`.
- [ ] **A pre-existing `source='manual'` tag SURVIVES a `rebuild`** (the
      headline safety test — seed a manual tag, rebuild, assert it's still
      there and auto tags were regenerated around it).
- [ ] Rebuild is idempotent for auto tags: two rebuilds → identical `tags`
      rows (no duplicates, no growth).
- [ ] On a <20-trade account, no `big-win`/`big-loss` rows appear (§9 gate).
- [ ] `uv run journal rebuild` succeeds and its output still parses.

**Anti-pattern guards:** never `DELETE FROM tags` without the
`source='auto'` filter; never key on `trades.id`; keep it in the existing
rebuild transaction (no second commit that could half-apply).

---

## Phase 3 — `annotate.py`: annotation + manual-tag writes (rebuild-safe, TDD)

**Write `tests/test_annotate.py` first.** New module `src/journal/annotate.py`,
copying `add_reconciliation`'s plain-function-over-`conn` shape:

```python
def set_annotation(conn, position_id, *, setup=None, confidence=None,
                   emotion=None, followed_plan=None, notes=None, segment=0): ...
def add_tag(conn, position_id, tag, *, segment=0): ...     # source='manual'
def remove_tag(conn, position_id, tag, *, segment=0): ...
def list_tags(conn, position_id, *, segment=0) -> list[...]: ...
```
- `set_annotation` is an **UPSERT** on the PK (`INSERT ... ON CONFLICT
  (account_login, position_id, segment) DO UPDATE`), setting `updated_at=now_ms()`
  and `created_at` only on first insert. `confidence` respects the 1–5 CHECK —
  surface a clean error, don't let sqlite's IntegrityError leak raw.
- `add_tag` writes `source='manual'` (`INSERT OR IGNORE` on the PK).
- Resolve `account_login` via `one_account_login(conn)` (the codebase
  convention — never a parameter).
- Validate the `position_id` exists in `trades` before writing, so a typo
  doesn't create an orphan annotation for a nonexistent trade.

**Verification checklist (Phase 3):**
- [ ] Tests first, fail first.
- [ ] `set_annotation` inserts then updates in place (second call updates
      `updated_at`, keeps `created_at`, doesn't duplicate).
- [ ] `confidence=6` / `confidence=0` rejected with a clear error, not a raw
      IntegrityError.
- [ ] `add_tag` is idempotent; `remove_tag` removes only that tag; both write/
      touch only `source='manual'`.
- [ ] Writing for a non-existent `position_id` is refused.
- [ ] A `rebuild` after annotating leaves annotations and manual tags intact
      (they're the never-rebuilt layer).

**Anti-pattern guards:** no `trades.id` key; don't swallow the CHECK violation
silently; don't let `add_tag` write `source='auto'`.

---

## Phase 4 — CLI: `journal annotate` / `journal tag` (→ M6 commit point)

Wire the Phase-3 functions into `cli.py`, copying the `reconcile` sub-Typer
pattern (`reconcile_app = typer.Typer(...)`, `app.add_typer(...)`) and the
`_one_account_login` friendly-error wrapper.

```
journal annotate <position_id> [--setup ..] [--confidence 1-5] [--emotion ..]
                                [--followed-plan/--no-followed-plan] [--notes ..]
journal tag add <position_id> <tag>
journal tag rm  <position_id> <tag>
journal tag ls  <position_id>
```
Echo the resulting state after each write (e.g. the annotation row / current
tag list), like the other commands print a small confirmation block.

**Verification checklist (Phase 4):**
- [ ] `uv run journal annotate <real position_id> --setup breakout
      --confidence 4 --notes "..."` writes and echoes it back.
- [ ] `uv run journal tag add/ls/rm` round-trips against the live DB.
- [ ] `journal chart <same id>` still works (shared `position_id` key).
- [ ] Full `uv run pytest` green; paste output.
- [ ] **Commit M6** (`feat(M6): annotations + tags`) — annotations + tags is a
      complete, useful slice on its own.

---

## Phase 5 — `analytics/weekly.py` + `render/weekly.py` (TDD)

**Write `tests/test_weekly.py` first.**

**5a. Promote shared aggregation in `report.py`** (small, mechanical): make the
per-bucket aggregator and constants importable so weekly reuses them instead of
re-deriving win/loss. Rename `_bucket_stat` → `bucket_stat` (or add a public
alias) and ensure `_MIN_N`, `_TOL`, `BucketStat` are importable. `build_report`
keeps working unchanged (update its internal call site). Re-run `test_report.py`
to prove no behaviour change.

**5b. `analytics/weekly.py`** — `build_weekly(conn, iso_year, iso_week) ->
WeeklyResult` (frozen dataclass), mirroring `build_report`:
- Week bounds in UTC: `start = datetime.fromisocalendar(iso_year, iso_week, 1)`
  at `tz=utc`; `end = start + 7 days`; as epoch-ms.
- **Attribute a trade to the week its `close_time_msc` falls in** (realized P&L
  lands the week it closed); only `status='closed'` with `close_time_msc` in
  `[start, end)`. Document this choice in the docstring.
- Compute the same money stats + `by_session` + `by_source` as `build_report`,
  via the promoted `bucket_stat` — all §9-gated (a week rarely clears n≥20, so
  most weekly figures show as gated; that is honest, not a bug).
- Include the human layer for that week's trades: annotations (via
  `v_trades_annotated`) and tags attached to those position_ids.

**5c. `render/weekly.py`** — `render_weekly_md(result) -> str`: pure
string builder producing Markdown (title with ISO week + date range, a money
table, session/source tables, an annotations/tags section). No file I/O here
(testable) — the CLI writes it in Phase 6.

**Verification checklist (Phase 5):**
- [ ] Tests first, fail first.
- [ ] Week bounds: a trade closed 23:59 UTC Sunday of W28 is in W28; 00:00 UTC
      Monday is in W29 (`[start, end)`).
- [ ] Weekly money/session/source stats match a hand-computed small fixture and
      are gated exactly like `build_report` (same `_MIN_N`, `_TOL`).
- [ ] `render_weekly_md` returns valid Markdown containing the week label,
      every session bucket, both source buckets, and any annotations/tags.
- [ ] `test_report.py` still green after the 5a refactor (no behaviour drift).

**Anti-pattern guards:** don't re-implement win classification in weekly (reuse
`bucket_stat`); don't do file I/O in `render_weekly_md`; UTC-only week math.

---

## Phase 6 — `journal weekly` CLI (→ M6.1 commit point)

Copy `journal chart`'s cache-write + path-echo shape.

```
journal weekly [--week YYYY-Www] [--cache-dir cache] [--db ...]
```
- Default (`--week` omitted): the **last complete ISO week** — the most recent
  Mon–Sun entirely before "now" UTC. Compute from `datetime.now(timezone.utc)`:
  take today's ISO week, step back one week.
- Parse `--week YYYY-Www` with `datetime.strptime(f"{s}-1", "%G-W%V-%u")` (ISO
  week → Monday) — validate and give a clean typer error on bad input, like
  `_parse_effective` (cli.py:530) does.
- `build_weekly` → `render_weekly_md` → write `cache/weekly-YYYY-Www.md` →
  `typer.echo` the path (mirror chart's `path:` line). Create `cache/` if
  missing (chart's `cache_dir` handling shows the idiom).

**Verification checklist (Phase 6):**
- [ ] `uv run journal weekly` writes `cache/weekly-<lastweek>.md` and echoes the
      path; open it — the numbers match `journal report` filtered to that week.
- [ ] `uv run journal weekly --week 2026-W28` targets that week; bad input
      (`--week 2026-W99`, `garbage`) gives a friendly error, not a traceback.
- [ ] The file is reproducible: delete it, re-run, identical content (rule 6).
- [ ] Full `uv run pytest` green; paste output.
- [ ] **Commit M6.1** (`feat(M6.1): weekly markdown report`).

---

## Phase 7 — Final verification & docs

- [ ] `uv run pytest` — **paste actual output** (was 150 after M5.1; expect
      new `test_tags.py`, `test_annotate.py`, `test_weekly.py`, and extended
      `test_reconstruct.py`/`test_report.py`).
- [ ] Guards: `grep -rn 'utcfromtimestamp(' src/journal/` → none;
      `grep -rn 'import MetaTrader5\|mt5\.' src/journal/{domain,analytics,render}/`
      → none; `grep -rn "DELETE FROM tags" src/journal/` → every hit carries
      `source = 'auto'`.
- [ ] `git diff --stat` shows **no change to `schema.sql`** (tables pre-existed).
- [ ] `uv run journal rebuild` still succeeds; a manual tag set before it
      survives it.
- [ ] Update `docs/HANDOFF.md`: mark M6 (+ M6.1) done with commits, record the
      decisions (annotations keyed on position_id; auto-tags manual-safe &
      §9-gated for money-magnitude; weekly attributes by close week, Markdown to
      cache/), roadmap → M6/M6.1 done, set next milestone.

---

## Files touched (summary)
- **new** `src/journal/domain/tags.py` (+ `tests/test_tags.py`)
- **new** `src/journal/annotate.py` (+ `tests/test_annotate.py`)
- **new** `src/journal/analytics/weekly.py`, `src/journal/render/weekly.py`
  (+ `tests/test_weekly.py`)
- **edit** `src/journal/domain/reconstruct.py` — `_fill_auto_tags` in `rebuild`
- **edit** `src/journal/analytics/report.py` — promote `bucket_stat`/constants
- **edit** `src/journal/cli.py` — `annotate`, `tag` sub-Typer, `weekly`
- **edit** `tests/test_reconstruct.py`, `tests/test_report.py`
- **edit** `docs/HANDOFF.md`
- **NO** change to `schema.sql`; **NO** migration (tables already exist).
