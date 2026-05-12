"""Bot tests. We mock the provider so the test never makes real HTTP calls.

This proves the routing logic: webhook -> detect provider -> parse event ->
list changed files -> review -> post comment + set status.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from migguard.bot.app import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_endpoint_lists_capabilities() -> None:
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "GitHub" in r.json()["supports"]
    assert "Azure DevOps" in r.json()["supports"]


def test_webhook_ignores_non_pr_github_events() -> None:
    client = TestClient(app)
    r = client.post(
        "/webhook",
        json={"action": "opened"},
        headers={"X-GitHub-Event": "push"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_webhook_ignores_pr_without_migration_files() -> None:
    client = TestClient(app)

    fake_changed: list[str] = ["README.md", "src/main.py"]

    with patch("migguard.bot.providers.GitHubProvider.list_changed_files",
               new=AsyncMock(return_value=fake_changed)):
        body: dict[str, Any] = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "head": {"sha": "abc123", "ref": "feature"},
                "base": {"ref": "main"},
                "title": "test pr",
            },
            "repository": {"full_name": "acme/widgets"},
        }
        r = client.post("/webhook", json=body, headers={"X-GitHub-Event": "pull_request"})
        assert r.status_code == 200
        assert r.json()["reviewed"] == 0


def test_webhook_reviews_migration_file_and_posts_comment() -> None:
    """End-to-end: a PR touching a migration file is reviewed and a comment posted."""
    client = TestClient(app)

    fake_changed = ["migrations/20260512_bad_migration.sql"]
    fake_file = "DELETE FROM dw.drdr_dmb;\nGO\n"

    posted: dict[str, str] = {}
    status: dict[str, str] = {}

    async def fake_post(_pr: object, body: str) -> None:
        posted["body"] = body

    async def fake_status(_pr: object, *, state: str, description: str) -> None:
        status["state"] = state
        status["description"] = description

    with (
        patch(
            "migguard.bot.providers.GitHubProvider.list_changed_files",
            new=AsyncMock(return_value=fake_changed),
        ),
        patch(
            "migguard.bot.providers.GitHubProvider.fetch_file",
            new=AsyncMock(return_value=fake_file),
        ),
        patch(
            "migguard.bot.providers.GitHubProvider.post_comment",
            new=AsyncMock(side_effect=fake_post),
        ),
        patch(
            "migguard.bot.providers.GitHubProvider.set_status",
            new=AsyncMock(side_effect=fake_status),
        ),
    ):
        body: dict[str, Any] = {
            "action": "opened",
            "pull_request": {
                "number": 7,
                "head": {"sha": "deadbeef", "ref": "feature"},
                "base": {"ref": "main"},
                "title": "Dangerous migration",
            },
            "repository": {"full_name": "acme/widgets"},
        }
        r = client.post("/webhook", json=body, headers={"X-GitHub-Event": "pull_request"})
        assert r.status_code == 200, r.text
        assert r.json()["reviewed"] == 1
        assert r.json()["blocks_merge"] is True
        assert posted, "expected post_comment to have been called"
        assert "MigGuard Review" in posted["body"]
        assert "HIGH RISK" in posted["body"]
        assert status["state"] == "failure"


def test_webhook_parses_azure_devops_event() -> None:
    client = TestClient(app)

    fake_changed = ["migrations/2026_drop.sql"]
    fake_file = "DROP TABLE dw.member_legacy_archive_2019;\n"

    with (
        patch(
            "migguard.bot.providers.AzureDevOpsProvider.list_changed_files",
            new=AsyncMock(return_value=fake_changed),
        ),
        patch(
            "migguard.bot.providers.AzureDevOpsProvider.fetch_file",
            new=AsyncMock(return_value=fake_file),
        ),
        patch(
            "migguard.bot.providers.AzureDevOpsProvider.post_comment",
            new=AsyncMock(),
        ),
        patch(
            "migguard.bot.providers.AzureDevOpsProvider.set_status",
            new=AsyncMock(),
        ),
    ):
        body: dict[str, Any] = {
            "eventType": "git.pullrequest.created",
            "resource": {
                "pullRequestId": 99,
                "title": "Drop legacy table",
                "sourceRefName": "refs/heads/feature",
                "targetRefName": "refs/heads/main",
                "lastMergeSourceCommit": {"commitId": "cafebabe"},
                "repository": {
                    "name": "data-platform",
                    "project": {"name": "ABCFinancial"},
                },
            },
        }
        r = client.post("/webhook", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["reviewed"] == 1
