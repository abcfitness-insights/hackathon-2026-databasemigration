"""FastAPI webhook receiver + local web playground.

Accepts PR events from both GitHub and Azure DevOps on a single endpoint. The
provider is auto-detected from the request headers / payload shape. Also
serves a local web playground at ``/playground`` so anyone in the company can
paste a migration script and see the review in their browser.

Routes:
    GET  /health        -> liveness
    GET  /              -> readme blurb
    POST /webhook       -> PR event (GitHub or Azure DevOps)
    GET  /playground    -> HTML form for pasting SQL
    POST /playground    -> JSON body { sql, dialect?, filename?, no_llm?, format? }
                           returns HTML report (default) or markdown PR comment
                           when format="markdown"

Environment:
    MIGGUARD_MIGRATION_PATHS  Comma-separated path prefixes that identify
                              migration files. Default: 'migrations/,db/migrations/,
                              sql/migrations/'.
    MIGGUARD_STRICT           If 'true', posts a 'failed' status when any HIGH
                              finding is present. Default: 'true'.

Plus the provider-specific env vars described in providers.py.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from migguard import __version__
from migguard.bot.providers import detect_provider
from migguard.cli.formatters import format_html, format_markdown_pr_comment
from migguard.core.engine import Engine
from migguard.core.parser import SUPPORTED_DIALECTS
from migguard.llm.analyzer import LLMAnalyzer

logger = logging.getLogger("migguard.bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="MigGuard AI", version=__version__)

_MIGRATION_PREFIXES = [
    p.strip().rstrip("/") + "/"
    for p in os.environ.get(
        "MIGGUARD_MIGRATION_PATHS", "migrations/,db/migrations/,sql/migrations/"
    ).split(",")
    if p.strip()
]
_STRICT = os.environ.get("MIGGUARD_STRICT", "true").lower() in ("1", "true", "yes")
_DIALECT = os.environ.get("MIGGUARD_DIALECT", "tsql")


def _sanitize_repo_path(path: str) -> str | None:
    """Normalize a path from a webhook to a safe relative path, or None to reject.

    Rejects absolute paths, drive letters, and ``..`` segments that would let
    a malicious payload write outside our tempdir.
    """
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        return None
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        return None
    parts = [p for p in normalized.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) if parts else None


def _is_migration_file(path: str) -> bool:
    p = path.lower()
    if not p.endswith(".sql"):
        return False
    return any(p.startswith(prefix) or f"/{prefix}" in p for prefix in _MIGRATION_PREFIXES)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "MigGuard AI",
        "version": __version__,
        "endpoints": "/health (GET), /webhook (POST), /playground (GET, POST)",
        "supports": "GitHub, Azure DevOps",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_PLAYGROUND_MAX_BYTES = 200 * 1024
_DEFAULT_PLAYGROUND_SQL = """\
-- Paste a migration script here, or pick an example from the dropdown above.
-- This one triggers one HIGH-severity finding (NOT NULL DEFAULT on a 12M-row
-- table) and one MEDIUM finding (DROP without IF EXISTS).

ALTER TABLE app.customer
    ADD priority_band VARCHAR(20) NOT NULL DEFAULT 'standard';

