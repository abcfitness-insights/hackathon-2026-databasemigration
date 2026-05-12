"""End-to-end demo runner. Runs MigGuard on every fixture and prints the
terminal report. Use this for the hackathon demo recording.

Usage:
    python demo/run_demo.py             # terminal output
    python demo/run_demo.py --markdown  # markdown PR comments
    python demo/run_demo.py --json      # JSON for inspection
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from migguard.cli import formatters
from migguard.core.engine import Engine
from migguard.llm.analyzer import LLMAnalyzer

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "migrations"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", action="store_true", help="Output as Markdown PR comments")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM analyzer")
    args = parser.parse_args()

    console = Console()
    llm = None if args.no_llm else LLMAnalyzer()
    engine = Engine(llm=llm)

    if llm and llm.enabled:
        console.print(f"[green]LLM analyzer enabled[/green] (provider={llm.provider}, model={llm.model})\n")
    else:
        console.print("[yellow]LLM analyzer disabled[/yellow] (no API key configured)\n")

    fixtures = sorted(FIXTURES.glob("*.sql"))
    if not fixtures:
        print(f"No fixtures found in {FIXTURES}", file=sys.stderr)
        sys.exit(2)

    for fx in fixtures:
        console.print(Rule(f"[bold]{fx.name}[/bold]", style="cyan"))
        report = engine.review([fx])

        if args.markdown:
            print(formatters.format_markdown_pr_comment(report))
            print()
        elif args.json:
            print(formatters.format_json(report))
            print()
        else:
            formatters.format_terminal(report, console=console)
        console.print()


if __name__ == "__main__":
    main()
