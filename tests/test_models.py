"""Model tests. The Report aggregations (counts, overall_severity, blocks_merge)
are consumed by the PR-comment formatter and the CI status check, so they need
strong tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from migguard.core.models import (
    Category,
    CodeLocation,
    Finding,
    Layer,
    Report,
    Severity,
)


def make_finding(severity: Severity, category: Category = Category.DATA_LOSS) -> Finding:
    return Finding(
        rule_id="test/example",
        title="example",
        severity=severity,
        category=category,
        layer=Layer.RULE,
        location=CodeLocation(file="m.sql", line_start=1, line_end=1),
        message="example message",
    )


def test_severity_ranks_ordered() -> None:
    assert Severity.HIGH.rank > Severity.MEDIUM.rank > Severity.LOW.rank > Severity.INFO.rank


def test_finding_blocks_merge_only_on_high() -> None:
    assert make_finding(Severity.HIGH).blocks_merge is True
    assert make_finding(Severity.MEDIUM).blocks_merge is False
    assert make_finding(Severity.LOW).blocks_merge is False


def test_code_location_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError):
        CodeLocation(file="x.sql", line_start=10, line_end=5)


def test_report_counts_and_overall_severity() -> None:
    report = Report(
        files_reviewed=["m.sql"],
        findings=[
            make_finding(Severity.HIGH),
            make_finding(Severity.MEDIUM),
            make_finding(Severity.MEDIUM),
            make_finding(Severity.LOW),
        ],
    )
    assert report.counts == {"high": 1, "medium": 2, "low": 1, "info": 0}
    assert report.overall_severity is Severity.HIGH
    assert report.blocks_merge is True


def test_empty_report_does_not_block() -> None:
    report = Report(files_reviewed=["m.sql"], findings=[])
    assert report.overall_severity is Severity.INFO
    assert report.blocks_merge is False


def test_findings_by_category_sorts_high_first() -> None:
    report = Report(
        files_reviewed=["m.sql"],
        findings=[
            make_finding(Severity.LOW, Category.DATA_LOSS),
            make_finding(Severity.HIGH, Category.DATA_LOSS),
            make_finding(Severity.MEDIUM, Category.LOCKING),
        ],
    )
    grouped = report.findings_by_category()
    assert grouped[Category.DATA_LOSS][0].severity is Severity.HIGH
    assert grouped[Category.DATA_LOSS][1].severity is Severity.LOW
    assert grouped[Category.LOCKING][0].severity is Severity.MEDIUM
