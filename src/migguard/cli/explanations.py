"""Human-readable explanations for every emitted rule ID.

Powers ``migguard explain <rule-id>``. Each entry teaches the *why* behind a
finding — what's risky, when it fires, an example violation, and the
canonical safe pattern. The goal is for the tool to double as a learning
resource for engineers who haven't seen the pattern before.

Rule IDs in this module match the strings emitted in ``Finding.rule_id``.
Adding a new rule? Add an entry here in the same PR.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleExplanation:
    """Long-form documentation for a single emitted rule_id."""

    rule_id: str
    title: str
    severity: str
    category: str
    why: str
    bad_example: str
    good_example: str
    references: tuple[str, ...] = ()


EXPLANATIONS: dict[str, RuleExplanation] = {
    "data-loss/delete-without-where": RuleExplanation(
        rule_id="data-loss/delete-without-where",
        title="DELETE without WHERE",
        severity="HIGH",
        category="data_loss",
        why=(
            "A DELETE statement with no WHERE clause removes every row in the "
            "table. There is no easy recovery without a backup, and on large "
            "tables it can also hold long write locks while the deletion runs."
        ),
        bad_example="DELETE FROM app.customer;",
        good_example=(
            "DELETE FROM app.customer WHERE last_login_date < '2020-01-01';\n"
            "-- Or use a soft-delete pattern:\n"
            "UPDATE app.customer SET deleted_at = SYSUTCDATETIME() "
            "WHERE last_login_date < '2020-01-01';"
        ),
    ),
    "data-loss/update-without-where": RuleExplanation(
        rule_id="data-loss/update-without-where",
        title="UPDATE without WHERE",
        severity="HIGH",
        category="data_loss",
        why=(
            "An UPDATE with no WHERE clause rewrites every row in the table. "
            "On large tables this is also a long write-locking operation that "
            "blocks reads."
        ),
        bad_example="UPDATE app.customer SET status = 'inactive';",
        good_example=(
            "UPDATE app.customer SET status = 'inactive' "
            "WHERE last_login_date < DATEADD(year, -2, SYSUTCDATETIME());"
        ),
    ),
    "data-loss/truncate": RuleExplanation(
        rule_id="data-loss/truncate",
        title="TRUNCATE TABLE",
        severity="HIGH",
        category="data_loss",
        why=(
            "TRUNCATE empties a table instantly and cannot be rolled back in "
            "many DBMS configurations. If the target is a customer-facing "
            "table, this is data loss disguised as a 'fast clear'."
        ),
        bad_example="TRUNCATE TABLE app.customer;",
        good_example=(
            "-- For staging tables this is usually intentional. Wrap it in a guard:\n"
            "IF OBJECT_ID(N'app.staging_inbox') IS NOT NULL\n"
            "    TRUNCATE TABLE app.staging_inbox;"
        ),
    ),
    "data-loss/drop-table": RuleExplanation(
        rule_id="data-loss/drop-table",
        title="DROP TABLE",
        severity="HIGH",
        category="data_loss",
        why=(
            "DROP TABLE removes both the data and the schema. Even with a "
            "backup, restoring is a multi-step procedure that involves "
            "downtime. Always include a rollback / down script for DROP "
            "operations."
        ),
        bad_example="DROP TABLE app.legacy_archive;",
        good_example=(
            "-- 1. Keep a companion .down.sql with the CREATE statement.\n"
            "-- 2. Use IF EXISTS for idempotency:\n"
            "DROP TABLE IF EXISTS app.legacy_archive;"
        ),
    ),
    "data-loss/drop-schema": RuleExplanation(
        rule_id="data-loss/drop-schema",
        title="DROP SCHEMA",
        severity="HIGH",
        category="data_loss",
        why=(
            "DROP SCHEMA can cascade-drop every table in the schema. This is "
            "almost always wrong outside of test fixtures."
        ),
        bad_example="DROP SCHEMA app;",
        good_example=(
            "-- If you really mean to remove a schema, drop the tables in it "
            "first, explicitly, each with their own rollback script."
        ),
    ),
    "locking/not-null-default-on-large-table": RuleExplanation(
        rule_id="locking/not-null-default-on-large-table",
        title="ALTER TABLE ADD NOT NULL DEFAULT on a large table",
        severity="HIGH on large tables, MEDIUM otherwise",
        category="locking",
        why=(
            "Adding a NOT NULL column with a DEFAULT rewrites every row to "
            "populate the new column. On large tables this holds a SCH-M "
            "lock for minutes and blocks every reader."
        ),
        bad_example=(
            "ALTER TABLE app.customer\n"
            "    ADD priority_band VARCHAR(20) NOT NULL DEFAULT 'standard';"
        ),
        good_example=(
            "-- Three-step pattern, each step short and online-safe:\n"
            "ALTER TABLE app.customer ADD priority_band VARCHAR(20) NULL;\n"
            "UPDATE app.customer SET priority_band = 'standard' WHERE priority_band IS NULL;\n"
            "ALTER TABLE app.customer ALTER COLUMN priority_band VARCHAR(20) NOT NULL;"
        ),
    ),
    "locking/create-index-without-online": RuleExplanation(
        rule_id="locking/create-index-without-online",
        title="CREATE INDEX without ONLINE = ON (T-SQL)",
        severity="MEDIUM",
        category="locking",
        why=(
            "Building an index offline in SQL Server / Synapse takes a "
            "schema-modification lock for the duration of the build, "
            "blocking all readers and writers on the base table."
        ),
        bad_example="CREATE INDEX IX_customer_email ON app.customer (email);",
        good_example=(
            "CREATE INDEX IX_customer_email ON app.customer (email) "
            "WITH (ONLINE = ON);"
        ),
    ),
    "idempotency/drop-without-if-exists": RuleExplanation(
        rule_id="idempotency/drop-without-if-exists",
        title="DROP without IF EXISTS",
        severity="MEDIUM",
        category="idempotency",
        why=(
            "A DROP statement without an existence guard fails on rerun. "
            "Migrations should be safe to apply twice — partial failures, "
            "retries, and replay-on-fresh-DB scenarios should all succeed."
        ),
        bad_example="DROP TABLE app.legacy_archive;",
        good_example="DROP TABLE IF EXISTS app.legacy_archive;",
    ),
    "idempotency/create-table-without-if-not-exists": RuleExplanation(
        rule_id="idempotency/create-table-without-if-not-exists",
        title="CREATE TABLE without IF NOT EXISTS guard",
        severity="LOW",
        category="idempotency",
        why=(
            "A CREATE TABLE without a guard fails the second time the "
            "migration runs. This breaks retries and idempotent deploys."
        ),
        bad_example="CREATE TABLE app.customer (id INT PRIMARY KEY);",
        good_example=(
            "IF OBJECT_ID(N'app.customer', 'U') IS NULL\n"
            "    CREATE TABLE app.customer (id INT PRIMARY KEY);"
        ),
    ),
    "idempotency/alter-add-column-without-guard": RuleExplanation(
        rule_id="idempotency/alter-add-column-without-guard",
        title="ALTER TABLE ADD COLUMN without existence guard",
        severity="LOW",
        category="idempotency",
        why=(
            "T-SQL doesn't support IF NOT EXISTS on ALTER TABLE ADD. Without "
            "a sys.columns guard, rerunning the migration throws an error."
        ),
        bad_example="ALTER TABLE app.customer ADD priority_band VARCHAR(20) NULL;",
        good_example=(
            "IF NOT EXISTS (SELECT 1 FROM sys.columns\n"
            "               WHERE object_id = OBJECT_ID(N'app.customer')\n"
            "               AND name = N'priority_band')\n"
            "BEGIN\n"
            "    ALTER TABLE app.customer ADD priority_band VARCHAR(20) NULL;\n"
            "END"
        ),
    ),
    "compatibility/merge-on-synapse": RuleExplanation(
        rule_id="compatibility/merge-on-synapse",
        title="MERGE statement on Synapse",
        severity="MEDIUM",
        category="compatibility",
        why=(
            "Synapse Dedicated SQL Pools do not support MERGE. The migration "
            "looks fine on SQL Server but breaks in Synapse."
        ),
        bad_example=(
            "MERGE INTO app.customer AS target\n"
            "USING #incoming AS source ON target.id = source.id\n"
            "WHEN MATCHED THEN UPDATE SET email = source.email\n"
            "WHEN NOT MATCHED THEN INSERT (id, email) VALUES (source.id, source.email);"
        ),
        good_example=(
            "-- Decompose into UPDATE + INSERT-where-not-exists:\n"
            "UPDATE t SET email = s.email\n"
            "FROM app.customer t JOIN #incoming s ON t.id = s.id;\n"
            "INSERT INTO app.customer (id, email)\n"
            "SELECT s.id, s.email FROM #incoming s\n"
            "WHERE NOT EXISTS (SELECT 1 FROM app.customer t WHERE t.id = s.id);"
        ),
    ),
    "permissions/grant-or-deny": RuleExplanation(
        rule_id="permissions/grant-or-deny",
        title="GRANT / DENY / REVOKE in a migration",
        severity="MEDIUM (or HIGH for cross-schema GRANTs)",
        category="permissions",
        why=(
            "Permission changes inside a migration bypass the normal access-"
            "review process. They should be flagged for security review, "
            "even if the resulting permission is correct."
        ),
        bad_example="GRANT SELECT, UPDATE ON SCHEMA::app TO [analytics_reader];",
        good_example=(
            "-- Move permission changes to your IAM / access-review tool. "
            "If they must live in a migration, link the access-review ticket "
            "in a comment so reviewers can verify it was approved."
        ),
    ),
    "transaction/missing-tran-wrapper": RuleExplanation(
        rule_id="transaction/missing-tran-wrapper",
        title="DML migration without an explicit transaction",
        severity="LOW",
        category="transaction",
        why=(
            "Multi-statement DML migrations should run inside an explicit "
            "BEGIN TRAN / COMMIT block with TRY/CATCH error handling. "
            "Otherwise a mid-migration failure leaves the schema in a "
            "half-applied state."
        ),
        bad_example=(
            "UPDATE app.customer SET status = 'A' WHERE id < 1000;\n"
            "INSERT INTO app.audit_log (event) VALUES ('migration_x_applied');"
        ),
        good_example=(
            "BEGIN TRY\n"
            "    BEGIN TRANSACTION;\n"
            "    UPDATE app.customer SET status = 'A' WHERE id < 1000;\n"
            "    INSERT INTO app.audit_log (event) VALUES ('migration_x_applied');\n"
            "    COMMIT TRANSACTION;\n"
            "END TRY\n"
            "BEGIN CATCH\n"
            "    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;\n"
            "    THROW;\n"
            "END CATCH"
        ),
    ),
    "rollback/no-down-script": RuleExplanation(
        rule_id="rollback/no-down-script",
        title="No companion .down.sql rollback script",
        severity="MEDIUM",
        category="rollback",
        why=(
            "Irreversible operations (DROP, TRUNCATE) should ship with a "
            "rollback script next to the forward migration. Without one, "
            "incident response on a bad migration involves restoring from "
            "backup."
        ),
        bad_example="V042__drop_legacy.sql contains DROP TABLE app.legacy_archive;",
        good_example=(
            "V042__drop_legacy.sql       -- the forward migration\n"
            "V042__drop_legacy.down.sql  -- the rollback: CREATE TABLE app.legacy_archive (...);"
        ),
    ),
    "naming/non-snake-case": RuleExplanation(
        rule_id="naming/non-snake-case",
        title="Identifier is not snake_case",
        severity="LOW",
        category="naming",
        why=(
            "Mixing snake_case and CamelCase across migrations leads to "
            "case-sensitivity bugs (especially on Postgres) and inconsistent "
            "query code. Pick one style and enforce it at migration time."
        ),
        bad_example="CREATE TABLE app.CustomerBadge ( ... );",
        good_example="CREATE TABLE app.customer_badge ( ... );",
    ),
    "naming/too-long": RuleExplanation(
        rule_id="naming/too-long",
        title="Identifier exceeds 63 characters",
        severity="LOW",
        category="naming",
        why=(
            "Postgres truncates identifiers longer than 63 characters; many "
            "tools assume that limit. Use clear, abbreviated names instead "
            "of run-on descriptors."
        ),
        bad_example="ADD a_column_with_a_name_that_is_intentionally_far_too_long_for_postgres_yes VARCHAR(16) NULL;",
        good_example="ADD priority_band VARCHAR(16) NULL;",
    ),
    "naming/reserved-word": RuleExplanation(
        rule_id="naming/reserved-word",
        title="Identifier collides with a reserved SQL keyword",
        severity="MEDIUM",
        category="naming",
        why=(
            "Using reserved words like `user`, `order`, `select` as column "
            "or table names forces every query to quote them, which is easy "
            "to forget and produces confusing errors when missed."
        ),
        bad_example="CREATE TABLE app.foo ( [user] NVARCHAR(64) NOT NULL );",
        good_example="CREATE TABLE app.foo ( username NVARCHAR(64) NOT NULL );",
    ),
    "sequencing/version-gap": RuleExplanation(
        rule_id="sequencing/version-gap",
        title="Gap in migration version sequence",
        severity="MEDIUM",
        category="ordering",
        why=(
            "A jump from V003 to V005 (skipping V004) usually means either "
            "the missing version exists on main but isn't in your branch, "
            "or somebody picked a non-sequential number by accident. Either "
            "way, the production sequence will diverge once this merges."
        ),
        bad_example="V001__a.sql, V002__b.sql, V004__c.sql",
        good_example="V001__a.sql, V002__b.sql, V003__c.sql (or pull V003 from main first)",
    ),
    "sequencing/duplicate-version": RuleExplanation(
        rule_id="sequencing/duplicate-version",
        title="Two migrations share the same version number",
        severity="HIGH",
        category="ordering",
        why=(
            "Whichever V003 applies first wins; the others may be silently "
            "skipped or fail depending on the migration tool. This is a "
            "merge collision masquerading as a passing build."
        ),
        bad_example="V003__add_created_at.sql AND V003__add_updated_at.sql",
        good_example="V003__add_created_at.sql AND V004__add_updated_at.sql",
    ),
    "lifetime/dropped-table-referenced": RuleExplanation(
        rule_id="lifetime/dropped-table-referenced",
        title="References a table dropped earlier in this review",
        severity="HIGH",
        category="ordering",
        why=(
            "An earlier migration in the same review DROP'd this table, and "
            "no later migration recreates it. Replaying these migrations "
            "against a fresh database will fail at this statement."
        ),
        bad_example=(
            "V005__drop_audit.sql:  DROP TABLE app.audit_log;\n"
            "V006__bad.sql:         SELECT id FROM app.audit_log;"
        ),
        good_example=(
            "-- Either recreate the table before the reference, "
            "or remove the reference."
        ),
    ),
    "lifetime/dropped-column-referenced": RuleExplanation(
        rule_id="lifetime/dropped-column-referenced",
        title="References a column dropped earlier in this review",
        severity="HIGH",
        category="ordering",
        why=(
            "An earlier migration in the same review DROP'd this column, "
            "and no later migration re-adds it. Replaying these migrations "
            "against a fresh database will fail at this statement."
        ),
        bad_example=(
            "V003__drop.sql:  ALTER TABLE app.customer DROP COLUMN status_code;\n"
            "V004__bad.sql:   SELECT status_code FROM app.customer;"
        ),
        good_example=(
            "-- Either re-add the column before the reference, "
            "or update the reference to a column that still exists."
        ),
    ),
    "mysql/alter-table-without-algorithm": RuleExplanation(
        rule_id="mysql/alter-table-without-algorithm",
        title="ALTER TABLE without ALGORITHM hint (MySQL)",
        severity="MEDIUM (HIGH on large tables)",
        category="locking",
        why=(
            "MySQL/InnoDB picks an ALGORITHM automatically when none is "
            "specified. The default varies by operation type and server "
            "version, and some operations silently fall back to "
            "ALGORITHM=COPY, which rewrites the whole table and holds a "
            "metadata lock for the duration. Be explicit so reviewers and "
            "the storage engine agree on the plan."
        ),
        bad_example="ALTER TABLE app.customer ADD email VARCHAR(255);",
        good_example=(
            "ALTER TABLE app.customer\n"
            "    ADD email VARCHAR(255),\n"
            "    ALGORITHM=INSTANT, LOCK=NONE;\n"
            "-- For very large tables, run via pt-online-schema-change or gh-ost."
        ),
    ),
    "mysql/utf8-not-utf8mb4": RuleExplanation(
        rule_id="mysql/utf8-not-utf8mb4",
        title="CHARACTER SET utf8 is the 3-byte alias (MySQL)",
        severity="MEDIUM",
        category="compatibility",
        why=(
            "In MySQL, the name `utf8` is a historical alias for `utf8mb3`, "
            "which only encodes 1- to 3-byte UTF-8. That excludes all emoji "
            "and several CJK characters (4 bytes). On a customer-facing "
            "table this manifests as `1366 Incorrect string value` errors "
            "the first time someone enters an emoji."
        ),
        bad_example=(
            "CREATE TABLE app.message (\n"
            "    id BIGINT AUTO_INCREMENT PRIMARY KEY,\n"
            "    body TEXT\n"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8;"
        ),
        good_example=(
            "CREATE TABLE app.message (\n"
            "    id BIGINT AUTO_INCREMENT PRIMARY KEY,\n"
            "    body TEXT\n"
            ") ENGINE=InnoDB\n"
            "  DEFAULT CHARSET=utf8mb4\n"
            "  COLLATE=utf8mb4_unicode_ci;\n"
            "-- On MySQL 8.0+ prefer utf8mb4_0900_ai_ci."
        ),
    ),
    "mysql/zero-date-default": RuleExplanation(
        rule_id="mysql/zero-date-default",
        title="Zero-date default rejected by STRICT mode (MySQL)",
        severity="MEDIUM",
        category="compatibility",
        why=(
            "MySQL 5.7+ rejects `'0000-00-00'` and `'0000-00-00 00:00:00'` "
            "in STRICT mode (`STRICT_TRANS_TABLES` + `NO_ZERO_DATE`), which "
            "is on by default in modern installs. A migration that ships a "
            "zero-date will fail on any STRICT-mode server, even though it "
            "may have worked on an older one."
        ),
        bad_example=(
            "ALTER TABLE app.customer\n"
            "    ADD updated_at DATETIME NOT NULL DEFAULT '0000-00-00 00:00:00';"
        ),
        good_example=(
            "ALTER TABLE app.customer\n"
            "    ADD updated_at DATETIME NULL;\n"
            "-- Or use a real epoch like '1970-01-01 00:00:00' if you "
            "specifically need a non-null sentinel."
        ),
    ),
    "joins/missing-on-clause": RuleExplanation(
        rule_id="joins/missing-on-clause",
        title="JOIN without ON clause (cartesian risk)",
        severity="HIGH (MEDIUM on explicit CROSS JOIN)",
        category="performance",
        why=(
            "A JOIN with no ON predicate produces a Cartesian product -- "
            "every row on the left paired with every row on the right. "
            "Against real production tables this turns a one-second query "
            "into a billion-row scan that locks resources for hours. "
            "Explicit CROSS JOIN is at least intentional, so it gets "
            "MEDIUM; an implicit `A JOIN B` with no ON is HIGH."
        ),
        bad_example=(
            "SELECT c.customer_id, o.order_id\n"
            "FROM   app.customer c\n"
            "JOIN   app.staging_inbox o;   -- no ON clause"
        ),
        good_example=(
            "SELECT c.customer_id, o.order_id\n"
            "FROM   app.customer c\n"
            "JOIN   app.staging_inbox o\n"
            "  ON   o.customer_id = c.customer_id;"
        ),
    ),
    "joins/many-to-many-risk": RuleExplanation(
        rule_id="joins/many-to-many-risk",
        title="Possible many-to-many JOIN on non-key columns",
        severity="MEDIUM",
        category="performance",
        why=(
            "Joining two large tables on non-key columns is the classic "
            "recipe for accidental row explosion: if either side has "
            "duplicate values for the join column, the result row count "
            "multiplies. This rule only fires when both tables are "
            "confirmed large in the schema snapshot, so it doesn't false-"
            "positive on small lookup tables or against unknown schemas."
        ),
        bad_example=(
            "-- app.customer (12.4M rows) JOIN app.event_log (47.2M rows)\n"
            "-- on a non-key business attribute can explode catastrophically:\n"
            "SELECT c.customer_id, e.event_id\n"
            "FROM   app.customer c\n"
            "JOIN   app.event_log e ON c.region_code = e.region_code;"
        ),
        good_example=(
            "-- Pre-aggregate the larger side, or join on the primary key:\n"
            "SELECT c.customer_id, e.last_event\n"
            "FROM   app.customer c\n"
            "JOIN ( SELECT customer_id, MAX(occurred_at) AS last_event\n"
            "       FROM   app.event_log\n"
            "       GROUP BY customer_id ) e\n"
            "  ON   e.customer_id = c.customer_id;"
        ),
    ),
    "joins/on-nullable-key": RuleExplanation(
        rule_id="joins/on-nullable-key",
        title="JOIN on a column that's nullable in this migration",
        severity="LOW",
        category="performance",
        why=(
            "A nullable column used as a join key silently drops rows on "
            "inner joins (NULL != NULL in SQL three-valued logic) and "
            "produces surprising results on outer joins. The rule fires "
            "only when a nullable column is added earlier in the SAME "
            "script and used in a later JOIN -- so it's zero-false-"
            "positive against pre-existing schema we know nothing about."
        ),
        bad_example=(
            "ALTER TABLE app.customer ADD region_code VARCHAR(10) NULL;\n"
            "GO\n"
            "SELECT c.customer_id, o.order_id\n"
            "FROM   app.customer c\n"
            "JOIN   app.staging_inbox o\n"
            "  ON   c.region_code = o.region_code;  -- silently drops NULLs"
        ),
        good_example=(
            "-- Backfill non-NULLs and enforce NOT NULL before any join uses it:\n"
            "ALTER TABLE app.customer ADD region_code VARCHAR(10) NULL;\n"
            "UPDATE app.customer SET region_code = 'US' WHERE region_code IS NULL;\n"
            "ALTER TABLE app.customer ALTER COLUMN region_code VARCHAR(10) NOT NULL;\n"
            "-- Or handle NULL explicitly in the predicate:\n"
            "--   ON ISNULL(c.region_code,'') = ISNULL(o.region_code,'')"
        ),
    ),
    "joins/function-on-key": RuleExplanation(
        rule_id="joins/function-on-key",
        title="Function call inside a JOIN's ON predicate",
        severity="MEDIUM",
        category="performance",
        why=(
            "Wrapping a column in a function (UPPER, LOWER, CAST, "
            "COALESCE, SUBSTRING, ...) inside an ON predicate prevents "
            "the optimizer from using an index seek. The function has to "
            "be evaluated for every row, forcing a full scan. This is "
            "the classic 'the query suddenly got 100x slower' pattern."
        ),
        bad_example=(
            "SELECT c.customer_id, o.order_id\n"
            "FROM   app.customer c\n"
            "JOIN   app.staging_inbox o\n"
            "  ON   UPPER(c.email) = LOWER(o.contact_email);"
        ),
        good_example=(
            "-- Normalize at write time so the join can compare bare columns:\n"
            "ALTER TABLE app.customer       ADD email_normalized AS LOWER(email) PERSISTED;\n"
            "ALTER TABLE app.staging_inbox  ADD email_normalized AS LOWER(contact_email) PERSISTED;\n"
            "-- Then index and join on the persisted column:\n"
            "SELECT c.customer_id, o.order_id\n"
            "FROM   app.customer c\n"
            "JOIN   app.staging_inbox o\n"
            "  ON   c.email_normalized = o.email_normalized;"
        ),
    ),
    "rollback/index-drop-without-recreate": RuleExplanation(
        rule_id="rollback/index-drop-without-recreate",
        title="DROP INDEX without a matching CREATE INDEX",
        severity="MEDIUM",
        category="rollback",
        why=(
            "A DROP INDEX without a matching CREATE INDEX in the same "
            "migration permanently changes query plans -- every query "
            "that previously used the dropped index will now scan the "
            "base table. This is almost always an oversight. If the "
            "index is genuinely obsolete, leave a code-review comment "
            "explaining why so the next reviewer doesn't reintroduce it."
        ),
        bad_example=(
            "DROP INDEX ix_customer_email ON app.customer;\n"
            "-- no matching CREATE INDEX anywhere in the migration"
        ),
        good_example=(
            "-- If you mean to swap definitions, do both in the same migration:\n"
            "DROP INDEX ix_customer_email ON app.customer;\n"
            "GO\n"
            "CREATE NONCLUSTERED INDEX ix_customer_email\n"
            "    ON app.customer (email, status)\n"
            "    WITH (ONLINE = ON);"
        ),
    ),
    "compatibility/dbcc-command": RuleExplanation(
        rule_id="compatibility/dbcc-command",
        title="DBCC command inside a migration (T-SQL)",
        severity="MEDIUM (HIGH on SHRINK*, DROPCLEANBUFFERS, REPAIR_ALLOW_DATA_LOSS)",
        category="compatibility",
        why=(
            "DBCC is the T-SQL diagnostic / maintenance family (CHECKDB, "
            "SHRINKFILE, DROPCLEANBUFFERS, ...). These commands are meant "
            "to be run interactively by a DBA, not committed to source "
            "control. Several variants are actively dangerous in "
            "production: `SHRINK*` causes severe index fragmentation, "
            "`DROPCLEANBUFFERS` flushes the buffer cache, and `CHECKDB "
            "... REPAIR_ALLOW_DATA_LOSS` can permanently delete rows to "
            "make a corrupt database readable."
        ),
        bad_example=(
            "DBCC SHRINKDATABASE ('AppDb', 10);\n"
            "DBCC CHECKDB ('AppDb', REPAIR_ALLOW_DATA_LOSS);"
        ),
        good_example=(
            "-- Move DBCC out of the schema-change pipeline entirely.\n"
            "-- Schedule routine maintenance through your operations\n"
            "-- runbook. If a DBA needs to repair corruption, run REPAIR\n"
            "-- interactively in a maintenance window, never via the\n"
            "-- forward-migration deploy job."
        ),
    ),
}


def find_explanation(rule_id: str) -> RuleExplanation | None:
    """Exact-match lookup. Returns None if the rule_id isn't documented."""
    return EXPLANATIONS.get(rule_id)


def suggest_similar(rule_id: str, limit: int = 5) -> list[str]:
    """Cheap substring-based suggestions for a misspelled rule_id."""
    needle = rule_id.lower()
    keys = list(EXPLANATIONS.keys())
    scored: list[tuple[int, str]] = []
    for key in keys:
        score = 0
        if needle in key:
            score += 3
        # Score by shared prefix segments (e.g. "data-loss/")
        for needle_part in needle.replace("/", "-").split("-"):
            if needle_part and needle_part in key:
                score += 1
        if score:
            scored.append((score, key))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [k for _, k in scored[:limit]]
