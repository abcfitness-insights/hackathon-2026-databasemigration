"""Idempotency rules. A migration that fails on rerun blocks every redeploy
for the rest of the day. These are MEDIUM by default — annoying, not catastrophic."""

from __future__ import annotations

import re

from sqlglot import exp

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext

_TSQL_ONLY = frozenset({"tsql"})


class DropWithoutIfExistsRule(Rule):
    rule_id = "idempotency/drop-without-if-exists"
    title = "DROP without IF EXISTS"
    category = Category.IDEMPOTENCY
    severity = Severity.MEDIUM

    _DROP_RE = re.compile(
        r"\bDROP\s+(TABLE|INDEX|VIEW|PROCEDURE|FUNCTION|TRIGGER|SCHEMA|COLUMN)\b",
        re.IGNORECASE,
    )
    _IF_EXISTS_RE = re.compile(r"\bIF\s+EXISTS\b", re.IGNORECASE)
    _OBJECT_ID_RE = re.compile(r"\bOBJECT_ID\s*\(", re.IGNORECASE)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            if not self._DROP_RE.search(stmt.raw_sql):
                continue
            if self._IF_EXISTS_RE.search(stmt.raw_sql):
                continue
            if self._OBJECT_ID_RE.search(stmt.raw_sql):
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`DROP` without `IF EXISTS` (or an `OBJECT_ID(...) IS NOT NULL` "
                        "guard) will fail on rerun, blocking the migration pipeline."
                    ),
                    suggestion=(
                        "Use `DROP TABLE IF EXISTS schema.table;` or wrap in an "
                        "`IF OBJECT_ID(...) IS NOT NULL` guard."
                    ),
                )
            )
        return out


class CreateTableWithoutIfNotExistsRule(Rule):
    rule_id = "idempotency/create-table-without-if-not-exists"
    title = "CREATE TABLE without IF NOT EXISTS"
    category = Category.IDEMPOTENCY
    severity = Severity.LOW

    _IF_NOT_EXISTS_RE = re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.IGNORECASE)
    _OBJECT_ID_NULL_RE = re.compile(r"OBJECT_ID\s*\([^)]+\)\s*IS\s*NULL", re.IGNORECASE)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, node in script.by_node_type(exp.Create):
            if (node.args.get("kind") or "").upper() != "TABLE":
                continue
            if self._IF_NOT_EXISTS_RE.search(stmt.raw_sql):
                continue
            if self._OBJECT_ID_NULL_RE.search(stmt.raw_sql):
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`CREATE TABLE` without an existence guard will fail "
                        "on rerun if the table already exists."
                    ),
                    suggestion=(
                        "Wrap in `IF OBJECT_ID(N'schema.table', 'U') IS NULL BEGIN ... END` "
                        "or use `CREATE TABLE IF NOT EXISTS` on engines that support it."
                    ),
                )
            )
        return out


class AlterTableWithoutGuardRule(Rule):
    """ALTER TABLE ... ADD col without checking sys.columns first will fail on
    rerun. We only flag column-add ALTERs (the most common rerun pitfall)."""

    rule_id = "idempotency/alter-add-column-without-guard"
    title = "ALTER TABLE ADD column without existence guard"
    category = Category.IDEMPOTENCY
    severity = Severity.LOW
    dialects = _TSQL_ONLY  # sys.columns / COLUMNPROPERTY are T-SQL

    _SYS_COLUMNS_RE = re.compile(r"sys\.columns", re.IGNORECASE)
    _COL_PROP_RE = re.compile(r"COLUMNPROPERTY\s*\(", re.IGNORECASE)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, alter in script.by_node_type(exp.Alter):
            adds_column = any(alter.find_all(exp.ColumnDef))
            if not adds_column:
                continue
            if self._SYS_COLUMNS_RE.search(stmt.raw_sql):
                continue
            if self._COL_PROP_RE.search(stmt.raw_sql):
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`ALTER TABLE ... ADD <col>` will fail on rerun if the "
                        "column already exists."
                    ),
                    suggestion=(
                        "Wrap with `IF NOT EXISTS (SELECT 1 FROM sys.columns "
                        "WHERE object_id = OBJECT_ID(...) AND name = N'...')`."
                    ),
                )
            )
        return out
