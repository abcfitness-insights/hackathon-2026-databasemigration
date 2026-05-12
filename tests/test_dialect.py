"""Multi-dialect support: parser dialects, rule filtering."""

from __future__ import annotations

from pathlib import Path

from migguard.core.engine import Engine
from migguard.core.parser import SUPPORTED_DIALECTS, parse_script
from migguard.rules.registry import all_rules

FIXTURES = Path(__file__).parent / "fixtures"


def test_all_supported_dialects_advertised() -> None:
    assert "tsql" in SUPPORTED_DIALECTS
    assert "postgres" in SUPPORTED_DIALECTS
    assert "mysql" in SUPPORTED_DIALECTS
    assert "sqlite" in SUPPORTED_DIALECTS


def test_postgres_migration_parses() -> None:
    script = parse_script(
        FIXTURES / "postgres" / "01_add_index_without_concurrently.sql",
        dialect="postgres",
    )
    assert not script.has_parse_errors, [
        s.parse_error for s in script.statements if s.parse_error
    ]
    assert script.dialect == "postgres"


def test_tsql_only_rules_skipped_on_postgres() -> None:
    """ONLINE=ON, BEGIN TRAN, MERGE-on-Synapse, sys.columns rules don't apply."""
    engine = Engine(llm=None, dialect="postgres")
    active = {r.rule_id for r in engine.active_rules}
    assert "locking/create-index-without-online" not in active
    assert "transaction/missing-tran-wrapper" not in active
    assert "compatibility/merge-on-synapse" not in active
    assert "idempotency/alter-add-column-without-guard" not in active


def test_universal_rules_still_apply_on_postgres() -> None:
    engine = Engine(llm=None, dialect="postgres")
    active = {r.rule_id for r in engine.active_rules}
    assert "data-loss/delete-without-where" in active
    assert "data-loss/drop-table" in active
    assert "naming/conventions" in active
    assert "sequencing/version-sequence" in active


def test_postgres_fixture_flags_delete_without_where() -> None:
    engine = Engine(llm=None, dialect="postgres")
    report = engine.review(
        [FIXTURES / "postgres" / "01_add_index_without_concurrently.sql"]
    )
    ids = {f.rule_id for f in report.findings}
    assert "data-loss/delete-without-where" in ids


def test_all_rules_tagged_with_at_least_one_dialect() -> None:
    for r in all_rules():
        assert r.dialects, f"rule {r.rule_id} has empty dialect set"
        for d in r.dialects:
            assert d in SUPPORTED_DIALECTS, f"unknown dialect {d!r} on rule {r.rule_id}"
