# Testing MigGuard

This is the complete step-by-step test plan. Anyone with the repo cloned should be able to follow it top to bottom in about 15 minutes and end up confident the tool works.

Three levels of fidelity, easiest to hardest:

| Level | What you prove | Time | Needs |
|---|---|---|---|
| **1** | The engine works — finds real issues in real SQL | 5 min | Python 3.10+ only |
| **2** | The PR bot works end-to-end (GitHub + Azure DevOps) | 10 min | Bot running locally |
| **3** | Real PR with a real comment posted to a real repo | 30 min | ngrok + a sandbox repo + a token |

**For a hackathon demo, Levels 1 + 2 are enough.** Level 3 is optional bragging rights.

---

## Prerequisites (one-time, 2 minutes)

```powershell
cd migguard
pip install -e ".[dev]"
```

This installs MigGuard in editable mode plus all dev dependencies (`pytest`, `fastapi`, `uvicorn`, etc.). You only do this once per clone.

Verify the install:

```powershell
python -m migguard.cli.main --help
```

Should show the `migguard` CLI help. If you see this, you're ready.

---

## Level 1 — CLI testing (the engine)

### 1.1 Run the test suite (the sanity check)

```powershell
python -m pytest -v
```

**Expected:** A long list of `PASSED` lines and at the bottom `42 passed in ~0.5s`. If you see 42 passed, the engine is healthy.

### 1.2 List all the rules MigGuard knows

```powershell
python -m migguard.cli.main rules
```

**Expected:** A table of 16 rules — their IDs, severities (high / medium / low), which dialects they apply to, one-line descriptions.

Try filtering by dialect:

```powershell
python -m migguard.cli.main rules --dialect postgres
```

**Expected:** 12 rules instead of 16 — the four T-SQL-specific rules disappear.

### 1.3 Review a clean migration (proves no false positives)

```powershell
python -m migguard.cli.main review tests/fixtures/migrations/01_clean_add_column.sql
```

**Expected:** Green `No findings. Migration looks safe to review.`

### 1.4 Review the showstopper bad migration

```powershell
python -m migguard.cli.main review tests/fixtures/migrations/02_delete_without_where.sql
```

**Expected:** A red **HIGH** finding that includes the literal text **"~47,200,000 rows"** — that's MigGuard cross-referencing the SQL with the schema snapshot to estimate impact. This is the demo moment.

### 1.5 Review a multi-issue file

```powershell
python -m migguard.cli.main review tests/fixtures/migrations/05_mixed_severity.sql
```

**Expected:** A report with findings across multiple categories: Data Loss, Idempotency, Compatibility, Permissions.

### 1.6 Run the full demo (loops over every fixture)

```powershell
python demo/run_demo.py --no-llm
```

**Expected:** The tool reviews every fixture in order — clean one is green, bad ones are red/yellow. Best command to record for a demo video.

### 1.7 Try all three output formats

```powershell
python -m migguard.cli.main review tests/fixtures/migrations/02_delete_without_where.sql
python -m migguard.cli.main review tests/fixtures/migrations/02_delete_without_where.sql --format json --output report.json
python -m migguard.cli.main review tests/fixtures/migrations/02_delete_without_where.sql --format markdown --output report.md
```

- **Terminal** format = colored output in the console
- **JSON** format = machine-readable, what CI/CD consumes
- **Markdown** format = exactly what would be posted as a PR comment

Open `report.md` in VS Code / Cursor — that's the literal text that goes on a PR.

### 1.8 Try a non-T-SQL dialect

```powershell
python -m migguard.cli.main review tests/fixtures/postgres/01_add_index_without_concurrently.sql --dialect postgres
```

**Expected:** Catches the DELETE-without-WHERE finding and silently skips rules that only apply to T-SQL.

### 1.9 Version sequencing across files (multi-file check)

```powershell
python -m migguard.cli.main review tests/fixtures/sequencing/
python -m migguard.cli.main review tests/fixtures/duplicates/
```

**Expected:**
- First command: MEDIUM finding `"Version sequence jumps from 2 to 4 - missing: V003"` (because the fixtures contain V001, V002, V004, V005)
- Second command: HIGH finding flagging that V003 is claimed twice

### 1.10 Strict mode (the CI behavior)

```powershell
python -m migguard.cli.main review tests/fixtures/migrations/02_delete_without_where.sql --strict
echo $LASTEXITCODE
```

