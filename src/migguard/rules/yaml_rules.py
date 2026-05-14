"""Loader for user-defined regex rule packs.

Teams can extend MigGuard without writing Python by dropping a YAML file
into their repo and pointing the CLI at it with
``migguard review --rules-config rules.yaml``.

The schema is intentionally small — anything truly complex should be a
real Python rule. Regex covers the 80%: 'never reference table X', 'all
tables must start with prefix Y', 'no NOLOCK hints in this repo'.

Example::

    # .migguard/rules.yaml
    version: 1
    rules:
      - id: company/no-nolock
        title: NOLOCK hint forbidden in this repo
        severity: medium
        category: locking
        pattern: '(?i)\\bWITH\\s*\\(\\s*NOLOCK\\s*\\)'
        message: NOLOCK reads dirty data; use snapshot isolation instead.
        dialects: [tsql]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import ALL_DIALECTS, Rule, RuleContext

_VALID_SEVERITIES = {s.value: s for s in Severity}
_VALID_CATEGORIES = {c.value: c for c in Category}


class YamlRuleConfigError(ValueError):
    """Raised when a user's rules YAML can't be loaded into rules."""


@dataclass
class RegexRule(Rule):
    """A rule that fires when a regex matches anywhere in a statement.

    Operates on raw SQL text (per statement) rather than the AST so users
    don't have to learn sqlglot to write a rule. Compiled regex is cached
    on the instance.
    """

    _pattern: re.Pattern[str] = re.compile("")
    _message: str = ""
    _suggestion: str | None = None

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        del ctx
        findings: list[Finding] = []
        for stmt in script.statements:
            if not self._pattern.search(stmt.raw_sql):
                continue
            findings.append(
                self.make_finding(
                    stmt,
                    script,
                    self._message,
                    suggestion=self._suggestion,
                )
            )
        return findings


def _build_rule(entry: dict, source: Path) -> RegexRule:
    rule_id = entry.get("id")
    if not rule_id or not isinstance(rule_id, str):
        raise YamlRuleConfigError(
            f"{source}: every rule needs a string 'id' (got {rule_id!r})"
        )

    title = entry.get("title") or rule_id
    pattern_src = entry.get("pattern")
    if not pattern_src or not isinstance(pattern_src, str):
        raise YamlRuleConfigError(
            f"{source}: rule '{rule_id}' is missing a 'pattern' regex"
        )
    try:
        pattern = re.compile(pattern_src)
    except re.error as exc:
        raise YamlRuleConfigError(
            f"{source}: rule '{rule_id}' has invalid regex: {exc}"
        ) from exc

    sev_name = (entry.get("severity") or "medium").lower()
    if sev_name not in _VALID_SEVERITIES:
        raise YamlRuleConfigError(
            f"{source}: rule '{rule_id}' has unknown severity '{sev_name}'. "
            f"Valid: {sorted(_VALID_SEVERITIES)}"
        )
    severity = _VALID_SEVERITIES[sev_name]

    cat_name = (entry.get("category") or "naming").lower()
    if cat_name not in _VALID_CATEGORIES:
        raise YamlRuleConfigError(
            f"{source}: rule '{rule_id}' has unknown category '{cat_name}'. "
            f"Valid: {sorted(_VALID_CATEGORIES)}"
        )
    category = _VALID_CATEGORIES[cat_name]

    dialects_raw = entry.get("dialects")
    if dialects_raw is None:
        dialects = ALL_DIALECTS
    else:
        if not isinstance(dialects_raw, list):
            raise YamlRuleConfigError(
                f"{source}: rule '{rule_id}' dialects must be a list"
            )
        dialects = frozenset(d.lower() for d in dialects_raw)
        unknown = dialects - ALL_DIALECTS
        if unknown:
            raise YamlRuleConfigError(
                f"{source}: rule '{rule_id}' references unknown dialects: {sorted(unknown)}"
            )

    message = entry.get("message") or f"Pattern {rule_id} matched in this statement."
    suggestion = entry.get("suggestion")

    rule = RegexRule()
    rule.rule_id = rule_id
    rule.title = title
    rule.category = category
    rule.severity = severity
    rule.dialects = dialects
    rule._pattern = pattern
    rule._message = message
    rule._suggestion = suggestion
    return rule


def load_yaml_rules(path: str | Path) -> list[RegexRule]:
    """Load and validate every rule from ``path``.

    Raises :class:`YamlRuleConfigError` with a precise per-rule message if
    the file is malformed. We fail loud rather than silently skipping bad
    rules — silent skips lead to teams believing they have coverage they
    don't.
    """
    p = Path(path)
    if not p.is_file():
        raise YamlRuleConfigError(f"rules config not found: {p}")

    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        raise YamlRuleConfigError(
            f"{p}: top-level YAML must be a mapping with 'rules' key"
        )

    rules_raw = data.get("rules")
    if not isinstance(rules_raw, list):
        raise YamlRuleConfigError(f"{p}: 'rules' must be a list")

    rules: list[RegexRule] = []
    seen_ids: set[str] = set()
    for entry in rules_raw:
        if not isinstance(entry, dict):
            raise YamlRuleConfigError(f"{p}: each rule entry must be a mapping")
        rule = _build_rule(entry, p)
        if rule.rule_id in seen_ids:
            raise YamlRuleConfigError(
                f"{p}: duplicate rule id '{rule.rule_id}'"
            )
        seen_ids.add(rule.rule_id)
        rules.append(rule)

    return rules
