"""Tests for the ``migguard explain <rule-id>`` subcommand."""

from __future__ import annotations

from click.testing import CliRunner

from migguard.cli.explanations import EXPLANATIONS, find_explanation, suggest_similar
from migguard.cli.main import cli


def test_explain_known_rule_prints_why_and_examples() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["explain", "data-loss/delete-without-where"])
    assert result.exit_code == 0
    assert "data-loss/delete-without-where" in result.output
    assert "Why this matters" in result.output
    assert "Risky pattern" in result.output
    assert "Safe pattern" in result.output
    assert "WHERE" in result.output


def test_explain_unknown_rule_exits_2_with_suggestion() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["explain", "data-loss/delete-without-wher"])
    assert result.exit_code == 2
    assert "No documented rule" in result.output
    assert "data-loss/delete-without-where" in result.output


def test_explain_completely_unknown_still_exits_2_cleanly() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["explain", "totally-made-up-rule-name"])
    assert result.exit_code == 2


def test_every_emitted_rule_is_documented() -> None:
    """Every rule_id we ship a check for must have an explanation.

    Keeps the docs honest: if you add a new rule and forget the docs entry,
    this test fails until you backfill it.
    """
    expected_rule_ids = {
        "data-loss/delete-without-where",
        "data-loss/update-without-where",
        "data-loss/truncate",
        "data-loss/drop-table",
        "data-loss/drop-schema",
        "locking/not-null-default-on-large-table",
        "locking/create-index-without-online",
        "idempotency/drop-without-if-exists",
        "idempotency/create-table-without-if-not-exists",
        "idempotency/alter-add-column-without-guard",
        "compatibility/merge-on-synapse",
        "permissions/grant-or-deny",
        "transaction/missing-tran-wrapper",
        "rollback/no-down-script",
        "sequencing/version-gap",
        "sequencing/duplicate-version",
        "lifetime/dropped-table-referenced",
        "lifetime/dropped-column-referenced",
        "mysql/alter-table-without-algorithm",
        "mysql/utf8-not-utf8mb4",
        "mysql/zero-date-default",
    }
    missing = expected_rule_ids - set(EXPLANATIONS.keys())
    assert not missing, f"Missing rule explanations: {missing}"


def test_suggest_similar_finds_close_matches() -> None:
    assert "data-loss/delete-without-where" in suggest_similar("delete-without-where")
    assert "lifetime/dropped-column-referenced" in suggest_similar("dropped-col")


def test_find_explanation_returns_none_for_nonsense() -> None:
    assert find_explanation("xxx/yyy") is None