**Expected:** Prints `1` — exit code indicating "blocking finding". This is how a CI pipeline blocks a bad migration from merging.

```powershell
python -m migguard.cli.main review tests/fixtures/migrations/01_clean_add_column.sql --strict
echo $LASTEXITCODE
```

**Expected:** Prints `0` — passed.

---

## Level 2 — Bot testing on your laptop (no deployment needed)

This is the path most teammates will care about. You run the FastAPI bot locally and send it fake webhook payloads. This proves the entire bot pipeline (event detection → provider routing → file fetching → review → comment) works for **both GitHub and Azure DevOps** from one binary.

### IMPORTANT — Windows PowerShell gotcha

In PowerShell, **`curl` is an alias for `Invoke-WebRequest`**, which is NOT the real curl and shows a scary "Script Execution Risk" prompt for HTTP responses. Always use **`curl.exe`** (with the `.exe`) on Windows. The real curl ships with Windows 10+ and works exactly as documented.

If you prefer pure PowerShell, replace `curl.exe` with `Invoke-RestMethod` — it's purpose-built for JSON APIs.

### 2.1 Start the bot in one terminal window

```powershell
python -m uvicorn migguard.bot.app:app --port 8080 --reload
```

**Expected output:**
```
INFO:     Uvicorn running on http://127.0.0.1:8080 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Application startup complete.
```

Leave this window running. Open a **second** PowerShell window for the rest of the steps.

### 2.2 Confirm the bot is alive

```powershell
curl.exe http://localhost:8080/health
```

**Expected:** `{"status":"ok"}`

```powershell
curl.exe http://localhost:8080/
```

**Expected:** JSON saying `{"name":"MigGuard AI","version":"0.1.0","endpoints":"/health (GET), /webhook (POST)","supports":"GitHub, Azure DevOps"}`

### 2.3 Send a fake GitHub webhook

We ship a sample payload at `tests/fixtures/webhooks/github_pr_opened.json` — it mimics what GitHub sends when a developer opens a PR.

```powershell
curl.exe -X POST http://localhost:8080/webhook `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: pull_request" `
  -d "@tests/fixtures/webhooks/github_pr_opened.json"
```

**What you'll see in the BOT window:**
```
INFO migguard.bot: PR event: github your-org/your-repo#42 (Add member_tier column)
```

That log line is the proof: **MigGuard detected the GitHub event, parsed it correctly, and routed it through the GitHub provider.** It then tries to call the real GitHub API to fetch the file — which fails because `your-org/your-repo` doesn't exist. That's expected and fine. The pipeline up to that point works.

**What you'll see in YOUR window (the curl response):**
A 502 error or a JSON error response. Don't worry — you weren't using a real repo.

### 2.4 Send a fake Azure DevOps webhook

```powershell
curl.exe -X POST http://localhost:8080/webhook `
  -H "Content-Type: application/json" `
  -d "@tests/fixtures/webhooks/azdo_pr_created.json"
```

Note: no special header for ADO — the bot detects it from the `eventType` field in the body.

**What you'll see in the BOT window:**
```
INFO migguard.bot: PR event: azdo your-project/your-repo#42 (Add member_tier column)
```

Same proof, for ADO: detection, parsing, and routing all work. Auto-detection between GitHub and Azure DevOps is confirmed.

### 2.5 Run the bot test suite (mocked end-to-end test)

Stop the bot (Ctrl+C in the first window). Now run:

```powershell
python -m pytest tests/test_bot.py -v
```