DROP TABLE app.legacy_archive;
"""

# Curated examples surfaced as a preset dropdown in the playground. Each entry
# is independently runnable and exercises a different rule category.
_PLAYGROUND_PRESETS: list[dict[str, str]] = [
    {
        "id": "risky-tsql",
        "label": "T-SQL: risky ALTER + DROP (default)",
        "dialect": "tsql",
        "sql": _DEFAULT_PLAYGROUND_SQL,
    },
    {
        "id": "clean-tsql",
        "label": "T-SQL: clean realistic migration (no findings)",
        "dialect": "tsql",
        "sql": (
            "-- Add a nullable column, backfill in batches, then enforce NOT NULL.\n"
            "-- Online-safe; no long write locks; companion .down.sql exists.\n\n"
            "IF NOT EXISTS (\n"
            "    SELECT 1 FROM sys.columns\n"
            "    WHERE object_id = OBJECT_ID(N'app.customer')\n"
            "      AND name = N'priority_band'\n"
            ")\n"
            "BEGIN\n"
            "    ALTER TABLE app.customer ADD priority_band VARCHAR(20) NULL;\n"
            "END;\n"
            "GO\n\n"
            "BEGIN TRY\n"
            "    BEGIN TRANSACTION;\n"
            "    UPDATE TOP (10000) app.customer\n"
            "    SET priority_band = 'standard'\n"
            "    WHERE priority_band IS NULL;\n"
            "    COMMIT TRANSACTION;\n"
            "END TRY\n"
            "BEGIN CATCH\n"
            "    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;\n"
            "    THROW;\n"
            "END CATCH;\n"
        ),
    },
    {
        "id": "risky-mysql",
        "label": "MySQL: ALTER TABLE without ALGORITHM hint",
        "dialect": "mysql",
        "sql": (
            "-- MySQL/InnoDB may pick ALGORITHM=COPY (full table rewrite + lock).\n"
            "ALTER TABLE app.customer ADD email VARCHAR(255);\n"
        ),
    },
    {
        "id": "mysql-utf8",
        "label": "MySQL: utf8 vs utf8mb4 (silent emoji loss)",
        "dialect": "mysql",
        "sql": (
            "-- CHARACTER SET utf8 is the 3-byte alias. Can't store 4-byte chars.\n"
            "CREATE TABLE app.message (\n"
            "    id BIGINT AUTO_INCREMENT PRIMARY KEY,\n"
            "    body TEXT,\n"
            "    sent_at DATETIME NOT NULL\n"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8;\n"
        ),
    },
    {
        "id": "lifetime-bug",
        "label": "Cross-migration: column dropped then referenced",
        "dialect": "tsql",
        "sql": (
            "-- Simulates two migrations in one paste so the lifetime rule\n"
            "-- can see the DROP then the later reference.\n\n"
            "ALTER TABLE app.customer DROP COLUMN status_code;\n"
            "GO\n\n"
            "UPDATE app.customer SET status_code = 'A' WHERE id < 1000;\n"
        ),
    },
]


@app.get("/playground", response_class=HTMLResponse)
def playground_form() -> HTMLResponse:
    """Serve the playground HTML form."""
    return HTMLResponse(_render_playground_form())


@app.post("/playground")
async def playground_review(request: Request) -> Response:
    """Run MigGuard on pasted SQL and return a rendered report.

    Accepts ``application/json`` with
    ``{sql, dialect?, filename?, no_llm?, format?}``.
    ``format`` is ``"html"`` (default) or ``"markdown"`` — the markdown
    variant is what the in-page "Copy as Markdown" button consumes. LLM is
    disabled by default so the playground works without API keys.
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e!r}") from e

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    sql = body.get("sql", "")
    if not isinstance(sql, str) or not sql.strip():
        raise HTTPException(status_code=400, detail="`sql` is required and must be non-empty")
    if len(sql.encode("utf-8")) > _PLAYGROUND_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"`sql` exceeds {_PLAYGROUND_MAX_BYTES // 1024} KB playground limit",
        )

    dialect = body.get("dialect", "tsql")
    if dialect not in SUPPORTED_DIALECTS:
        raise HTTPException(
            status_code=400,
            detail=f"`dialect` must be one of {sorted(SUPPORTED_DIALECTS)}",
        )

    raw_filename = body.get("filename") or "pasted.sql"
    if not isinstance(raw_filename, str):
        raise HTTPException(status_code=400, detail="`filename` must be a string")
    filename = Path(raw_filename).name or "pasted.sql"
    if not filename.lower().endswith(".sql"):
        filename = f"{filename}.sql"

    no_llm = bool(body.get("no_llm", True))

    out_format = (body.get("format") or "html").lower()
    if out_format not in ("html", "markdown"):
        raise HTTPException(
            status_code=400,
            detail="`format` must be 'html' or 'markdown'",
        )

    with tempfile.TemporaryDirectory(prefix="migguard-playground-") as tmp:
        local = Path(tmp) / filename
        local.write_text(sql, encoding="utf-8")
        llm = None if no_llm else LLMAnalyzer()
        engine = Engine(llm=llm, dialect=dialect)
        try:
            report = engine.review([local])
        except Exception as e:
            logger.exception("playground review crashed")
            raise HTTPException(status_code=500, detail=f"review failed: {e!r}") from e

    if out_format == "markdown":
        return PlainTextResponse(format_markdown_pr_comment(report))
    return HTMLResponse(format_html(report))


