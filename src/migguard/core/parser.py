"""T-SQL parser wrapper around sqlglot.

sqlglot is pure-Python with broad T-SQL coverage but not perfect — some
Synapse / SQL Server idioms (e.g. ``DROP INDEX X ON Y``) fail to parse. The
engine treats AST parsing as best-effort:

- Rules that benefit from AST (DROP TABLE, ALTER TABLE column constraints) use it.
- Rules that need to work when parsing fails fall back to regex on the raw SQL.

Either way, every statement keeps its original line range so findings point at
the right spot in the source file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_SQLGLOT_LOGGING_CONFIGURED = False


def _quiet_sqlglot_once() -> None:
    """Suppress sqlglot's chatty INFO/WARNING logs the first time we parse."""
    global _SQLGLOT_LOGGING_CONFIGURED
    if not _SQLGLOT_LOGGING_CONFIGURED:
        logging.getLogger("sqlglot").setLevel(logging.ERROR)
        _SQLGLOT_LOGGING_CONFIGURED = True


@dataclass
class ParsedStatement:
    """One parsed SQL statement with its source location."""

    index: int
    raw_sql: str
    ast: exp.Expression | None
    line_start: int
    line_end: int
    parse_error: str | None = None

    @property
    def parsed_ok(self) -> bool:
        return self.ast is not None


@dataclass
class ParsedScript:
    """A full migration script split into statements."""

    file_path: str
    raw_text: str
    statements: list[ParsedStatement] = field(default_factory=list)
    dialect: str = "tsql"

    @property
    def has_parse_errors(self) -> bool:
        return any(s.parse_error for s in self.statements)

    def by_node_type(
        self, *types: type[exp.Expression]
    ) -> list[tuple[ParsedStatement, exp.Expression]]:
        """For every statement, return (statement, node) for AST nodes of given types.

        Uses sqlglot's ``find_all`` which includes the root node in v23+.
        Used by rule checks:
            for stmt, drop in script.by_node_type(exp.Drop): ...
        """
        results: list[tuple[ParsedStatement, exp.Expression]] = []
        for stmt in self.statements:
            if not stmt.ast:
                continue
            for node in stmt.ast.find_all(*types):
                results.append((stmt, node))
        return results


def _split_statements(text: str) -> list[tuple[str, int, int]]:
    """Split a T-SQL script into individual statements.

    Two-phase split:
    1. Break on ``GO`` lines into batches (T-SQL batch terminator, not parseable SQL).
    2. Within each batch, break on top-level ``;`` (statement terminator).

    Respects ``--`` and ``/* ... */`` comments and quoted strings so we never
    split inside them. Empty / comment-only chunks are dropped.

    Returns (statement_text, line_start, line_end) with 1-based line numbers
    relative to the original source.
    """
    batches: list[tuple[list[str], int]] = []
    current_lines: list[str] = []
    batch_start_line = 1

    for line_idx, raw_line in enumerate(text.splitlines(keepends=True), start=1):
        if raw_line.strip().upper() == "GO":
            if current_lines:
                batches.append((current_lines, batch_start_line))
            current_lines = []
            batch_start_line = line_idx + 1
            continue
        current_lines.append(raw_line)
    if current_lines:
        batches.append((current_lines, batch_start_line))

    statements: list[tuple[str, int, int]] = []
    for batch_lines, batch_first_line in batches:
        batch_text = "".join(batch_lines)
        statements.extend(_split_batch(batch_text, batch_first_line))
    return statements


def _split_batch(batch_text: str, first_line: int) -> list[tuple[str, int, int]]:
    """Split a single GO-batch on top-level ``;``."""
    out: list[tuple[str, int, int]] = []
    buf: list[str] = []
    stmt_start_line: int | None = None
    cur_line = first_line
    in_block_comment = False
    in_line_comment = False
    in_single_quote = False
    in_double_quote = False

    def flush(line_end: int) -> None:
        nonlocal buf, stmt_start_line
        chunk = "".join(buf)
        if chunk.strip() and not _is_only_comments(chunk):
            out.append((chunk, stmt_start_line or line_end, line_end))
        buf = []
        stmt_start_line = None

    i = 0
    while i < len(batch_text):
        ch = batch_text[i]
        nxt = batch_text[i + 1] if i + 1 < len(batch_text) else ""

        if ch == "\n":
            in_line_comment = False
            buf.append(ch)
            cur_line += 1
            i += 1
            continue

        if in_line_comment:
            buf.append(ch)
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_single_quote:
            buf.append(ch)
            if ch == "'":
                in_single_quote = False
            i += 1
            continue
        if in_double_quote:
            buf.append(ch)
            if ch == '"':
                in_double_quote = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch); buf.append(nxt)
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch); buf.append(nxt)
            i += 2
            continue
        if ch == "'":
            in_single_quote = True
            if stmt_start_line is None:
                stmt_start_line = cur_line
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double_quote = True
            if stmt_start_line is None:
                stmt_start_line = cur_line
            buf.append(ch)
            i += 1
            continue

        if ch == ";":
            buf.append(ch)
            flush(cur_line)
            i += 1
            continue

        if not ch.isspace() and stmt_start_line is None:
            stmt_start_line = cur_line
        buf.append(ch)
        i += 1

    flush(cur_line)
    return out


def _is_only_comments(sql: str) -> bool:
    """True if the chunk contains only line comments / whitespace."""
    stripped = sql.strip()
    if not stripped:
        return True
    lines = [ln.strip() for ln in stripped.splitlines()]
    return all(not ln or ln.startswith("--") for ln in lines)


SUPPORTED_DIALECTS = ("tsql", "postgres", "mysql", "sqlite")


def parse_script(
    file_path: str | Path,
    text: str | None = None,
    *,
    dialect: str = "tsql",
) -> ParsedScript:
    """Parse a migration file into statements with source-line metadata.

    Args:
        file_path: Source file path (for reporting; read only if ``text`` is None).
        text: Pre-loaded text. If None, the file is read from ``file_path``.
        dialect: SQL dialect for sqlglot. One of SUPPORTED_DIALECTS. The
            statement splitter (GO + ``;``) is T-SQL specific but harmless on
            other dialects (no GO line will appear, so it never fires).
    """
    _quiet_sqlglot_once()
    if dialect not in SUPPORTED_DIALECTS:
        raise ValueError(
            f"unsupported dialect {dialect!r}; expected one of {SUPPORTED_DIALECTS}"
        )

    path = Path(file_path)
    if text is None:
        text = path.read_text(encoding="utf-8")

    script = ParsedScript(file_path=str(path), raw_text=text, dialect=dialect)
    chunks = _split_statements(text)

    for idx, (raw_sql, line_start, line_end) in enumerate(chunks):
        ast: exp.Expression | None = None
        parse_error: str | None = None
        try:
            parsed_list = sqlglot.parse(raw_sql, dialect=dialect)
            non_null = [p for p in parsed_list if p is not None]
            ast = non_null[0] if non_null else None
        except ParseError as e:
            parse_error = str(e).splitlines()[0] if str(e) else "parse error"
        except Exception as e:  # noqa: BLE001
            parse_error = f"unexpected parser error: {e!r}"

        script.statements.append(
            ParsedStatement(
                index=idx,
                raw_sql=raw_sql,
                ast=ast,
                line_start=line_start,
                line_end=line_end,
                parse_error=parse_error,
            )
        )

    return script
