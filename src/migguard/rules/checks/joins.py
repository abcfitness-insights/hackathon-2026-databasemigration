"""Join-risk rules.

Catches join patterns that frequently cause query plans to explode or produce
silently-wrong results:

- ``joins/missing-on-clause``    (HIGH)   -- implicit cartesian product
- ``joins/many-to-many-risk``    (MEDIUM) -- both sides large + non-key columns
- ``joins/on-nullable-key``      (LOW)    -- join on column added NULL in this script
- ``joins/function-on-key``      (MEDIUM) -- function call on a join key kills index usage
"""

from __future__ import annotations

import re

from sqlglot import exp

from migguard.core.models import Category, Finding, Severity
from migguard.core.parser import ParsedScript
from migguard.rules.base import Rule, RuleContext

# Names that look like primary-key / foreign-key identifiers. We assume joins
# on these columns are intentional 1-to-many lookups, not many-to-many.
_KEY_LIKE_RE = re.compile(r"(?i)^(id|.+_id|.*_pk|pk_.+|.*_key|key_.+|.*_uid|uid_.+)$")

# Function-call node types in the sqlglot AST. We treat anything inheriting
# from exp.Func as a function, plus a small set of named expressions that are
# their own AST node types in some sqlglot versions (Cast, Coalesce, etc.).
_FUNCTION_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Func,
    exp.Cast,
    exp.Coalesce,
    exp.Upper,
    exp.Lower,
    exp.Substring,
    exp.Trim,
)


def _alias_map(node: exp.Expression) -> dict[str, tuple[str | None, str]]:
    """Map alias-or-name -> (schema, table) for every Table in a query AST."""
    out: dict[str, tuple[str | None, str]] = {}
    for tbl in node.find_all(exp.Table):
        alias = (tbl.alias_or_name or tbl.name or "").lower()
        if alias:
            out[alias] = (tbl.db or None, tbl.name or "")
    return out


def _column_table(
    col: exp.Column, alias_map: dict[str, tuple[str | None, str]]
) -> tuple[str | None, str | None]:
    """Resolve a column to (schema, table) using a SELECT's alias map. Returns
    (None, None) for unqualified columns when the binding is ambiguous."""
    qual = col.args.get("table")
    if qual is None:
        return None, None
    name = getattr(qual, "name", None) or str(qual)
    return alias_map.get(name.lower(), (None, None))


def _joined_table_name(join: exp.Join) -> str:
    """Best-effort name for the right side of a JOIN, for error messages."""
    target = join.args.get("this")
    if isinstance(target, exp.Table) and target.name:
        return target.name
    if target is not None:
        inner = target.find(exp.Table)
        if inner is not None and inner.name:
            return inner.name
    return "<table>"


class JoinMissingOnRule(Rule):
    """Implicit cartesian product — a JOIN with no ON / USING predicate.

    Includes both ``A JOIN B`` (no ON) and the explicit ``A CROSS JOIN B``
    form, since both have the same risk profile: result row count is the
    product of the two sides. CROSS JOIN is at least intentional, so it gets
    a MEDIUM-severity variant of the same finding; implicit cartesians are
    HIGH.
    """

    rule_id = "joins/missing-on-clause"
    title = "JOIN without ON clause (cartesian risk)"
    category = Category.PERFORMANCE
    severity = Severity.HIGH

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            ast = stmt.ast
            if ast is None:
                continue
            for join in ast.find_all(exp.Join):
                kind = (join.args.get("kind") or "").upper()
                has_on = join.args.get("on") is not None
                has_using = join.args.get("using") is not None
                if has_on or has_using:
                    continue
                rhs = _joined_table_name(join)
                if kind == "CROSS":
                    sev = Severity.MEDIUM
                    msg = (
                        f"Explicit `CROSS JOIN` against `{rhs}` multiplies the row "
                        "count of the left side by the row count of the right side."
                    )
                    suggestion = (
                        "Confirm the cartesian is intentional. If a key relationship "
                        "exists, prefer `INNER JOIN ... ON ...` so the optimizer can "
                        "use an index."
                    )
                else:
                    sev = Severity.HIGH
                    msg = (
                        f"`JOIN {rhs}` has no `ON` or `USING` clause. This is an "
                        "implicit cartesian product — every row on the left is paired "
                        "with every row on the right."
                    )
                    suggestion = (
                        "Add an `ON` predicate joining the two sides on their key "
                        "columns. If a cartesian is genuinely intended, use the "
                        "explicit `CROSS JOIN` keyword so the intent is documented."
                    )
                out.append(
                    self.make_finding(
                        stmt,
                        script,
                        severity=sev,
                        message=msg,
                        suggestion=suggestion,
                    )
                )
        return out


