"""Tests for the ``--fail-on`` exit-code threshold and the ``--strict`` alias.

The threshold lets a CI pipeline pick the severity floor at which the build
turns red: HIGH only (old ``--strict`` behaviour), MEDIUM, LOW, or never
(report only). The CLI accepts either flag; ``--fail-on`` wins if both are
passed so a team can override their CI YAML without editing it.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from migguard.cli.main import _resolve_fail_threshold, cli
from migguard.core.models import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "migrations"


def test_resolve_fail_on_high() -> None:
    assert _resolve_fail_threshold(fail_on="high", strict=False) is Severity.HIGH


def test_resolve_fail_on_medium() -> None:
    assert _resolve_fail_threshold(fail_on="medium", strict=False) is Severity.MEDIUM


def test_resolve_fail_on_low() -> None:
    assert _resolve_fail_threshold(fail_on="low", strict=False) is Severity.LOW


def test_resolve_fail_on_never_means_no_gate() -> None:
    assert _resolve_fail_threshold(fail_on="never", strict=False) is None


def test_resolve_strict_alone_is_high() -> None:
    assert _resolve_fail_threshold(fail_on=None, strict=True) is Severity.HIGH


def test_resolve_fail_on_overrides_strict() -> None:
    """Both passed -> --fail-on wins so CI YAML can override an old flag."""
    assert (
        _resolve_fail_threshold(fail_on="medium", strict=True)
        is Severity.MEDIUM
    )
    assert _resolve_fail_threshold(fail_on="never", strict=True) is None


def test_no_flags_means_no_gate() -> None:
    assert _resolve_fail_threshold(fail_on=None, strict=False) is None


def test_high_finding_with_fail_on_high_exits_nonzero() -> None:
    runner = CliRunner()
    bad_file = FIXTURES / "02_delete_without_where.sql"
    result = runner.invoke(
        cli,
        ["review", str(bad_file), "--no-llm", "--fail-on", "high"],
    )
    assert result.exit_code == 1


def test_high_finding_with_fail_on_never_exits_zero() -> None:
    runner = CliRunner()
    bad_file = FIXTURES / "02_delete_without_where.sql"
    result = runner.invoke(
        cli,
        ["review", str(bad_file), "--no-llm", "--fail-on", "never"],
    )
    assert result.exit_code == 0


def test_strict_alias_still_works() -> None:
    runner = CliRunner()
    bad_file = FIXTURES / "02_delete_without_where.sql"
    result = runner.invoke(
        cli, ["review", str(bad_file), "--no-llm", "--strict"]
    )
    assert result.exit_code == 1
