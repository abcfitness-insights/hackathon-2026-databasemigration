"""Version sequencing rule tests."""

from __future__ import annotations

from pathlib import Path

from migguard.core.engine import Engine
from migguard.core.models import Severity

FIXTURES = Path(__file__).parent / "fixtures"


def _rule_ids(report) -> set[str]:
    return {f.rule_id for f in report.findings}


def test_version_gap_detected_flyway() -> None:
    engine = Engine(llm=None)
    files = sorted((FIXTURES / "sequencing").glob("*.sql"))
    assert files, "expected sequencing fixtures to exist"
    report = engine.review(files)
    ids = _rule_ids(report)
    assert "sequencing/version-gap" in ids, ids
    gap = next(f for f in report.findings if f.rule_id == "sequencing/version-gap")
    assert gap.severity is Severity.MEDIUM
    assert "V003" in gap.message or "3" in gap.message


def test_duplicate_version_detected() -> None:
    engine = Engine(llm=None)
    files = sorted((FIXTURES / "duplicates").glob("*.sql"))
    assert len(files) == 2
    report = engine.review(files)
    ids = _rule_ids(report)
    assert "sequencing/duplicate-version" in ids, ids
    dup = next(f for f in report.findings if f.rule_id == "sequencing/duplicate-version")
    assert dup.severity is Severity.HIGH
    assert "V003__alpha.sql" in dup.message
    assert "V003__beta.sql" in dup.message


def test_single_file_review_skips_sequencing() -> None:
    engine = Engine(llm=None)
    report = engine.review([FIXTURES / "migrations" / "01_clean_add_column.sql"])
    ids = _rule_ids(report)
    assert "sequencing/version-gap" not in ids
    assert "sequencing/duplicate-version" not in ids