def _render_playground_form() -> str:
    """Build the playground form page. Self-contained HTML/CSS/JS, no external deps."""
    import html as _html
    import json as _json

    dialect_options = "".join(
        f'<option value="{d}"{" selected" if d == "tsql" else ""}>{d}</option>'
        for d in SUPPORTED_DIALECTS
    )
    preset_options = "".join(
        f'<option value="{_html.escape(p["id"])}">{_html.escape(p["label"])}</option>'
        for p in _PLAYGROUND_PRESETS
    )
    # Embed preset SQL as a JSON object the browser can index by id. Using
    # json.dumps gives us correct JS escaping for quotes / newlines /
    # angle brackets so this is safe inside a <script> tag.
    presets_json = _json.dumps(
        {p["id"]: {"dialect": p["dialect"], "sql": p["sql"]} for p in _PLAYGROUND_PRESETS}
    )
    return (
        _PLAYGROUND_HTML_TEMPLATE
        .replace("__DIALECTS__", dialect_options)
        .replace("__PRESETS__", preset_options)
        .replace("__PRESETS_JSON__", presets_json)
        .replace("__EXAMPLE_SQL__", _html.escape(_DEFAULT_PLAYGROUND_SQL))
        .replace("__VERSION__", __version__)
    )


_PLAYGROUND_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MigGuard Playground</title>
<style>
:root {
    --primary: #1e40af;
    --accent: #6d28d9;
    --bg: #f8fafc;
    --card: #ffffff;
    --border: #e5e7eb;
    --text: #1f2937;
    --muted: #6b7280;
    --err: #dc2626;
    --ok: #15803d;
    --gutter-bg: #f3f4f6;
    --gutter-fg: #9ca3af;
    --shadow: 0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.06);
    --mono: "SF Mono", Monaco, Consolas, "Liberation Mono", monospace;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.55;
}
header {
    background: linear-gradient(135deg, var(--primary) 0%, var(--accent) 100%);
    color: white;
    padding: 2rem 1.5rem;
}
header .inner { max-width: 1100px; margin: 0 auto; }
h1 { margin: 0; font-size: 2rem; font-weight: 700; letter-spacing: -0.02em; }
header p { margin: 0.4rem 0 0; opacity: 0.92; }
main { max-width: 1100px; margin: 1.5rem auto; padding: 0 1.5rem 3rem; }
.card {
    background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 1.25rem 1.5rem; box-shadow: var(--shadow); margin-bottom: 1.25rem;
}
label { display: block; font-weight: 600; margin-bottom: 0.35rem; font-size: 0.92rem; }
.preset-row { display: flex; gap: 0.75rem; align-items: flex-end; margin-bottom: 0.9rem; flex-wrap: wrap; }
.preset-row > div { flex: 1; min-width: 240px; }

/* Line-numbered editor: parent flexbox owns the border + focus ring,
   children share the same line-height and monospace font. */
.sql-editor-wrap {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    background: #fcfcfd;
}
.sql-editor-wrap:focus-within {
    outline: 2px solid var(--primary);
    outline-offset: 1px;
    border-color: transparent;
}
.line-numbers {
    margin: 0;
    padding: 0.85rem 0.55rem 0.85rem 0.85rem;
    font-family: var(--mono);
    font-size: 0.9rem;
    line-height: 1.5;
    color: var(--gutter-fg);
    background: var(--gutter-bg);
    border-right: 1px solid var(--border);
    text-align: right;
    user-select: none;
    overflow-y: auto;
    scrollbar-width: none;
    -ms-overflow-style: none;
    min-width: 3rem;
    white-space: pre;
}
.line-numbers::-webkit-scrollbar { display: none; }
.sql-editor-wrap textarea {
    flex: 1;
    min-height: 320px;
    padding: 0.85rem 1rem;
    font-family: var(--mono);
    font-size: 0.9rem;
    line-height: 1.5;
    border: none;
    background: transparent;
    resize: vertical;
}
.sql-editor-wrap textarea:focus { outline: none; }

