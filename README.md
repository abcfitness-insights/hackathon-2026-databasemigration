# MigGuard AI

> **Reviews database migration scripts for consistency and risk before execution.**

An AI-powered SQL migration reviewer. Reads your `.sql` migrations and tells you what will break, what will be slow, and how to fix it — *before* anyone runs them against a database.

Built for the ABC Fitness Engineering AI hackathon. Targets T-SQL / SQL Server / Synapse. Works with both **GitHub** and **Azure DevOps** out of the box.

---

## What it does

| | |
|--|--|
| **Catches** | Data loss, locking / downtime, missing rollback, idempotency gaps, missing transactions, Synapse-incompatible constructs, permission changes, version-sequence gaps and duplicates, naming-convention violations |
| **Where it runs** | Local CLI, GitHub PR bot, Azure DevOps PR bot — all from the same engine |
| **Dialects** | T-SQL / Synapse (default), Postgres, MySQL, SQLite — dialect-specific rules filter themselves automatically |
| **How** | Deterministic rule pack (16 rules) for the well-known patterns + optional LLM layer for contextual judgment + schema-grounded impact estimates |
| **Output** | Rich terminal report, JSON artifact, Markdown PR comment with collapsible suggested fixes, plus PR status check (`succeeded` / `failed`) |

## The killer demo moment

Most static SQL linters say "this `ALTER` might be slow."

MigGuard says:

> **HIGH — Locking.** `ALTER TABLE dw.member ADD loyalty_tier VARCHAR(20) NOT NULL DEFAULT 'standard'`
>
> `dw.member` has **12,400,000 rows** and **3 active indexes**. On Synapse, this rewrites the entire table and holds a SCH-M lock, blocking all readers.
>
> **Estimated SCH-M lock window: 2–6 min.**
>
> **Suggested fix:** add the column as nullable, backfill in batches, then `ALTER COLUMN ... NOT NULL`.

Real numbers from real tables. The schema-facts layer reads a JSON snapshot for the demo (no DB writes, ever — and zero risk to the demo if the DB is offline). In production you swap the snapshot loader for a thin wrapper around your read-only Synapse MCP that queries `sys.dm_db_partition_stats` and `sys.indexes`.

---

## Quick start (60 seconds)

```bash
cd migguard
pip install -e ".[dev]"
pytest                              # 42 tests, all green
python demo/run_demo.py --no-llm    # run on every bundled fixture
```

> **For teammates testing this:** see **[TESTING.md](TESTING.md)** for the complete step-by-step test plan — CLI, local bot with fake GitHub + Azure DevOps webhooks, and real-PR setup with ngrok. About 15 minutes start to finish.
>
> **For judges and reviewers:** see **[docs/PITCH.md](docs/PITCH.md)** for the one-page pitch — problem, solution, what makes it unique, safety guarantees, and build details.

## CLI

```bash
migguard review path/to/migration.sql
migguard review path/to/migrations/                       # whole directory (enables sequencing checks)
migguard review m.sql --format json --output report.json
migguard review m.sql --format markdown                   # for PR comment
migguard review m.sql --strict                            # exit 1 if any HIGH
migguard review pg/                  --dialect postgres   # multi-dialect
migguard rules                                            # list all rules
migguard rules --dialect postgres                         # filter rules by dialect
```

### Supported dialects

`tsql` (default, includes Synapse), `postgres`, `mysql`, `sqlite`. T-SQL-specific rules
(``ONLINE = ON``, ``sys.columns``, ``BEGIN TRAN``, ``MERGE``-on-Synapse) are automatically
skipped when the dialect doesn't match — there are no false positives across dialects.

## PR bot

The same engine that powers the CLI also serves a webhook for both **GitHub** and **Azure DevOps**. Detection is automatic — point both webhooks at `/webhook` and MigGuard figures out the rest.

### GitHub setup

1. Create a fine-grained PAT with `Pull requests: read+write` and `Contents: read`.
2. In repo settings, add a webhook:
   - Payload URL: `https://your-host/webhook`
   - Content type: `application/json`
   - Events: **Pull requests** only
3. Deploy MigGuard with `GITHUB_TOKEN` set.

### Azure DevOps setup

1. Create a PAT with `Code (read & write)` scope on the org.
2. In project settings, add a Service Hook:
   - Trigger: **Pull request created** AND **Pull request updated**
   - Action: **Web Hooks**
   - URL: `https://your-host/webhook`
3. Deploy MigGuard with `AZDO_PAT` and `AZDO_ORG_URL` set.

That's it. Open a PR that touches a `.sql` file under `migrations/`, `db/migrations/`, or `sql/migrations/` (configurable via `MIGGUARD_MIGRATION_PATHS`), and MigGuard will post a review comment and set a status check within 30 seconds.

## LLM (optional)

If you set one of these, the LLM layer adds judgment-call findings, a plain-English summary, and an auto-generated rollback script. If neither is set, MigGuard runs deterministically with no LLM — still fully useful.

