"""Smoke tests for the .pre-commit-hooks.yaml manifest.

The pre-commit framework (https://pre-commit.com) validates manifest YAML
on install. These tests catch obvious mistakes (missing keys, typos in
``id``/``entry``, regex that doesn't match .sql) before a downstream user
pulls a broken hook.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

MANIFEST = Path(__file__).parent.parent / ".pre-commit-hooks.yaml"


def _load_manifest() -> list[dict]:
    with MANIFEST.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, list), "pre-commit-hooks.yaml must be a list"
    return data


def test_manifest_exists() -> None:
    assert MANIFEST.is_file(), f"{MANIFEST} should exist"


def test_manifest_is_valid_yaml_list_of_hooks() -> None:
    hooks = _load_manifest()
    assert hooks, "manifest must declare at least one hook"


def test_each_hook_has_required_keys() -> None:
    hooks = _load_manifest()
    required = {"id", "name", "entry", "language"}
    for hook in hooks:
        missing = required - hook.keys()
        assert not missing, f"hook {hook.get('id')} missing keys: {missing}"


def test_hook_ids_are_unique() -> None:
    hooks = _load_manifest()
    ids = [h["id"] for h in hooks]
    assert len(ids) == len(set(ids)), f"duplicate hook ids: {ids}"


@pytest.mark.parametrize(
    "filename",
    [
        "migrations/V001__add_column.sql",
        "db/V042__drop_legacy.SQL",
        "scripts/manual_fixup.sql",
    ],
)
def test_files_regex_matches_sql_paths(filename: str) -> None:
    hooks = _load_manifest()
    pattern = hooks[0]["files"]
    assert re.search(pattern, filename, flags=re.IGNORECASE), (
        f"{filename} should match {pattern}"
    )


def test_files_regex_rejects_non_sql() -> None:
    hooks = _load_manifest()
    pattern = hooks[0]["files"]
    assert not re.search(pattern, "src/migguard/core/parser.py")
    assert not re.search(pattern, "README.md")


def test_hook_entry_invokes_migguard_review() -> None:
    hooks = _load_manifest()
    for hook in hooks:
        assert "migguard.cli.main" in hook["entry"]
        assert "review" in hook["entry"]


def test_default_hook_disables_llm_and_fails_on_high() -> None:
    hooks = _load_manifest()
    default = next(h for h in hooks if h["id"] == "migguard-review")
    args = default.get("args", [])
    assert "--no-llm" in args
    assert "--fail-on" in args
    assert "high" in args
