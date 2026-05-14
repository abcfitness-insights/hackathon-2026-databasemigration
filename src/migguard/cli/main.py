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

_FAIL_ON_MAP: dict[str, Severity | None] = {
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    "never": None,
}


def _resolve_fail_threshold(
    *, fail_on: str | None, strict: bool
) -> Severity | None:
    """Translate ``--fail-on`` / ``--strict`` into a severity threshold.

    ``--strict`` is preserved as a backward-compat alias for
    ``--fail-on high``. If both are passed, ``--fail-on`` wins so users can
    deliberately override the legacy flag without removing it from CI YAML.
    """
    if fail_on is not None:
        return _FAIL_ON_MAP[fail_on.lower()]
    if strict:
        return Severity.HIGH
    return None


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
    type=click.Choice(["terminal", "json", "markdown", "html", "sarif"]),
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
    "--rules-config",
    "rules_config",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a YAML file with additional regex-based rules.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Alias for --fail-on high. Kept for backward compatibility.",
)
@click.option(
    "--fail-on",
    "fail_on",
    type=click.Choice(["high", "medium", "low", "never"], case_sensitive=False),
    default=None,
    help=(
        "Exit non-zero when the overall severity meets or exceeds this level. "
        "Defaults to 'never' (no gate). 'high' matches old --strict behaviour."
    ),
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
    rules_config: str | None,
    strict: bool,
    fail_on: str | None,
    dialect: str,
) -> None:
    """Review one or more migration files / directories."""
    files = _collect_sql_files(targets)
    if not files:
        click.echo("No .sql files found.", err=True)
        sys.exit(2)

    from migguard.rules.registry import all_rules

    rules = all_rules()
    if rules_config:
        from migguard.rules.yaml_rules import YamlRuleConfigError, load_yaml_rules

        try:
            rules = rules + load_yaml_rules(rules_config)
        except YamlRuleConfigError as exc:
            click.echo(f"Error loading rules config: {exc}", err=True)
            sys.exit(2)

    llm = None if no_llm else LLMAnalyzer()
    engine = Engine(
        rules=rules,
        schema_snapshot_path=schema_snapshot,
        llm=llm,
        dialect=dialect,
    )
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
    elif fmt == "html":
        text = formatters.format_html(report)
        if output:
            Path(output).write_text(text, encoding="utf-8")
        else:
            click.echo(text)
    elif fmt == "sarif":
        text = formatters.format_sarif(report)
        if output:
            Path(output).write_text(text, encoding="utf-8")
        else:
            click.echo(text)

    effective_threshold = _resolve_fail_threshold(fail_on=fail_on, strict=strict)
    if effective_threshold is not None and report.overall_severity.rank >= effective_threshold.rank:
        sys.exit(1)


@cli.command()
@click.option(
    "--dialect",
    type=click.Choice([*SUPPORTED_DIALECTS, "all"]),
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


@cli.command()
@click.argument("rule_id", required=True)
def explain(rule_id: str) -> None:
    """Explain a rule_id with rationale, a bad example, and the safe pattern.

    Example:
        migguard explain data-loss/delete-without-where
    """
    from migguard.cli.explanations import find_explanation, suggest_similar

    explanation = find_explanation(rule_id)
    if explanation is None:
        click.echo(f"No documented rule with ID '{rule_id}'.", err=True)
        suggestions = suggest_similar(rule_id)
        if suggestions:
            click.echo("\nDid you mean one of:", err=True)
            for s in suggestions:
                click.echo(f"  - {s}", err=True)
        click.echo("\nList all rules: migguard rules", err=True)
        sys.exit(2)

    console = Console()
    console.print(f"[bold]{explanation.rule_id}[/bold]")
    console.print(f"[dim]{explanation.title}[/dim]")
    console.print(
        f"Severity: [bold]{explanation.severity}[/bold]   "
        f"Category: [bold]{explanation.category}[/bold]\n"
    )
    console.print("[bold]Why this matters[/bold]")
    console.print(explanation.why)
    console.print("")
    console.print("[bold red]Risky pattern[/bold red]")
    console.print(f"[red]{explanation.bad_example}[/red]")
    console.print("")
    console.print("[bold green]Safe pattern[/bold green]")
    console.print(f"[green]{explanation.good_example}[/green]")
    if explanation.references:
        console.print("")
        console.print("[bold]References[/bold]")
        for ref in explanation.references:
            console.print(f"  - {ref}")


if __name__ == "__main__":
    cli()
