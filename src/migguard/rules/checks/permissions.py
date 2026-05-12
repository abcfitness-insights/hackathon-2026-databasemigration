"""Permission rules. GRANT/REVOKE/DENY in a migration should always get a
security-review pair of eyes — not because they're necessarily wrong, but
because they bypass normal access-review processes if merged silently."""

from __future__ import annotations

import re

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext


class GrantOrDenyRule(Rule):
    rule_id = "permissions/grant-or-deny"
    title = "GRANT / REVOKE / DENY in migration"
    category = Category.PERMISSIONS
    severity = Severity.MEDIUM

    _RE = re.compile(r"\b(GRANT|REVOKE|DENY)\b", re.IGNORECASE)

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            m = self._RE.search(stmt.raw_sql)
            if not m:
                continue
            verb = m.group(1).upper()
            sev = Severity.HIGH if verb in ("GRANT", "DENY") else Severity.MEDIUM
            out.append(
                self.make_finding(
                    stmt,
                    script,
                    severity=sev,
                    message=(
                        f"`{verb}` statement in a data migration changes "
                        "permissions outside the normal access-review process."
                    ),
                    suggestion=(
                        "Confirm this permission change has been reviewed by "
                        "InfoSec / DBAs and that the principal is least-privilege."
                    ),
                )
            )
        return out
