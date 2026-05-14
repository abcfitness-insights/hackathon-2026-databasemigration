"""Cross-migration object lifetime tracker.

Walks a chain of migrations in version order and flags statements that
reference objects (tables, columns) that an earlier migration in the same
review DROP'd and did not recreate. Catches stale-reference bugs that pass
silently in production but explode on replay against a fresh database.

Two findings:

- ``lifetime/dropped-table-referenced``  (HIGH) -- DML references a table that
                                                   an earlier migration dropped.
- ``lifetime/dropped-column-referenced`` (HIGH) -- DML references a column that
                                                   an earlier migration dropped.

Deliberate scope limits (chosen to keep false positives at zero):

- Skips statements that failed to parse.
- Only tracks tables and columns we have explicitly seen ALTER / CREATE /
  DROP for in the chain. Pre-existing schema objects we know nothing about
  are never flagged.
- Resolves unqualified columns only when exactly one table is in the
  statement's FROM scope. Multi-table joins with unqualified columns are
  skipped.
- Re-creating a dropped object (e.g. ``DROP TABLE t; CREATE TABLE t(...)``)
  clears the dropped state, so subsequent references are not flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlglot import exp

from migguard.core.models import Category, Severity
from migguard.core.parser import ParsedScript, ParsedStatement
from migguard.rules.base import Rule, RuleContext
from migguard.rules.checks.sequencing import _extract_version

_DEFAULT_SCHEMA = "dbo"


@dataclass
class _Lifetime:
    """Mutable state tracked while walking the migration chain."""

    dropped_tables: set[tuple[str, str]] = field(default_factory=set)
    dropped_columns: set[tuple[str, str, str]] = field(default_factory=set)
    known_columns: set[tuple[str, str, str]] = field(default_factory=set)

    def create_table(self, schema: str, table: str, columns: list[str]) -> None:
        self.dropped_tables.discard((schema, table))
        for col in columns:
            self.known_columns.add((schema, table, col))
            self.dropped_columns.discard((schema, table, col))

    def drop_table(self, schema: str, table: str) -> None:
        key = (schema, table)
        self.dropped_tables.add(key)
        for s, t, c in list(self.known_columns):
            if (s, t) == key:
                self.dropped_columns.add((s, t, c))

    def add_column(self, schema: str, table: str, column: str) -> None:
        self.known_columns.add((schema, table, column))
        self.dropped_columns.discard((schema, table, column))

    def drop_column(self, schema: str, table: str, column: str) -> None:
        self.known_columns.add((schema, table, column))
        self.dropped_columns.add((schema, table, column))


def _identifier_name(node: exp.Expression | None) -> str | None:
    if node is None:
        return None
    name = getattr(node, "name", None)
    if name:
        return name
    return str(node)


def _table_key(node: exp.Table | None) -> tuple[str, str] | None:
    if node is None or not node.name:
        return None
    db = node.args.get("db")
    schema = _identifier_name(db) if db is not None else None
    return ((schema or _DEFAULT_SCHEMA).lower(), node.name.lower())


def _cte_names(ast: exp.Expression) -> set[str]:
    """Names introduced by ``WITH cte AS ( ... )`` -- not real tables."""
    out: set[str] = set()
    for cte in ast.find_all(exp.CTE):
        alias = cte.args.get("alias")
        name = _identifier_name(alias)
        if name:
            out.add(name.lower())
    return out


def _alter_table_target(ast: exp.Alter) -> tuple[str, str] | None:
    """Resolve the (schema, table) being ALTER'd."""
    direct = ast.args.get("this")
    if isinstance(direct, exp.Table):
        return _table_key(direct)
    for tbl in ast.find_all(exp.Table):
        return _table_key(tbl)
    return None


