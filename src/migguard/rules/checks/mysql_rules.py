"""MySQL-specific rules.

These fire only on ``--dialect mysql``. They cover the high-impact
MySQL/InnoDB risks that the universal rules don't catch:

* ``ALTER TABLE`` without an ``ALGORITHM=`` hint — MySQL/InnoDB may pick
  ``ALGORITHM=COPY`` (full table rewrite + metadata lock).
* ``CHARACTER SET utf8`` — the 3-byte legacy alias for ``utf8mb3``. Can't
  store 4-byte characters: most emoji, some CJK, mathematical script.
* ``'0000-00-00'`` / ``'0000-00-00 00:00:00'`` defaults — rejected by
  MySQL 5.7+ STRICT mode (the default in modern installs).

The universal rules (data loss, naming, rollback, sequencing, lifetime,
permissions, NOT NULL DEFAULT on large tables) still apply on top.
"""

from __future__ import annotations

import re

from sqlglot import exp

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext
from migguard.rules.checks.data_loss import _table_name

_MYSQL_ONLY = frozenset({"mysql"})


class AlterTableWithoutAlgorithmRule(Rule):
    """``ALTER TABLE ...`` without an explicit ``ALGORITHM=`` clause.

    MySQL/InnoDB picks an ALGORITHM automatically when none is specified.
    The default varies by operation type and server version — and some
    operations silently fall back to ``ALGORITHM=COPY``, which rewrites
    the whole table and holds a metadata lock for the duration.

    Being explicit (``INSTANT`` / ``INPLACE`` / ``COPY``) is the safe
    default. For very large tables, ``pt-online-schema-change`` or
    ``gh-ost`` should be preferred regardless.
    """

    rule_id = "mysql/alter-table-without-algorithm"
    title = "ALTER TABLE without ALGORITHM hint"
    category = Category.LOCKING
    severity = Severity.MEDIUM
    dialects = _MYSQL_ONLY

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for stmt, alter in script.by_node_type(exp.Alter):
            kind = (alter.args.get("kind") or "").upper() if alter.args.get("kind") else ""
            # ALTER VIEW / INDEX / DATABASE / etc. aren't this rule's problem.
            if kind and kind != "TABLE":
                continue
            if self._has_algorithm_hint(alter):
                continue

            schema, table = _table_name(alter)
            facts = ctx.fact_for(schema, table)
            severity = self.severity
            schema_context: str | None = None
            if facts and facts.is_huge:
                severity = Severity.HIGH
                schema_context = (
                    f"{facts.schema}.{facts.name} has ~{facts.row_count:,} rows; "
                    "an unintended ALGORITHM=COPY would rewrite the entire table "
                    "and hold a metadata lock for the duration."
                )
            elif facts and facts.is_large:
                severity = Severity.HIGH
                schema_context = (
                    f"{facts.schema}.{facts.name} has ~{facts.row_count:,} rows; "
                    "an explicit ALGORITHM hint is strongly recommended."
                )

            findings.append(
                self.make_finding(
                    stmt,
                    script,
                    severity=severity,
                    schema_context=schema_context,
                    message=(
                        "`ALTER TABLE` without an explicit `ALGORITHM=` clause. "
                        "MySQL/InnoDB will pick one for you - and for some "
                        "operations that means `ALGORITHM=COPY` (full table "
                        "rewrite + metadata lock)."
                    ),
                    suggestion=(
                        "Be explicit at the end of the change list. For most "
                        "modern operations:\n"
                        "  `ALTER TABLE app.customer ADD ..., ALGORITHM=INSTANT, LOCK=NONE;`\n"
                        "For very large tables prefer pt-online-schema-change "
                        "or gh-ost rather than a single ALTER."
                    ),
                )
            )
        return findings

    @staticmethod
    def _has_algorithm_hint(alter: exp.Expression) -> bool:
        """True if the ALTER carries an ALGORITHM= option in its AST.

        Reading the AST instead of regex-scanning ``raw_sql`` avoids false
        negatives from SQL comments that contain the literal text
        ``ALGORITHM=`` (and false positives from the same).
        """
        options = alter.args.get("options") or []
        for opt in options:
            if isinstance(opt, exp.AlgorithmProperty):
                return True
            cls_name = type(opt).__name__
            if cls_name.lower().startswith("algorithm"):
                return True
        return False


class Utf8NotUtf8mb4Rule(Rule):
    """``CHARACTER SET utf8`` (the 3-byte alias) instead of ``utf8mb4``.

    In MySQL, the name ``utf8`` is a historical alias for ``utf8mb3``,
    which only encodes 1- to 3-byte UTF-8. That excludes all emoji and
    several CJK characters (4 bytes). New tables and columns should use
    ``utf8mb4`` (and ideally collation ``utf8mb4_unicode_ci`` or
    ``utf8mb4_0900_ai_ci`` on MySQL 8.0+).
    """

    rule_id = "mysql/utf8-not-utf8mb4"
    title = "CHARACTER SET utf8 is the 3-byte alias"
    category = Category.COMPATIBILITY
    severity = Severity.MEDIUM
    dialects = _MYSQL_ONLY

    # \butf8\b distinguishes utf8 from utf8mb4 because the word boundary fails
    # before "m" (which is a word character).
    _RE = re.compile(
        r"\b(?:DEFAULT\s+)?(?:CHARACTER\s+SET|CHARSET)\s*=?\s*utf8\b",
        re.IGNORECASE,
    )

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        del ctx
        findings: list[Finding] = []
        for stmt in script.statements:
            if not self._RE.search(stmt.raw_sql):
                continue
            findings.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`CHARACTER SET utf8` in MySQL is the 3-byte legacy alias "
                        "for `utf8mb3`. It cannot store 4-byte characters: "
                        "most emoji, some CJK, and a fair amount of mathematical "
                        "and historical script. Use `utf8mb4` for new tables."
                    ),
                    suggestion=(
                        "`CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`\n"
                        "-- or on MySQL 8.0+: `utf8mb4_0900_ai_ci`"
                    ),
                )
            )
        return findings


class ZeroDateDefaultRule(Rule):
    """Zero-date literal (``'0000-00-00'``) used as a value or DEFAULT.

    MySQL 5.7+ rejects ``'0000-00-00'`` and ``'0000-00-00 00:00:00'`` in
    STRICT mode (``STRICT_TRANS_TABLES`` + ``NO_ZERO_DATE``), which is on
    by default in modern installs. Migrations that ship a zero-date will
    fail on any STRICT-mode server.
    """

    rule_id = "mysql/zero-date-default"
    title = "Zero-date default rejected by STRICT mode"
    category = Category.COMPATIBILITY
    severity = Severity.MEDIUM
    dialects = _MYSQL_ONLY

    _RE = re.compile(r"'0000-00-00(?:[ T][0-9: ]+)?'")

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        del ctx
        findings: list[Finding] = []
        for stmt in script.statements:
            if not self._RE.search(stmt.raw_sql):
                continue
            findings.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "Zero-date literal `'0000-00-00'` as a value or DEFAULT. "
                        "MySQL 5.7+ rejects this in STRICT mode (the default in "
                        "modern installs), so the migration will fail on any "
                        "STRICT-mode server."
                    ),
                    suggestion=(
                        "Use `NULL` (and declare the column nullable) for "
                        "'no value yet' semantics, or a real epoch like "
                        "`'1970-01-01 00:00:00'`."
                    ),
                )
            )
        return findings
