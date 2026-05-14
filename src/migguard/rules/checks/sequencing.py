"""Version sequencing rule.

Operates across the *set* of files in a review, not per-file. Detects:

- ``sequencing/duplicate-version``  (HIGH)  -- two files share the same version
- ``sequencing/version-gap``        (MEDIUM) -- e.g. V001, V002 then V004 (skips V003)

Supports common migration naming conventions:

- **Flyway-style**: ``V001__name.sql``, ``V2__name.sql``, ``V001.1__name.sql``
- **Plain numeric**:  ``001_name.sql``, ``42-name.sql``
- **Date-only**:      ``20260512_name.sql``  (sequencing not enforced - same-day skips ok)
- **Timestamp**:      ``20260512_120100_name.sql``  (sequencing enforced by ordering only,
  gaps not flagged - timestamps don't have to be contiguous)

We only flag gaps for Flyway / plain-numeric styles where engineers explicitly
pick a sequence number. Timestamps are treated as monotonic, not contiguous.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

from migguard.core.models import (
    Category,
    CodeLocation,
    Finding,
    Layer,
    Severity,
)
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext

_FLYWAY_RE = re.compile(r"^V(?P<n>\d+)(?:\.\d+)?__", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^(?P<n>\d{1,4})[_-]")
_TIMESTAMP_RE = re.compile(r"^(?P<n>\d{10,14})[_-]")


def _extract_version(name: str) -> tuple[str, int] | None:
    """Return (style, sequence_int) or None if the filename doesn't follow a known pattern."""
    m = _FLYWAY_RE.match(name)
    if m:
        return "flyway", int(m.group("n"))
    m = _NUMERIC_RE.match(name)
    if m:
        return "numeric", int(m.group("n"))
    m = _TIMESTAMP_RE.match(name)
    if m:
        return "timestamp", int(m.group("n"))
    return None


class VersionSequencingRule(Rule):
    """Single rule that emits two distinct ``rule_id`` values depending on what it finds."""

    rule_id = "sequencing/version-sequence"
    title = "Migration version sequencing"
    category = Category.ORDERING
    severity = Severity.MEDIUM

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        return []

    def check_collection(
        self, scripts: list[ParsedScript], ctx: RuleContext
    ) -> list[Finding]:
        if len(scripts) < 2:
            return []

        versions: list[tuple[str, int, ParsedScript]] = []
        for s in scripts:
            name = Path(s.file_path).name
            ver = _extract_version(name)
            if ver is None:
                continue
            style, seq = ver
            versions.append((style, seq, s))

        if not versions:
            return []

        out: list[Finding] = []
        out.extend(self._check_duplicates(versions))
        out.extend(self._check_gaps(versions))
        return out

    def _check_duplicates(
        self, versions: list[tuple[str, int, ParsedScript]]
    ) -> list[Finding]:
        seen: dict[tuple[str, int], list[ParsedScript]] = {}
        for style, seq, s in versions:
            seen.setdefault((style, seq), []).append(s)

        out: list[Finding] = []
        for (style, seq), scripts in seen.items():
            if len(scripts) < 2:
                continue
            file_list = ", ".join(Path(s.file_path).name for s in scripts)
            primary = scripts[0]
            out.append(
                Finding(
                    rule_id="sequencing/duplicate-version",
                    title="Duplicate migration version",
                    severity=Severity.HIGH,
                    category=Category.ORDERING,
                    layer=Layer.RULE,
                    location=CodeLocation(file=primary.file_path, line_start=1, line_end=1),
                    message=(
                        f"Multiple migrations claim version {seq} ({style} style): "
                        f"{file_list}. Whichever applies first wins; the others may be "
                        "silently skipped or fail."
                    ),
                    suggestion=(
                        "Bump one of the duplicates to the next available version "
                        "and update any cross-references."
                    ),
                )
            )
        return out

    def _check_gaps(
        self, versions: list[tuple[str, int, ParsedScript]]
    ) -> list[Finding]:
        by_style: dict[str, list[tuple[int, ParsedScript]]] = {}
        for style, seq, s in versions:
            if style == "timestamp":
                continue
            by_style.setdefault(style, []).append((seq, s))

        out: list[Finding] = []
        for style, items in by_style.items():
            unique = sorted({seq for seq, _ in items})
            if len(unique) < 2:
                continue
            for prev, nxt in pairwise(unique):
                if nxt - prev > 1:
                    missing = list(range(prev + 1, nxt))
                    target_script = next(s for seq, s in items if seq == nxt)
                    missing_str = ", ".join(f"V{m:03d}" if style == "flyway" else str(m) for m in missing)
                    out.append(
                        Finding(
                            rule_id="sequencing/version-gap",
                            title="Gap in migration version sequence",
                            severity=Severity.MEDIUM,
                            category=Category.ORDERING,
                            layer=Layer.RULE,
                            location=CodeLocation(
                                file=target_script.file_path,
                                line_start=1,
                                line_end=1,
                            ),
                            message=(
                                f"Version sequence jumps from {prev} to {nxt} - "
                                f"missing: {missing_str}. If those migrations exist "
                                "on main but aren't in this PR, your sequence will "
                                "diverge from production once merged."
                            ),
                            suggestion=(
                                "Verify the missing version(s) are intentional. If "
                                "they exist on main, pull them in. Otherwise renumber."
                            ),
                        )
                    )
        return out
