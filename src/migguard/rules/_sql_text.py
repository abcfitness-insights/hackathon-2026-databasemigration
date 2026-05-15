"""Shared text utilities for raw-SQL rules.

Some rules pattern-match against ``ParsedStatement.raw_sql`` rather than the
AST — usually because sqlglot can't reliably parse a dialect-specific form
(``DROP INDEX X ON Y`` on T-SQL, ``DBCC`` family, ...). Two classes of text
inside ``raw_sql`` look like SQL keywords but are NOT executed by the engine:

1. **Comments** — ``-- line`` and ``/* block */``. The parser keeps any
   leading comments attached to the next statement's ``raw_sql``.
2. **String literals** — ``'foo'`` is payload data, not syntax. An
   ``INSERT`` whose VALUES clause stores the text ``'Ran DBCC SHRINKDATABASE
   on staging'`` must not fire the DBCC rule.

:func:`strip_sql_comments` and :func:`strip_sql_strings` each scrub one
class; :func:`strip_sql_noise` is the canonical "scrub everything" helper
that raw-SQL keyword-matching rules should call. Centralising the logic
keeps the rules consistent — every raw-SQL rule treats non-executable text
the same way.
"""

from __future__ import annotations

import re

_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Universal SQL single-quoted string literal with the standard `''` escape
# (e.g. `'it''s'`). Matches across all dialects we ship rules for. Does NOT
# attempt Postgres dollar-quoting ($$...$$) or MySQL backslash escapes
# (`'don\'t'`) — both are rare in migration scripts and would need
# dialect-aware parsing; documented in strip_sql_strings's docstring.
_SINGLE_QUOTED_STRING_RE = re.compile(r"'(?:[^']|'')*'")


def strip_sql_comments(sql: str) -> str:
    """Return ``sql`` with ``-- line`` and ``/* block */`` comments removed.

    Block comments collapse to a single space so identifiers that hugged a
    ``/*...*/`` block on either side don't get glued together; line comments
    are deleted outright (the trailing newline is preserved by the regex).
    Line numbers within the returned text are preserved so downstream
    formatters can still point at the right source line.
    """
    no_block = _BLOCK_COMMENT_RE.sub(" ", sql)
    return _LINE_COMMENT_RE.sub("", no_block)


def strip_sql_strings(sql: str) -> str:
    """Blank out single-quoted SQL string literals (with ``''`` escapes).

    String literals are payload data, not syntax, so any SQL keyword that
    appears inside one is non-executable and must not fire a keyword-
    matching rule. Without this scrub, statements like ::

        INSERT INTO audit_log (msg) VALUES
            ('Ran DBCC SHRINKDATABASE on staging last night');

    would wrongly fire the DBCC rule at HIGH severity against the audit-log
    text.

    Newlines inside multi-line literals are preserved so character / line
    offsets in the scrubbed text stay aligned with the original.

    Limitations (accepted for now): does NOT handle Postgres dollar-quoted
    strings (``$$...$$``) or MySQL backslash escapes inside single quotes
    (``'don\\'t'``). Both are rare in migration scripts and would need
    dialect-aware parsing; if a real-world false positive surfaces from
    either form we'll extend this helper. Bracket / backtick quoting is
    deliberately untouched — those are identifiers, not strings.
    """
    return _SINGLE_QUOTED_STRING_RE.sub(
        lambda m: "\n" * m.group(0).count("\n") or " ",
        sql,
    )


def strip_sql_noise(sql: str) -> str:
    """Strip both comments AND string literals — the canonical scrub for
    raw-SQL keyword-matching rules.

    Equivalent to ``strip_sql_strings(strip_sql_comments(sql))``. Comments
    are stripped first so an ``-- line comment`` that itself contains a
    quote doesn't confuse the string-literal regex. Any rule that searches
    ``stmt.raw_sql`` for SQL keywords (``DBCC``, ``DROP INDEX``, ...)
    should call this function rather than ``strip_sql_comments`` alone —
    both classes of non-executable text need to be removed to avoid
    false-positive findings.
    """
    return strip_sql_strings(strip_sql_comments(sql))
