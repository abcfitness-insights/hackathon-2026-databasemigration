# MigGuard AI

> **Reviews database migration scripts for consistency and risk before execution.**

An AI-powered SQL migration reviewer. Reads your `.sql` migrations and tells you what will break, what will be slow, and how to fix it — *before* anyone runs them against a database.

Built for the ABC Fitness Engineering AI hackathon. Targets T-SQL / SQL Server / Synapse. Works with both **GitHub** and **Azure DevOps** out of the box.

---

## What it does

| | |
|--|--|
| **Catches** | Data loss, locking / downtime, missing rollback, idempotency gaps, missing transactions, Synapse-incompatible constructs, permission changes, version-sequence gaps and duplicates, naming-convention violations, **cross-migration object-lifetime bugs** (column referenced in V4 after V3 dropped it), **MySQL InnoDB footguns** (ALGORITHM hint, utf8mb4, zero-date defaults), **join-explosion risks** (cartesian, many-to-many, function-on-key, nullable-key), **`DROP INDEX` without recreation**, **DBCC commands inside migrations** (escalates to HIGH for `SHRINK*` / `REPAIR_ALLOW_DATA_LOSS` / `DROPCLEANBUFFERS`), **plus team-specific regex rule packs** (no-NOLOCK, schema prefixes, etc.) |
| **Where it runs** | Local CLI, **pre-commit hook**, GitHub PR bot, Azure DevOps PR bot, GitHub Actions CI, local web playground, Cursor IDE skill — all from the same engine |
| **Databases** | **SQL Server, Azure SQL, Synapse (deep), MySQL (deep)** — parser also accepts Postgres and SQLite via the dialect-agnostic rules; deeper Postgres/Snowflake/Databricks rule packs are explicit future work, added on team demand |
| **How** | Deterministic rule pack (26 built-in rules + your own YAML regex rules) for the well-known patterns + optional LLM layer for contextual judgment + schema-grounded impact estimates |
| **Output** | Rich terminal report, JSON, **SARIF 2.1.0** (GitHub Advanced Security / SonarQube), Markdown PR comment with TL;DR header and per-severity collapsing, self-contained HTML report, local web playground, plus PR status check (`succeeded` / `failed`) |

## The killer demo moment

Most static SQL linters say "this `ALTER` might be slow."

MigGuard says:

> **HIGH — Locking.** `ALTER TABLE app.customer ADD priority_band VARCHAR(20) NOT NULL DEFAULT 'standard'`
>
> `app.customer` has **12,400,000 rows** and **3 active indexes**. On Synapse, this rewrites the entire table and holds a SCH-M lock, blocking all readers.
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
pytest                              # 169 tests, all green
python demo/run_demo.py --no-llm    # run on every bundled fixture
```

> **For teammates testing this:** see **[TESTING.md](TESTING.md)** for the complete step-by-step test plan — CLI, local bot with fake GitHub + Azure DevOps webhooks, and real-PR setup with ngrok. About 15 minutes start to finish.
>
> **For judges and reviewers:** see **[docs/PITCH.md](docs/PITCH.md)** for the one-page pitch — problem, solution, what makes it unique, safety guarantees, and build details.

## Web playground (local)

For a no-install, browser-based demo, run the bot locally and open the playground:

```bash
python -m uvicorn migguard.bot.app:app --reload --port 8080
# then open http://localhost:8080/playground
```

Paste any SQL migration, pick a dialect, click **Review**. Findings render inline as a colored HTML report with severity badges, schema-grounded context, and collapsible suggested fixes. The script is never executed against any database — it is only parsed into a `sqlglot` AST.

Sharing the playground with the team during a demo? Expose it temporarily via ngrok:

```bash
ngrok http 8080
```

> The playground caps input at 200 KB, validates the dialect against the supported list, strips path components from the uploaded filename, and disables the LLM by default — so it works without any API keys.

## Cursor IDE skill

For Cursor users, MigGuard ships an agent skill that runs the reviewer inline while you write a migration — no terminal, no PR required.

The canonical source lives at `skills/migguard-review/`. To enable it locally, copy or symlink the folder into your Cursor skills directory:

```bash
# Personal (available across all your Cursor projects)
cp -r skills/migguard-review/ ~/.cursor/skills/

