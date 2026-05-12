"""Rollback rules. We flag irreversible operations that don't have a matching
'down' script in the same PR / directory.

Heuristic: for a migration file ``20260512_foo.sql``, look for a sibling
named ``20260512_foo.down.sql`` or ``20260512_foo.rollback.sql``.
"""

from __future__ import annotations

import re
from pathlib import Path

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext


class IrreversibleWithoutDownScriptRule(Rule):
    rule_id = "rollback/no-down-script"
    title = "Irreversible op without rollback script"
    category = Category.ROLLBACK
    severity = Severity.MEDIUM

    _IRREVERSIBLE_RE = re.compile(
        r"\b(DROP\s+(TABLE|SCHEMA|COLUMN|VIEW|PROCEDURE|FUNCTION)|TRUNCATE\s+TABLE)\b",
        re.IGNORECASE,
    )

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        if not any(self._IRREVERSIBLE_RE.search(s.raw_sql) for s in script.statements):
            return []

        path = Path(script.file_path)
        candidates = [
            path.with_name(path.stem + ".down" + path.suffix),
            path.with_name(path.stem + ".rollback" + path.suffix),
        ]
        if ".up" in path.stem:
            candidates.append(
                path.with_name(path.stem.replace(".up", ".down") + path.suffix)
            )
        if any(c.exists() for c in candidates):
            return []

        out: list[Finding] = []
        for stmt in script.statements:
            if not self._IRREVERSIBLE_RE.search(stmt.raw_sql):
                continue
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    message=(
                        "This migration contains an irreversible operation, but "
                        "no matching `.down.sql` / `.rollback.sql` script was found "
                        "in the same directory."
                    ),
                    suggestion=(
                        f"Add a rollback script alongside this file, e.g. "
                        f"`{path.stem}.down{path.suffix}`. MigGuard can draft one "
                        "for you via the LLM rollback generator."
                    ),
                )
            )
        return out
