"""Compatibility rules — constructs that work on SQL Server but break on
Synapse, or vice versa."""

from __future__ import annotations

import re

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules._sql_text import strip_sql_noise
from migguard.rules.base import Rule, RuleContext


class MergeOnSynapseRule(Rule):
    """``MERGE`` is supported on Synapse only with significant restrictions
    (no `OUTPUT`, only on round-robin / replicated, etc.). It's a common
    porting pitfall."""

    rule_id = "compatibility/merge-on-synapse"
    title = "MERGE on Synapse"
    category = Category.COMPATIBILITY
    severity = Severity.MEDIUM
    dialects = frozenset({"tsql"})  # Synapse caveat is T-SQL specific

    _MERGE_RE = re.compile(r"\bMERGE\s+INTO\b", re.IGNORECASE)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            # Scrub comments + string literals so `-- using MERGE INTO` or
            # `('Considered MERGE INTO but used DELETE+INSERT')` doesn't
            # fire the Synapse-compat warning.
            if not self._MERGE_RE.search(strip_sql_noise(stmt.raw_sql)):
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`MERGE` has significant restrictions on Synapse "
                        "(no `OUTPUT`, only on round-robin / replicated tables, "
                        "and historically discouraged due to correctness bugs)."
                    ),
                    suggestion=(
                        "Prefer a `DELETE` + `INSERT` pattern within a transaction. "
                        "If the target is Azure SQL DB / SQL Server, MERGE is fine; "
                        "flag this comment as resolved."
                    ),
                )
            )
        return out