| Provider | Env vars |
|----------|----------|
| Azure OpenAI | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` (default `gpt-4o`), `AZURE_OPENAI_API_VERSION` (default `2024-06-01`) |
| OpenAI direct | `OPENAI_API_KEY`, `MIGGUARD_LLM_MODEL` (default `gpt-4o-mini`) |
| Disabled | `MIGGUARD_LLM_PROVIDER=disabled` |

## What it catches (rule pack)

| Rule ID | Severity | Category | Dialects |
|---------|----------|----------|----------|
| `data-loss/delete-without-where` | HIGH | Data loss | all |
| `data-loss/update-without-where` | HIGH | Data loss | all |
| `data-loss/truncate` | HIGH | Data loss | all |
| `data-loss/drop-table` | HIGH | Data loss | all |
| `data-loss/drop-schema` | HIGH | Data loss | all |
| `locking/not-null-default-on-large-table` | MEDIUM (HIGH on >1M rows) | Locking | all |
| `locking/create-index-without-online` | MEDIUM | Locking | tsql |
| `idempotency/drop-without-if-exists` | MEDIUM | Idempotency | all |
| `idempotency/create-table-without-if-not-exists` | LOW | Idempotency | all |
| `idempotency/alter-add-column-without-guard` | LOW | Idempotency | tsql |
| `compatibility/merge-on-synapse` | MEDIUM | Compatibility | tsql |
| `permissions/grant-or-deny` | MEDIUM / HIGH | Permissions | all |
| `transaction/missing-tran-wrapper` | LOW | Transactions | tsql |
| `rollback/no-down-script` | MEDIUM | Rollback | all |
| `naming/non-snake-case`, `naming/too-long`, `naming/reserved-word` | LOW / MEDIUM | Naming | all |
| `sequencing/version-gap` | MEDIUM | Ordering | all |
| `sequencing/duplicate-version` | HIGH | Ordering | all |

LLM-only layer (when enabled) adds:
- Cross-statement ordering risks
- Plain-English summary for non-DBA reviewers
- Auto-generated rollback script (always labeled "review required")

## Architecture

```
.sql file
    |
    v
sqlglot parser  ----------+
    |                     |
    v                     v
Deterministic rules   Schema facts
(AST + regex)         (JSON snapshot)
    |                     |
    +-------+-------------+
            v
       LLM analyzer (optional)
            |
            v
       Report (Pydantic)
            |
    +-------+-------+----------------+
    |       |       |                |
    v       v       v                v
Terminal  JSON   Markdown    PR comment + status
                              (GitHub or ADO)
```

## Project layout

```
migguard/
  pyproject.toml
  README.md
  src/migguard/
    core/
      engine.py          # orchestrator
      parser.py          # sqlglot T-SQL wrapper + GO / ; splitting
      models.py          # Finding / Report Pydantic models
      schema_client.py   # JSON-snapshot schema facts (no live DB)
    rules/
      base.py            # Rule base class + RuleContext + TableFacts
      registry.py        # all_rules()
      checks/            # one file per rule category
    llm/
      analyzer.py        # Azure OpenAI / OpenAI client, optional
      prompts/           # risk_review.md, rollback_gen.md
    cli/
      main.py            # click-based CLI
      formatters.py      # terminal / JSON / markdown
    bot/
      app.py             # FastAPI webhook
      providers.py       # GitHub + Azure DevOps adapters
    data/
      schema_snapshot.json   # demo schema facts
  demo/
    run_demo.py          # runs every fixture
  deploy/
    Dockerfile
  tests/
    test_models.py
    test_parser.py
    test_rules.py
    test_bot.py
    fixtures/migrations/ # 5 example migrations (clean + 4 bad)
```

## Safety guarantees

- **Zero DB writes.** The engine has no DB connection code anywhere. The schema-facts layer reads a JSON snapshot. To wire in live data, replace `core/schema_client.py` with read-only `SELECT`s against `sys.dm_db_partition_stats` / `sys.indexes`. The hackathon implementation never touches a DB.
- **Read-only PR access.** Both provider adapters only need `pull_requests:read+write` and `contents:read`. No code is pushed, nothing is merged.
- **LLM optional.** The deterministic rule pack is the source of truth. The LLM only adds findings; it never overrides or downgrades a rule finding.

## Deployment

```bash
docker build -t migguard:0.1.0 -f deploy/Dockerfile .
docker run -p 8080:8080 \
  -e GITHUB_TOKEN=ghp_... \
  -e AZDO_PAT=... \
  -e AZDO_ORG_URL=https://dev.azure.com/your-org \
  -e AZURE_OPENAI_API_KEY=... \
  -e AZURE_OPENAI_ENDPOINT=https://....openai.azure.com/ \
  -e AZURE_OPENAI_DEPLOYMENT=gpt-4o \
  migguard:0.1.0
```

The container exposes `:8080`. Deploy anywhere that can receive webhooks — Azure Container Apps, AWS App Runner, Fly.io, Render, a VM with Caddy in front, your call.

## Future work (post-hackathon)

- Live schema facts via the read-only Synapse MCP (replace JSON snapshot)
- More dialect-specific rules for Postgres / MySQL (e.g. `CREATE INDEX CONCURRENTLY`, MySQL `pt-online-schema-change` patterns)
- Cross-migration object lifetime tracking (deterministic, no LLM) — flag if column added in V3 is dropped in V5 but referenced in V4
- Cursor IDE skill wrapping the CLI — instant feedback as you write the migration
- Slack notifier for HIGH-risk migrations across the org
- Historical mode: analyze the last 6 months of merged migrations and produce a risk dashboard
- Checksum drift detection on applied migrations (requires persistent state)
- `EXPLAIN` / `EXPLAIN ANALYZE` dry-run via the read-only Synapse MCP (no DB writes; deferred for safety review)
- Ranked finding deduplication (when both AST and regex rules fire on the same statement)

## License & Author

Proprietary — ABC Fitness internal use.

Built by **Nishant Mishra** · ABC Fitness Engineering AI Hackathon 2026.