class ObjectLifetimeRule(Rule):
    """Flags references to objects that were dropped earlier in the chain."""

    rule_id = "lifetime/object-lifetime"
    title = "Cross-migration object lifetime"
    category = Category.ORDERING
    severity = Severity.HIGH

    def check(self, script: ParsedScript, ctx: RuleContext) -> list:
        return []

    def check_collection(
        self, scripts: list[ParsedScript], ctx: RuleContext
    ) -> list:
        if len(scripts) < 2:
            return []

        def sort_key(s: ParsedScript) -> tuple[int, int, str]:
            ver = _extract_version(Path(s.file_path).name)
            if ver is None:
                return (1, 0, s.file_path)
            return (0, ver[1], s.file_path)

        ordered = sorted(scripts, key=sort_key)
        state = _Lifetime()
        findings: list = []

        for script in ordered:
            for stmt in script.statements:
                if stmt.ast is None:
                    continue
                self._apply_ddl(stmt, state)
                findings.extend(self._check_references(stmt, script, state))

        return findings

    def _apply_ddl(self, stmt: ParsedStatement, state: _Lifetime) -> None:
        ast = stmt.ast
        if ast is None:
            return

        if isinstance(ast, exp.Create):
            if (ast.args.get("kind") or "").upper() != "TABLE":
                return
            tbl = ast.find(exp.Table)
            key = _table_key(tbl)
            if key is None:
                return
            schema, name = key
            columns: list[str] = []
            for col_def in ast.find_all(exp.ColumnDef):
                col_name = _identifier_name(col_def.args.get("this"))
                if col_name:
                    columns.append(col_name.lower())
            state.create_table(schema, name, columns)
            return

        if isinstance(ast, exp.Drop):
            if (ast.args.get("kind") or "").upper() != "TABLE":
                return
            for tbl in ast.find_all(exp.Table):
                key = _table_key(tbl)
                if key is not None:
                    state.drop_table(*key)
            return

        if isinstance(ast, exp.Alter):
            key = _alter_table_target(ast)
            if key is None:
                return
            schema, name = key
            for action in ast.args.get("actions") or []:
                if isinstance(action, exp.ColumnDef):
                    col_name = _identifier_name(action.args.get("this"))
                    if col_name:
                        state.add_column(schema, name, col_name.lower())
                elif isinstance(action, exp.Drop):
                    if (action.args.get("kind") or "").upper() == "COLUMN":
                        target = action.args.get("this")
                        col_name: str | None = None
                        if isinstance(target, exp.Column):
                            col_name = _identifier_name(target.args.get("this"))
                        elif target is not None:
                            col_name = _identifier_name(target)
                        if col_name:
                            state.drop_column(schema, name, col_name.lower())

    def _check_references(
        self, stmt: ParsedStatement, script: ParsedScript, state: _Lifetime
    ) -> list:
        ast = stmt.ast
        if ast is None:
            return []
        if isinstance(ast, exp.Create | exp.Drop | exp.Alter):
            return []
        if not isinstance(ast, exp.Select | exp.Update | exp.Insert | exp.Delete):
            return []

        out: list = []
        cte_names = _cte_names(ast)

        alias_to_table: dict[str, tuple[str, str]] = {}
        real_tables: list[tuple[str, str]] = []
        for tbl_node in ast.find_all(exp.Table):
            if tbl_node.name and tbl_node.name.lower() in cte_names:
                continue
            key = _table_key(tbl_node)
            if key is None:
                continue
            alias = (tbl_node.alias_or_name or tbl_node.name or "").lower()
            if alias:
                alias_to_table[alias] = key
            real_tables.append(key)

        seen_tables: set[tuple[str, str]] = set()
        for key in real_tables:
            if key in seen_tables:
                continue
            seen_tables.add(key)
            if key in state.dropped_tables:
                schema, table = key
                out.append(
                    self.make_finding(
                        stmt,
                        script,
                        rule_id="lifetime/dropped-table-referenced",
                        title=f"References dropped table {schema}.{table}",
                        message=(
                            f"This statement references table `{schema}.{table}`, but "
                            "an earlier migration in this review DROP'd it and did not "
                            "recreate it. Replaying these migrations against a fresh "
                            "database will fail here."
                        ),
                        suggestion=(
                            f"Recreate `{schema}.{table}` before this statement, "
                            "or remove the reference."
                        ),
                        severity=Severity.HIGH,
                    )
                )

        single_table: tuple[str, str] | None = (
            real_tables[0] if len(set(real_tables)) == 1 else None
        )

        seen_cols: set[tuple[str, str, str]] = set()
        for col_node in ast.find_all(exp.Column):
            col_name = (col_node.name or "").lower()
            if not col_name:
                continue

            table_arg = col_node.args.get("table")
            tbl_qual = _identifier_name(table_arg) if table_arg is not None else None
            tbl_qual = tbl_qual.lower() if tbl_qual else None

            resolved: tuple[str, str] | None = None
            if tbl_qual:
                if tbl_qual in cte_names:
                    continue
                resolved = alias_to_table.get(tbl_qual)
            elif single_table is not None:
                resolved = single_table

            if resolved is None:
                continue

            key = (resolved[0], resolved[1], col_name)
            if key in seen_cols:
                continue
            seen_cols.add(key)

            if key in state.dropped_columns:
                schema, table, column = key
                out.append(
                    self.make_finding(
                        stmt,
                        script,
                        rule_id="lifetime/dropped-column-referenced",
                        title=(
                            f"References dropped column {schema}.{table}.{column}"
                        ),
                        message=(
                            f"This statement references column "
                            f"`{schema}.{table}.{column}`, but an earlier migration "
                            "in this review DROP'd it and did not re-add it. "
                            "Replaying these migrations against a fresh database "
                            "will fail here."
                        ),
                        suggestion=(
                            f"Re-add `{column}` before this statement, or update "
                            "the reference to a column that still exists."
                        ),
                        severity=Severity.HIGH,
                    )
                )

        return out
