"""Output formatters: rich terminal, JSON, Markdown PR comment."""

from __future__ import annotations

import json
import re
from typing import IO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from migguard.core.models import Category, Report, Severity

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

    grouped = report.findings_by_category()
    for category, findings in grouped.items():
        cat_title = category.value.replace("_", " ").title()
        lines.append(f"### {cat_title} ({len(findings)})")
        lines.append("")
        for f in findings:
            sev = f.severity.value.upper()
            loc = (
                f"`{f.location.file}:{f.location.line_start}"
                + (f"-{f.location.line_end}`" if f.location.line_end != f.location.line_start else "`")
            )
            lines.append(f"- **{sev}** - {f.title} {loc}  ")
            lines.append(f"  _{f.rule_id}_  ")
            lines.append(f"  {f.message}")
            if f.schema_context:
                lines.append(f"  > {f.schema_context}")
            if f.suggestion:
                lines.append("")
                lines.append("  <details><summary>Suggested fix</summary>")
                lines.append("")
                fence = "sql" if _looks_like_sql(f.suggestion) else ""
                if fence:
                    lines.append(f"  ```{fence}")
                    for sl in f.suggestion.splitlines():
                        lines.append(f"  {sl}")
                    lines.append("  ```")
                else:
                    for sl in f.suggestion.splitlines():
                        lines.append(f"  {sl}")
                lines.append("  </details>")
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


def write(text: str, out: IO[str] | None = None) -> None:
    if out is None:
        print(text)
    else:
        out.write(text)
        if not text.endswith("\n"):
            out.write("\n")
