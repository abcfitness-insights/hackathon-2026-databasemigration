"""Pydantic models for MigGuard findings and reports.

These models are the contract between every component:
- Rule checks produce Finding objects
- The LLM analyzer returns a list of Finding objects (JSON-schema constrained)
- The engine aggregates them into a Report
- Formatters (terminal, JSON, SARIF, Markdown for PR comments) consume Report
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, computed_field, model_validator


class Severity(StrEnum):
    """Risk severity. HIGH blocks merge in strict mode; MEDIUM warns; LOW informs."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Numeric rank for sorting and aggregation. Higher = worse."""
        return {"high": 3, "medium": 2, "low": 1, "info": 0}[self.value]


class Category(StrEnum):
    """Finding category. Drives grouping in the PR comment output."""

    DATA_LOSS = "data_loss"
    LOCKING = "locking"
    ROLLBACK = "rollback"
    IDEMPOTENCY = "idempotency"
    TRANSACTION = "transaction"
    COMPATIBILITY = "compatibility"
    PERMISSIONS = "permissions"
    PERFORMANCE = "performance"
    NAMING = "naming"
    ORDERING = "ordering"
    OTHER = "other"


class Layer(StrEnum):
    """Which analysis layer produced this finding. Critical for trust."""

    RULE = "rule"
    LLM = "llm"
    SCHEMA = "schema"


class CodeLocation(BaseModel):
    """Where in the source a finding applies."""

    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    column_start: int | None = Field(default=None, ge=1)
    column_end: int | None = Field(default=None, ge=1)
    snippet: str | None = None

    @model_validator(mode="after")
    def _check_line_range(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError(
                f"line_end ({self.line_end}) must be >= line_start ({self.line_start})"
            )
        return self


class Finding(BaseModel):
    """One issue found by the reviewer. The atomic unit of feedback."""

    rule_id: str = Field(
        description="Stable rule identifier, e.g. 'data-loss/delete-without-where'."
    )
    title: str = Field(description="Short, human-readable summary.")
    severity: Severity
    category: Category
    layer: Layer
    location: CodeLocation
    message: str = Field(description="Full explanation of why this is risky.")
    suggestion: str | None = Field(
        default=None,
        description="Concrete suggested fix or rewrite. None if not actionable.",
    )
    schema_context: str | None = Field(
        default=None,
        description=(
            "Live schema facts that justify the severity, e.g. "
            "'dw.member has 12.4M rows, 3 active indexes'."
        ),
    )
    references: list[str] = Field(
        default_factory=list,
        description="Docs / runbook URLs supporting the finding.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocks_merge(self) -> bool:
        """High-severity findings block merge in strict mode."""
        return self.severity == Severity.HIGH


class Report(BaseModel):
    """Aggregated review output for one or more migration files."""

    files_reviewed: list[str]
    findings: list[Finding]
    summary: str | None = Field(
        default=None,
        description="LLM-generated plain-English summary for non-DBA reviewers.",
    )
    rollback_script: str | None = Field(
        default=None,
        description="Best-effort auto-generated rollback. Always marked 'review required'.",
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    engine_version: str = "0.1.0"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def counts(self) -> dict[str, int]:
        """Findings count grouped by severity."""
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overall_severity(self) -> Severity:
        """The worst severity present, or INFO if no findings."""
        if not self.findings:
            return Severity.INFO
        return max(self.findings, key=lambda f: f.severity.rank).severity

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocks_merge(self) -> bool:
        """True if any finding is HIGH severity."""
        return any(f.blocks_merge for f in self.findings)

    def findings_by_category(self) -> dict[Category, list[Finding]]:
        """Group findings by category, sorted by severity descending within each."""
        grouped: dict[Category, list[Finding]] = {}
        for f in self.findings:
            grouped.setdefault(f.category, []).append(f)
        for cat in grouped:
            grouped[cat].sort(key=lambda f: f.severity.rank, reverse=True)
        return grouped