class JoinOnNullableKeyRule(Rule):
    """JOIN on a column that was added as nullable in this same migration.

    Nullable join keys silently drop rows on inner joins (``NULL != NULL``) and
    can produce surprising results on outer joins. This rule fires only when a
    column with no ``NOT NULL`` constraint is introduced earlier in the same
    script and a later ``JOIN ... ON`` uses that column — zero false positives
    against pre-existing schema we know nothing about.
    """

    rule_id = "joins/on-nullable-key"
    title = "JOIN on a nullable column"
    category = Category.PERFORMANCE
    severity = Severity.LOW

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        nullable: set[tuple[str, str]] = set()
        for stmt in script.statements:
            ast = stmt.ast
            if not isinstance(ast, exp.Alter):
                continue
            tbl_node = ast.find(exp.Table)
            if tbl_node is None or not tbl_node.name:
                continue
            tbl_name = tbl_node.name.lower()
            for action in ast.args.get("actions") or []:
                if not isinstance(action, exp.ColumnDef):
                    continue
                col_target = action.args.get("this")
                col_name = getattr(col_target, "name", None) or ""
                if not col_name:
                    continue
                constraints = action.args.get("constraints") or []
                has_not_null = any(
                    isinstance(c, exp.ColumnConstraint)
                    and isinstance(
                        c.args.get("kind"), exp.NotNullColumnConstraint
                    )
                    for c in constraints
                )
                if not has_not_null:
                    nullable.add((tbl_name, col_name.lower()))

        if not nullable:
            return []

        out: list[Finding] = []
        seen: set[tuple[int, str, str]] = set()  # dedup per (stmt, table, col)
        for stmt in script.statements:
            ast = stmt.ast
            if ast is None:
                continue
            # alias_map is invariant across joins within the same AST, so
            # compute it once per statement (matches ManyToManyJoinRule).
            aliases = _alias_map(ast)
            for join in ast.find_all(exp.Join):
                on = join.args.get("on")
                if on is None:
                    continue
                for col in on.find_all(exp.Column):
                    col_name = (col.name or "").lower()
                    if not col_name:
                        continue
                    _, table = _column_table(col, aliases)
                    if not table:
                        continue
                    key = (table.lower(), col_name)
                    if key not in nullable:
                        continue
                    dedup = (stmt.index, *key)
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    out.append(
                        self.make_finding(
                            stmt,
                            script,
                            message=(
                                f"JOIN uses column `{table}.{col_name}` which was "
                                "added as nullable in this same migration. NULL "
                                "join keys silently drop rows on inner joins and "
                                "produce surprising results on outer joins."
                            ),
                            suggestion=(
                                "Either backfill non-NULL values and add a `NOT "
                                "NULL` constraint before any join uses the column, "
                                "or handle NULLs explicitly in the predicate "
                                "(`ISNULL(...)`, `IS NULL`)."
                            ),
                        )
                    )
        return out


