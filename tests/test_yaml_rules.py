"""Tests for the YAML rule-pack loader and the RegexRule class."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from migguard.cli.main import cli
from migguard.core.engine import Engine
from migguard.core.models import Category, Severity
from migguard.rules.base import ALL_DIALECTS
from migguard.rules.yaml_rules import (
    YamlRuleConfigError,
    load_yaml_rules,
)


def _write(tmp_path: Path, body: str, name: str = "rules.yaml") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_minimal_rule_loads(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/no-nolock
    pattern: '(?i)\\bnolock\\b'
""",
    )
    rules = load_yaml_rules(p)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_id == "company/no-nolock"
    assert rule.severity is Severity.MEDIUM  # default
    assert rule.category is Category.NAMING  # default
    assert rule.dialects == ALL_DIALECTS


def test_full_rule_with_all_fields_loads(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/no-truncate-customer
    title: TRUNCATE on customer is forbidden
    severity: high
    category: data_loss
    dialects: [tsql, postgres]
    pattern: '(?i)truncate\\s+table\\s+app\\.customer'
    message: Truncating the customer table destroys the system of record.
    suggestion: Use a soft-delete column instead.
""",
    )
    rules = load_yaml_rules(p)
    rule = rules[0]
    assert rule.severity is Severity.HIGH
    assert rule.category is Category.DATA_LOSS
    assert rule.dialects == frozenset({"tsql", "postgres"})


def test_missing_pattern_is_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/broken
    severity: medium
""",
    )
    with pytest.raises(YamlRuleConfigError, match="missing a 'pattern'"):
        load_yaml_rules(p)


def test_invalid_regex_is_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/broken
    pattern: '[unclosed'
""",
    )
    with pytest.raises(YamlRuleConfigError, match="invalid regex"):
        load_yaml_rules(p)


def test_unknown_severity_is_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/broken
    pattern: 'foo'
    severity: catastrophic
""",
    )
    with pytest.raises(YamlRuleConfigError, match="unknown severity"):
        load_yaml_rules(p)


def test_unknown_category_is_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/broken
    pattern: 'foo'
    category: vibes_based
""",
    )
    with pytest.raises(YamlRuleConfigError, match="unknown category"):
        load_yaml_rules(p)


def test_unknown_dialect_is_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/broken
    pattern: 'foo'
    dialects: [oracle]
""",
    )
    with pytest.raises(YamlRuleConfigError, match="unknown dialects"):
        load_yaml_rules(p)


def test_duplicate_rule_id_is_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/dup
    pattern: 'foo'
  - id: company/dup
    pattern: 'bar'
""",
    )
    with pytest.raises(YamlRuleConfigError, match="duplicate rule id"):
        load_yaml_rules(p)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(YamlRuleConfigError, match="not found"):
        load_yaml_rules(tmp_path / "does-not-exist.yaml")


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = _write(tmp_path, "- just a list")
    with pytest.raises(YamlRuleConfigError, match="must be a mapping"):
        load_yaml_rules(p)


def test_regex_rule_fires_on_matching_statement(tmp_path: Path) -> None:
    config = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/no-nolock
    title: NOLOCK forbidden
    severity: medium
    category: locking
    dialects: [tsql]
    pattern: '(?i)\\bWITH\\s*\\(\\s*NOLOCK\\s*\\)'
    message: NOLOCK reads dirty data.
""",
    )
    sql_file = tmp_path / "V001__bad.sql"
    sql_file.write_text(
        "SELECT * FROM app.customer WITH (NOLOCK);", encoding="utf-8"
    )

    from migguard.rules.registry import all_rules

    custom = load_yaml_rules(config)
    engine = Engine(rules=all_rules() + custom, dialect="tsql")
    report = engine.review([sql_file])
    rule_ids = {f.rule_id for f in report.findings}
    assert "company/no-nolock" in rule_ids


def test_cli_loads_rules_config_and_fires(tmp_path: Path) -> None:
    config = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/no-select-star
    title: SELECT * forbidden
    severity: low
    category: performance
    pattern: '(?i)select\\s+\\*'
    message: Be explicit about columns.
""",
    )
    sql_file = tmp_path / "V001__bad.sql"
    sql_file.write_text("SELECT * FROM app.customer;", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "review",
            str(sql_file),
            "--no-llm",
            "--rules-config",
            str(config),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    assert "company/no-select-star" in result.output


def test_cli_invalid_rules_config_exits_2(tmp_path: Path) -> None:
    bad = _write(
        tmp_path,
        """\
version: 1
rules:
  - id: company/broken
""",
    )
    sql_file = tmp_path / "V001.sql"
    sql_file.write_text("SELECT 1;", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["review", str(sql_file), "--no-llm", "--rules-config", str(bad)],
    )
    assert result.exit_code == 2
    assert "Error loading rules config" in result.output


def test_shipped_example_rules_pack_loads_cleanly() -> None:
    """The example file in the repo must always be valid; it ships as docs."""
    example = (
        Path(__file__).parent.parent / "examples" / "custom-rules.yaml"
    )
    rules = load_yaml_rules(example)
    assert len(rules) >= 3
    ids = {r.rule_id for r in rules}
    assert "company/no-nolock-hint" in ids
