"""Base class for deterministic rule checks.

Every rule:
- Has a stable ``rule_id`` (e.g. ``data-loss/delete-without-where``)
- Belongs to a ``Category`` (groups it in the PR comment)
- Has a default ``severity`` (rules can override per finding)
- Implements ``check(script, context) -> list[Finding]``

Rules are stateless. Context carries shared info (schema facts, config).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from migguard.core.models import (
    Category,
    CodeLocation,
    Finding,
    Layer,
    Severity,
)
from migguard.core.parser import ParsedScript, ParsedStatement


@dataclass
class RuleContext:
    """Shared context passed to every rule.

    Carries optional schema facts (row counts, indexes) keyed by
    ``schema.table``. Rules use this to escalate severity (e.g. NOT NULL
    DEFAULT on a small lookup table is fine; on a 47M-row fact table it's HIGH).
    """

    schema_facts: dict[str, "TableFacts"] = field(default_factory=dict)
    config: dict[str, object] = field(default_factory=dict)

    def fact_for(self, schema: str | None, table: str) -> "TableFacts | None":
        if not table:
            return None
        key = f"{(schema or 'dbo').lower()}.{table.lower()}"
        return self.schema_facts.get(key)


@dataclass
class TableFacts:
    """Facts about a real table, used to ground risk assessments."""

    schema: str
    name: str
    row_count: int
    index_count: int
    foreign_key_count: int = 0
    size_mb: float = 0.0

    @property
    def is_large(self) -> bool:
        return self.row_count >= 1_000_000

    @property
    def is_huge(self) -> bool:
        return self.row_count >= 10_000_000


ALL_DIALECTS = frozenset({"tsql", "postgres", "mysql", "sqlite"})


class Rule(ABC):
    """Base class for all deterministic rule checks.

    Rules can declare which SQL dialects they apply to via ``dialects``. Default
    is ``ALL_DIALECTS`` — the rule fires regardless of dialect. Rules that rely
    on T-SQL syntax (``OBJECT_ID``, ``ONLINE = ON``, ``BEGIN TRAN``, ``sys.columns``)
    should narrow this to ``{"tsql"}``.

    Rules may optionally implement ``check_collection`` to run once across all
    files in a review (sequencing, cross-file FK ordering, etc).
    """

    rule_id: str
    title: str
    category: Category
    severity: Severity
    dialects: frozenset[str] = ALL_DIALECTS

    def applies_to(self, dialect: str) -> bool:
        return dialect in self.dialects

    @abstractmethod
    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:  # pragma: no cover
        ...

    def check_collection(
        self, scripts: list[ParsedScript], ctx: RuleContext
    ) -> list[Finding]:
        """Run once per review with every parsed script. Default: no-op."""
        return []

    def make_finding(
        self,
        stmt: ParsedStatement,
        script: ParsedScript,
        message: str,
        *,
        title: str | None = None,
        suggestion: str | None = None,
        severity: Severity | None = None,
        schema_context: str | None = None,
    ) -> Finding:
        """Convenience factory — fills in rule_id, category, layer, location."""
        snippet = stmt.raw_sql.strip()
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        return Finding(
            rule_id=self.rule_id,
            title=title or self.title,
            severity=severity or self.severity,
            category=self.category,
            layer=Layer.RULE,
            location=CodeLocation(
                file=script.file_path,
                line_start=stmt.line_start,
                line_end=stmt.line_end,
                snippet=snippet,
            ),
            message=message,
            suggestion=suggestion,
            schema_context=schema_context,
        )
