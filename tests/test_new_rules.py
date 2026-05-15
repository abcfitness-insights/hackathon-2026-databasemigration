"""Tests for the six rules added in the Youssef_New_rules_addition branch.

Covers:

- joins/missing-on-clause                (HIGH)
- joins/on-nullable-key                  (LOW)
- joins/function-on-key                  (MEDIUM)
- joins/many-to-many-risk                (MEDIUM, schema-grounded)
- rollback/index-drop-without-recreate   (MEDIUM)
- compatibility/dbcc-command             (MEDIUM, HIGH for SHRINK* / REPAIR_ALLOW_DATA_LOSS)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from migguard.core.engine import Engine
from migguard.core.models import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "migrations"

_NEW_RULE_IDS = {
    "joins/missing-on-clause",
    "joins/on-nullable-key",
    "joins/function-on-key",
    "joins/many-to-many-risk",
    "rollback/index-drop-without-recreate",
    "compatibility/dbcc-command",
}


@pytest.fixture
def engine() -> Engine:
    return Engine(llm=None)


def _findings(engine: Engine, fixture: str):
    return engine.review([FIXTURES / fixture]).findings


def _fired_ids(engine: Engine, fixture: str) -> set[str]:
    return {f.rule_id for f in _findings(engine, fixture)}


def test_join_missing_on_is_high(engine: Engine) -> None:
    findings = _findings(engine, "07_join_explosion.sql")
    missing = [f for f in findings if f.rule_id == "joins/missing-on-clause"]
    assert len(missing) == 1, [f.rule_id for f in findings]
    assert missing[0].severity is Severity.HIGH


def test_join_function_on_key_fires(engine: Engine) -> None:
    findings = _findings(engine, "07_join_explosion.sql")
    fn = [f for f in findings if f.rule_id == "joins/function-on-key"]
    assert fn, "expected at least one function-on-key finding"
    assert all(f.severity is Severity.MEDIUM for f in fn)


def test_join_on_nullable_key_fires(engine: Engine) -> None:
    findings = _findings(engine, "07_join_explosion.sql")
    nullable = [f for f in findings if f.rule_id == "joins/on-nullable-key"]
    assert nullable, "expected nullable-key finding for region_code"
    assert all(f.severity is Severity.LOW for f in nullable)


def test_many_to_many_risk_is_schema_grounded(engine: Engine) -> None:
    findings = _findings(engine, "07_join_explosion.sql")
    m2m = [f for f in findings if f.rule_id == "joins/many-to-many-risk"]
    assert m2m, "expected many-to-many-risk finding on customer x event_log"
    assert m2m[0].severity is Severity.MEDIUM
    assert m2m[0].schema_context, "many-to-many finding must cite row counts"
    assert "12," in m2m[0].schema_context or "47," in m2m[0].schema_context


def test_index_drop_without_recreate_fires_once(engine: Engine) -> None:
    findings = _findings(engine, "08_index_drop_no_recreate.sql")
    idx = [f for f in findings if f.rule_id == "rollback/index-drop-without-recreate"]
    assert len(idx) == 1, "DROP that has matching CREATE must not fire"
    assert idx[0].severity is Severity.MEDIUM
    assert "ix_customer_email" in idx[0].message


def test_dbcc_rule_counts_and_severities(engine: Engine) -> None:
    findings = _findings(engine, "09_dbcc_commands.sql")
    dbcc = [f for f in findings if f.rule_id == "compatibility/dbcc-command"]
    assert len(dbcc) == 4, [f.message for f in dbcc]
    high = [f for f in dbcc if f.severity is Severity.HIGH]
    med = [f for f in dbcc if f.severity is Severity.MEDIUM]
    assert len(high) == 2, "SHRINKDATABASE + REPAIR_ALLOW_DATA_LOSS should be HIGH"
    assert len(med) == 2, "CHECKDB + OPENTRAN should be MEDIUM"


def test_dbcc_rule_only_fires_on_tsql() -> None:
    """DBCC is T-SQL only; running the same fixture under another dialect
    should silence the rule."""
    pg_engine = Engine(llm=None, dialect="postgres")
    findings = pg_engine.review([FIXTURES / "09_dbcc_commands.sql"]).findings
    dbcc = [f for f in findings if f.rule_id == "compatibility/dbcc-command"]
    assert not dbcc, "DBCC rule must not fire under postgres dialect"


def test_new_rules_do_not_misfire_on_clean_fixture(engine: Engine) -> None:
    """The canonical clean fixture must stay clean after the new rules
    land — no false positives."""
    fired = _fired_ids(engine, "01_clean_add_column.sql")
    leaked = fired & _NEW_RULE_IDS
    assert not leaked, f"new rules misfired on clean fixture: {sorted(leaked)}"


def test_rule_pack_count_grew_by_six() -> None:
    """Hard guard: the registry must include the six new rules."""
    from migguard.rules.registry import all_rules

    ids = {r.rule_id for r in all_rules()}
    missing = _NEW_RULE_IDS - ids
    assert not missing, f"new rules not registered: {sorted(missing)}"
