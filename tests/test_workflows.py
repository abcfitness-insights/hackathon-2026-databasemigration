"""Smoke tests for the GitHub Actions workflows.

GitHub Actions silently ignores invalid workflow YAML — broken workflows just
don't run, which is a terrible failure mode for a hackathon repo. These tests
parse both workflow files and pin down the contract that matters:

* Both files are valid YAML and define the expected jobs.
* The MigGuard review workflow runs on ``pull_request``, has
  ``pull-requests: write`` permission, invokes the real CLI with
  ``--strict --no-llm --format markdown``, and posts a sticky PR comment.
* The Tests workflow runs ``pytest`` on push + PR against master/main.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"
MIGGUARD_WORKFLOW = WORKFLOWS_DIR / "migguard.yml"
TESTS_WORKFLOW = WORKFLOWS_DIR / "tests.yml"


def _load(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f"workflow {path} not present")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _get_trigger(wf: dict) -> dict:
    """Return the workflow's ``on:`` section.

    PyYAML deserializes the bare YAML key ``on`` to the Python boolean ``True``
    because ``on`` is a YAML 1.1 boolean literal. Handle both shapes so the
    test is robust to either parser version.
    """
    return wf.get("on") or wf.get(True) or {}


def test_workflow_files_exist() -> None:
    assert MIGGUARD_WORKFLOW.exists(), f"missing {MIGGUARD_WORKFLOW}"
    assert TESTS_WORKFLOW.exists(), f"missing {TESTS_WORKFLOW}"


def test_migguard_workflow_is_valid_yaml_with_pr_trigger() -> None:
    wf = _load(MIGGUARD_WORKFLOW)
    assert wf["name"] == "MigGuard review"
    triggers = _get_trigger(wf)
    assert "pull_request" in triggers, "MigGuard workflow must trigger on pull_request"
    pr_cfg = triggers["pull_request"]
    branches = pr_cfg.get("branches", [])
    assert "master" in branches or "main" in branches


def test_migguard_workflow_has_write_permissions_for_comments() -> None:
    wf = _load(MIGGUARD_WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("pull-requests") == "write", (
        "workflow must request pull-requests: write to post review comments"
    )
    assert perms.get("contents") == "read"


def test_migguard_workflow_runs_the_real_cli() -> None:
    body = MIGGUARD_WORKFLOW.read_text(encoding="utf-8")
    assert "python -m migguard.cli.main review" in body
    assert "--format markdown" in body
    assert "--no-llm" in body, "CI must default to deterministic mode (no API keys required)"
    assert "--strict" in body, "CI must block merge on HIGH-severity findings"


def test_migguard_workflow_posts_sticky_comment() -> None:
    body = MIGGUARD_WORKFLOW.read_text(encoding="utf-8")
    assert "actions/github-script@v7" in body
    assert "migguard-review-comment" in body, (
        "comment must include a hidden marker so it can be found and updated on push"
    )
    assert "updateComment" in body and "createComment" in body, (
        "workflow must support both creating and updating the sticky comment"
    )


def test_migguard_workflow_uploads_report_artifact() -> None:
    wf = _load(MIGGUARD_WORKFLOW)
    steps = wf["jobs"]["review"]["steps"]
    artifact_step = next(
        (s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")),
        None,
    )
    assert artifact_step is not None, "report must be uploaded as a workflow artifact"
    assert artifact_step["with"]["name"] == "migguard-report"


def test_tests_workflow_runs_pytest_on_pr_and_push() -> None:
    wf = _load(TESTS_WORKFLOW)
    assert wf["name"] == "Tests"
    triggers = _get_trigger(wf)
    assert "push" in triggers and "pull_request" in triggers

    body = TESTS_WORKFLOW.read_text(encoding="utf-8")
    assert "pytest" in body
    assert "actions/setup-python@v5" in body
    assert 'python-version: "3.11"' in body or "python-version: '3.11'" in body


def test_workflows_install_migguard_in_editable_mode() -> None:
    """Both workflows must install the local package so they test the real code."""
    for wf_path in (MIGGUARD_WORKFLOW, TESTS_WORKFLOW):
        body = wf_path.read_text(encoding="utf-8")
        assert 'pip install -e ".[dev]"' in body, (
            f"{wf_path.name} must install migguard in editable mode with dev extras"
        )