# Or, project-scoped (shared via repo) — add it to your own repo's .cursor/skills/
cp -r skills/migguard-review/ .cursor/skills/
```

On Windows:

```powershell
# Personal
Copy-Item -Recurse skills\migguard-review $HOME\.cursor\skills\

# Or symlink (admin shell):
New-Item -ItemType SymbolicLink -Path "$HOME\.cursor\skills\migguard-review" `
    -Target "$PWD\skills\migguard-review"
```

Once installed, Cursor picks it up automatically. Open a `.sql` migration file (or paste SQL into the agent) and the skill triggers — runs `python -m migguard.cli.main review <file> --format markdown --no-llm`, summarizes findings, and offers to apply suggested fixes through Cursor's standard file-edit approval flow.

> The skill never connects to a database. It only invokes the same local CLI you'd run yourself.

## Pre-commit hook

For the fastest feedback loop, wire MigGuard into [pre-commit](https://pre-commit.com). Every `git commit` that stages a `.sql` file runs the reviewer locally before the commit lands.

Add to `.pre-commit-config.yaml` in any consuming repo:

```yaml
repos:
  - repo: https://github.com/abcfitness-insights/hackathon-2026-databasemigration
    rev: master
    hooks:
      - id: migguard-review            # blocks on HIGH (default)
      # - id: migguard-review-strict   # blocks on MEDIUM too
```

Then:

```bash
pre-commit install
git add migrations/V123__add_column.sql
git commit -m "Add priority_band"     # MigGuard runs against the staged file
```

The hook runs with `--no-llm`, so it's fast and works offline. Findings print to the terminal; the commit aborts if anything blocks.

## Custom rule packs (YAML)

Teams have policies that the built-in rules can't know about — "never reference `legacy_2018.*`", "all new tables must live in the `app` schema", "no `WITH (NOLOCK)`". You can encode those as regex rules in a YAML file and load them alongside the built-ins:

```yaml
# rules.yaml
version: 1
rules:
  - id: company/no-nolock-hint
    title: NOLOCK hint forbidden
    severity: medium
    category: locking
    dialects: [tsql]
    pattern: '(?i)\bWITH\s*\(\s*NOLOCK\s*\)'
    message: NOLOCK reads dirty data. Use snapshot isolation.
```

```bash
migguard review migrations/ --rules-config rules.yaml
```

See `examples/custom-rules.yaml` for a fully commented sample. Valid `category` values match the built-in categories (`data_loss`, `locking`, `idempotency`, `compatibility`, `permissions`, `transaction`, `rollback`, `naming`, `ordering`, `dependency`, `performance`, `other`); valid `severity` values are `high`, `medium`, `low`, `info`. Bad YAML fails loud with a precise per-rule error message — there's no silent skipping.

## SARIF for code-scanning dashboards

`--format sarif` emits a [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html) log that drops straight into GitHub Advanced Security, Azure DevOps Advanced Security, SonarQube, Codacy, and most enterprise code-scanning dashboards. Inline annotations on the PR diff are then handled by the scanner, not the bot.

```yaml
# .github/workflows/migguard.yml addition
- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: migguard.sarif
```

## CLI

```bash
migguard review path/to/migration.sql
migguard review path/to/migrations/                       # whole directory (enables sequencing checks)
migguard review m.sql --format json --output report.json
migguard review m.sql --format markdown                   # for PR comment
migguard review m.sql --format html --output report.html  # self-contained HTML page
migguard review m.sql --format sarif --output mg.sarif    # for GitHub / Azure code scanning
migguard review m.sql --fail-on high                      # exit 1 if any HIGH (CI gate)
migguard review m.sql --fail-on medium                    # stricter gate
migguard review m.sql --fail-on never                     # report-only
migguard review m.sql --rules-config rules.yaml           # add custom regex rules
migguard review pg/                  --dialect postgres   # multi-dialect
migguard rules                                            # list all rules
migguard rules --dialect postgres                         # filter rules by dialect
migguard explain data-loss/delete-without-where           # docs for one rule
```

> `--strict` is preserved as an alias for `--fail-on high` so existing CI YAML keeps working.

### Supported databases

| Database | `--dialect` | Rule depth |
|----------|-------------|------------|
| SQL Server, Azure SQL, **Synapse** | `tsql` (default) | **Deep** — 23 rules including T-SQL-specific (`ONLINE = ON`, `sys.columns` guards, `BEGIN TRAN` wrappers, `MERGE`-on-Synapse, `DBCC` commands) |
| **MySQL** | `mysql` | **Deep** — 21 rules: universal coverage + MySQL/InnoDB specifics (`ALGORITHM=` hint, `utf8mb4` vs `utf8`, zero-date defaults) |
| PostgreSQL | `postgres` | **Parser + universal** — 18 dialect-agnostic rules (data loss, naming, rollback, sequencing, lifetime, permissions, NOT NULL DEFAULT, join-risk, index-lifecycle). Deeper Postgres rule pack (`CREATE INDEX CONCURRENTLY`, `LOCK ACCESS EXCLUSIVE`, etc.) is future work. |
| SQLite | `sqlite` | Parser + universal — 18 rules |
| Oracle, Snowflake, Databricks, BigQuery, Redshift, DB2 | not yet | Future work, added on team demand |

Dialect-specific rules filter themselves automatically — a `mysql/*` rule never fires when `--dialect tsql` is used, and vice versa. There are no false positives across dialects.

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

## CI / GitHub Actions

The repo ships two GitHub Actions workflows so every PR is automatically reviewed and tested — no webhook server to deploy, no secrets to configure.

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `.github/workflows/migguard.yml` | `pull_request` (master / main) | Runs MigGuard on every `.sql` file changed in the PR, posts a sticky review comment, uploads the report as an artifact, and fails the check if any HIGH-severity finding is present. |
| `.github/workflows/tests.yml`    | `push` + `pull_request` (master / main) | Runs the full `pytest` suite as a regression guard. |

**No setup required** — the workflows use the built-in `GITHUB_TOKEN` and run with `--no-llm`, so they work the moment the repo is cloned to GitHub. Just make sure **Settings → Actions → General → Workflow permissions** is set to **Read and write permissions** so the review workflow can post PR comments.

Sticky comments: the review workflow tags its comment with a hidden HTML marker (`<!-- migguard-review-comment -->`) and rewrites the same comment on every push, instead of flooding the PR with one comment per push.

Want LLM-generated summaries and rollback scripts in CI too? Add `AZURE_OPENAI_*` secrets in **Settings → Secrets and variables → Actions**, then drop `--no-llm` from `migguard.yml`.

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
| `joins/missing-on-clause` | HIGH (MEDIUM on explicit `CROSS JOIN`) | Performance | all |
| `joins/many-to-many-risk` | MEDIUM | Performance | all |
| `joins/on-nullable-key` | LOW | Performance | all |
| `joins/function-on-key` | MEDIUM | Performance | all |
| `rollback/index-drop-without-recreate` | MEDIUM | Rollback | all |
| `compatibility/dbcc-command` | MEDIUM (HIGH on `SHRINK*` / `REPAIR_ALLOW_DATA_LOSS` / `DROPCLEANBUFFERS`) | Compatibility | tsql |
| `lifetime/dropped-table-referenced` | HIGH | Ordering | all |
| `lifetime/dropped-column-referenced` | HIGH | Ordering | all |
| `mysql/alter-table-without-algorithm` | MEDIUM (HIGH on >1M rows) | Locking | mysql |
| `mysql/utf8-not-utf8mb4` | MEDIUM | Compatibility | mysql |
| `mysql/zero-date-default` | MEDIUM | Compatibility | mysql |

LLM-only layer (when enabled) adds:
- Cross-statement ordering risks
- Plain-English summary for non-DBA reviewers
- Auto-generated rollback script (always labeled "review required")

## Architecture

```
.sql file
    |
    v
sqlglot parser  ----------+----------+
    |                     |          |
    v                     v          v
Built-in rules        Schema facts   YAML rule packs
(AST + regex)         (JSON snapshot)   (user regex)
    |                     |          |
    +---------+-----------+----------+
              v
         LLM analyzer (optional)
              |
              v
         Report (Pydantic)
              |
    +-----+-----+--------+-------+----------+----------+----------+----------+
    |     |     |        |       |          |          |          |          |
    v     v     v        v       v          v          v          v          v
Terminal JSON  SARIF  Markdown   HTML   Playground  Cursor    Pre-commit  PR comment
                                       (browser)   IDE skill   hook       + status
                                                                          (GitHub / ADO)
```

## Project layout

```
migguard/
  pyproject.toml
  README.md
  .pre-commit-hooks.yaml          # shared pre-commit hook manifest
  src/migguard/
    core/
      engine.py          # orchestrator
      parser.py          # sqlglot T-SQL wrapper + GO / ; splitting
      models.py          # Finding / Report Pydantic models + grouping helpers
      schema_client.py   # JSON-snapshot schema facts (no live DB)
    rules/
      base.py            # Rule base class + RuleContext + TableFacts
      registry.py        # all_rules()
      checks/            # one file per rule category (incl. mysql_rules.py)
      yaml_rules.py      # YAML regex rule-pack loader
    llm/
      analyzer.py        # Azure OpenAI / OpenAI client, optional
      prompts/           # risk_review.md, rollback_gen.md
    cli/
      main.py            # click-based CLI: review, rules, explain
      formatters.py      # terminal / JSON / markdown / HTML / SARIF
      explanations.py    # per-rule docstrings for `migguard explain`
    bot/
      app.py             # FastAPI webhook + local web playground
      providers.py       # GitHub + Azure DevOps adapters
    data/
      schema_snapshot.json   # demo schema facts
  skills/
    migguard-review/
      SKILL.md           # Cursor IDE agent skill
  examples/
    custom-rules.yaml    # sample YAML rule pack
  demo/
    run_demo.py          # runs every fixture
  deploy/
    Dockerfile
  .github/
    workflows/
      migguard.yml       # PR review on every changed .sql
      tests.yml          # pytest regression guard
  tests/
    test_models.py
    test_parser.py
    test_rules.py
    test_sequencing.py
    test_naming.py
    test_dialect.py
    test_bot.py
    test_playground.py        # web playground GET/POST
    test_html_format.py       # HTML report formatter
    test_skill_smoke.py       # Cursor skill frontmatter + body contract
    test_workflows.py         # GitHub Actions workflow contract
    test_lifetimes.py         # cross-migration object lifetime rule
    test_sarif_format.py      # SARIF 2.1.0 output
    test_findings_grouping.py # per-location finding grouping
    test_md_collapse.py       # markdown TL;DR + LOW/INFO collapse
    test_explain.py           # `migguard explain` subcommand
    test_fail_on.py           # --fail-on threshold + --strict alias
    test_yaml_rules.py        # custom YAML rule packs
    test_precommit_hooks.py   # .pre-commit-hooks.yaml manifest
    test_mysql_rules.py       # MySQL-specific rule pack
    fixtures/migrations/  # example migrations (clean + risky)
    fixtures/lifetimes/   # cross-migration object lifetime fixtures
    fixtures/mysql/       # MySQL rule pack fixtures
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
- Deeper Postgres rule pack (`CREATE INDEX CONCURRENTLY`, `LOCK ACCESS EXCLUSIVE`, `ALTER TABLE ... SET NOT NULL` rewrites, `VACUUM FULL`)
- Snowflake rule pack (`CREATE OR REPLACE TABLE` data loss, time-travel retention, warehouse cost ops)
- Databricks SQL / Delta Lake rule pack (`VACUUM` retention, schema-enforcement bypass, partition-pruning regressions)
- Slack notifier for HIGH-risk migrations across the org
- Historical mode: analyze the last 6 months of merged migrations and produce a risk dashboard
- Checksum drift detection on applied migrations (requires persistent state)
- `EXPLAIN` / `EXPLAIN ANALYZE` dry-run via the read-only Synapse MCP (no DB writes; deferred for safety review)
- `migguard fix` interactive mode (apply suggested fixes via Cursor's edit-approval flow)
- Stored-proc / view dependency tracking across the catalog
- `--baseline` / `--diff-only` review modes (only fail on net-new findings — adoption unlock for legacy repos)
- Webhook signature verification (GitHub `X-Hub-Signature-256` + ADO equivalent)

## License & Author

Proprietary — ABC Fitness internal use.

Built by **Nishant Mishra** · ABC Fitness Engineering AI Hackathon 2026.
