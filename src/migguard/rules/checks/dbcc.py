"""DBCC-command rule.

``DBCC`` is the T-SQL family of diagnostic / maintenance commands
(``CHECKDB``, ``SHRINKFILE``, ``DROPCLEANBUFFERS``, ...) that should rarely
appear inside a forward-only schema migration. They're meant to be run
interactively by a DBA, not committed to source control.

A few variants are actively dangerous in production and get escalated to
HIGH severity:

- ``DBCC SHRINKDATABASE`` / ``DBCC SHRINKFILE`` -- causes severe index
  fragmentation; Microsoft specifically recommends against it.
- ``DBCC DROPCLEANBUFFERS``                       -- flushes the buffer cache;
  every subsequent query reads from disk.
- ``DBCC CHECKDB ('db', REPAIR_ALLOW_DATA_LOSS)`` -- can permanently delete
  data to repair corruption.

Other DBCC commands (``CHECKDB`` without ``REPAIR_ALLOW_DATA_LOSS``,
``OPENTRAN``, ``USEROPTIONS``, ``SQLPERF``, ...) are flagged MEDIUM as
"shouldn't be in a migration".
"""

from __future__ import annotations

import re

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext

_DBCC_RE = re.compile(r"\bDBCC\s+(?P<cmd>[A-Za-z_]+)", re.IGNORECASE)
_REPAIR_ALLOW_LOSS_RE = re.compile(r"\bREPAIR_ALLOW_DATA_LOSS\b", re.IGNORECASE)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_DANGEROUS_SHRINK = {"SHRINKDATABASE", "SHRINKFILE"}
_DANGEROUS_OTHER = {"DROPCLEANBUFFERS"}


def _strip_comments(sql: str) -> str:
    """Remove SQL comments before pattern-matching. The parser keeps leading
    comments attached to the next statement's ``raw_sql``, so a generic
    keyword like ``DBCC`` can appear inside a comment block that documents
    a fixture — we don't want that to fire the rule."""
    no_block = _BLOCK_COMMENT_RE.sub(" ", sql)
    return _LINE_COMMENT_RE.sub("", no_block)


class DbccCommandRule(Rule):
    """Flag DBCC commands inside migrations; escalate dangerous variants."""

    rule_id = "compatibility/dbcc-command"
    title = "DBCC command in migration"
    category = Category.OTHER
    severity = Severity.MEDIUM
    dialects = frozenset({"tsql"})

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            sql = _strip_comments(stmt.raw_sql)
            m = _DBCC_RE.search(sql)
            if not m:
                continue
            cmd = m.group("cmd").upper()
            has_repair_loss = bool(_REPAIR_ALLOW_LOSS_RE.search(sql))

            if has_repair_loss:
                sev = Severity.HIGH
                msg = (
                    f"`DBCC {cmd}` with `REPAIR_ALLOW_DATA_LOSS` can permanently "
                    "delete rows to make a corrupt database usable. This must "
                    "never run as part of a routine migration."
                )
            elif cmd in _DANGEROUS_SHRINK:
                sev = Severity.HIGH
                msg = (
                    f"`DBCC {cmd}` shrinks the database file. This causes severe "
                    "index fragmentation and blocks queries. Microsoft "
                    "specifically recommends against it in production."
                )
            elif cmd in _DANGEROUS_OTHER:
                sev = Severity.HIGH
                msg = (
                    f"`DBCC {cmd}` flushes the entire buffer cache. Safe for dev "
                    "or performance testing only; in production every subsequent "
                    "query reads from disk."
                )
            else:
                sev = Severity.MEDIUM
                msg = (
                    f"`DBCC {cmd}` is a diagnostic / maintenance command. These "
                    "rarely belong in a forward-only migration — they're "
                    "typically run interactively by a DBA, not committed to "
                    "source control."
                )

            out.append(
                self.make_finding(
                    stmt,
                    script,
                    severity=sev,
                    message=msg,
                    suggestion=(
                        "Move DBCC commands out of the migration. If routine "
                        "maintenance is required, schedule it through your "
                        "operations runbook, not the schema-change pipeline."
                    ),
                )
            )
        return out