class JoinFunctionOnKeyRule(Rule):
    """Function call in a JOIN's ON predicate.

    Wrapping a column in ``UPPER(...)`` / ``LOWER(...)`` / ``ISNULL(...)`` /
    ``CAST(...)`` inside an ``ON`` predicate prevents the optimizer from
    using an index seek — it has to evaluate the function for every row,
    forcing a full scan. The classic "this query suddenly got 100x slower"
    pattern.
    """

    rule_id = "joins/function-on-key"
    title = "Function call on JOIN key"
    category = Category.PERFORMANCE
    severity = Severity.MEDIUM

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            ast = stmt.ast
            if ast is None:
                continue
            for join in ast.find_all(exp.Join):
                on = join.args.get("on")
                if on is None:
                    continue
                fns = list(on.find_all(*_FUNCTION_NODE_TYPES))
                if not fns:
                    continue
                names = sorted({type(f).__name__.upper() for f in fns})
                out.append(
                    self.make_finding(
                        stmt,
                        script,
                        message=(
                            f"JOIN `ON` predicate contains function call(s): "
                            f"{', '.join(names)}. Wrapping a column in a function "
                            "prevents an index seek and forces a full scan."
                        ),
                        suggestion=(
                            "Pre-compute the function value into a persisted / "
                            "indexed computed column, or normalize the data at "
                            "write time so the join can compare bare columns."
                        ),
                    )
                )
        return out


class ManyToManyJoinRule(Rule):
    """Heuristic many-to-many-risk detector.

    Fires when a JOIN's ``ON`` predicate is a single equality between two
    columns where:

    - Both columns are *not* named like a primary-key identifier
      (``id`` / ``*_id`` / ``pk_*`` / ``*_key``).
    - Both joined tables are present in the schema-facts snapshot with row
      counts >= 1M.

    The combination of "large" + "non-key column" is the classic recipe for an
    accidental cartesian-style row explosion (e.g. joining two fact tables on
    a non-unique business attribute). Schema-grounded so it only fires when we
    have evidence; zero false positives against unknown tables.
    """

    rule_id = "joins/many-to-many-risk"
    title = "Possible many-to-many JOIN on non-key columns"
    category = Category.PERFORMANCE
    severity = Severity.MEDIUM

    def check(self, script: ParsedScript, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for stmt in script.statements:
            ast = stmt.ast
            if ast is None:
                continue
            aliases = _alias_map(ast)
            for join in ast.find_all(exp.Join):
                on = join.args.get("on")
                if not isinstance(on, exp.EQ):
                    continue
                left = on.args.get("this")
                right = on.args.get("expression")
                if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                    continue
                left_col = (left.name or "").lower()
                right_col = (right.name or "").lower()
                if not left_col or not right_col:
                    continue
                if _KEY_LIKE_RE.match(left_col) or _KEY_LIKE_RE.match(right_col):
                    continue
                left_tbl = _column_table(left, aliases)
                right_tbl = _column_table(right, aliases)
                # fact_for() already accepts a None schema (it defaults to
                # the dialect's implicit schema -- "dbo" for T-SQL) and
                # returns None when the table name is missing. Guarding
                # with `all(left_tbl)` here would suppress legitimate
                # lookups for unqualified references like `FROM customer c`,
                # making the rule silently no-op on the common case. Match
                # the pattern used by every other schema-grounded rule
                # (data_loss, locking, mysql_rules) and call fact_for
                # directly.
                left_facts = ctx.fact_for(*left_tbl)
                right_facts = ctx.fact_for(*right_tbl)
                if not (left_facts and right_facts):
                    continue
                if not (left_facts.is_large and right_facts.is_large):
                    continue
                out.append(
                    self.make_finding(
                        stmt,
                        script,
                        schema_context=(
                            f"{left_facts.schema}.{left_facts.name} has "
                            f"~{left_facts.row_count:,} rows; "
                            f"{right_facts.schema}.{right_facts.name} has "
                            f"~{right_facts.row_count:,} rows."
                        ),
                        message=(
                            f"JOIN on `{left_col}` = `{right_col}` connects two "
                            "large tables on non-key columns. If either side has "
                            "duplicate values for the join column, the result row "
                            "count multiplies — classic many-to-many explosion."
                        ),
                        suggestion=(
                            "Confirm at least one side is unique on the join column "
                            "(PK or `UNIQUE` index). If not, aggregate / `DISTINCT` "
                            "before joining, or join on the primary key instead."
                        ),
                    )
                )
        return out
