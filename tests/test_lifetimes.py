"""Tests for ObjectLifetimeRule -- the object lifetime tracker.

The rule walks the review's statements in version order and flags DML that
references tables / columns dropped earlier in the same review (across files
OR within a single file). These tests pin down the contract:

* True positives:
    * stale column read after ALTER ... DROP COLUMN (cross-file)
    * stale table read after DROP TABLE (cross-file)
    * DROP then reference inside one script (single-file)
* True negatives (no false positives):
    * safe use of a column AFTER it was added but BEFORE it was dropped
    * read of a table that was dropped THEN recreated in the same chain
    * unqualified columns in multi-table joins (ambiguous -- skip)
    * single-script review where no DROP is seen
"""

from __future__ import annotations

from pathlib import Path

from migguard.core.engine import Engine
from migguard.core.models import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "lifetimes"


def _review(*names: str) -> list:
    paths = [FIXTURES / n for n in names]
    report = Engine(llm=None).review(paths)
    return report.findings


def _ids(findings: list) -> list[str]:
    return [f.rule_id for f in findings]


def test_dropped_column_referenced_is_flagged() -> None:
    """V001 adds status_code, V003 drops it, V004 reads it -> flag V004."""
    findings = _review(
        "V001__add_status_column.sql",
        "V003__drop_status_column.sql",
        "V004__stale_column_read.sql",
    )
    matches = [f for f in findings if f.rule_id == "lifetime/dropped-column-referenced"]
    assert matches, f"expected dropped-column-referenced finding, got {_ids(findings)}"
    finding = matches[0]
    assert finding.severity is Severity.HIGH
    assert "status_code" in finding.message.lower()
    assert finding.location.file.endswith("V004__stale_column_read.sql")


def test_dropped_table_referenced_is_flagged() -> None:
    """V005 drops audit_log, V006 reads it -> flag V006."""
    findings = _review(
        "V005__drop_audit_table.sql",
        "V006__stale_table_read.sql",
    )
    matches = [f for f in findings if f.rule_id == "lifetime/dropped-table-referenced"]
    assert matches, f"expected dropped-table-referenced finding, got {_ids(findings)}"
    finding = matches[0]
    assert finding.severity is Severity.HIGH
    assert "audit_log" in finding.message.lower()
    assert finding.location.file.endswith("V006__stale_table_read.sql")


def test_use_before_drop_is_safe() -> None:
    """V002 reads status_code AFTER V001 added it and BEFORE V003 drops it."""
    findings = _review(
        "V001__add_status_column.sql",
        "V002__safe_use_status.sql",
    )
    lifetime_findings = [f for f in findings if f.rule_id.startswith("lifetime/")]
    assert not lifetime_findings, (
        f"safe pre-drop usage should not fire lifetime rule; got {lifetime_findings}"
    )


def test_recreated_table_is_safe() -> None:
    """V005 drops audit_log, V007 recreates it, V008 reads it -> no finding."""
    findings = _review(
        "V005__drop_audit_table.sql",
        "V007__recreate_audit_table.sql",
        "V008__safe_table_read.sql",
    )
    matches = [
        f for f in findings if f.rule_id == "lifetime/dropped-table-referenced"
    ]
    assert not matches, (
        "table that was dropped THEN recreated should not be flagged; "
        f"got {[(f.rule_id, f.location.file) for f in matches]}"
    )


def test_multi_table_join_does_not_false_positive() -> None:
    """Ambiguous unqualified column in a join must not fire even if a column of
    that name was dropped elsewhere in the chain."""
    findings = _review(
        "V001__add_status_column.sql",
        "V003__drop_status_column.sql",
        "V009__multi_table_join.sql",
    )
    matches = [
        f
        for f in findings
        if f.rule_id == "lifetime/dropped-column-referenced"
        and f.location.file.endswith("V009__multi_table_join.sql")
    ]
    assert not matches, (
        f"multi-table join must not produce dropped-column finding; got {matches}"
    )


def test_single_script_without_seen_drop_emits_no_finding() -> None:
    """If the only script in the review never DROPs anything, nothing can be
    flagged as a stale reference -- the rule only tracks objects it has
    explicitly seen ALTER / CREATE / DROP for."""
    findings = _review("V004__stale_column_read.sql")
    lifetime_findings = [f for f in findings if f.rule_id.startswith("lifetime/")]
    assert not lifetime_findings


def test_single_script_with_drop_then_reference_is_flagged() -> None:
    """DROP COLUMN followed by a reference to that column INSIDE THE SAME
    file must fire the lifetime rule. This covers the playground case where
    a user pastes one migration containing both the DROP and the later DML."""
    findings = _review("V010__single_file_drop_then_ref.sql")
    matches = [
        f
        for f in findings
        if f.rule_id == "lifetime/dropped-column-referenced"
        and f.location.file.endswith("V010__single_file_drop_then_ref.sql")
    ]
    assert matches, (
        "DROP COLUMN followed by a reference in the same script must fire "
        f"lifetime/dropped-column-referenced; got {_ids(findings)}"
    )
    finding = matches[0]
    assert finding.severity is Severity.HIGH
    assert "status_code" in finding.message.lower()


def test_full_chain_produces_both_findings() -> None:
    """Running the full fixture set should surface both the stale column and
    stale table reads, while not producing them for V002 / V008 / V009."""
    findings = _review(
        "V001__add_status_column.sql",
        "V002__safe_use_status.sql",
        "V003__drop_status_column.sql",
        "V004__stale_column_read.sql",
        "V005__drop_audit_table.sql",
        "V006__stale_table_read.sql",
        "V007__recreate_audit_table.sql",
        "V008__safe_table_read.sql",
        "V009__multi_table_join.sql",
    )
    lifetime = [f for f in findings if f.rule_id.startswith("lifetime/")]
    by_rule = {f.rule_id: f for f in lifetime}

    assert "lifetime/dropped-column-referenced" in by_rule
    assert "lifetime/dropped-table-referenced" in by_rule
    assert by_rule["lifetime/dropped-column-referenced"].location.file.endswith(
        "V004__stale_column_read.sql"
    )
    assert by_rule["lifetime/dropped-table-referenced"].location.file.endswith(
        "V006__stale_table_read.sql"
    )
    safe_files = ("V002", "V008", "V009")
    for f in lifetime:
        for safe in safe_files:
            assert safe not in f.location.file, (
                f"safe script {safe} should not produce a lifetime finding; got {f}"
            )


def test_rule_is_registered() -> None:
    """Smoke-check that ObjectLifetimeRule is actually loaded by the engine."""
    from migguard.rules.registry import all_rules

    rule_ids = [r.rule_id for r in all_rules()]
    assert "lifetime/object-lifetime" in rule_ids
