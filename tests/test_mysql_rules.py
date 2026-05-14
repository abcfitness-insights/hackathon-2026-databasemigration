"""Tests for the MySQL-specific rule pack.

These three rules raise MigGuard's MySQL coverage from "parser accepts it"
to "catches the high-impact InnoDB footguns that universal rules can't see":

* ``mysql/alter-table-without-algorithm``  ALGORITHM not specified - InnoDB
  may pick COPY (full table rewrite + metadata lock).
* ``mysql/utf8-not-utf8mb4``                CHARACTER SET utf8 is utf8mb3.
* ``mysql/zero-date-default``               '0000-00-00' values rejected by
                                            STRICT mode on MySQL 5.7+.

The rules must:
* Fire only on the correct positive cases.
* Stay silent on negative cases (explicit ALGORITHM, utf8mb4, normal date).
* Stay silent when ``--dialect`` is anything other than ``mysql``.
* Escalate ALGORITHM severity from MEDIUM to HIGH when schema facts say
  the target table is large/huge.
"""

from __future__ import annotations

from pathlib import Path

from migguard.core.engine import Engine
from migguard.core.models import Severity
from migguard.rules.checks.mysql_rules import (
    AlterTableWithoutAlgorithmRule,
    Utf8NotUtf8mb4Rule,
    ZeroDateDefaultRule,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mysql"


def _rule_ids(report) -> list[str]:
    return [f.rule_id for f in report.findings]


# ---- ALTER without ALGORITHM ----------------------------------------------


def test_alter_without_algorithm_fires_on_mysql() -> None:
    engine = Engine(dialect="mysql")
    report = engine.review([FIXTURES / "V001__alter_without_algorithm.sql"])
    assert "mysql/alter-table-without-algorithm" in _rule_ids(report)


def test_alter_with_algorithm_does_not_fire() -> None:
    engine = Engine(dialect="mysql")
    report = engine.review([FIXTURES / "V002__alter_with_algorithm_ok.sql"])
    assert "mysql/alter-table-without-algorithm" not in _rule_ids(report)


def test_alter_without_algorithm_escalates_to_high_on_large_table() -> None:
    """app.customer is 12.4M rows in the bundled schema snapshot."""
    engine = Engine(dialect="mysql")
    report = engine.review([FIXTURES / "V001__alter_without_algorithm.sql"])
    finding = next(
        f
        for f in report.findings
        if f.rule_id == "mysql/alter-table-without-algorithm"
    )
    assert finding.severity is Severity.HIGH
    assert finding.schema_context is not None
    assert "rows" in finding.schema_context


# ---- utf8 vs utf8mb4 ------------------------------------------------------


def test_utf8_charset_fires() -> None:
    engine = Engine(dialect="mysql")
    report = engine.review([FIXTURES / "V003__bad_charset.sql"])
    assert "mysql/utf8-not-utf8mb4" in _rule_ids(report)


def test_utf8mb4_does_not_fire() -> None:
    engine = Engine(dialect="mysql")
    report = engine.review([FIXTURES / "V004__good_charset.sql"])
    assert "mysql/utf8-not-utf8mb4" not in _rule_ids(report)


def test_utf8mb4_lookahead_is_strict() -> None:
    """Direct check on the regex: utf8mb4 must NOT be flagged, utf8 must."""
    rule = Utf8NotUtf8mb4Rule()
    assert rule._RE.search("CHARSET=utf8")
    assert rule._RE.search("CHARACTER SET utf8")
    assert rule._RE.search("DEFAULT CHARSET=utf8")
    assert rule._RE.search("DEFAULT CHARSET = utf8 COLLATE foo")
    assert not rule._RE.search("CHARSET=utf8mb4")
    assert not rule._RE.search("DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci")


# ---- Zero-date default ----------------------------------------------------


def test_zero_date_default_fires() -> None:
    engine = Engine(dialect="mysql")
    report = engine.review([FIXTURES / "V005__zero_date_default.sql"])
    assert "mysql/zero-date-default" in _rule_ids(report)


def test_zero_date_regex_matches_both_forms() -> None:
    rule = ZeroDateDefaultRule()
    assert rule._RE.search("DEFAULT '0000-00-00'")
    assert rule._RE.search("DEFAULT '0000-00-00 00:00:00'")
    assert not rule._RE.search("DEFAULT '1970-01-01'")
    assert not rule._RE.search("DEFAULT NULL")


# ---- Clean fixture (positive control) ------------------------------------


def test_clean_mysql_migration_produces_no_mysql_findings() -> None:
    engine = Engine(dialect="mysql")
    report = engine.review([FIXTURES / "V006__clean_mysql_migration.sql"])
    mysql_findings = [f for f in report.findings if f.rule_id.startswith("mysql/")]
    assert mysql_findings == []


# ---- Dialect filtering ----------------------------------------------------


def test_mysql_rules_do_not_fire_on_tsql_dialect() -> None:
    """Same SQL, --dialect tsql should NOT raise mysql/* findings."""
    engine = Engine(dialect="tsql")
    report = engine.review([FIXTURES / "V003__bad_charset.sql"])
    mysql_findings = [f for f in report.findings if f.rule_id.startswith("mysql/")]
    assert mysql_findings == []


def test_alter_algorithm_rule_only_targets_mysql() -> None:
    rule = AlterTableWithoutAlgorithmRule()
    assert "mysql" in rule.dialects
    assert "tsql" not in rule.dialects
    assert "postgres" not in rule.dialects


def test_utf8_rule_only_targets_mysql() -> None:
    rule = Utf8NotUtf8mb4Rule()
    assert rule.dialects == frozenset({"mysql"})


def test_zero_date_rule_only_targets_mysql() -> None:
    rule = ZeroDateDefaultRule()
    assert rule.dialects == frozenset({"mysql"})


# ---- Registry wiring ------------------------------------------------------


def test_all_three_rules_are_registered() -> None:
    from migguard.rules.registry import all_rules

    ids = {r.rule_id for r in all_rules()}
    assert "mysql/alter-table-without-algorithm" in ids
    assert "mysql/utf8-not-utf8mb4" in ids
    assert "mysql/zero-date-default" in ids
