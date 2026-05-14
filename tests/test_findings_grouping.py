"""Tests for per-location finding grouping in the report formatters.

Rules can fire on the same statement (e.g. ``DROP TABLE x`` triggers both
``data-loss/drop-table`` and ``idempotency/drop-without-if-exists``). The
formatters render those as one **primary** finding plus a compact "also
flagged on this statement" line, instead of repeating the same line N times.
"""

from __future__ import annotations

from migguard.cli.formatters import format_html, format_markdown_pr_comment
from migguard.core.models import (
    Category,
    CodeLocation,
    Finding,
    Layer,
    Report,
    Severity,
)


def _f(
    rule_id: str,
    severity: Severity,
    category: Category = Category.DATA_LOSS,
    file: str = "V001__drop.sql",
    line: int = 5,
    title: str | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        title=title or rule_id,
        severity=severity,
        category=category,
        layer=Layer.RULE,
        location=CodeLocation(file=file, line_start=line, line_end=line),
        message=f"violation: {rule_id}",
    )


def test_report_groups_findings_by_location() -> None:
    findings = [
        _f("data-loss/drop-table", Severity.HIGH),
        _f("idempotency/drop-without-if-exists", Severity.MEDIUM, Category.IDEMPOTENCY),
        _f("rollback/no-down-script", Severity.MEDIUM, Category.ROLLBACK),
        _f("naming/too-long", Severity.LOW, Category.NAMING, line=10),
    ]
    report = Report(files_reviewed=["V001__drop.sql"], findings=findings)
    groups = report.findings_by_location()
    assert len(groups) == 2
    first_group = groups[0]
    assert first_group[0].severity is Severity.HIGH
    assert first_group[0].rule_id == "data-loss/drop-table"
    assert {f.rule_id for f in first_group} == {
        "data-loss/drop-table",
        "idempotency/drop-without-if-exists",
        "rollback/no-down-script",
    }
    second_group = groups[1]
    assert len(second_group) == 1
    assert second_group[0].rule_id == "naming/too-long"


def test_markdown_renders_grouped_findings_once_with_related_chips() -> None:
    findings = [
        _f("data-loss/drop-table", Severity.HIGH, title="DROP TABLE on customer data"),
        _f(
            "idempotency/drop-without-if-exists",
            Severity.MEDIUM,
            Category.DATA_LOSS,
            title="DROP without IF EXISTS",
        ),
    ]
    report = Report(files_reviewed=["V001__drop.sql"], findings=findings)
    md = format_markdown_pr_comment(report)

    # The primary title appears once in the per-category section body
    # (and may also appear in the TL;DR "Top issues to look at first" header,
    # but never twice in the section body).
    body = md.split("### ", 1)[1]
    assert body.count("DROP TABLE on customer data") == 1
    assert "DROP without IF EXISTS" not in body
    assert "Also flagged on this statement" in md
    assert "idempotency/drop-without-if-exists" in md


def test_html_renders_grouped_findings_once_with_related_chips() -> None:
    findings = [
        _f("data-loss/drop-table", Severity.HIGH, title="DROP TABLE on customer data"),
        _f(
            "idempotency/drop-without-if-exists",
            Severity.MEDIUM,
            Category.DATA_LOSS,
            title="DROP without IF EXISTS",
        ),
    ]
    report = Report(files_reviewed=["V001__drop.sql"], findings=findings)
    html_out = format_html(report)
    assert html_out.count("DROP TABLE on customer data") == 1
    assert html_out.count("DROP without IF EXISTS") == 0
    assert "Also flagged on this statement" in html_out
    assert "mg-related" in html_out


def test_grouping_preserves_severity_ordering_across_groups() -> None:
    """Outer list must order groups by primary severity, descending."""
    findings = [
        _f("naming/too-long", Severity.LOW, Category.NAMING, line=20),
        _f("data-loss/drop-table", Severity.HIGH, line=5),
        _f("rollback/no-down-script", Severity.MEDIUM, Category.ROLLBACK, line=12),
    ]
    report = Report(files_reviewed=["V001.sql"], findings=findings)
    groups = report.findings_by_location()
    levels = [g[0].severity for g in groups]
    assert levels == [Severity.HIGH, Severity.MEDIUM, Severity.LOW]
