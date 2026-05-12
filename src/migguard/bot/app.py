"""FastAPI webhook receiver.

Accepts PR events from both GitHub and Azure DevOps on a single endpoint. The
provider is auto-detected from the request headers / payload shape.

Routes:
    GET  /health        -> liveness
    GET  /              -> readme blurb
    POST /webhook       -> PR event (GitHub or Azure DevOps)

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

from migguard import __version__
from migguard.bot.providers import PullRequest, detect_provider
from migguard.cli.formatters import format_markdown_pr_comment
from migguard.core.engine import Engine
from migguard.core.models import Severity
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
        "endpoints": "/health (GET), /webhook (POST)",
        "supports": "GitHub, Azure DevOps",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, content_type: str | None = Header(default=None)) -> dict:
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to list changed files")
        raise HTTPException(status_code=502, detail=f"upstream error: {e!r}") from e

    migration_files = [f for f in changed if _is_migration_file(f)]
    if not migration_files:
        logger.info("PR has no migration files; skipping review")
        return {"status": "ok", "reviewed": 0, "reason": "no migration files in PR"}

    logger.info("Reviewing %d migration file(s): %s", len(migration_files), migration_files)

    with tempfile.TemporaryDirectory(prefix="migguard-") as tmp:
        tmp_root = Path(tmp)
        local_paths: list[Path] = []
        for path in migration_files:
            try:
                text = await provider.fetch_file(pr, path)
            except Exception as e:  # noqa: BLE001
                logger.warning("could not fetch %s: %r", path, e)
                continue
            local = tmp_root / path.replace("\\", "/")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(text, encoding="utf-8")
            local_paths.append(local)

        engine = Engine(llm=LLMAnalyzer(), dialect=_DIALECT)
        report = engine.review(local_paths)
        comment_md = format_markdown_pr_comment(report)

    try:
        await provider.post_comment(pr, comment_md)
    except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to set PR status: %r", e)

    return {
        "status": "ok",
        "reviewed": len(migration_files),
        "overall_severity": report.overall_severity.value,
        "blocks_merge": report.blocks_merge,
    }
