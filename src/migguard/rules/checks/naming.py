"""Naming convention rules.

Reasonable defaults:
- snake_case for table / column / index names
- max identifier length 63 chars (the Postgres limit; SQL Server's is 128)
- reject common reserved keywords as identifiers

All checks operate on AST nodes when available so they're robust across
dialects. Names that come from a CREATE TABLE / ALTER TABLE / CREATE INDEX
are the primary targets.
"""

from __future__ import annotations

import re

from sqlglot import exp

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript, ParsedStatement
from migguard.rules.base import Rule, RuleContext

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_LEN = 63

_RESERVED = frozenset(
    {
        "user", "order", "table", "select", "where", "group", "having", "case",
        "when", "then", "else", "end", "join", "outer", "inner", "left", "right",
        "from", "into", "values", "default", "null", "true", "false", "primary",
        "key", "foreign", "references", "check", "constraint", "index", "view",
        "schema", "grant", "revoke", "deny", "begin", "commit", "rollback",
        "transaction", "procedure", "function", "trigger", "session", "current",
        "timestamp", "date", "time", "year", "month", "day",
    }
)


def _statement_for_node(script: ParsedScript, node: exp.Expression) -> ParsedStatement | None:
    """Find which parsed statement owns this AST node."""
    for stmt in script.statements:
        if stmt.ast is node:
            return stmt
        if stmt.ast and any(n is node for n in stmt.ast.walk()):
            return stmt
    return None


class NamingConventionRule(Rule):
    """Single rule, three sub-findings: non-snake-case, too-long, reserved-word.

    We emit different ``rule_id`` values for each so dashboards can chart them
    separately.
    """

    rule_id = "naming/conventions"
    title = "Identifier naming"
    category = Category.NAMING
    severity = Severity.LOW

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []

        identifiers: list[tuple[ParsedStatement, str, str]] = []  # (stmt, kind, name)

        for stmt, create in script.by_node_type(exp.Create):
            kind = (create.args.get("kind") or "").upper()
            for tbl in create.find_all(exp.Table):
                if tbl.name:
                    identifiers.append((stmt, kind or "OBJECT", tbl.name))
            for col_def in create.find_all(exp.ColumnDef):
                if col_def.name:
                    identifiers.append((stmt, "COLUMN", col_def.name))

        for stmt, alter in script.by_node_type(exp.Alter):
            for col_def in alter.find_all(exp.ColumnDef):
                if col_def.name:
                    identifiers.append((stmt, "COLUMN", col_def.name))

        seen: set[tuple[int, str, str]] = set()
        for stmt, kind, name in identifiers:
            key = (stmt.index, kind, name)
            if key in seen:
                continue
            seen.add(key)
            out.extend(self._check_one(script, stmt, name, kind))

        return out

    def _check_one(
        self,
        script: ParsedScript,
        stmt: ParsedStatement,
        name: str,
        kind: str,
    ) -> list[Finding]:
        out: list[Finding] = []
        if not _SNAKE_CASE_RE.match(name):
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    rule_id="naming/non-snake-case",
                    title="Non-snake_case identifier",
                    severity=Severity.LOW,
                    message=(
                        f"{kind.capitalize()} `{name}` is not snake_case. "
                        "Mixed casing causes friction across dialects (some "
                        "fold case, some don't)."
                    ),
                    suggestion=f"Rename to `{self._suggest_snake_case(name)}`.",
                )
            )

        if len(name) > _MAX_LEN:
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    rule_id="naming/too-long",
                    title="Identifier too long",
                    severity=Severity.LOW,
                    message=(
                        f"{kind.capitalize()} `{name}` is {len(name)} chars (>{_MAX_LEN}). "
                        "Postgres truncates at 63 chars by default; longer names risk "
                        "collisions when porting."
                    ),
                    suggestion="Shorten the name; abbreviate non-key components.",
                )
            )

        if name.lower() in _RESERVED:
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    rule_id="naming/reserved-word",
                    title="Reserved word used as identifier",
                    severity=Severity.MEDIUM,
                    message=(
                        f"`{name}` is a reserved word in standard SQL. Using it as "
                        "an identifier forces every query to quote it and is a frequent "
                        "source of cross-dialect bugs."
                    ),
                    suggestion=f"Rename to something like `{name}_id` or `{name}_name`.",
                )
            )

        return out

    @staticmethod
    def _suggest_snake_case(name: str) -> str:
        """Convert CamelCase / mixed to snake_case."""
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
        return s2.lower().replace("__", "_").strip("_")
