"""Output formatters: rich terminal, JSON, Markdown PR comment, HTML report."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from typing import IO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from migguard.core.models import (
    Finding,
    Report,
    Severity,
    group_findings_by_location,
)

_SQL_KEYWORDS_RE = re.compile(
    r"(?im)^\s*(ALTER|CREATE|DROP|SELECT|INSERT|UPDATE|DELETE|MERGE|TRUNCATE|"
    r"BEGIN|COMMIT|ROLLBACK|GRANT|REVOKE|WITH)\b"
)


def _looks_like_sql(text: str) -> bool:
    """Heuristic: only fence suggestions that begin with a SQL keyword on some line."""
    return bool(_SQL_KEYWORDS_RE.search(text))


_SEV_STYLE = {
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}

_SEV_BADGE = {
    Severity.HIGH: "HIGH",
    Severity.MEDIUM: "MED ",
    Severity.LOW: "LOW ",
    Severity.INFO: "INFO",
}

_HTML_SEV_CLASS = {
    Severity.HIGH: "high",
    Severity.MEDIUM: "medium",
    Severity.LOW: "low",
    Severity.INFO: "info",
}

_SARIF_LEVEL = {
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "none",
}


def format_terminal(report: Report, *, console: Console | None = None) -> None:
    """Pretty-print a Report to stdout (or the given console)."""
    console = console or Console()

    counts = report.counts
    header = (
        f"[bold]MigGuard Review[/bold]  -  "
        f"{len(report.files_reviewed)} file(s), "
        f"[red]{counts['high']} high[/red] / "
        f"[yellow]{counts['medium']} med[/yellow] / "
        f"[cyan]{counts['low']} low[/cyan] / "
        f"[dim]{counts['info']} info[/dim]"
    )
    overall = report.overall_severity
    border_style = _SEV_STYLE[overall]
    blocks = " (BLOCKS MERGE)" if report.blocks_merge else ""
    console.print(Panel(header + blocks, border_style=border_style, expand=False))

    if not report.findings:
        console.print("[green]No findings. Migration looks safe to review.[/green]")
        return

    grouped = report.findings_by_category()
    for category, findings in grouped.items():
        table = Table(
            title=f"[bold]{category.value.replace('_', ' ').title()}[/bold]  "
            f"({len(findings)} finding{'s' if len(findings) != 1 else ''})",
            title_justify="left",
            show_lines=True,
            expand=True,
        )
        table.add_column("Sev", width=4)
        table.add_column("Where", width=28, overflow="fold")
        table.add_column("Issue", overflow="fold")

        for f in findings:
            sev_text = Text(_SEV_BADGE[f.severity], style=_SEV_STYLE[f.severity])
            loc = (
                f"{f.location.file}\n"
                f"line {f.location.line_start}"
                + (f"-{f.location.line_end}" if f.location.line_end != f.location.line_start else "")
            )
            msg = f"[bold]{f.title}[/bold]  [dim]({f.rule_id})[/dim]\n{f.message}"
            if f.schema_context:
                msg += f"\n[dim italic]{f.schema_context}[/dim italic]"
            if f.suggestion:
                msg += f"\n[green]Fix:[/green] {f.suggestion}"
            table.add_row(sev_text, loc, msg)

        console.print(table)

    if report.summary:
        console.print(Panel(report.summary, title="LLM Summary", border_style="blue"))


def format_json(report: Report, *, indent: int = 2) -> str:
    """Serialize the Report to JSON. Used for CI artifacts and tests."""
    return report.model_dump_json(indent=indent)


def format_sarif(report: Report, *, indent: int = 2) -> str:
    """Render a Report as a SARIF 2.1.0 log.

    SARIF is the industry-standard format consumed by GitHub Advanced
    Security, Azure DevOps Advanced Security, SonarQube, Codacy, and most
    enterprise code-scanning dashboards. Emitting SARIF lets MigGuard plug
    into those surfaces with zero extra integration code.

    Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
    """
    from migguard import __version__

    unique_rules: dict[str, dict] = {}
    for f in report.findings:
        if f.rule_id in unique_rules:
            continue
        unique_rules[f.rule_id] = {
            "id": f.rule_id,
            "name": f.rule_id.replace("/", "_").replace("-", "_"),
            "shortDescription": {"text": f.title},
            "fullDescription": {"text": f.title},
            "defaultConfiguration": {"level": _SARIF_LEVEL[f.severity]},
            "properties": {
                "category": f.category.value,
                "severity": f.severity.value,
            },
        }

    results = []
    for f in report.findings:
        result = {
            "ruleId": f.rule_id,
            "level": _SARIF_LEVEL[f.severity],
            "message": {"text": f.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.location.file},
                        "region": {
                            "startLine": f.location.line_start,
                            "endLine": f.location.line_end,
                        },
                    }
                }
            ],
            "properties": {
                "category": f.category.value,
                "severity": f.severity.value,
                "layer": f.layer.value,
            },
        }
        if f.suggestion:
            result["fixes"] = [
                {
                    "description": {"text": "Suggested fix"},
                    "artifactChanges": [
                        {
                            "artifactLocation": {"uri": f.location.file},
                            "replacements": [
                                {
                                    "deletedRegion": {
                                        "startLine": f.location.line_start,
                                        "endLine": f.location.line_end,
                                    },
                                    "insertedContent": {"text": f.suggestion},
                                }
                            ],
                        }
                    ],
                }
            ]
        if f.schema_context:
            result["properties"]["schemaContext"] = f.schema_context
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "MigGuard",
                        "version": __version__,
                        "informationUri": "https://github.com/abcfitness-insights/hackathon-2026-databasemigration",
                        "rules": list(unique_rules.values()),
                    }
                },
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "endTimeUtc": report.generated_at.isoformat().replace(
                            "+00:00", "Z"
                        ),
                    }
                ],
                "properties": {
                    "filesReviewed": report.files_reviewed,
                    "blocksMerge": report.blocks_merge,
                    "counts": report.counts,
                },
            }
        ],
    }
    return json.dumps(sarif, indent=indent)


def _render_md_group(lines: list[str], group: list[Finding]) -> None:
    """Render a single (file, line) finding group into the markdown buffer.

    The first item in ``group`` is treated as the primary; the rest appear as
    a compact 'also flagged on this statement' list.
    """
    primary = group[0]
    related = group[1:]
    sev = primary.severity.value.upper()
    loc = (
        f"`{primary.location.file}:{primary.location.line_start}"
        + (
            f"-{primary.location.line_end}`"
            if primary.location.line_end != primary.location.line_start
            else "`"
        )
    )
    lines.append(f"- **{sev}** - {primary.title} {loc}  ")
    lines.append(f"  _{primary.rule_id}_  ")
    lines.append(f"  {primary.message}")
    if primary.schema_context:
        lines.append(f"  > {primary.schema_context}")
    if related:
        also = ", ".join(f"`{f.rule_id}` ({f.severity.value})" for f in related)
        lines.append(f"  _Also flagged on this statement:_ {also}")
    if primary.suggestion:
        lines.append("")
        lines.append("  <details><summary>Suggested fix</summary>")
        lines.append("")
        fence = "sql" if _looks_like_sql(primary.suggestion) else ""
        if fence:
            lines.append(f"  ```{fence}")
            for sl in primary.suggestion.splitlines():
                lines.append(f"  {sl}")
            lines.append("  ```")
        else:
            for sl in primary.suggestion.splitlines():
                lines.append(f"  {sl}")
        lines.append("  </details>")
    lines.append("")


def format_markdown_pr_comment(report: Report) -> str:
    """Render a Report as a GitHub / Azure DevOps PR comment.

    The Markdown is intentionally compatible with both: GitHub-flavoured
    Markdown supports collapsed details; ADO renders the same syntax.
    """
    counts = report.counts
    overall = report.overall_severity
    badge = {
        Severity.HIGH: "HIGH RISK",
        Severity.MEDIUM: "MEDIUM RISK",
        Severity.LOW: "LOW RISK",
        Severity.INFO: "CLEAN",
    }[overall]

    lines: list[str] = []
    blocks_note = " (blocks merge)" if report.blocks_merge else ""
    lines.append(f"## MigGuard Review - **{badge}**{blocks_note}")
    lines.append("")
    lines.append(
        f"`{counts['high']}` high / `{counts['medium']}` medium / "
        f"`{counts['low']}` low / `{counts['info']}` info across "
        f"{len(report.files_reviewed)} file(s)."
    )
    lines.append("")

    if not report.findings:
        lines.append("No findings. Migration looks safe to review.")
        return "\n".join(lines)

    # 2-line TL;DR for big reports: name the top-3 highest-severity items
    # before the section breakdown so reviewers don't have to scan.
    top_three = sorted(
        report.findings, key=lambda f: f.severity.rank, reverse=True
    )[:3]
    if top_three:
        lines.append("**Top issues to look at first:**")
        for f in top_three:
            lines.append(
                f"- `{f.severity.value.upper()}` {f.title} "
                f"_({f.location.file}:{f.location.line_start})_"
            )
        lines.append("")

    grouped = report.findings_by_category()
    for category, findings in grouped.items():
        cat_title = category.value.replace("_", " ").title()
        # Per-category severity counts so the heading itself summarizes weight.
        cat_counts = {s: sum(1 for f in findings if f.severity is s) for s in Severity}
        sev_summary = " ".join(
            f"{cat_counts[s]}{s.value[0].upper()}"
            for s in (Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO)
            if cat_counts[s]
        )
        lines.append(f"### {cat_title} ({len(findings)}) - {sev_summary}")
        lines.append("")
        # Group findings on the same (file, line_start) so multiple rules
        # firing on the same statement render as primary + related instead of
        # repeating the same line N times. Grouping algorithm lives in
        # `group_findings_by_location` so all formatters stay in sync.
        # Split groups into expanded (anything with a HIGH/MEDIUM primary) and
        # collapsed (LOW + INFO only), so a 60-finding report doesn't drown the
        # reviewer in style-tier noise.
        expanded_groups: list[list[Finding]] = []
        collapsed_groups: list[list[Finding]] = []
        for group in group_findings_by_location(findings):
            if group[0].severity.rank >= Severity.MEDIUM.rank:
                expanded_groups.append(group)
            else:
                collapsed_groups.append(group)

        for group in expanded_groups:
            _render_md_group(lines, group)

        if collapsed_groups:
            total_low_info = sum(len(g) for g in collapsed_groups)
            lines.append(
                f"<details><summary>Show {total_low_info} lower-severity "
                f"finding(s)</summary>"
            )
            lines.append("")
            for group in collapsed_groups:
                _render_md_group(lines, group)
            lines.append("</details>")
            lines.append("")

    if report.summary:
        lines.append("---")
        lines.append("")
        lines.append("### Plain-English summary")
        lines.append("")
        lines.append(report.summary)
        lines.append("")

    if report.rollback_script:
        lines.append("---")
        lines.append("")
        lines.append("### Auto-generated rollback (review required)")
        lines.append("")
        lines.append("<details><summary>Click to expand rollback script</summary>")
        lines.append("")
        lines.append("```sql")
        lines.append(report.rollback_script)
        lines.append("```")
        lines.append("</details>")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Generated by [MigGuard AI](https://github.com/your-org/migguard) - "
        "review every suggestion before applying._"
    )
    return "\n".join(lines)


def format_html(report: Report) -> str:
    """Render a Report as a self-contained HTML page (embedded CSS, no external deps).

    The same function powers ``--format html`` and the web playground at
    ``/playground`` so we have a single source of truth for the visual report.
    """
    counts = report.counts
    overall = report.overall_severity
    overall_class = _HTML_SEV_CLASS[overall]
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "<title>MigGuard Review</title>",
        "<style>",
        _HTML_CSS,
        "</style>",
        "</head><body>",
        f'<header class="mg-header mg-{overall_class}">',
        '<div class="mg-header-inner">',
        '<h1>MigGuard <span class="mg-sub">Review</span></h1>',
        f'<p class="mg-tagline">SQL migration script review &middot; Generated {html.escape(timestamp)}</p>',
        '<div class="mg-counts">',
        f'<span class="mg-badge mg-bg-high">{counts["high"]} High</span>',
        f'<span class="mg-badge mg-bg-medium">{counts["medium"]} Medium</span>',
        f'<span class="mg-badge mg-bg-low">{counts["low"]} Low</span>',
        f'<span class="mg-badge mg-bg-info">{counts["info"]} Info</span>',
        "</div>",
    ]

    if report.files_reviewed:
        files_html = ", ".join(f"<code>{html.escape(f)}</code>" for f in report.files_reviewed)
        parts.append(
            f'<p class="mg-files">{len(report.files_reviewed)} file(s) reviewed: {files_html}</p>'
        )

    if report.blocks_merge:
        parts.append('<p class="mg-blocks">This review would block merge in strict mode.</p>')

    parts.append("</div></header>")
    parts.append("<main>")

    if not report.findings:
        parts.append(
            '<div class="mg-empty"><strong>No findings.</strong> '
            "Migration looks safe to review.</div>"
        )
    else:
        grouped = report.findings_by_category()
        for category, findings in grouped.items():
            cat_title = category.value.replace("_", " ").title()
            parts.append('<section class="mg-category">')
            parts.append(
                f"<h2>{html.escape(cat_title)} "
                f"<span class='mg-count'>({len(findings)})</span></h2>"
            )
            for group in group_findings_by_location(findings):
                primary = group[0]
                related = group[1:]
                sev_class = _HTML_SEV_CLASS[primary.severity]
                line_range = str(primary.location.line_start)
                if primary.location.line_end != primary.location.line_start:
                    line_range += f"-{primary.location.line_end}"
                parts.append(f'<article class="mg-finding mg-{sev_class}">')
                parts.append('<header class="mg-finding-head">')
                parts.append(
                    f'<span class="mg-badge mg-bg-{sev_class}">'
                    f"{primary.severity.value.upper()}</span>"
                )
                parts.append(f"<h3>{html.escape(primary.title)}</h3>")
                parts.append(
                    f'<code class="mg-rule-id">{html.escape(primary.rule_id)}</code>'
                )
                parts.append("</header>")
                parts.append(
                    f'<p class="mg-loc"><code>{html.escape(primary.location.file)}</code> '
                    f"line {line_range}</p>"
                )
                parts.append(f"<p class='mg-msg'>{html.escape(primary.message)}</p>")
                if primary.schema_context:
                    parts.append(
                        f'<blockquote class="mg-schema">'
                        f"{html.escape(primary.schema_context)}</blockquote>"
                    )
                if related:
                    chips = " ".join(
                        f'<code class="mg-related">{html.escape(f.rule_id)} '
                        f"({html.escape(f.severity.value)})</code>"
                        for f in related
                    )
                    parts.append(
                        f"<p class='mg-also'>Also flagged on this statement: {chips}</p>"
                    )
                if primary.suggestion:
                    parts.append("<details class='mg-fix'><summary>Suggested fix</summary>")
                    parts.append(f"<pre><code>{html.escape(primary.suggestion)}</code></pre>")
                    parts.append("</details>")
                parts.append("</article>")
            parts.append("</section>")

    if report.summary:
        parts.append('<section class="mg-summary">')
        parts.append("<h2>Plain-English summary</h2>")
        parts.append(f"<p>{html.escape(report.summary)}</p>")
        parts.append("</section>")

    if report.rollback_script:
        parts.append('<section class="mg-rollback">')
        parts.append(
            "<h2>Auto-generated rollback "
            "<span class='mg-warn'>review required</span></h2>"
        )
        parts.append("<details><summary>Click to expand rollback script</summary>")
        parts.append(f"<pre><code>{html.escape(report.rollback_script)}</code></pre>")
        parts.append("</details>")
        parts.append("</section>")

    parts.append("</main>")
    parts.append('<footer class="mg-footer">')
    parts.append(
        "<p>Generated by <strong>MigGuard AI</strong> &middot; "
        "review every suggestion before applying.</p>"
    )
    parts.append("</footer>")
    parts.append("</body></html>")
    return "\n".join(parts)


_HTML_CSS = """
:root {
    --high: #dc2626;
    --medium: #d97706;
    --low: #2563eb;
    --info: #6b7280;
    --bg: #f8fafc;
    --card: #ffffff;
    --border: #e5e7eb;
    --text: #1f2937;
    --muted: #6b7280;
    --shadow: 0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.06);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.55;
}
.mg-header {
    background: linear-gradient(135deg, #1e40af 0%, #6d28d9 100%);
    color: white;
    padding: 2.5rem 1.5rem;
}
.mg-header.mg-high { background: linear-gradient(135deg, #991b1b 0%, #7c2d12 100%); }
.mg-header.mg-medium { background: linear-gradient(135deg, #b45309 0%, #92400e 100%); }
.mg-header.mg-low, .mg-header.mg-info { background: linear-gradient(135deg, #1e40af 0%, #6d28d9 100%); }
.mg-header-inner { max-width: 1100px; margin: 0 auto; }
.mg-header h1 { margin: 0; font-size: 2.25rem; font-weight: 700; letter-spacing: -0.025em; }
.mg-header .mg-sub { font-weight: 300; opacity: 0.85; }
.mg-tagline { margin: 0.4rem 0 1.5rem 0; opacity: 0.9; }
.mg-counts { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 1rem; }
.mg-badge {
    display: inline-block; padding: 0.35rem 0.8rem; border-radius: 999px;
    font-size: 0.85rem; font-weight: 600; letter-spacing: 0.02em;
}
.mg-bg-high { background: var(--high); color: white; }
.mg-bg-medium { background: var(--medium); color: white; }
.mg-bg-low { background: var(--low); color: white; }
.mg-bg-info { background: var(--info); color: white; }
.mg-files { margin: 0.5rem 0 0; opacity: 0.9; font-size: 0.95rem; }
.mg-files code { background: rgba(255,255,255,0.15); padding: 0.15rem 0.4rem; border-radius: 4px; }
.mg-blocks { margin: 0.8rem 0 0; padding: 0.6rem 1rem; background: rgba(0,0,0,0.25); border-left: 3px solid #fbbf24; border-radius: 4px; font-weight: 500; }
main { max-width: 1100px; margin: 2rem auto; padding: 0 1.5rem; }
.mg-empty { background: #ecfdf5; border: 1px solid #6ee7b7; color: #065f46; padding: 1.25rem; border-radius: 8px; font-size: 1.05rem; }
.mg-category { margin-bottom: 2rem; }
.mg-category h2 { font-size: 1.35rem; margin: 0 0 1rem 0; border-bottom: 2px solid var(--border); padding-bottom: 0.5rem; }
.mg-count { color: var(--muted); font-weight: 400; font-size: 1rem; }
.mg-finding { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--info); border-radius: 8px; padding: 1.1rem 1.25rem; margin-bottom: 0.85rem; box-shadow: var(--shadow); }
.mg-finding.mg-high { border-left-color: var(--high); }
.mg-finding.mg-medium { border-left-color: var(--medium); }
.mg-finding.mg-low { border-left-color: var(--low); }
.mg-finding-head { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.5rem; }
.mg-finding-head h3 { margin: 0; font-size: 1.1rem; flex: 1; min-width: 0; }
.mg-rule-id { background: #f3f4f6; padding: 0.18rem 0.45rem; border-radius: 4px; font-size: 0.8rem; color: var(--muted); }
.mg-also { margin: 0.45rem 0 0.2rem; font-size: 0.85rem; color: var(--muted); }
.mg-related { background: #f3f4f6; padding: 0.1rem 0.4rem; border-radius: 4px; margin-right: 0.3rem; font-size: 0.8rem; }
.mg-loc { margin: 0.25rem 0; font-size: 0.9rem; color: var(--muted); }
.mg-loc code { background: #f3f4f6; padding: 0.1rem 0.35rem; border-radius: 3px; }
.mg-msg { margin: 0.5rem 0; }
.mg-schema { margin: 0.6rem 0; padding: 0.5rem 0.9rem; background: #fefce8; border-left: 3px solid #facc15; border-radius: 4px; font-size: 0.9rem; color: #713f12; }
.mg-fix { margin-top: 0.65rem; }
.mg-fix summary { cursor: pointer; font-weight: 600; color: var(--low); padding: 0.3rem 0; user-select: none; }
.mg-fix summary:hover { color: #1e40af; }
.mg-fix pre { background: #1e293b; color: #f1f5f9; padding: 0.9rem 1.1rem; border-radius: 6px; overflow-x: auto; margin: 0.5rem 0 0; }
.mg-fix code, .mg-rollback code { font-family: "SF Mono", Monaco, Consolas, "Liberation Mono", monospace; font-size: 0.88rem; }
.mg-summary { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem 1.5rem; margin-bottom: 1.5rem; box-shadow: var(--shadow); }
.mg-summary h2 { margin-top: 0; }
.mg-rollback { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem 1.5rem; margin-bottom: 1.5rem; box-shadow: var(--shadow); }
.mg-rollback h2 { margin-top: 0; }
.mg-warn { font-size: 0.8rem; background: #fef3c7; color: #92400e; padding: 0.2rem 0.55rem; border-radius: 999px; font-weight: 500; margin-left: 0.5rem; vertical-align: middle; }
.mg-rollback summary { cursor: pointer; color: var(--low); font-weight: 600; padding: 0.3rem 0; user-select: none; }
.mg-rollback pre { background: #1e293b; color: #f1f5f9; padding: 1rem; border-radius: 6px; overflow-x: auto; margin: 0.7rem 0 0; }
.mg-footer { max-width: 1100px; margin: 2rem auto 3rem; padding: 0 1.5rem; color: var(--muted); font-size: 0.88rem; text-align: center; border-top: 1px solid var(--border); padding-top: 1.5rem; }
"""


def write(text: str, out: IO[str] | None = None) -> None:
    if out is None:
        print(text)
    else:
        out.write(text)
        if not text.endswith("\n"):
            out.write("\n")
