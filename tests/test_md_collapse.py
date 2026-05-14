"""Tests for the markdown PR-comment summary header and LOW/INFO collapsing.

For big reports the PR comment used to dump every finding inline; on a 60-
finding migration that was unreadable. The formatter now:

* Emits a "Top issues to look at first" header with up to three highest-
  severity items
* Adds a per-category severity-count badge to each section heading
* Wraps any LOW + INFO findings in a `<details>` collapsible block
"""

from __future__ import annotations

from migguard.cli.formatters import format_markdown_pr_comment
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
    category: Category = Category.NAMING,
    line: int = 1,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        title=rule_id,
        severity=severity,
        category=category,
        layer=Layer.RULE,
        location=CodeLocation(file="V001.sql", line_start=line, line_end=line),
        message=f"violation: {rule_id}",
    )


def test_tldr_header_lists_top_three_highest_severity() -> None:
    findings = [
        _f("naming/too-long", Severity.LOW, line=1),
        _f("naming/non-snake-case", Severity.LOW, line=2),
        _f("data-loss/drop-table", Severity.HIGH, Category.DATA_LOSS, line=3),
        _f("locking/index-build", Severity.MEDIUM, Category.LOCKING, line=4),
        _f("rollback/no-down", Severity.MEDIUM, Category.ROLLBACK, line=5),
    ]
    report = Report(files_reviewed=["V001.sql"], findings=findings)
    md = format_markdown_pr_comment(report)

    assert "Top issues to look at first" in md
    top_section = md.split("Top issues to look at first")[1].split("###", 1)[0]
    assert "data-loss/drop-table" in top_section
    assert "locking/index-build" in top_section
    assert "rollback/no-down" in top_section
    assert "naming/too-long" not in top_section


def test_category_heading_shows_severity_counts() -> None:
    findings = [
        _f("naming/too-long", Severity.LOW, line=1),
        _f("naming/non-snake-case", Severity.LOW, line=2),
        _f("naming/reserved-word", Severity.MEDIUM, line=3),
    ]
    report = Report(files_reviewed=["V001.sql"], findings=findings)
    md = format_markdown_pr_comment(report)
    # 1 medium + 2 low -> "1M 2L"
    assert "### Naming (3) - 1M 2L" in md


def test_low_info_findings_are_collapsed_in_details() -> None:
    findings = [
        _f("naming/too-long", Severity.LOW, line=1),
        _f("naming/non-snake-case", Severity.LOW, line=2),
        _f("naming/info-only", Severity.INFO, line=3),
    ]
    report = Report(files_reviewed=["V001.sql"], findings=findings)
    md = format_markdown_pr_comment(report)
    assert "<details><summary>Show 3 lower-severity finding(s)" in md
    # The findings themselves should still be inside the details block.
    details_block = md.split("<details><summary>Show 3 lower-severity")[1]
    assert "naming/too-long" in details_block
    assert "naming/info-only" in details_block


def test_high_and_medium_findings_are_not_collapsed() -> None:
    findings = [
        _f("data-loss/drop-table", Severity.HIGH, Category.DATA_LOSS, line=3),
        _f("locking/index-build", Severity.MEDIUM, Category.LOCKING, line=4),
    ]
    report = Report(files_reviewed=["V001.sql"], findings=findings)
    md = format_markdown_pr_comment(report)
    assert "<details><summary>Show" not in md
    assert "data-loss/drop-table" in md
    assert "locking/index-build" in md


def test_mixed_category_with_high_and_low_keeps_high_expanded() -> None:
    findings = [
        _f("data-loss/drop-table", Severity.HIGH, Category.DATA_LOSS, line=3),
        _f("data-loss/info-thing", Severity.INFO, Category.DATA_LOSS, line=4),
    ]
    report = Report(files_reviewed=["V001.sql"], findings=findings)
    md = format_markdown_pr_comment(report)
    # In the data_loss category: 1 expanded (HIGH), 1 collapsed (INFO).
    assert "data-loss/drop-table" in md
    assert "Show 1 lower-severity finding" in md