**Expected:** All bot tests pass. These tests use **mocked** GitHub and ADO providers (so they don't need real tokens) but exercise the FULL pipeline: webhook in → engine runs → comment formatted → comment posted (mocked) → status set (mocked).

If you want to see what's tested, open `tests/test_bot.py` — it's short and readable.

---

## Level 3 — Real PR testing with ngrok (optional, ~30 min)

This is the "money shot" — an actual MigGuard comment on an actual PR in an actual GitHub or Azure DevOps repo. Skip this for the demo if you're short on time; Levels 1 + 2 prove the tool works.

You need:
- A sandbox repo you don't mind getting test PRs (NOT a production repo)
- A free ngrok account (or any other public-URL tool: cloudflared, localtunnel, etc.)
- A GitHub Personal Access Token OR an Azure DevOps PAT (see below)

### 3.1 Start the bot locally

Same as Level 2.1:

```powershell
python -m uvicorn migguard.bot.app:app --port 8080 --reload
```

### 3.2 Expose the bot to the internet with ngrok

In a second PowerShell window:

```powershell
ngrok http 8080
```

ngrok prints a public URL like `https://abc123.ngrok-free.app`. This forwards public traffic to your local bot. **Keep this window open** — the URL only lives while ngrok is running.

### 3.3a Real PR on GitHub

**Set up the token (in the bot's terminal, before starting it):**

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token
2. Select your sandbox repo
3. Permissions: `Contents: Read`, `Pull requests: Read and Write`
4. Copy the token

Set it as an env var **before** starting the bot:

```powershell
$env:GITHUB_TOKEN = "github_pat_..."
python -m uvicorn migguard.bot.app:app --port 8080 --reload
```

**Configure the webhook in your sandbox repo:**

1. Repo → Settings → Webhooks → Add webhook
2. **Payload URL:** `https://abc123.ngrok-free.app/webhook` (your ngrok URL)
3. **Content type:** `application/json`
4. **Events:** select "Let me choose individual events" → check **only** "Pull requests"
5. Save

**Trigger a real review:**

1. In the sandbox repo, create a branch — call it `test/migguard-demo`
2. Add a file `migrations/V001__bad_test.sql` with:
   ```sql
   DELETE FROM dw.drdr_dmb;
   GO
   DROP TABLE dw.member;
   ```
3. Commit and push
4. Open a PR from `test/migguard-demo` → `main`

**Expected within ~10 seconds:**
- A new comment from the MigGuard bot on the PR with HIGH findings, line references, and impact estimates
- A red status check next to the merge button labeled `MigGuard / migration review` with description `2 high / 0 medium / 0 low findings`

If you make the status check a required check in branch protection rules, the merge button will be blocked. That's the full production behavior.

### 3.3b Real PR on Azure DevOps

**Set up the token:**

1. ADO → User Settings → Personal Access Tokens → New Token
2. Scopes: `Code (Read & Write)`
3. Copy the token

Set env vars and start the bot:

```powershell
$env:AZDO_PAT = "..."
$env:AZDO_ORG_URL = "https://dev.azure.com/your-org"
python -m uvicorn migguard.bot.app:app --port 8080 --reload
```

**Configure the service hook in your sandbox project:**

1. Project → Project settings → Service hooks → Create subscription
2. **Service:** Web Hooks
3. **Event:** Pull request created (add another subscription for "Pull request updated")
4. **Filter:** your sandbox repo
5. **Action URL:** `https://abc123.ngrok-free.app/webhook` (your ngrok URL)
6. Save

**Trigger a real review:**

Same as GitHub — create a branch, add a bad migration file under `migrations/`, push, open a PR.

**Expected within ~10 seconds:**
- A new thread comment on the PR with the MigGuard findings
- A status badge in the PR overview labeled `migguard/migration-review` showing `failed`

If you set the status as required in branch policies, the merge button is blocked.

---

## Bonus test — Write your own bad migration (the most fun)

The best test is to try to outsmart MigGuard. Open Cursor / VS Code, create a file `my_test.sql` anywhere, and write some intentionally bad SQL:

```sql
TRUNCATE TABLE dw.member;
GO

DROP TABLE dw.tmp_thing;
GO

UPDATE dw.drdr_dmb SET DAILY_AMOUNT = 0;
GO

GRANT ALL ON SCHEMA::dw TO PUBLIC;
GO
```

Run it:

```powershell
python -m migguard.cli.main review my_test.sql
```

You should see a wall of findings — TRUNCATE flagged, DROP TABLE flagged with the 47M row count for `dw.drdr_dmb` and 12.4M for `dw.member`, UPDATE-without-WHERE flagged, GRANT to PUBLIC flagged.

**If you can write bad SQL that MigGuard misses, that's a real bug we can fix.** Bring it up in the team channel.

---

## Recommended demo sequence (for the hackathon submission video)

Total runtime: ~6 minutes. Each step is one terminal command.

1. **The premise** (30 sec, no command)
   "Database migration scripts can take prod down. We built a tool that reviews them before they run."

2. **The test suite passes** (10 sec)
   ```powershell
   python -m pytest -v
   ```
   Just shows `42 passed` for credibility.

3. **The rule catalog** (15 sec)
   ```powershell
   python -m migguard.cli.main rules
   ```
   Shows what the tool knows.

4. **A clean migration — no false positives** (15 sec)
   ```powershell
   python -m migguard.cli.main review tests/fixtures/migrations/01_clean_add_column.sql
   ```
   Green output.

5. **The killer moment — schema-aware impact estimate** (30 sec)
   ```powershell
   python -m migguard.cli.main review tests/fixtures/migrations/02_delete_without_where.sql
   ```
   Point at the "47,200,000 rows" line. Pause. "That's the entire daily-detail table."

6. **The full demo loop** (90 sec)
   ```powershell
   python demo/run_demo.py --no-llm
   ```
   Watch every fixture get reviewed.

7. **The PR comment preview** (45 sec)
   ```powershell
   python -m migguard.cli.main review tests/fixtures/migrations/05_mixed_severity.sql --format markdown
   ```
   "This is the literal text that would appear as a comment on a GitHub or Azure DevOps PR."

8. **The bot is real, both providers work** (90 sec)
   - Start the bot
   - `curl.exe http://localhost:8080/health` → ok
   - `curl.exe` the GitHub fixture → bot log shows "PR event: github ..."
   - `curl.exe` the ADO fixture → bot log shows "PR event: azdo ..."
   - "Same endpoint, same code, same Docker image — works for both."

9. **The safety story** (30 sec, no command)
   "Zero database writes. The whole tool is read-only by architecture. Three layers of defense."

10. **Future work** (15 sec, no command)
    "Live schema facts via Synapse MCP, Cursor IDE skill, Slack notifier, historical risk dashboard."

That's the demo. 6 minutes, no infrastructure required.

---

## Troubleshooting

### `curl` shows a "Script Execution Risk" prompt in PowerShell

You used PowerShell's built-in `curl` alias (which is `Invoke-WebRequest`). Use `curl.exe` (with the `.exe`) instead. See section "Windows PowerShell gotcha" above.

### `ModuleNotFoundError: No module named 'migguard'`

You didn't install in editable mode. Run:
```powershell
cd migguard
pip install -e ".[dev]"
```

### Tests fail with `sqlglot.errors.ParseError`

You have an older `sqlglot` cached. Force-upgrade:
```powershell
pip install --upgrade --force-reinstall sqlglot
```

### Bot returns 502 when I send a fake webhook

Expected — the bot tried to call GitHub or Azure DevOps to fetch files from a fake repo. The first log line in the bot window proves detection and parsing worked. That's what Level 2 is testing.

### Bot returns 200 with `{"status":"ignored","reason":"non-actionable event"}`

The webhook payload was recognized but the `action` (GitHub) wasn't in `{opened, synchronize, reopened, edited}` or the ADO `eventType` wasn't `git.pullrequest.created` / `git.pullrequest.updated`. Use the fixture files we ship — those have correct values.

### Bot returns 200 with `"reason":"no migration files in PR"`

The list of changed files came back empty or none matched the migration path prefix (`migrations/`, `db/migrations/`, `sql/migrations/`). For a real test, make sure your test SQL file lives under one of those folders. You can override the prefixes with the env var `MIGGUARD_MIGRATION_PATHS`.

### Real PR test — MigGuard didn't comment on my PR

Check, in order:
1. ngrok is still running and the URL didn't expire
2. The webhook URL in GitHub/ADO matches the current ngrok URL exactly, including `/webhook`
3. The bot is still running and shows logs when you trigger a PR event
4. The PAT has the right scopes (`Pull requests: read+write`, `Contents: read`)
5. The PR actually contains a `.sql` file under a migration path (`migrations/`, `db/migrations/`, `sql/migrations/`)

GitHub's webhook delivery log (Settings → Webhooks → your hook → Recent Deliveries) shows what was sent and the response — useful for debugging.

### LLM-related output didn't appear

The LLM layer is optional. If you didn't set `AZURE_OPENAI_API_KEY` or `OPENAI_API_KEY`, MigGuard runs deterministically without LLM. That's fine and expected — all the rule-based findings still fire. To enable the LLM, set the env vars from the README "LLM" section.

---

## What's next

When you're confident MigGuard works, share this repo with your teammates. They follow Level 1 + Level 2 in about 15 minutes and have the same confidence you do.

For a production rollout (post-hackathon), the README "Deployment" section has the Docker steps. The only code change required for production is `core/schema_client.py` — point it at a real schema snapshot instead of the bundled one. Everything else already works.
