"""The orchestrator: parse, run rules, optionally call LLM, return a Report."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from migguard.core.models import Finding, Report
from migguard.core.parser import ParsedScript, parse_script
from migguard.core.schema_client import load_schema_facts
from migguard.llm.analyzer import LLMAnalyzer
from migguard.rules.base import Rule, RuleContext
from migguard.rules.registry import all_rules


class Engine:
    """The top-level reviewer. Stateless after construction."""

    def __init__(
        self,
        *,
        rules: Iterable[Rule] | None = None,
        schema_snapshot_path: Path | str | None = None,
        llm: LLMAnalyzer | None = None,
        dialect: str = "tsql",
    ) -> None:
        self.rules: list[Rule] = list(rules) if rules is not None else all_rules()
        self.context = RuleContext(schema_facts=load_schema_facts(schema_snapshot_path))
        self.llm = llm
        self.dialect = dialect

    @property
    def active_rules(self) -> list[Rule]:
        """Rules that apply to the current dialect."""
        return [r for r in self.rules if r.applies_to(self.dialect)]

    def review(self, paths: Iterable[Path | str]) -> Report:
        """Review one or more migration files and return an aggregated Report."""
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
                    findings = rule.check(script, self.context)
                except Exception as e:  # noqa: BLE001 -- one bad rule must not crash the review
                    findings = []
                    print(f"[migguard] rule {rule.rule_id} crashed: {e!r}")
                all_findings.extend(findings)

        for rule in self.active_rules:
            try:
                all_findings.extend(rule.check_collection(parsed_scripts, self.context))
            except Exception as e:  # noqa: BLE001
                print(f"[migguard] rule {rule.rule_id} collection check crashed: {e!r}")

        summary: str | None = None
        rollback: str | None = None
        if self.llm is not None and self.llm.enabled and parsed_scripts:
            try:
                llm_findings, summary, rollback = self.llm.analyze(
                    parsed_scripts[0], all_findings, self.context
                )
                all_findings.extend(llm_findings)
            except Exception as e:  # noqa: BLE001
                print(f"[migguard] LLM analyzer failed: {e!r}")

        return Report(
            files_reviewed=files,
            findings=all_findings,
            summary=summary,
            rollback_script=rollback,
        )


def review_files(paths: Iterable[Path | str], *, dialect: str = "tsql") -> Report:
    """Convenience function for one-shot review."""
    return Engine(dialect=dialect).review(paths)
