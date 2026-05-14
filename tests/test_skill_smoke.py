"""Smoke tests for the Cursor skill at ``skills/migguard-review/SKILL.md``.

We can't invoke Cursor's agent runtime from pytest, but we can verify the
contract that lets it discover and apply the skill:

* The file exists at the canonical path.
* It opens with a YAML frontmatter block containing ``name`` and ``description``.
* ``name`` follows Cursor's validation rules (lowercase, hyphens, <= 64 chars).
* ``description`` is non-empty, <= 1024 chars, and mentions trigger terms so
  the agent actually picks the skill up.
* The body references the real CLI command users will see.
* The skill stays under the 500-line budget recommended in the create-skill
  guide.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_PATH = Path(__file__).parent.parent / "skills" / "migguard-review" / "SKILL.md"


def _read_skill() -> str:
    if not SKILL_PATH.exists():
        pytest.skip(f"skill file not found at {SKILL_PATH}")
    return SKILL_PATH.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Tiny YAML frontmatter parser; only handles flat key: value pairs."""
    if not text.startswith("---\n"):
        raise ValueError("missing opening '---' frontmatter delimiter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("missing closing '---' frontmatter delimiter")
    block = text[4:end]
    out: dict[str, str] = {}
    current_key: str | None = None
    for raw_line in block.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.startswith(" ") and current_key is not None:
            out[current_key] = (out[current_key] + " " + raw_line.strip()).strip()
            continue
        if ":" in raw_line:
            key, _, value = raw_line.partition(":")
            current_key = key.strip()
            out[current_key] = value.strip().strip(">")
    return out


def test_skill_file_exists() -> None:
    assert SKILL_PATH.exists(), f"expected skill at {SKILL_PATH}"


def test_skill_frontmatter_has_required_fields() -> None:
    fm = _parse_frontmatter(_read_skill())
    assert "name" in fm, "frontmatter missing 'name'"
    assert "description" in fm, "frontmatter missing 'description'"


def test_skill_name_matches_cursor_rules() -> None:
    fm = _parse_frontmatter(_read_skill())
    name = fm["name"]
    assert len(name) <= 64
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", name), (
        f"name {name!r} must be lowercase letters, digits, hyphens only"
    )
    assert name == "migguard-review"


def test_skill_description_is_useful() -> None:
    fm = _parse_frontmatter(_read_skill())
    description = fm["description"]
    assert 0 < len(description) <= 1024

    lowered = description.lower()
    for trigger in ("migration", "sql", "review"):
        assert trigger in lowered, f"description should mention '{trigger}' as a trigger term"

    assert (
        "i can" not in lowered and "i help" not in lowered
    ), "description should be third-person per Cursor skill conventions"


def test_skill_body_references_real_cli_command() -> None:
    body = _read_skill()
    assert "python -m migguard.cli.main review" in body, (
        "skill must reference the real CLI invocation users actually run"
    )
    assert "--format markdown" in body, "skill should default to markdown output"
    assert "--no-llm" in body, (
        "skill should default to deterministic mode so it works without API keys"
    )


def test_skill_body_documents_safety_posture() -> None:
    body = _read_skill().lower()
    assert (
        "never connects to a database" in body
        or "no database connection" in body
        or "never connect" in body
    ), "skill must call out that MigGuard does not touch any DB"


def test_skill_stays_under_500_lines() -> None:
    lines = _read_skill().splitlines()
    assert len(lines) <= 500, (
        f"SKILL.md should stay under 500 lines per Cursor guidance "
        f"(currently {len(lines)})"
    )


def test_skill_links_are_one_level_deep() -> None:
    """Cursor recommends file references stay one level deep (no nested links)."""
    body = _read_skill()
    internal_links = re.findall(r"\]\(([^)#h][^)]*)\)", body)
    relative_links = [link for link in internal_links if not link.startswith(("http", "/", "#"))]
    for link in relative_links:
        parts = [p for p in link.split("/") if p and p != "."]
        assert len(parts) <= 2, f"relative link {link!r} is deeper than one level"
