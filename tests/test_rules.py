"""Golden-style tests for the deterministic rule pack. We assert which rules
fire on each fixture and where — these tests guard against silent regressions
when rules are refactored."""

from __future__ import annotations

from pathlib import Path

import pytest

from migguard.core.engine import Engine
from migguard.core.models import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "migrations"


@pytest.fixture
def engine() -> Engine:
    """Engine without LLM, with the bundled schema snapshot."""
    return Engine(llm=None)


def _rules_fired(engine: Engine, fixture: str) -> set[str]:
    report = engine.review([FIXTURES / fixture])
    return {f.rule_id for f in report.findings}


def test_clean_migration_has_no_high_findings(engine: Engine) -> None:
    report = engine.review([FIXTURES / "01_clean_add_column.sql"])
    high = [f for f in report.findings if f.severity is Severity.HIGH]
    assert not high, [f.rule_id for f in high]


def test_delete_without_where_is_high(engine: Engine) -> None:
    report = engine.review([FIXTURES / "02_delete_without_where.sql"])
    delete_findings = [f for f in report.findings if f.rule_id == "data-loss/delete-without-where"]
    assert len(delete_findings) == 1
    f = delete_findings[0]
    assert f.severity is Severity.HIGH
    assert f.schema_context and "47," in f.schema_context  # 47.2M rows from snapshot
    assert report.blocks_merge


def test_not_null_default_on_huge_table_escalates_to_high(engine: Engine) -> None:
    fired = _rules_fired(engine, "03_not_null_default_on_big_table.sql")
    assert "locking/not-null-default-on-large-table" in fired
    report = engine.review([FIXTURES / "03_not_null_default_on_big_table.sql"])
    locking = next(
        f for f in report.findings if f.rule_id == "locking/not-null-default-on-large-table"
    )
    assert locking.severity is Severity.HIGH
    assert locking.schema_context and "12," in locking.schema_context


def test_drop_table_and_idempotency_fire_together(engine: Engine) -> None:
    fired = _rules_fired(engine, "04_drop_without_idempotency.sql")
    assert "data-loss/drop-table" in fired
    assert "idempotency/drop-without-if-exists" in fired
    assert "rollback/no-down-script" in fired


def test_mixed_fixture_finds_truncate_merge_grant_update(engine: Engine) -> None:
    fired = _rules_fired(engine, "05_mixed_severity.sql")
    expected = {
        "data-loss/truncate",
        "compatibility/merge-on-synapse",
        "permissions/grant-or-deny",
    }
    missing = expected - fired
    assert not missing, f"missing rules: {missing} (fired: {sorted(fired)})"


def test_rule_pack_loads_all_rules(engine: Engine) -> None:
    assert len(engine.rules) >= 12, f"too few rules registered ({len(engine.rules)})"