.controls { display: grid; grid-template-columns: 1fr 1fr auto; gap: 1rem; align-items: end; margin-top: 1rem; }
@media (max-width: 700px) { .controls { grid-template-columns: 1fr; } }
input[type=text], select {
    width: 100%; padding: 0.55rem 0.7rem; border: 1px solid var(--border);
    border-radius: 6px; font-size: 0.95rem; background: white;
}
.toggle { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; color: var(--muted); margin-top: 0.7rem; }
.shortcut {
    color: var(--muted); font-size: 0.8rem; font-family: var(--mono);
    background: var(--gutter-bg); padding: 0.1rem 0.4rem; border-radius: 4px;
}
button.primary, button.secondary {
    border: none; padding: 0.7rem 1.4rem; border-radius: 6px;
    font-size: 0.95rem; font-weight: 600; cursor: pointer;
    transition: background 0.15s;
}
button.primary { background: var(--primary); color: white; }
button.primary:hover { background: #1e3a8a; }
button.primary:disabled { background: #9ca3af; cursor: wait; }
button.secondary {
    background: white; color: var(--text);
    border: 1px solid var(--border); padding: 0.55rem 1rem;
    font-size: 0.88rem; font-weight: 500;
}
button.secondary:hover { background: var(--gutter-bg); }
.error {
    background: #fef2f2; border: 1px solid #fecaca; color: var(--err);
    padding: 0.85rem 1rem; border-radius: 6px; margin: 1rem 0;
}
.spinner {
    display: inline-block; width: 14px; height: 14px; border: 2px solid white;
    border-top-color: transparent; border-radius: 50%; animation: spin 0.7s linear infinite;
    margin-right: 0.5rem; vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }
.hint { color: var(--muted); font-size: 0.85rem; margin: 0.3rem 0 0; }
.result-actions {
    display: none;
    gap: 0.6rem; align-items: center; flex-wrap: wrap;
    margin: 1rem 0 0.6rem;
}
.result-actions.visible { display: flex; }
.feedback { color: var(--ok); font-size: 0.85rem; }
.feedback.error { color: var(--err); }
#result { margin-top: 0.5rem; }
#result iframe { width: 100%; min-height: 600px; border: 1px solid var(--border); border-radius: 8px; background: white; }
footer { color: var(--muted); font-size: 0.85rem; text-align: center; padding: 1.5rem; border-top: 1px solid var(--border); margin-top: 2rem; }
</style>
</head>
<body>
<header>
<div class="inner">
<h1>MigGuard Playground</h1>
<p>Paste a SQL migration script and get an instant risk review. Nothing is executed against any database.</p>
</div>
</header>

<main>
<form id="reviewForm" class="card" onsubmit="return submitReview(event)">
<div class="preset-row">
<div>
<label for="preset">Load an example</label>
<select id="preset">
<option value="">-- pick a preset --</option>
__PRESETS__
</select>
<p class="hint">Each preset shows one rule category in action; edit freely.</p>
</div>
</div>

<label for="sql">SQL migration script</label>
<div class="sql-editor-wrap">
<pre class="line-numbers" id="lineNumbers" aria-hidden="true">1</pre>
<textarea id="sql" name="sql" spellcheck="false" required>__EXAMPLE_SQL__</textarea>
</div>

<div class="controls">
<div>
<label for="filename">Filename (optional)</label>
<input type="text" id="filename" name="filename" placeholder="V042__add_status.sql">
<p class="hint">Used in the report header. Path components are stripped.</p>
</div>
<div>
<label for="dialect">SQL dialect</label>
<select id="dialect" name="dialect">
__DIALECTS__
</select>
</div>
<button type="submit" class="primary" id="submitBtn">Review <span class="shortcut">&#8984;&#9166;</span></button>
</div>

<label class="toggle">
<input type="checkbox" id="no_llm" checked>
Skip LLM (recommended for local demo; rules-only review is instant)
</label>
</form>

<div id="error"></div>

<div id="resultActions" class="result-actions">
<button type="button" class="secondary" id="downloadHtmlBtn">Download HTML</button>
<button type="button" class="secondary" id="copyMarkdownBtn">Copy as Markdown</button>
<span id="actionFeedback" class="feedback"></span>
</div>

<div id="result"></div>
</main>

<footer>
MigGuard v__VERSION__ &middot; Local playground &middot; Reviews run in-process; no SQL is ever executed.
</footer>

<script>
const PRESETS = __PRESETS_JSON__;
const STORAGE_KEY = "migguard-playground-v1";

const sqlEl = document.getElementById("sql");
const gutter = document.getElementById("lineNumbers");
const dialectEl = document.getElementById("dialect");
const filenameEl = document.getElementById("filename");
const noLlmEl = document.getElementById("no_llm");
const presetEl = document.getElementById("preset");
const resultActions = document.getElementById("resultActions");
const actionFeedback = document.getElementById("actionFeedback");

let lastHtmlReport = null;
let lastReviewInput = null;  // { sql, dialect, filename, noLlm }

function updateLineNumbers() {
    const lineCount = (sqlEl.value.match(/\\n/g) || []).length + 1;
    let out = "";
    for (let i = 1; i <= lineCount; i++) out += i + "\\n";
    gutter.textContent = out;
}

sqlEl.addEventListener("input", () => { updateLineNumbers(); saveState(); });
sqlEl.addEventListener("scroll", () => { gutter.scrollTop = sqlEl.scrollTop; });

// Cmd/Ctrl+Enter inside the textarea submits the form.
sqlEl.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        document.getElementById("reviewForm").requestSubmit();
    }
});

