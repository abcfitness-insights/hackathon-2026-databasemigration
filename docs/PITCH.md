# MigGuard AI — One-Pager

**Reviews database migration scripts for consistency and risk before execution.**

## The problem (90 seconds)

Every team at ABC Fitness ships database migrations against Synapse / SQL Server. Today, those migrations get reviewed by whichever senior engineer has time. The same root causes show up in postmortems again and again:

- `DELETE` / `UPDATE` without a `WHERE` clause
- `ALTER TABLE ADD ... NOT NULL DEFAULT` on a 12M-row table that locks for 10 minutes
- `DROP TABLE` with no rollback script
- Migrations that fail on rerun because no `IF EXISTS` guard
- `MERGE` statements that work on SQL Server but break on Synapse
- Silent `GRANT` statements that change permissions outside the access-review process

Senior engineers spend hours catching these. Juniors don't know the patterns. Things slip through.

## What MigGuard does

A hybrid AI bot that reviews every migration PR automatically:

1. **Parses** the SQL with sqlglot — T-SQL (SQL Server, Azure SQL, Synapse) and MySQL with deep dialect-specific rules; Postgres and SQLite via universal rules.
2. **Runs 26 deterministic rules** for the well-known dangerous patterns. Each finding includes severity, rule ID, line range, message, and a concrete suggested fix. Teams can add their own regex rules via a YAML pack.
3. **Grounds severity in real schema facts** — row counts, indexes, FKs. `NOT NULL DEFAULT` on a small lookup table is MEDIUM; on `app.customer` (12.4M rows) it's HIGH with an estimated lock window.
4. **Optionally invokes an LLM** for cross-statement ordering risks, a plain-English summary, and a best-effort auto-generated rollback script.
5. **Posts a Markdown review comment** with collapsible suggestions and sets a PR status check (`succeeded` / `failed`).

## Why it's useful for everyone

- **Same engine, three entry points**: CLI, GitHub PR bot, Azure DevOps PR bot.
- **Auto-detect**: point both webhooks at one endpoint. No team has to switch their workflow.
- **Zero adoption friction**: a PR that touches `migrations/*.sql` gets reviewed automatically. Nobody has to remember a command.
- **LLM is optional**: works fully offline with deterministic rules. The LLM only adds, never overrides.

## Safety

- **Zero DB writes.** No DB connection code in the engine. Schema facts come from a JSON snapshot, swappable for read-only `SELECT`s against `sys.dm_db_partition_stats`.
- **Read-only PR scopes.** `pull_requests:read+write` and `contents:read`. Nothing else.
- **Stripped LLM input.** Only the migration text and aggregated table stats. No PII, no live data.

## Build / deploy / extend

- 30 unit tests, all green
- Single `Dockerfile` — runs on any container host
- One YAML rule pack (extend by adding a check class — no engine changes needed)
- Swap-in points marked for: live schema facts, more dialects, Slack notifier, IDE plugin

## The killer demo

```
HIGH — Locking.
ALTER TABLE app.customer ADD priority_band VARCHAR(20) NOT NULL DEFAULT 'standard'

app.customer has 12,400,000 rows and 3 active indexes.
On Synapse, this rewrites the entire table and holds a SCH-M lock.
Estimated SCH-M lock window: 2-6 min.

Suggested fix:
  1. ALTER TABLE app.customer ADD priority_band VARCHAR(20) NULL;
  2. Backfill in batches (UPDATE ... TOP (N)).
  3. ALTER TABLE app.customer ALTER COLUMN priority_band VARCHAR(20) NOT NULL;
```

That's not a "this might be slow" warning. That's a senior-DBA review, rendered automatically.

## What's next

- Wire live Synapse schema facts via the existing read-only MCPs (1-day swap)
- Deeper Postgres rule pack (`CREATE INDEX CONCURRENTLY`, lock-level analysis)
- Snowflake + Databricks rule packs (different risk model: data loss + cost, not locking)
- Historical mode — score every merged migration of the last 6 months, dashboard the trend
- `--baseline` / `--diff-only` modes so legacy repos can adopt without a cleanup sprint

---

*Built by **Nishant Mishra** for the ABC Fitness Engineering AI Hackathon 2026.*
