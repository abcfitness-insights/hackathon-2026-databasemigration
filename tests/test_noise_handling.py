"""Cross-rule regression suite for the comment + string-literal scrub.

Every rule that searches ``stmt.raw_sql`` for SQL keywords should route the
text through :func:`migguard.rules._sql_text.strip_sql_noise` first. That
function removes two classes of non-executable text:

* SQL comments (``-- line``, ``/* block */``)
* Single-quoted string literals (with the universal ``''`` escape)

Without that scrub, a SQL keyword appearing inside an audit-log payload or
a TODO comment fires the rule as if it were real SQL.

This module covers both directions:

* **False positives** -- trigger keywords sitting inside string literals
  must NOT cause a finding. Fixture: ``13_noise_handling_false_positives.sql``.
* **False negatives** -- guard keywords sitting inside comments must NOT
  suppress a finding on a real (unguarded) operation. Fixture:
  ``14_noise_handling_false_negatives.sql``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from migguard.core.engine import Engine

FIXTURES = Path(__file__).parent / "fixtures" / "migrations"

# The rules that were routed through strip_sql_noise as part of the sweep.
# The false-positive fixture must produce ZERO findings from any of these.
SCRUBBED_RULE_IDS: frozenset[str] = frozenset(
    {
        "idempotency/drop-without-if-exists",
        "idempotency/create-table-without-if-not-exists",
        "idempotency/alter-add-column-without-guard",
        "compatibility/merge-on-synapse",
        "compatibility/dbcc-command",
        "data-loss/truncate",
        "permissions/grant-or-deny",
        "rollback/no-down-script",
        "rollback/index-drop-without-recreate",
        "locking/create-index-without-online",
        "mysql/utf8-not-utf8mb4",
    }
)


@pytest.fixture
def tsql_engine() -> Engine:
    return Engine(llm=None, dialect="tsql")


@pytest.fixture
def mysql_engine() -> Engine:
    return Engine(llm=None, dialect="mysql")


def _findings(engine: Engine, fixture: str):
    return engine.review([FIXTURES / fixture]).findings


# ---- False-positive sweep ---------------------------------------------------


def test_no_false_positives_from_keywords_in_string_literals_tsql(
    tsql_engine: Engine,
) -> None:
    """No T-SQL keyword-matching rule should fire on a file whose only SQL
    keyword occurrences are inside single-quoted string literals or block
    comments. The fixture is wrapped in BEGIN TRAN so the transaction
    rule doesn't muddy the signal."""
    findings = _findings(tsql_engine, "13_noise_handling_false_positives.sql")
    misfires = [f for f in findings if f.rule_id in SCRUBBED_RULE_IDS]
    assert not misfires, (
        "string-literal / comment text triggered a keyword rule: "
        + ", ".join(f"{f.rule_id} (line {f.location.line_start})" for f in misfires)
    )


def test_no_false_positives_from_keywords_in_string_literals_mysql(
    mysql_engine: Engine,
) -> None:
    """Same fixture re-run under --dialect mysql so the
    ``mysql/utf8-not-utf8mb4`` rule is active. The literal ``CHARACTER SET
    utf8`` inside an audit-log message and inside a block comment must
    not trigger the rule."""
    findings = _findings(mysql_engine, "13_noise_handling_false_positives.sql")
    utf8 = [f for f in findings if f.rule_id == "mysql/utf8-not-utf8mb4"]
    assert not utf8, (
        "mysql/utf8-not-utf8mb4 fired on `CHARACTER SET utf8` text that "
        "lives only inside a string literal / block comment"
    )


# ---- False-negative sweep ---------------------------------------------------


def test_drop_without_if_exists_fires_when_guard_only_in_comment(
    tsql_engine: Engine,
) -> None:
    """A ``-- TODO ... IF EXISTS ...`` comment above a real ``DROP TABLE``
    must NOT count as a guard."""
    findings = _findings(tsql_engine, "14_noise_handling_false_negatives.sql")
    drops = [f for f in findings if f.rule_id == "idempotency/drop-without-if-exists"]
    assert drops, "real DROP without IF EXISTS must fire despite IF EXISTS in comment"
    # Sanity: the finding points at the actual DROP statement, not at the
    # comment / earlier statements.
    assert any(
        f.location.file.endswith("14_noise_handling_false_negatives.sql") for f in drops
    )


