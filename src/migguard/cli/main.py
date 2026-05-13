"""Command-line interface."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from migguard import __version__
from migguard.cli import formatters
from migguard.core.engine import Engine
from migguard.core.models import Severity
from migguard.core.parser import SUPPORTED_DIALECTS
from migguard.llm.analyzer import LLMAnalyzer
from migguard.rules.base import ALL_DIALECTS


def _collect_sql_files(targets: tuple[str, ...]) -> list[Path]:
    """Expand a mix of files / directories into a sorted list of *.sql files."""
    out: list[Path] = []
    for raw in targets:
        path = Path(raw)
        if path.is_dir():
            out.extend(sorted(path.rglob("*.sql")))
        elif path.is_file():
            out.append(path)
        else:
            click.echo(f"warning: {path} does not exist, skipping", err=True)
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(p)
    return unique


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="migguard")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """MigGuard AI - SQL migration script reviewer."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.argument("targets", nargs=-1, required=True, type=click.Path())
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["terminal", "json", "markdown"]),
    default="terminal",
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write output to a file instead of stdout.",
)
@click.option(
    "--schema-snapshot",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a schema-facts JSON snapshot. Defaults to the bundled one.",
)
@click.option(
    "--no-llm",
    is_flag=True,
    default=False,
    help="Disable the LLM analyzer even if API keys are present.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit with code 1 if any HIGH-severity finding is present.",
)
@click.option(
    "--dialect",
    type=click.Choice(list(SUPPORTED_DIALECTS)),
    default="tsql",
    show_default=True,
    help="SQL dialect of the migrations being reviewed.",
)
def review(
    targets: tuple[str, ...],
    fmt: str,
    output: str | None,
    schema_snapshot: str | None,
    no_llm: bool,
    strict: bool,
    dialect: str,
) -> None:
    """Review one or more migration files / directories."""
    files = _collect_sql_files(targets)
    if not files:
        click.echo("No .sql files found.", err=True)
        sys.exit(2)

    llm = None if no_llm else LLMAnalyzer()
    engine = Engine(schema_snapshot_path=schema_snapshot, llm=llm, dialect=dialect)
    report = engine.review(files)

    if fmt == "terminal":
        if output:
            with open(output, "w", encoding="utf-8") as fh:
                console = Console(file=fh, force_terminal=False, width=120)
                formatters.format_terminal(report, console=console)
        else:
            formatters.format_terminal(report)
    elif fmt == "json":
        text = formatters.format_json(report)
        if output:
            Path(output).write_text(text, encoding="utf-8")
        else:
            click.echo(text)
    elif fmt == "markdown":
        text = formatters.format_markdown_pr_comment(report)
        if output:
            Path(output).write_text(text, encoding="utf-8")
        else:
            click.echo(text)

    if strict and report.overall_severity is Severity.HIGH:
        sys.exit(1)


@cli.command()
@click.option(
    "--dialect",
    type=click.Choice(list(SUPPORTED_DIALECTS) + ["all"]),
    default="all",
    show_default=True,
)
def rules(dialect: str) -> None:
    """List enabled rules. Filter by dialect with ``--dialect``."""
    from migguard.rules.registry import all_rules

    for r in all_rules():
        if dialect != "all" and not r.applies_to(dialect):
            continue
        dialect_tag = "all" if r.dialects == ALL_DIALECTS else ",".join(sorted(r.dialects))
        click.echo(
            f"{r.severity.value:>6}  {r.rule_id:<55}  [{dialect_tag:<20}]  {r.title}"
        )


if __name__ == "__main__":
    cli()
