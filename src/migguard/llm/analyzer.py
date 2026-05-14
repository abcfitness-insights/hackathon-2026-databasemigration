"""LLM-powered semantic analyzer.

Optional layer that runs after deterministic rules. It receives:
- The migration script (raw text)
- The deterministic findings produced so far
- The schema context (table facts)

And returns:
- Zero or more additional Finding objects (judgment calls rules can't make)
- A plain-English summary of the migration's risk
- A best-effort rollback script (always marked 'review required')

If no API key is configured, the analyzer is disabled and silently no-ops.
That keeps the CLI usable offline / for non-LLM environments and makes the
hackathon demo robust if the OpenAI service is flaky.

Supports both Azure OpenAI and OpenAI direct via env vars:
- MIGGUARD_LLM_PROVIDER = "azure" | "openai" | "disabled" (default: auto-detect)
- AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT
- OPENAI_API_KEY, MIGGUARD_LLM_MODEL (default: "gpt-4o-mini")
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from migguard.core.models import (
    Category,
    CodeLocation,
    Finding,
    Layer,
    Severity,
)
from migguard.core.parser import ParsedScript
from migguard.rules.base import RuleContext

PROMPTS_DIR = Path(__file__).parent / "prompts"
logger = logging.getLogger("migguard.llm")


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class LLMAnalyzer:
    """Wraps an OpenAI-compatible chat client behind a feature flag."""

    def __init__(self) -> None:
        self.provider, self.client, self.model = self._init_client()
        self.enabled = self.client is not None

    @staticmethod
    def _init_client() -> tuple[str, Any, str]:
        provider = os.environ.get("MIGGUARD_LLM_PROVIDER", "auto").lower()
        try:
            azure_key = os.environ.get("AZURE_OPENAI_API_KEY")
            if provider in ("azure", "auto") and azure_key:
                azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
                if not azure_endpoint:
                    logger.warning(
                        "AZURE_OPENAI_API_KEY set but AZURE_OPENAI_ENDPOINT missing; "
                        "disabling LLM layer"
                    )
                    return "disabled", None, ""
                from openai import AzureOpenAI

                client = AzureOpenAI(
                    api_key=azure_key,
                    azure_endpoint=azure_endpoint,
                    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01"),
                )
                deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
                return "azure", client, deployment

            if provider in ("openai", "auto") and os.environ.get("OPENAI_API_KEY"):
                from openai import OpenAI

                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
                model = os.environ.get("MIGGUARD_LLM_MODEL", "gpt-4o-mini")
                return "openai", client, model
        except Exception as e:
            logger.warning("init failed: %r; disabling LLM layer", e)

        return "disabled", None, ""

    def analyze(
        self,
        scripts: list[ParsedScript],
        rule_findings: list[Finding],
        ctx: RuleContext,
    ) -> tuple[list[Finding], str | None, str | None]:
        """Run risk-review and rollback prompts across every script.

        Findings are aggregated per-file. Summary and rollback are produced for
        the first file only (multi-file summaries would be misleading without
        an explicit aggregation prompt).

        Returns: (additional_findings, summary, rollback_script).
        """
        if not self.enabled or not scripts:
            return [], None, None

        all_findings: list[Finding] = []
        summary: str | None = None
        rollback: str | None = None
        for idx, script in enumerate(scripts):
            scoped = [f for f in rule_findings if f.location.file == script.file_path]
            review = self._risk_review(script, scoped, ctx)
            all_findings.extend(review.get("findings", []))
            if idx == 0:
                summary = review.get("summary")
                rollback = self._rollback(script, scoped)
        return all_findings, summary, rollback

    def _risk_review(
        self,
        script: ParsedScript,
        rule_findings: list[Finding],
        ctx: RuleContext,
    ) -> dict[str, Any]:
        sys_prompt = _load_prompt("risk_review.md")
        user_prompt = self._format_review_prompt(script, rule_findings, ctx)
        raw = self._chat_json(sys_prompt, user_prompt)
        findings: list[Finding] = []
        for item in raw.get("findings", []) or []:
            try:
                findings.append(
                    Finding(
                        rule_id=item.get("rule_id", "llm/ordering-or-context"),
                        title=item.get("title", "LLM observation"),
                        severity=Severity(item.get("severity", "low")),
                        category=Category(item.get("category", "ordering")),
                        layer=Layer.LLM,
                        location=CodeLocation(
                            file=script.file_path,
                            line_start=max(1, int(item.get("line_start", 1))),
                            line_end=max(
                                1,
                                int(item.get("line_end", item.get("line_start", 1))),
                            ),
                            snippet=item.get("snippet"),
                        ),
                        message=item.get("message", ""),
                        suggestion=item.get("suggestion"),
                    )
                )
            except Exception as e:
                logger.warning("dropped malformed finding: %r", e)
        return {"findings": findings, "summary": raw.get("summary")}

    def _rollback(self, script: ParsedScript, rule_findings: list[Finding]) -> str | None:
        sys_prompt = _load_prompt("rollback_gen.md")
        user_prompt = (
            f"### Migration ({script.file_path})\n```sql\n{script.raw_text}\n```\n\n"
            "Generate a best-effort rollback (down) script for the operations above."
        )
        result = self._chat_text(sys_prompt, user_prompt)
        if not result:
            return None
        return (
            "-- AUTO-GENERATED BY MigGuard. REVIEW BEFORE EXECUTING.\n"
            "-- This is a best-effort rollback; for irreversible operations\n"
            "-- (DROP TABLE, TRUNCATE) full restoration may not be possible.\n\n"
            + result
        )

    def _format_review_prompt(
        self,
        script: ParsedScript,
        rule_findings: list[Finding],
        ctx: RuleContext,
    ) -> str:
        facts_block = (
            "\n".join(
                f"- {k}: {v.row_count:,} rows, {v.index_count} indexes, "
                f"{v.foreign_key_count} FKs"
                for k, v in ctx.schema_facts.items()
            )
            or "(no schema facts loaded)"
        )
        rule_block = (
            "\n".join(
                f"- [{f.severity.value.upper()}] {f.rule_id}: {f.title} "
                f"(line {f.location.line_start})"
                for f in rule_findings
            )
            or "(no deterministic findings yet)"
        )
        return (
            f"### Migration file: {script.file_path}\n"
            f"```sql\n{script.raw_text}\n```\n\n"
            f"### Known table facts\n{facts_block}\n\n"
            f"### Deterministic findings so far\n{rule_block}\n\n"
            "Respond with strictly the JSON schema specified in the system prompt."
        )

    def _chat_json(self, sys_prompt: str, user_prompt: str) -> dict[str, Any]:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as e:
            logger.warning("risk_review failed: %r", e)
            return {}

    def _chat_text(self, sys_prompt: str, user_prompt: str) -> str | None:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
            )
            return (resp.choices[0].message.content or "").strip() or None
        except Exception as e:
            logger.warning("rollback_gen failed: %r", e)
            return None