presetEl.addEventListener("change", (e) => {
    const preset = PRESETS[e.target.value];
    if (!preset) return;
    sqlEl.value = preset.sql;
    dialectEl.value = preset.dialect;
    updateLineNumbers();
    saveState();
    sqlEl.focus();
});

function saveState() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
            sql: sqlEl.value,
            dialect: dialectEl.value,
            filename: filenameEl.value,
            no_llm: noLlmEl.checked,
        }));
    } catch (_) { /* storage may be blocked; ignore */ }
}

function loadState() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return;
        const state = JSON.parse(raw);
        if (typeof state.sql === "string" && state.sql.length > 0) sqlEl.value = state.sql;
        if (typeof state.dialect === "string") dialectEl.value = state.dialect;
        if (typeof state.filename === "string") filenameEl.value = state.filename;
        if (typeof state.no_llm === "boolean") noLlmEl.checked = state.no_llm;
    } catch (_) { /* corrupt storage; ignore */ }
}

[dialectEl, filenameEl].forEach(el => el.addEventListener("input", saveState));
noLlmEl.addEventListener("change", saveState);

loadState();
updateLineNumbers();

async function submitReview(e) {
    e.preventDefault();
    const sql = sqlEl.value;
    const dialect = dialectEl.value;
    const filename = filenameEl.value.trim() || "pasted.sql";
    const noLlm = noLlmEl.checked;
    const btn = document.getElementById("submitBtn");
    const errBox = document.getElementById("error");
    const resultBox = document.getElementById("result");
    errBox.innerHTML = "";
    resultBox.innerHTML = "";
    resultActions.classList.remove("visible");
    actionFeedback.textContent = "";
    actionFeedback.classList.remove("error");
    btn.disabled = true;
    const originalLabel = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span>Reviewing...';
    try {
        const resp = await fetch("/playground", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sql, dialect, filename, no_llm: noLlm }),
        });
        const text = await resp.text();
        if (!resp.ok) {
            let msg = text;
            try { msg = JSON.parse(text).detail || text; } catch (_) {}
            errBox.innerHTML = '<div class="error">' + escapeHtml(String(msg)) + '</div>';
            return false;
        }
        lastHtmlReport = text;
        lastReviewInput = { sql, dialect, filename, noLlm };
        const iframe = document.createElement("iframe");
        iframe.srcdoc = text;
        iframe.title = "MigGuard review report";
        resultBox.appendChild(iframe);
        resultActions.classList.add("visible");
        iframe.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
        errBox.innerHTML = '<div class="error">Request failed: ' + escapeHtml(String(err)) + '</div>';
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalLabel;
    }
    return false;
}

