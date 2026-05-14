"""The orchestrator: parse, run rules, optionally call LLM, return a Report."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from migguard.core.models import (
    Category,
    CodeLocation,
    Finding,
    Layer,
    Report,
    Severity,
)
from migguard.core.parser import ParsedScript, parse_script
from migguard.core.schema_client import load_schema_facts
from migguard.llm.analyzer import LLMAnalyzer
from migguard.rules.base import Rule, RuleContext
from migguard.rules.registry import all_rules

logger = logging.getLogger("migguard.engine")


def _rule_crash_finding(rule_id: str, file_path: str, err: Exception) -> Finding:
    return Finding(
        rule_id="internal/rule-crash",
        title=f"Rule {rule_id} crashed",
        severity=Severity.INFO,
        category=Category.OTHER,
        layer=Layer.RULE,
        location=CodeLocation(file=file_path, line_start=1, line_end=1),
        message=(
            f"Rule `{rule_id}` raised {type(err).__name__} and was skipped. "
            "Other rules still ran; consider reporting this as a MigGuard bug."
        ),
    )


class Engine:
    """The top-level reviewer."""

    def __init__(
        self,
        *,
        rules: Iterable[Rule] | None = None,
        schema_snapshot_path: Path | str | None = None,
        llm: LLMAnalyzer | None = None,
        dialect: str = "tsql",
    ) -> None:
        self.rules: list[Rule] = list(rules) if rules is not None else all_rules()
        self._schema_snapshot_path = schema_snapshot_path
        self.llm = llm
        self.dialect = dialect

    @property
    def active_rules(self) -> list[Rule]:
        """Rules that apply to the current dialect."""
        return [r for r in self.rules if r.applies_to(self.dialect)]

    def review(self, paths: Iterable[Path | str]) -> Report:
        """Review one or more migration files and return an aggregated Report."""
        context = RuleContext(schema_facts=load_schema_facts(self._schema_snapshot_path))
        all_findings: list[Finding] = []
        files: list[str] = []
        parsed_scripts: list[ParsedScript] = []

        for raw_path in paths:
            path = Path(raw_path)
            files.append(str(path))
            script = parse_script(path, dialect=self.dialect)
            parsed_scripts.append(script)
            for rule in self.active_rules:
                try:
                    findings = rule.check(script, context)
                except Exception as e:
                    logger.warning("rule %s crashed on %s: %r", rule.rule_id, path, e)
                    all_findings.append(_rule_crash_finding(rule.rule_id, str(path), e))
                    continue
                all_findings.extend(findings)

        for rule in self.active_rules:
            try:
                all_findings.extend(rule.check_collection(parsed_scripts, context))
            except Exception as e:
                logger.warning("rule %s collection check crashed: %r", rule.rule_id, e)
                file_for_crash = files[0] if files else "<collection>"
                all_findings.append(_rule_crash_finding(rule.rule_id, file_for_crash, e))

        summary: str | None = None
        rollback: str | None = None
        if self.llm is not None and self.llm.enabled and parsed_scripts:
            try:
                llm_findings, summary, rollback = self.llm.analyze(
                    parsed_scripts, all_findings, context
                )
                all_findings.extend(llm_findings)
            except Exception as e:
                logger.warning("LLM analyzer failed: %r", e)

        return Report(
            files_reviewed=files,
            findings=all_findings,
            summary=summary,
            rollback_script=rollback,
        )


def review_files(paths: Iterable[Path | str], *, dialect: str = "tsql") -> Report:
    """Convenience function for one-shot review."""
    return Engine(dialect=dialect).review(paths)
