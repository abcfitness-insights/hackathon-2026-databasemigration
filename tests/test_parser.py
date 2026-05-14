"""Parser tests. Pins down line-number resolution, statement splitting, and
AST root inclusion — everything downstream (rule checks, PR comments) depends
on these guarantees."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlglot import exp

from migguard.core.parser import parse_script

FIXTURES = Path(__file__).parent / "fixtures" / "migrations"


def test_clean_migration_parses_without_errors() -> None:
    script = parse_script(FIXTURES / "01_clean_add_column.sql")
    assert not script.has_parse_errors, [
        s.parse_error for s in script.statements if s.parse_error
    ]
    assert len(script.statements) >= 1


def test_delete_without_where_detected_at_root() -> None:
    """sqlglot parses ``DELETE FROM tbl`` as a Delete; we must include the root."""
    script = parse_script(FIXTURES / "02_delete_without_where.sql")
    deletes = script.by_node_type(exp.Delete)
    assert len(deletes) == 1, "expected exactly one DELETE at the AST root"
    _, node = deletes[0]
    assert node.find(exp.Where) is None, "fixture must NOT have a WHERE clause"


def test_alter_table_add_column_detected() -> None:
    """sqlglot v30 uses ``exp.Alter`` (not AlterTable)."""
    script = parse_script(FIXTURES / "03_not_null_default_on_big_table.sql")
    alters = script.by_node_type(exp.Alter)
    assert len(alters) == 1
    _, node = alters[0]
    column_defs = list(node.find_all(exp.ColumnDef))
    assert column_defs, "ALTER TABLE ... ADD col should yield a ColumnDef"


def test_drop_table_detected_at_root() -> None:
    """Drop nodes also live at the AST root after our find_nodes fix."""
    script = parse_script(FIXTURES / "04_drop_without_idempotency.sql")
    drops = script.by_node_type(exp.Drop)
    table_drops = [d for _, d in drops if d.args.get("kind") == "TABLE"]
    assert len(table_drops) == 1


def test_drop_index_not_parseable_but_visible_as_raw_sql() -> None:
    """sqlglot can't parse ``DROP INDEX X ON Y`` in T-SQL dialect.
    Rules must fall back to raw_sql / regex for these cases."""
    script = parse_script(FIXTURES / "04_drop_without_idempotency.sql")
    drop_index_stmts = [s for s in script.statements if "DROP INDEX" in s.raw_sql.upper()]
    assert len(drop_index_stmts) == 1
    assert drop_index_stmts[0].parse_error is not None  # confirms sqlglot couldn't parse


def test_mixed_migration_has_multiple_statements() -> None:
    script = parse_script(FIXTURES / "05_mixed_severity.sql")
    parseable = [s for s in script.statements if s.parsed_ok]
    assert len(parseable) >= 3, [s.parse_error for s in script.statements]


def test_go_batch_separator_splits_statements() -> None:
    text = "SELECT 1;\nGO\nSELECT 2;\nGO\n"
    script = parse_script("inline.sql", text=text)
    assert len(script.statements) == 2
    assert script.statements[0].line_start == 1
    assert script.statements[1].line_start == 3


@pytest.mark.parametrize(
    "fixture",
    [
        "01_clean_add_column.sql",
        "02_delete_without_where.sql",
        "03_not_null_default_on_big_table.sql",
        "04_drop_without_idempotency.sql",
        "05_mixed_severity.sql",
    ],
)
def test_all_fixtures_load_and_partition(fixture: str) -> None:
    script = parse_script(FIXTURES / fixture)
    assert script.statements, f"{fixture} produced zero statements"
    for stmt in script.statements:
        assert stmt.line_start >= 1
        assert stmt.line_end >= stmt.line_start
        assert stmt.raw_sql.strip()
