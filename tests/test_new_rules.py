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

import json
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


def test_many_to_many_risk_fires_for_unqualified_tables(tmp_path: Path) -> None:
    """Regression: the previous ``all(left_tbl)`` guard treated
    ``(None, "customer")`` as "skip" even though ``RuleContext.fact_for``
    is contracted to accept a None schema and default it to ``dbo``. That
    silently no-op'd the rule for any SQL written as ``FROM customer c``
    instead of ``FROM dbo.customer c`` -- the common case under SQL Server's
    default schema.

    We exercise the fix end-to-end: a fixture using unqualified table names,
    plus a temp snapshot keyed on ``dbo.*`` with large row counts so the
    heuristic has evidence to fire on. With the guard removed the rule
    fires; with the old guard it would not.
    """
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "dbo.customer": {
                    "schema": "dbo",
                    "name": "customer",
                    "row_count": 12_400_000,
                    "index_count": 3,
                    "foreign_key_count": 2,
                    "size_mb": 1820.5,
                },
                "dbo.event_log": {
                    "schema": "dbo",
                    "name": "event_log",
                    "row_count": 47_200_000,
                    "index_count": 5,
                    "foreign_key_count": 1,
                    "size_mb": 6320.1,
                },
            }
        ),
        encoding="utf-8",
    )

    engine = Engine(llm=None, schema_snapshot_path=snapshot)
    findings = engine.review([FIXTURES / "11_join_unqualified_tables.sql"]).findings
    m2m = [f for f in findings if f.rule_id == "joins/many-to-many-risk"]
    assert m2m, (
        "many-to-many-risk must fire on unqualified `FROM customer c JOIN "
        "event_log e` once fact_for is allowed to resolve schema=None -> dbo"
    )
    assert m2m[0].schema_context, "schema-grounded finding must cite row counts"
    assert "dbo.customer" in m2m[0].schema_context
    assert "dbo.event_log" in m2m[0].schema_context


def test_index_drop_without_recreate_fires_once(engine: Engine) -> None:
    findings = _findings(engine, "08_index_drop_no_recreate.sql")
    idx = [f for f in findings if f.rule_id == "rollback/index-drop-without-recreate"]
    assert len(idx) == 1, "DROP that has matching CREATE must not fire"
    assert idx[0].severity is Severity.MEDIUM
    assert "ix_customer_email" in idx[0].message


def test_index_rule_ignores_sql_inside_comments(engine: Engine) -> None:
    """Regression: ``IndexDropWithoutRecreateRule`` previously matched
    ``DROP INDEX`` / ``CREATE INDEX`` keywords inside SQL comments. That
    produced both false positives (DROP mentioned in a code-review note)
    and false negatives (TODO comment naming a future CREATE silently
    cancelling a real DROP). The fix strips comments before regex
    matching via :func:`migguard.rules._sql_text.strip_sql_comments`.

    Fixture ``10_index_comment_edge_cases.sql`` exercises three scenarios:

    A. Real ``DROP INDEX ix_alpha`` with a ``CREATE INDEX ix_alpha``
       mentioned only in a leading line comment. The DROP must still fire.
    B. ``CREATE TABLE`` plus a line comment containing ``DROP INDEX
       ix_beta``. The rule must NOT fire on the prose.
    C. ``CREATE TABLE`` plus a ``/* ... */`` block comment containing
       ``DROP INDEX ix_gamma``. The rule must NOT fire on the prose.
    """
    findings = _findings(engine, "10_index_comment_edge_cases.sql")
    idx = [f for f in findings if f.rule_id == "rollback/index-drop-without-recreate"]
    assert len(idx) == 1, (
        f"expected exactly one finding (Scenario A), got {[f.message for f in idx]}"
    )

    msg = idx[0].message.lower()
    assert "ix_alpha" in msg, f"Scenario A's DROP must fire; got {idx[0].message}"

    # Hard guards against the previously observed misfires.
    for f in idx:
        m = f.message.lower()
        assert "ix_beta" not in m, "Scenario B false positive: matched DROP inside line comment"
        assert "ix_gamma" not in m, "Scenario C false positive: matched DROP inside block comment"


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


def test_dbcc_rule_ignores_keywords_in_string_literals(engine: Engine) -> None:
    """Regression: ``DbccCommandRule`` previously matched ``DBCC`` keywords
    inside ``'...'`` string literals. A statement like::

        INSERT INTO app.audit_log (event_type, message)
        VALUES ('maintenance', 'Ran DBCC SHRINKDATABASE on staging');

    would wrongly fire as HIGH severity ("dangerous shrink"). The fix
    routes raw_sql through :func:`migguard.rules._sql_text.strip_sql_noise`
    so string-literal payloads are blanked before regex matching. The
    fixture also covers the universal ``''``-escape form (``'it''s'``).
    """
    findings = _findings(engine, "12_string_literal_edge_cases.sql")
    dbcc = [f for f in findings if f.rule_id == "compatibility/dbcc-command"]
    assert not dbcc, (
        f"DBCC rule misfired on string-literal content: "
        f"{[(f.severity.value, f.message[:80]) for f in dbcc]}"
    )


def test_index_rule_ignores_keywords_in_string_literals(engine: Engine) -> None:
    """Regression: ``IndexDropWithoutRecreateRule`` shared the same
    raw-SQL keyword-matching path as the DBCC rule and had the same
    string-literal false positive. ``'...DROP INDEX ix_phantom...'`` inside
    an INSERT payload was being reported as a missing-recreate finding.
    Same fix: route through ``strip_sql_noise``.
    """
    findings = _findings(engine, "12_string_literal_edge_cases.sql")
    idx = [f for f in findings if f.rule_id == "rollback/index-drop-without-recreate"]
    assert not idx, (
        f"index rule misfired on string-literal content: "
        f"{[f.message[:80] for f in idx]}"
    )


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