def test_alter_add_column_fires_when_guard_only_in_comment(
    tsql_engine: Engine,
) -> None:
    """A comment mentioning ``sys.columns`` above a real
    ``ALTER TABLE ... ADD`` must NOT suppress the idempotency finding."""
    findings = _findings(tsql_engine, "14_noise_handling_false_negatives.sql")
    alters = [
        f
        for f in findings
        if f.rule_id == "idempotency/alter-add-column-without-guard"
    ]
    assert alters, (
        "real ALTER TABLE ADD without sys.columns guard must fire even when "
        "sys.columns appears in a comment"
    )


def test_create_index_without_online_fires_when_keyword_only_in_comment(
    tsql_engine: Engine,
) -> None:
    """A comment mentioning ``ONLINE = ON`` above a real ``CREATE INDEX``
    must NOT suppress the locking finding."""
    findings = _findings(tsql_engine, "14_noise_handling_false_negatives.sql")
    ix = [f for f in findings if f.rule_id == "locking/create-index-without-online"]
    assert ix, (
        "real CREATE INDEX without ONLINE = ON must fire even when "
        "the keyword appears in a comment"
    )


# ---- YAML rules opt-in ------------------------------------------------------


def test_yaml_rule_scrub_noise_opt_in(tmp_path: Path) -> None:
    """User-defined YAML rules default to no scrubbing (backwards-compatible).
    Setting ``scrub_noise: true`` enables the same protection as built-in
    raw-SQL rules.

    Fixture: a single statement whose VALUES clause contains the trigger
    keyword in a string literal. With ``scrub_noise: false`` (default) the
    rule fires (literal match). With ``scrub_noise: true`` it stays quiet.
    """
    from migguard.rules.yaml_rules import load_yaml_rules

    rules_yaml = tmp_path / "rules.yaml"
    rules_yaml.write_text(
        """
version: 1
rules:
  - id: company/no-nolock-default
    title: NOLOCK forbidden (default scrub off)
    severity: medium
    category: locking
    pattern: '(?i)\\bWITH\\s*\\(\\s*NOLOCK\\s*\\)'
    message: NOLOCK reads dirty data.
    dialects: [tsql]
  - id: company/no-nolock-scrubbed
    title: NOLOCK forbidden (scrub on)
    severity: medium
    category: locking
    pattern: '(?i)\\bWITH\\s*\\(\\s*NOLOCK\\s*\\)'
    message: NOLOCK reads dirty data.
    dialects: [tsql]
    scrub_noise: true
""",
        encoding="utf-8",
    )

    sql = tmp_path / "test.sql"
    sql.write_text(
        "INSERT INTO app.audit_log (note) "
        "VALUES ('Avoid WITH (NOLOCK) in OLTP paths');\n",
        encoding="utf-8",
    )

    yaml_rules = load_yaml_rules(rules_yaml)
    assert len(yaml_rules) == 2
    from migguard.rules.registry import all_rules

    engine = Engine(rules=[*all_rules(), *yaml_rules], dialect="tsql", llm=None)
    findings = engine.review([sql]).findings

    default_hits = [f for f in findings if f.rule_id == "company/no-nolock-default"]
    scrubbed_hits = [f for f in findings if f.rule_id == "company/no-nolock-scrubbed"]
    assert default_hits, (
        "default (scrub_noise=false) rule must match literal text -- this "
        "preserves backwards-compatible behaviour for existing YAML packs"
    )
    assert not scrubbed_hits, (
        "scrub_noise=true rule must ignore the keyword when it only appears "
        "inside a string literal"
    )


def test_yaml_rule_rejects_non_boolean_scrub_noise(tmp_path: Path) -> None:
    """``scrub_noise`` must be a real boolean; strings like ``"yes"`` are
    rejected at load time so misconfigurations fail loud instead of
    silently behaving as the default."""
    from migguard.rules.yaml_rules import YamlRuleConfigError, load_yaml_rules

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
version: 1
rules:
  - id: company/x
    title: X
    severity: low
    category: naming
    pattern: 'foo'
    scrub_noise: "yes"
""",
        encoding="utf-8",
    )
    with pytest.raises(YamlRuleConfigError, match="scrub_noise"):
        load_yaml_rules(bad)
