"""Data-loss rules. These are the highest-stakes category — any HIGH finding here
should be treated as a blocker until reviewed by a senior engineer / DBA."""

from __future__ import annotations

import re

from sqlglot import exp

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules._sql_text import strip_sql_noise
from migguard.rules.base import Rule, RuleContext


def _table_name(node: exp.Expression) -> tuple[str | None, str]:
    """Best-effort extraction of (schema, table) from a sqlglot node."""
    table = node.find(exp.Table)
    if not table:
        return None, "<unknown>"
    return table.db or None, table.name or "<unknown>"


class DeleteWithoutWhereRule(Rule):
    rule_id = "data-loss/delete-without-where"
    title = "DELETE without WHERE clause"
    category = Category.DATA_LOSS
    severity = Severity.HIGH

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, node in script.by_node_type(exp.Delete):
            if node.find(exp.Where) is not None:
                continue
            schema, table = _table_name(node)
            facts = ctx.fact_for(schema, table)
            sev = Severity.HIGH
            schema_ctx = None
            if facts:
                schema_ctx = (
                    f"{facts.schema}.{facts.name} currently has ~{facts.row_count:,} rows."
                )
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    severity=sev,
                    schema_context=schema_ctx,
                    message=(
                        f"`DELETE FROM {schema or 'dbo'}.{table}` has no `WHERE` clause. "
                        "This will delete every row in the table."
                    ),
                    suggestion=(
                        "Add a `WHERE` clause that narrows the rows, or — if you "
                        "really mean to empty the table — use `TRUNCATE` explicitly "
                        "with a code-review note."
                    ),
                )
            )
        return out


class UpdateWithoutWhereRule(Rule):
    rule_id = "data-loss/update-without-where"
    title = "UPDATE without WHERE clause"
    category = Category.DATA_LOSS
    severity = Severity.HIGH

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, node in script.by_node_type(exp.Update):
            if node.find(exp.Where) is not None:
                continue
            schema, table = _table_name(node)
            schema_ctx = None
            facts = ctx.fact_for(schema, table)
            if facts:
                schema_ctx = (
                    f"{facts.schema}.{facts.name} currently has ~{facts.row_count:,} rows."
                )
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    schema_context=schema_ctx,
                    message=(
                        f"`UPDATE {schema or 'dbo'}.{table}` has no `WHERE` clause. "
                        "Every row will be overwritten."
                    ),
                    suggestion="Add a `WHERE` clause restricting the affected rows.",
                )
            )
        return out


class TruncateRule(Rule):
    rule_id = "data-loss/truncate"
    title = "TRUNCATE TABLE"
    category = Category.DATA_LOSS
    severity = Severity.HIGH

    _RE = re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            # Scrub so `('Considered TRUNCATE TABLE staging')` audit-log
            # entries or `-- TRUNCATE TABLE was rejected` comments don't
            # trigger a HIGH-severity finding on benign INSERTs.
            if not self._RE.search(strip_sql_noise(stmt.raw_sql)):
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`TRUNCATE TABLE` removes all rows and resets identity. "
                        "It is not logged per-row and not easily recoverable."
                    ),
                    suggestion=(
                        "If this is intentional and the data is reproducible, OK. "
                        "Otherwise consider a controlled `DELETE` with a `WHERE` "
                        "clause and a backup taken first."
                    ),
                )
            )
        return out


class DropTableRule(Rule):
    rule_id = "data-loss/drop-table"
    title = "DROP TABLE"
    category = Category.DATA_LOSS
    severity = Severity.HIGH

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, node in script.by_node_type(exp.Drop):
            if (node.args.get("kind") or "").upper() != "TABLE":
                continue
            schema, table = _table_name(node)
            facts = ctx.fact_for(schema, table)
            schema_ctx = (
                f"{facts.schema}.{facts.name} currently has ~{facts.row_count:,} rows."
                if facts else None
            )
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    schema_context=schema_ctx,
                    message=(
                        f"`DROP TABLE {schema or 'dbo'}.{table}` is irreversible "
                        "and destroys all data in the table."
                    ),
                    suggestion=(
                        "Take a backup or rename the table (e.g. `_archive_<date>`) "
                        "first, and provide a matching down-script."
                    ),
                )
            )
        return out


class DropSchemaRule(Rule):
    rule_id = "data-loss/drop-schema"
    title = "DROP SCHEMA"
    category = Category.DATA_LOSS
    severity = Severity.HIGH

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, node in script.by_node_type(exp.Drop):
            if (node.args.get("kind") or "").upper() != "SCHEMA":
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`DROP SCHEMA` destroys all objects in the schema. "
                        "Verify nothing else depends on it before merge."
                    ),
                    suggestion="Drop individual objects explicitly, with a down-script.",
                )
            )
        return out
