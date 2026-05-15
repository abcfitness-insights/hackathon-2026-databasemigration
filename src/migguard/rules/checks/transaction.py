"""Transaction rules. Write operations outside an explicit transaction can't
be rolled back atomically if anything after them fails."""

from __future__ import annotations

import re

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules._sql_text import strip_sql_noise
from migguard.rules.base import Rule, RuleContext


class WriteWithoutTransactionRule(Rule):
    rule_id = "transaction/missing-tran-wrapper"
    title = "Write operation outside explicit transaction"
    category = Category.TRANSACTION
    severity = Severity.LOW
    dialects = frozenset({"tsql"})  # BEGIN TRAN / TRY-CATCH are T-SQL

    _WRITE_RE = re.compile(
        r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE\s+TABLE)\b",
        re.IGNORECASE,
    )
    _BEGIN_TRAN_RE = re.compile(r"\bBEGIN\s+(TRY\b.*\bBEGIN\s+)?TRAN(SACTION)?\b", re.IGNORECASE | re.DOTALL)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        # Scrub the whole file once for the BEGIN TRAN guard check (so a
        # `-- This batch is wrapped in BEGIN TRAN` header comment doesn't
        # fool the rule) and again per-statement for the write detection
        # (so `'INSERT into ...'` inside a string literal isn't reported as
        # an unwrapped write).
        if self._BEGIN_TRAN_RE.search(strip_sql_noise(script.raw_text)):
            return []
        for stmt in script.statements:
            if not self._WRITE_RE.search(strip_sql_noise(stmt.raw_sql)):
                continue
            return [
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "This migration performs write operations but is not "
                        "wrapped in an explicit `BEGIN TRANSACTION ... COMMIT` "
                        "with `TRY/CATCH`. If a later statement fails, earlier "
                        "writes cannot be rolled back atomically."
                    ),
                    suggestion=(
                        "Wrap the whole batch:\n"
                        "  BEGIN TRY\n"
                        "    BEGIN TRANSACTION;\n"
                        "    -- statements\n"
                        "    COMMIT TRANSACTION;\n"
                        "  END TRY\n"
                        "  BEGIN CATCH\n"
                        "    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;\n"
                        "    THROW;\n"
                        "  END CATCH;"
                    ),
                )
            ]
        return []
