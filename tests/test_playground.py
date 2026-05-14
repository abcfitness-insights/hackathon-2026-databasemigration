"""Tests for the local web playground (`GET /playground`, `POST /playground`).

The playground is the most user-facing surface in MigGuard. These tests pin
down the contract:

* GET renders a working form (textarea + dialect select + submit).
* POST with a known-risky script returns an HTML report containing the
  expected finding.
* Empty / oversized / invalid input is rejected with a useful error.
* Filenames are sanitized so a malicious paste cannot influence disk layout.
* Dialects outside SUPPORTED_DIALECTS are rejected.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from migguard.bot.app import _PLAYGROUND_MAX_BYTES, app


def test_playground_get_renders_form() -> None:
    client = TestClient(app)
    r = client.get("/playground")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "MigGuard Playground" in body
    assert "<textarea" in body
    assert 'id="dialect"' in body
    assert 'value="tsql"' in body
    assert "Review" in body


def test_playground_post_returns_html_report_with_finding() -> None:
    client = TestClient(app)
    r = client.post(
        "/playground",
        json={
            "sql": "DELETE FROM app.audit_log;",
            "dialect": "tsql",
            "filename": "V099__cleanup.sql",
        },
    )
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "MigGuard" in body
    assert "DELETE" in body.upper()
    assert "V099__cleanup.sql" in body
    assert "HIGH" in body or "mg-bg-high" in body


def test_playground_post_clean_script_renders_no_findings_message() -> None:
    client = TestClient(app)
    r = client.post(
        "/playground",
        json={"sql": "SELECT 1;", "dialect": "tsql"},
    )
    assert r.status_code == 200
    assert "No findings" in r.text or "0 High" in r.text


def test_playground_post_rejects_empty_sql() -> None:
    client = TestClient(app)
    r = client.post("/playground", json={"sql": "   ", "dialect": "tsql"})
    assert r.status_code == 400
    assert "sql" in r.json()["detail"].lower()


def test_playground_post_rejects_oversized_sql() -> None:
    client = TestClient(app)
    big = "-- pad\n" + ("SELECT 1;\n" * (_PLAYGROUND_MAX_BYTES // 4))
    r = client.post("/playground", json={"sql": big, "dialect": "tsql"})
    assert r.status_code == 413


def test_playground_post_rejects_unknown_dialect() -> None:
    client = TestClient(app)
    r = client.post(
        "/playground",
        json={"sql": "SELECT 1;", "dialect": "oracle"},
    )
    assert r.status_code == 400
    assert "dialect" in r.json()["detail"].lower()


def test_playground_post_sanitizes_malicious_filename() -> None:
    """A filename like '../../etc/passwd' must be stripped to its basename."""
    client = TestClient(app)
    r = client.post(
        "/playground",
        json={
            "sql": "DELETE FROM app.x;",
            "dialect": "tsql",
            "filename": "../../etc/passwd",
        },
    )
    assert r.status_code == 200
    body = r.text
    assert "../" not in body
    assert "passwd" in body


def test_playground_post_rejects_invalid_json_body() -> None:
    client = TestClient(app)
    r = client.post(
        "/playground",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_root_endpoint_advertises_playground() -> None:
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "/playground" in r.json()["endpoints"]


# -- new UX features ------------------------------------------------------


def test_playground_get_renders_preset_dropdown_with_examples() -> None:
    """The preset dropdown is the primary onboarding affordance."""
    client = TestClient(app)
    body = client.get("/playground").text
    assert 'id="preset"' in body
    # Each preset id must appear as an <option value="...">.
    for preset_id in ("risky-tsql", "clean-tsql", "risky-mysql", "mysql-utf8", "lifetime-bug"):
        assert f'value="{preset_id}"' in body
    # The JS-side preset map must also include the SQL bodies.
    assert "const PRESETS = {" in body
    # Verify dialect routing is correctly embedded.
    assert '"dialect": "mysql"' in body


def test_playground_get_renders_line_number_gutter() -> None:
    """The line-number gutter helps users correlate findings with their SQL."""
    client = TestClient(app)
    body = client.get("/playground").text
    assert 'id="lineNumbers"' in body
    assert "sql-editor-wrap" in body
    assert "updateLineNumbers" in body


def test_playground_get_persists_input_via_localstorage() -> None:
    """Refreshing the page must not lose the user's pasted SQL."""
    client = TestClient(app)
    body = client.get("/playground").text
    assert "localStorage" in body
    assert "migguard-playground-v1" in body
    assert "saveState" in body and "loadState" in body


def test_playground_get_supports_keyboard_submit_shortcut() -> None:
    """Cmd/Ctrl+Enter inside the textarea should submit the form."""
    client = TestClient(app)
    body = client.get("/playground").text
    assert "metaKey" in body and "ctrlKey" in body
    assert "requestSubmit" in body


def test_playground_get_renders_share_buttons() -> None:
    """Reviewers need a one-click way to share the result."""
    client = TestClient(app)
    body = client.get("/playground").text
    assert 'id="downloadHtmlBtn"' in body
    assert 'id="copyMarkdownBtn"' in body
    assert "Download HTML" in body
    assert "Copy as Markdown" in body


def test_playground_post_markdown_format_returns_plaintext_pr_comment() -> None:
    """The 'Copy as Markdown' button calls POST /playground with format=markdown."""
    client = TestClient(app)
    r = client.post(
        "/playground",
        json={
            "sql": "DELETE FROM app.audit_log;",
            "dialect": "tsql",
            "filename": "V099__cleanup.sql",
            "format": "markdown",
        },
    )
    assert r.status_code == 200
    # Plain text, not HTML; the markdown formatter produces a header like
    # "## MigGuard review" with the filename and severity counts.
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert body.lower().lstrip().startswith("## migguard review")
    assert "V099__cleanup.sql" in body
    assert "DELETE" in body.upper()
    # Markdown output should NOT be wrapped in a full HTML document.
    assert "<!DOCTYPE html>" not in body
    assert "<html" not in body.lower()


def test_playground_post_rejects_unknown_format() -> None:
    """Only 'html' and 'markdown' are accepted; anything else is a 400."""
    client = TestClient(app)
    r = client.post(
        "/playground",
        json={"sql": "SELECT 1;", "dialect": "tsql", "format": "pdf"},
    )
    assert r.status_code == 400
    assert "format" in r.json()["detail"].lower()


def test_playground_post_default_format_is_still_html() -> None:
    """Omitting `format` must continue to return HTML (backward compatible)."""
    client = TestClient(app)
    r = client.post("/playground", json={"sql": "SELECT 1;", "dialect": "tsql"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_playground_get_default_example_sql_is_html_escaped() -> None:
    """The default example is injected into the textarea; characters like <, &
    must be escaped so they cannot break out of the element or be mis-parsed."""
    client = TestClient(app)
    body = client.get("/playground").text
    # The default example is inside <textarea>...</textarea>. The closing
    # </textarea> must only appear once (the real one); any < character from
    # the example must come through as `&lt;`.
    assert body.count("</textarea>") == 1
