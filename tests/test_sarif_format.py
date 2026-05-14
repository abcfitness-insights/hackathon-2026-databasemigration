"""Tests for the SARIF 2.1.0 formatter.

SARIF is consumed by GitHub Advanced Security, Azure DevOps Advanced Security,
and most enterprise code-scanning dashboards. The format is well-specified
(OASIS standard), so these tests pin down the parts that matter for that
integration:

* Top-level shape (`$schema`, `version`, `runs[]`)
* Tool descriptor with the rule catalog deduplicated by `rule_id`
* Severity → SARIF level mapping (HIGH=error, MEDIUM=warning, LOW=note, INFO=none)
* Physical locations with 1-based line numbers
* Suggestions surface as ``fixes`` with replacement regions
"""

from __future__ import annotations

import json

from migguard.cli.formatters import format_sarif
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
    file: str = "V001__bad.sql",
    line: int = 3,
    message: str = "DELETE without a WHERE clause removes every row.",
    suggestion: str | None = "DELETE FROM app.customer WHERE id < 100;",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        title=title,
        severity=severity,
        category=category,
        layer=Layer.RULE,
        location=CodeLocation(file=file, line_start=line, line_end=line),
        message=message,
        suggestion=suggestion,
    )


def _parse(report: Report) -> dict:
    return json.loads(format_sarif(report))


def test_empty_report_is_valid_sarif() -> None:
    report = Report(files_reviewed=["V001__clean.sql"], findings=[])
    sarif = _parse(report)
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].endswith("sarif-2.1.0.json")
    assert len(sarif["runs"]) == 1
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "MigGuard"
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


def test_finding_renders_as_sarif_result() -> None:
    finding = _make_finding()
    report = Report(files_reviewed=["V001__bad.sql"], findings=[finding])
    sarif = _parse(report)

    run = sarif["runs"][0]
    assert len(run["results"]) == 1
    result = run["results"][0]
    assert result["ruleId"] == "data-loss/delete-without-where"
    assert result["level"] == "error"
    assert "DELETE" in result["message"]["text"]
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "V001__bad.sql"
    assert loc["region"]["startLine"] == 3
    assert loc["region"]["endLine"] == 3


def test_severity_maps_to_correct_sarif_level() -> None:
    findings = [
        _make_finding(rule_id="r/high", severity=Severity.HIGH),
        _make_finding(rule_id="r/med", severity=Severity.MEDIUM),
        _make_finding(rule_id="r/low", severity=Severity.LOW),
        _make_finding(rule_id="r/info", severity=Severity.INFO),
    ]
    report = Report(files_reviewed=["x.sql"], findings=findings)
    sarif = _parse(report)
    levels = {r["ruleId"]: r["level"] for r in sarif["runs"][0]["results"]}
    assert levels == {
        "r/high": "error",
        "r/med": "warning",
        "r/low": "note",
        "r/info": "none",
    }


def test_rules_in_tool_driver_are_deduplicated() -> None:
    """Multiple findings of the same rule -> rule appears once in the catalog."""
    findings = [
        _make_finding(rule_id="r/dup", line=3),
        _make_finding(rule_id="r/dup", line=7),
        _make_finding(rule_id="r/dup", line=11),
    ]
    report = Report(files_reviewed=["x.sql"], findings=findings)
    sarif = _parse(report)
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules) == 1
    assert rules[0]["id"] == "r/dup"
    assert len(sarif["runs"][0]["results"]) == 3


def test_suggestion_renders_as_sarif_fix() -> None:
    finding = _make_finding(suggestion="DELETE FROM app.customer WHERE id = 1;")
    report = Report(files_reviewed=["V001__bad.sql"], findings=[finding])
    sarif = _parse(report)
    result = sarif["runs"][0]["results"][0]
    assert "fixes" in result
    fix = result["fixes"][0]
    replacement = fix["artifactChanges"][0]["replacements"][0]
    assert replacement["insertedContent"]["text"] == "DELETE FROM app.customer WHERE id = 1;"
    assert replacement["deletedRegion"]["startLine"] == 3


def test_no_suggestion_means_no_fixes() -> None:
    finding = _make_finding(suggestion=None)
    report = Report(files_reviewed=["x.sql"], findings=[finding])
    sarif = _parse(report)
    assert "fixes" not in sarif["runs"][0]["results"][0]


def test_report_metadata_in_properties() -> None:
    finding = _make_finding()
    report = Report(files_reviewed=["a.sql", "b.sql"], findings=[finding])
    sarif = _parse(report)
    props = sarif["runs"][0]["properties"]
    assert props["filesReviewed"] == ["a.sql", "b.sql"]
    assert props["blocksMerge"] is True
    assert props["counts"]["high"] == 1