document.getElementById("downloadHtmlBtn").addEventListener("click", () => {
    if (!lastHtmlReport) return;
    const filename = (lastReviewInput && lastReviewInput.filename) || "pasted.sql";
    const base = filename.replace(/\\.sql$/i, "");
    const blob = new Blob([lastHtmlReport], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "migguard-review-" + base + ".html";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showFeedback("Report downloaded.");
});

document.getElementById("copyMarkdownBtn").addEventListener("click", async () => {
    if (!lastReviewInput) return;
    actionFeedback.textContent = "";
    actionFeedback.classList.remove("error");
    try {
        const resp = await fetch("/playground", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...lastReviewInput, no_llm: lastReviewInput.noLlm, format: "markdown" }),
        });
        const text = await resp.text();
        if (!resp.ok) throw new Error(text);
        await navigator.clipboard.writeText(text);
        showFeedback("Markdown copied to clipboard.");
    } catch (err) {
        actionFeedback.textContent = "Copy failed: " + String(err);
        actionFeedback.classList.add("error");
    }
});

function showFeedback(msg) {
    actionFeedback.textContent = msg;
    setTimeout(() => {
        if (actionFeedback.textContent === msg) actionFeedback.textContent = "";
    }, 3500);
}

function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
</script>
</body>
</html>
"""


@app.post("/webhook")
async def webhook(request: Request, content_type: str | None = Header(default=None)) -> dict:
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e!r}") from e

    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = detect_provider(headers, body)
    if provider is None:
        return {"status": "ignored", "reason": "unrecognized provider"}

    pr = provider.parse_event(headers, body)
    if pr is None:
        return {"status": "ignored", "reason": "non-actionable event"}

    logger.info("PR event: %s %s#%s (%s)", provider.name, pr.repo, pr.pr_id, pr.title)

    try:
        changed = await provider.list_changed_files(pr)
    except Exception as e:
        logger.exception("failed to list changed files")
        raise HTTPException(status_code=502, detail=f"upstream error: {e!r}") from e

    migration_files = [f for f in changed if _is_migration_file(f)]
    if not migration_files:
        logger.info("PR has no migration files; skipping review")
        return {"status": "ok", "reviewed": 0, "reason": "no migration files in PR"}

    logger.info("Reviewing %d migration file(s): %s", len(migration_files), migration_files)

    with tempfile.TemporaryDirectory(prefix="migguard-") as tmp:
        tmp_root = Path(tmp).resolve()
        local_paths: list[Path] = []
        for path in migration_files:
            try:
                text = await provider.fetch_file(pr, path)
            except Exception as e:
                logger.warning("could not fetch %s: %r", path, e)
                continue
            safe_rel = _sanitize_repo_path(path)
            if safe_rel is None:
                logger.warning("rejecting suspicious path from webhook: %r", path)
                continue
            local = (tmp_root / safe_rel).resolve()
            if not str(local).startswith(str(tmp_root) + os.sep):
                logger.warning("rejecting path traversal attempt: %r", path)
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(text, encoding="utf-8")
            local_paths.append(local)

        engine = Engine(llm=LLMAnalyzer(), dialect=_DIALECT)
        report = engine.review(local_paths)
        comment_md = format_markdown_pr_comment(report)

    try:
        await provider.post_comment(pr, comment_md)
    except Exception as e:
        logger.exception("failed to post PR comment")
        raise HTTPException(status_code=502, detail=f"comment failed: {e!r}") from e

    blocks = report.blocks_merge and _STRICT
    state = "failure" if blocks else "success"
    desc = (
        f"{report.counts['high']} high / {report.counts['medium']} medium / "
        f"{report.counts['low']} low findings"
    )
    try:
        await provider.set_status(pr, state=state, description=desc)
    except Exception as e:
        logger.warning("failed to set PR status: %r", e)

    return {
        "status": "ok",
        "reviewed": len(migration_files),
        "overall_severity": report.overall_severity.value,
        "blocks_merge": report.blocks_merge,
    }
