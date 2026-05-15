---
name: migguard-review
description: Reviews SQL migration scripts for data-loss, locking, idempotency, rollback, and naming risks before they reach any database. Use when the user opens, edits, writes, or asks to review a .sql migration file; when they paste SQL and ask "is this safe to run", "will this lock", or "what could go wrong"; when working with files under migrations/, db/migrations/, or sql/migrations/; or when they mention MigGuard, schema migration, or pre-merge SQL review.
---

# MigGuard Review

A Cursor skill that runs the [MigGuard](https://github.com/abcfitness-insights/hackathon-2026-databasemigration) reviewer on SQL migration scripts and surfaces the findings inline.

MigGuard never connects to a database. It parses the SQL with `sqlglot`, applies a deterministic rule pack, and optionally adds LLM judgment. Use it freely — there is zero risk of executing anything.

## Quick start

When the user is working with a SQL migration file or pastes a migration script, run:

```bash
python -m migguard.cli.main review <path-to-file> --format markdown --no-llm
```

Capture the markdown output and present it to the user as the review. The output already includes severity badges, line references, and collapsible suggested fixes — just relay it.

If the user is in a directory of migrations (e.g. `migrations/`, `db/migrations/`), point the command at the whole directory so cross-file checks (version sequencing, duplicate version numbers) also run:

```bash
python -m migguard.cli.main review migrations/ --format markdown --no-llm
```

## When to invoke

Trigger the skill proactively when **any** of these are true:

- The user opens or edits a `.sql` file whose path contains `migrations/`, `db/migrations/`, or `sql/migrations/`.
- The user pastes SQL containing `CREATE TABLE`, `ALTER TABLE`, `DROP`, `DELETE`, `UPDATE`, `TRUNCATE`, or `GRANT` and asks any variant of "is this safe", "will this lock", "what could go wrong", or "review this".
- The user mentions MigGuard, migration review, schema migration, pre-merge SQL review, or risky migration.
- The user is about to commit, push, or open a PR that touches a `.sql` migration file.

Do **not** invoke for routine analytical `SELECT` queries, BI / reporting SQL, or one-off ad-hoc queries — those aren't migrations.

## Output handling

The CLI emits a Markdown PR-comment style report. Render it back to the user inline. Optionally summarize the top findings in one or two sentences before the full report, like:

> Two HIGH findings: `DELETE` without `WHERE` on line 4, and `DROP TABLE` without `IF EXISTS` on line 12. Suggested fixes below.

Severity guidance for your summary:

| Severity | Meaning | What to tell the user |
|----------|---------|-----------------------|
| HIGH | Data loss or downtime | Block before merge; show suggested fix |
| MEDIUM | Locking, missing rollback, idempotency gap | Recommend a fix; note it as merge-warning |
| LOW | Style, naming, soft idempotency | Note it; leave to user discretion |
| INFO | Internal / informational | Mention only if asked |

## Suggested-fix workflow

When a finding includes a suggested fix:

1. Show the suggestion to the user in the review output.
2. If they ask to apply it, follow Cursor's standard file-edit approval flow — show the proposed `StrReplace` diff in chat first, wait for explicit approval (`apply`, `do it`, etc.), then edit.
3. Re-run MigGuard after applying any fix to confirm the finding is resolved.

Never silently apply a fix. Migrations are high-blast-radius — every change should be a human decision.

## Dialect selection

MigGuard defaults to T-SQL / Synapse. For other dialects, add `--dialect`:

```bash
python -m migguard.cli.main review schema.sql --format markdown --no-llm --dialect postgres
python -m migguard.cli.main review schema.sql --format markdown --no-llm --dialect mysql
python -m migguard.cli.main review schema.sql --format markdown --no-llm --dialect sqlite
```

If the user's repo path suggests a specific dialect (e.g. `flyway/postgres/`, `liquibase/mysql/`), use it. Otherwise stick with the default and mention dialect in your summary so the user can correct it if needed.

## LLM mode

By default the skill runs with `--no-llm` for instant deterministic results that work without any API keys. If the user has Azure OpenAI configured and asks for an "AI summary", a "plain-English explanation", or a "rollback script", drop the flag:

```bash
python -m migguard.cli.main review <file> --format markdown
```

The LLM layer adds a one-paragraph summary for non-DBA reviewers and a best-effort rollback script (always labeled "review required").

## What the rule pack covers

Surface this list when the user asks "what does MigGuard check for" or "what rules are enabled":

- **Data loss**: `DELETE` / `UPDATE` without `WHERE`, `TRUNCATE`, `DROP TABLE`, `DROP SCHEMA`
- **Locking**: `NOT NULL DEFAULT` on large tables, `CREATE INDEX` without `ONLINE = ON` (T-SQL)
- **Idempotency**: `DROP` without `IF EXISTS`, `CREATE TABLE` without `IF NOT EXISTS`, `ALTER TABLE ADD COLUMN` without column-existence guard
- **Compatibility**: `MERGE` on Synapse (unsupported), `DBCC` commands in migrations (T-SQL — escalates to HIGH for `SHRINK*` / `REPAIR_ALLOW_DATA_LOSS` / `DROPCLEANBUFFERS`)
- **Permissions**: `GRANT` / `DENY` / `REVOKE` outside an allowlist
- **Performance / joins**: `JOIN` without `ON` (cartesian), possible many-to-many joins between large tables on non-key columns, joins on columns added as nullable in the same migration, function calls on join keys (`UPPER(...)` / `CAST(...)` etc. — kills index seeks)
- **Transactions**: missing `BEGIN TRAN` / `COMMIT` wrapper (T-SQL)
- **Rollback**: missing companion `.down.sql` script, `DROP INDEX` without a matching `CREATE INDEX` in the same migration
- **Naming**: non-snake-case, too-long, reserved-word table/column names
- **Sequencing**: version gaps and duplicates across a directory of migrations

A full list with rule IDs lives in the repo README under "What it catches (rule pack)".

## Safety guarantees

- The CLI has **no database connection code anywhere**. It cannot read or write to any DB.
- Schema-impact estimates ("app.customer has 12.4M rows") come from a bundled JSON snapshot at `src/migguard/data/schema_snapshot.json`, not a live connection.
- The skill runs MigGuard as a local Python process. No network calls unless the user explicitly enables the LLM mode.

## Examples

### Example 1 — risky DELETE

User: "Quick check on this migration before I push it: `DELETE FROM app.audit_log;`"

Skill action: write the SQL to a temp file (or use `--stdin` if available), run MigGuard with `--format markdown --no-llm`, summarize:

> HIGH — `DELETE` without `WHERE` on `app.audit_log`. This removes every row in the table. Suggested fix: add a `WHERE` clause scoped to the rows you want to delete, or use a soft-delete pattern. Full report below.

Then render the markdown report.

### Example 2 — opens a migration file

User opens `migrations/V042__add_status_column.sql`.

Skill action: proactively offer "Want me to run MigGuard on this?" If they accept, run:

```bash
python -m migguard.cli.main review migrations/V042__add_status_column.sql --format markdown --no-llm
```

Summarize top findings, render markdown.

### Example 3 — directory of migrations

User: "Review the whole migrations folder before the release."

Skill action:

```bash
python -m migguard.cli.main review migrations/ --format markdown --no-llm
```

Highlight any sequencing issues (version gaps / duplicates) first — those are batch-level findings.

## If MigGuard isn't installed

Run from the repo root:

```bash
pip install -e ".[dev]"
```

Then re-run the review.

## Resources

- Repo: https://github.com/abcfitness-insights/hackathon-2026-databasemigration
- Full README with rule list, CLI reference, web playground, PR bot setup
- TESTING.md for the complete teammate test plan
- docs/PITCH.md for the one-page judge brief
