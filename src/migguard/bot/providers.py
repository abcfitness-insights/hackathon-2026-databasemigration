"""Provider adapters for GitHub and Azure DevOps.

Both providers send webhook events on PR open / synchronize / etc. and accept
REST calls to post review comments and set commit-status checks. The shape of
both is different enough that we use one adapter per provider and a shared
``PRReviewer`` runs the engine and routes to the right adapter.

Required env vars per provider:

- GitHub: ``GITHUB_TOKEN`` (PAT or app installation token), ``GITHUB_API_URL``
  (default https://api.github.com).
- Azure DevOps: ``AZDO_PAT``, ``AZDO_ORG_URL`` (e.g. https://dev.azure.com/abc).

Both adapters are HTTP-only via ``httpx`` so there are no SDK dependencies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class PullRequest:
    """Provider-neutral PR identity."""

    provider: str  # "github" or "azdo"
    repo: str      # "owner/repo" for GitHub; "project/repo" for ADO
    pr_id: int
    head_sha: str
    head_ref: str
    base_ref: str
    title: str = ""


class Provider(Protocol):
    """Interface every provider adapter must implement."""

    name: str

    def parse_event(self, headers: dict[str, str], body: dict) -> PullRequest | None: ...
    async def list_changed_files(self, pr: PullRequest) -> list[str]: ...
    async def fetch_file(self, pr: PullRequest, path: str) -> str: ...
    async def post_comment(self, pr: PullRequest, body: str) -> None: ...
    async def set_status(self, pr: PullRequest, *, state: str, description: str) -> None: ...


class GitHubProvider:
    """GitHub adapter. Uses REST v3."""

    name = "github"

    def __init__(self) -> None:
        self.token = os.environ.get("GITHUB_TOKEN", "")
        self.api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def parse_event(self, headers: dict[str, str], body: dict) -> PullRequest | None:
        event = headers.get("x-github-event") or headers.get("X-GitHub-Event")
        if event != "pull_request":
            return None
        action = body.get("action")
        if action not in {"opened", "synchronize", "reopened", "edited"}:
            return None
        pr = body.get("pull_request") or {}
        repo = body.get("repository") or {}
        return PullRequest(
            provider="github",
            repo=repo.get("full_name", ""),
            pr_id=pr.get("number", 0),
            head_sha=(pr.get("head") or {}).get("sha", ""),
            head_ref=(pr.get("head") or {}).get("ref", ""),
            base_ref=(pr.get("base") or {}).get("ref", ""),
            title=pr.get("title", ""),
        )

    async def list_changed_files(self, pr: PullRequest) -> list[str]:
        url = f"{self.api}/repos/{pr.repo}/pulls/{pr.pr_id}/files?per_page=100"
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as client:
            r = await client.get(url)
            r.raise_for_status()
            return [item["filename"] for item in r.json()]

    async def fetch_file(self, pr: PullRequest, path: str) -> str:
        url = f"{self.api}/repos/{pr.repo}/contents/{path}?ref={pr.head_sha}"
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github.raw"})
            r.raise_for_status()
            return r.text

    async def post_comment(self, pr: PullRequest, body: str) -> None:
        url = f"{self.api}/repos/{pr.repo}/issues/{pr.pr_id}/comments"
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as client:
            r = await client.post(url, json={"body": body})
            r.raise_for_status()

    async def set_status(self, pr: PullRequest, *, state: str, description: str) -> None:
        url = f"{self.api}/repos/{pr.repo}/statuses/{pr.head_sha}"
        gh_state = {"success": "success", "failure": "failure", "pending": "pending"}.get(
            state, "success"
        )
        payload = {
            "state": gh_state,
            "context": "MigGuard / migration review",
            "description": description[:140],
        }
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()


class AzureDevOpsProvider:
    """Azure DevOps adapter. Uses REST 7.1."""

    name = "azdo"

    def __init__(self) -> None:
        self.pat = os.environ.get("AZDO_PAT", "")
        self.org_url = os.environ.get("AZDO_ORG_URL", "").rstrip("/")

    def _auth(self) -> httpx.Auth:
        return httpx.BasicAuth("", self.pat)

    def parse_event(self, headers: dict[str, str], body: dict) -> PullRequest | None:
        event_type = body.get("eventType", "")
        if event_type not in {"git.pullrequest.created", "git.pullrequest.updated"}:
            return None
        resource = body.get("resource") or {}
        repo = resource.get("repository") or {}
        project = (repo.get("project") or {}).get("name", "")
        repo_name = repo.get("name", "")
        return PullRequest(
            provider="azdo",
            repo=f"{project}/{repo_name}",
            pr_id=resource.get("pullRequestId", 0),
            head_sha=(resource.get("lastMergeSourceCommit") or {}).get("commitId", ""),
            head_ref=resource.get("sourceRefName", "").replace("refs/heads/", ""),
            base_ref=resource.get("targetRefName", "").replace("refs/heads/", ""),
            title=resource.get("title", ""),
        )

    def _pr_base_url(self, pr: PullRequest) -> str:
        project, repo = pr.repo.split("/", 1)
        return (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullRequests/{pr.pr_id}"
        )

    async def list_changed_files(self, pr: PullRequest) -> list[str]:
        url = (
            f"{self._pr_base_url(pr)}/iterations?api-version=7.1"
        )
        async with httpx.AsyncClient(auth=self._auth(), timeout=30) as client:
            r = await client.get(url)
            r.raise_for_status()
            iterations = r.json().get("value", [])
            if not iterations:
                return []
            latest = iterations[-1]["id"]
            changes_url = (
                f"{self._pr_base_url(pr)}/iterations/{latest}/changes?api-version=7.1"
            )
            r2 = await client.get(changes_url)
            r2.raise_for_status()
            entries = r2.json().get("changeEntries", [])
            return [
                (e.get("item") or {}).get("path", "").lstrip("/")
                for e in entries
                if (e.get("item") or {}).get("path")
            ]

    async def fetch_file(self, pr: PullRequest, path: str) -> str:
        project, repo = pr.repo.split("/", 1)
        url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/items"
            f"?path=/{path.lstrip('/')}&versionDescriptor.version={pr.head_sha}"
            f"&versionDescriptor.versionType=commit&api-version=7.1"
        )
        async with httpx.AsyncClient(
            auth=self._auth(), timeout=30, headers={"Accept": "text/plain"}
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.text

    async def post_comment(self, pr: PullRequest, body: str) -> None:
        url = f"{self._pr_base_url(pr)}/threads?api-version=7.1"
        payload = {
            "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
            "status": 1,
        }
        async with httpx.AsyncClient(auth=self._auth(), timeout=30) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()

    async def set_status(self, pr: PullRequest, *, state: str, description: str) -> None:
        url = f"{self._pr_base_url(pr)}/statuses?api-version=7.1"
        azdo_state = {"success": "succeeded", "failure": "failed", "pending": "pending"}.get(
            state, "succeeded"
        )
        payload = {
            "state": azdo_state,
            "description": description[:140],
            "context": {"name": "migration-review", "genre": "migguard"},
        }
        async with httpx.AsyncClient(auth=self._auth(), timeout=30) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()


def detect_provider(headers: dict[str, str], body: dict) -> Provider | None:
    """Return the right adapter for an incoming webhook, or None if we should ignore."""
    lower_headers = {k.lower(): v for k, v in headers.items()}
    if "x-github-event" in lower_headers:
        return GitHubProvider()
    lower_body_keys = {k.lower() for k in body}
    if "eventtype" in lower_body_keys or body.get("publisherId") == "tfs":
        return AzureDevOpsProvider()
    return None
