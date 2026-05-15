"""Index lifecycle rules.

Catches ``DROP INDEX`` statements that are not accompanied by a matching
``CREATE INDEX`` for the same index name in the same migration. Dropping an
index without recreating it changes query plans permanently — every query
that previously used the dropped index will now scan. That's rarely the
intended outcome of a schema migration; it's almost always an oversight.

We deliberately use regex against the raw SQL rather than the AST: sqlglot
v23+ frequently fails to parse ``DROP INDEX X ON Y`` on the T-SQL dialect
(see ``test_drop_index_not_parseable_but_visible_as_raw_sql``).
"""

from __future__ import annotations

import re

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext

# DROP INDEX [IF EXISTS] [schema.]index [ON [schema.]table]
_DROP_INDEX_RE = re.compile(
    r"\bDROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?"
    r"(?P<idx>(?:\[?[A-Za-z_][\w]*\]?\.)?\[?[A-Za-z_][\w]*\]?)"
    r"(?:\s+ON\s+(?P<table>(?:\[?[A-Za-z_][\w]*\]?\.)?\[?[A-Za-z_][\w]*\]?))?",
    re.IGNORECASE,
)

# CREATE [UNIQUE] [CLUSTERED|NONCLUSTERED] INDEX [IF NOT EXISTS] [schema.]name
_CREATE_INDEX_RE = re.compile(
    r"\bCREATE\s+"
    r"(?:UNIQUE\s+)?"
    r"(?:CLUSTERED\s+|NONCLUSTERED\s+)?"
    r"INDEX\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<idx>(?:\[?[A-Za-z_][\w]*\]?\.)?\[?[A-Za-z_][\w]*\]?)",
    re.IGNORECASE,
)


def _norm_ident(ident: str) -> str:
    """Lowercase and strip T-SQL ``[...]`` quoting from an identifier."""
    return ident.lower().replace("[", "").replace("]", "")


class IndexDropWithoutRecreateRule(Rule):
    """Flag ``DROP INDEX`` without a matching ``CREATE INDEX`` of the same
    name in the same migration script."""

    rule_id = "rollback/index-drop-without-recreate"
    title = "DROP INDEX without recreation"
    category = Category.ROLLBACK
    severity = Severity.MEDIUM

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        created: set[str] = set()
        for stmt in script.statements:
            for m in _CREATE_INDEX_RE.finditer(stmt.raw_sql):
                idx_qual = _norm_ident(m.group("idx"))
                created.add(idx_qual.split(".")[-1])

        out: list[Finding] = []
        seen: set[tuple[int, str]] = set()
        for stmt in script.statements:
            for m in _DROP_INDEX_RE.finditer(stmt.raw_sql):
                idx_qual = _norm_ident(m.group("idx"))
                idx_name = idx_qual.split(".")[-1]
                if idx_name in created:
                    continue
                dedup = (stmt.index, idx_name)
                if dedup in seen:
                    continue
                seen.add(dedup)
                table = m.group("table")
                target = f" on `{_norm_ident(table)}`" if table else ""
                out.append(
                    self.make_finding(
                        stmt,
                        script,
                        message=(
                            f"`DROP INDEX {idx_name}`{target} has no matching "
                            "`CREATE INDEX` in this migration. Dropping an index "
                            "without recreating it changes query plans permanently "
                            "— queries that previously used the index will now scan."
                        ),
                        suggestion=(
                            f"Add a matching `CREATE INDEX {idx_name} ON ...` "
                            "statement in this same migration, or provide a "
                            "companion `.down.sql` script that recreates the index "
                            "with the same definition. If the index is genuinely "
                            "obsolete, add a code-review comment explaining why."
                        ),
                    )
                )
        return out
