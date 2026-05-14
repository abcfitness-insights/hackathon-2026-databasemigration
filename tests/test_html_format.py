"""Tests for the HTML formatter.

The HTML format is the deliverable for the local web playground and for
``migguard review --format html``. These tests pin down the contract:

* Empty reports render a clean "no findings" page.
* Findings render with severity, rule_id, location, message, schema context.
* User-supplied strings are HTML-escaped (defence against malicious filenames /
  finding messages).
* Suggestions render inside a ``<details>`` element so the page stays compact.
"""

from __future__ import annotations

from migguard.cli.formatters import format_html
from migguard.core.models import (
    Category,
    CodeLocation,
    Finding,
    Layer,
    Report,
    Severity,
)


def _make_finding(
    *,
    rule_id: str = "data-loss/delete-without-where",
    title: str = "DELETE without WHERE",
    severity: Severity = Severity.HIGH,
    category: Category = Category.DATA_LOSS,
    file: str = "V001__delete_all.sql",
    message: str = "DELETE without a WHERE clause will remove every row.",
    suggestion: str | None = "DELETE FROM app.customer WHERE created_at < '2020-01-01';",
    schema_context: str | None = "app.customer has 12.4M rows",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        title=title,
        severity=severity,
        category=category,
        layer=Layer.RULE,
        location=CodeLocation(file=file, line_start=3, line_end=3),
        message=message,
        suggestion=suggestion,
        schema_context=schema_context,
    )


def test_empty_report_renders_no_findings_message() -> None:
    report = Report(files_reviewed=["V001__clean.sql"], findings=[])
    html_out = format_html(report)

    assert "<!DOCTYPE html>" in html_out
    assert "MigGuard" in html_out
    assert "No findings" in html_out
    assert "V001__clean.sql" in html_out
    assert "0 High" in html_out
    assert "0 Medium" in html_out


def test_finding_renders_with_severity_and_location() -> None:
    finding = _make_finding()
    report = Report(files_reviewed=["V001__delete_all.sql"], findings=[finding])
    html_out = format_html(report)

    assert "HIGH" in html_out
    assert "mg-bg-high" in html_out
    assert "data-loss/delete-without-where" in html_out
    assert "DELETE without WHERE" in html_out
    assert "V001__delete_all.sql" in html_out
    assert "line 3" in html_out
    assert "DELETE without a WHERE clause" in html_out
    assert "app.customer has 12.4M rows" in html_out
    assert "1 High" in html_out
    assert report.blocks_merge
    assert "block merge" in html_out.lower()


def test_user_strings_are_html_escaped() -> None:
    """Filenames, messages, suggestions, and titles must never break out of HTML."""

    malicious = "<script>alert('xss')</script>"
    finding = _make_finding(
        title=malicious,
        message=f"Bad input: {malicious}",
        file=f"{malicious}.sql",
        suggestion=f"-- {malicious}\nDELETE FROM x;",
        schema_context=f"oops: {malicious}",
    )
    report = Report(files_reviewed=[f"{malicious}.sql"], findings=[finding])
    html_out = format_html(report)

    assert "<script>alert" not in html_out
    assert "&lt;script&gt;alert" in html_out


def test_suggestion_renders_inside_details_block() -> None:
    finding = _make_finding(suggestion="DELETE FROM app.customer WHERE id = 1;")
    report = Report(files_reviewed=["V001__delete_all.sql"], findings=[finding])
    html_out = format_html(report)

    assert "<details" in html_out
    assert "Suggested fix" in html_out
    assert "DELETE FROM app.customer WHERE id = 1;" in html_out
    assert "<pre>" in html_out
