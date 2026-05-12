"""Naming convention rule tests."""

from __future__ import annotations

from pathlib import Path

from migguard.core.engine import Engine
from migguard.core.models import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "migrations"


def test_naming_rules_fire_on_bad_fixture() -> None:
    engine = Engine(llm=None)
    report = engine.review([FIXTURES / "06_bad_naming.sql"])
    ids = {f.rule_id for f in report.findings}

    assert "naming/non-snake-case" in ids, ids
    assert "naming/reserved-word" in ids, ids
    assert "naming/too-long" in ids, ids


def test_reserved_word_finding_is_medium() -> None:
    engine = Engine(llm=None)
    report = engine.review([FIXTURES / "06_bad_naming.sql"])
    reserved = [f for f in report.findings if f.rule_id == "naming/reserved-word"]
    assert reserved
    assert reserved[0].severity is Severity.MEDIUM


def test_clean_fixture_has_no_naming_findings() -> None:
    engine = Engine(llm=None)
    report = engine.review([FIXTURES / "01_clean_add_column.sql"])
    naming = [f for f in report.findings if f.rule_id.startswith("naming/")]
    assert not naming, [f.rule_id for f in naming]
