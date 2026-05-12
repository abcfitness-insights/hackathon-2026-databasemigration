"""Locking / downtime rules. These catch the "this migration will lock prod
for 10 minutes" failure modes."""

from __future__ import annotations

import re

from sqlglot import exp

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext
from migguard.rules.checks.data_loss import _table_name

_TSQL_ONLY = frozenset({"tsql"})


class NotNullDefaultOnLargeTableRule(Rule):
    """ALTER TABLE ... ADD col TYPE NOT NULL DEFAULT 'x'

    On Synapse and on SQL Server < 2012 SP1, this rewrites the entire table
    and holds a SCH-M lock. On a 12M-row table that can easily be 10 minutes.
    """

    rule_id = "locking/not-null-default-on-large-table"
    title = "NOT NULL + DEFAULT column add"
    category = Category.LOCKING
    severity = Severity.MEDIUM

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, alter in script.by_node_type(exp.Alter):
            for col_def in alter.find_all(exp.ColumnDef):
                constraints = list(col_def.find_all(exp.ColumnConstraint))
                kinds = {type(c.args.get("kind")).__name__ for c in constraints if c.args.get("kind")}
                has_not_null = "NotNullColumnConstraint" in kinds
                has_default = "DefaultColumnConstraint" in kinds
                if not (has_not_null and has_default):
                    continue

                schema, table = _table_name(alter)
                facts = ctx.fact_for(schema, table)
                sev = Severity.MEDIUM
                schema_ctx = None
                downtime = ""
                if facts:
                    schema_ctx = (
                        f"{facts.schema}.{facts.name} has ~{facts.row_count:,} rows "
                        f"and {facts.index_count} index(es)."
                    )
                    if facts.is_huge:
                        sev = Severity.HIGH
                        downtime = " Estimated SCH-M lock window: 8-15 min."
                    elif facts.is_large:
                        sev = Severity.HIGH
                        downtime = " Estimated SCH-M lock window: 2-6 min."

                col_name = col_def.name or "<col>"
                out.append(
                    self.make_finding(
                        stmt,
                        script,
                        severity=sev,
                        schema_context=schema_ctx,
                        message=(
                            f"Adding `{col_name}` as `NOT NULL DEFAULT` on "
                            f"`{schema or 'dbo'}.{table}` will rewrite the whole "
                            f"table on Synapse / older SQL Server.{downtime}"
                        ),
                        suggestion=(
                            "Three-step pattern instead:\n"
                            "  1. `ALTER TABLE ... ADD <col> <type> NULL;`\n"
                            "  2. Backfill in batches (`UPDATE ... TOP (N)`).\n"
                            "  3. `ALTER TABLE ... ALTER COLUMN <col> <type> NOT NULL;`"
                        ),
                    )
                )
        return out


class CreateIndexWithoutOnlineRule(Rule):
    """``CREATE INDEX`` without ``WITH (ONLINE = ON)`` blocks writers on the
    base table for the duration of the build. On large tables this is downtime.

    Note: ``ONLINE = ON`` is Enterprise Edition only on SQL Server; on Synapse
    it's not supported. We emit MEDIUM and let reviewers decide.
    """

    rule_id = "locking/create-index-without-online"
    title = "CREATE INDEX without ONLINE = ON"
    category = Category.LOCKING
    severity = Severity.MEDIUM
    dialects = _TSQL_ONLY  # ONLINE = ON is SQL Server syntax

    _ONLINE_RE = re.compile(r"ONLINE\s*=\s*ON", re.IGNORECASE)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt, node in script.by_node_type(exp.Create):
            if (node.args.get("kind") or "").upper() != "INDEX":
                continue
            if self._ONLINE_RE.search(stmt.raw_sql):
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "`CREATE INDEX` without `WITH (ONLINE = ON)` blocks "
                        "writes on the base table for the build duration."
                    ),
                    suggestion=(
                        "Add `WITH (ONLINE = ON)` if running on SQL Server "
                        "Enterprise. On Synapse, schedule during a maintenance "
                        "window or use a CTAS pattern."
                    ),
                )
            )
        return out
