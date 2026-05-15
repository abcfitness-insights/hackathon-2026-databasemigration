-- Cross-rule false-positive sweep for the comment + string-literal scrub.
--
-- Every statement here is a legitimate INSERT into an audit-log table whose
-- VALUES happen to mention a SQL keyword as plain text. With strip_sql_noise
-- routed through every raw-SQL keyword-matching rule, NONE of the
-- following rule_ids should fire on this file:
--
--   * idempotency/drop-without-if-exists
--   * idempotency/create-table-without-if-not-exists
--   * idempotency/alter-add-column-without-guard
--   * compatibility/merge-on-synapse
--   * compatibility/dbcc-command
--   * data-loss/truncate
--   * permissions/grant-or-deny
--   * rollback/no-down-script
--   * rollback/index-drop-without-recreate
--   * locking/create-index-without-online
--   * mysql/utf8-not-utf8mb4
--
-- Wrapped in BEGIN TRAN to silence transaction/missing-tran-wrapper so the
-- test can simply assert "no fixed-rule findings exist".

BEGIN TRY
BEGIN TRANSACTION;

-- idempotency/drop-without-if-exists trigger inside a literal.
INSERT INTO app.audit_log (event_type, message)
VALUES ('change-log', 'Discussed DROP TABLE app.legacy_thing but rejected');

-- compatibility/merge-on-synapse trigger inside a literal.
INSERT INTO app.audit_log (event_type, message)
VALUES ('design-doc', 'Originally planned MERGE INTO app.customer pattern');

-- compatibility/dbcc-command trigger inside a literal (HIGH severity if fires).
INSERT INTO app.audit_log (event_type, message)
VALUES ('maintenance', 'Ran DBCC SHRINKDATABASE on staging last night');

-- data-loss/truncate trigger inside a literal (HIGH severity if fires).
INSERT INTO app.audit_log (event_type, message)
VALUES ('post-mortem', 'Junior engineer ran TRUNCATE TABLE staging by accident');

-- permissions/grant-or-deny trigger inside a literal.
INSERT INTO app.audit_log (event_type, message)
VALUES ('access-review', 'Reviewed GRANT to svc_user — confirmed least-priv');

-- rollback/no-down-script & rollback/index-drop-without-recreate triggers
-- inside literals. No real DROP / TRUNCATE / DROP INDEX is present in this
-- file, so neither rule should fire.
INSERT INTO app.audit_log (event_type, message)
VALUES ('design', 'Considered DROP TABLE then chose archive-rename');
INSERT INTO app.audit_log (event_type, message)
VALUES ('change-log', 'Note: DROP INDEX ix_phantom ON app.customer was avoided');

-- mysql/utf8-not-utf8mb4 trigger inside a literal (also covered by the
-- dialect filter; with --dialect mysql, the literal must still be ignored).
INSERT INTO app.audit_log (event_type, message)
VALUES ('design', 'Migrated tables from CHARACTER SET utf8 to utf8mb4 in 2023');

-- Same keyword inside a /* block comment */ — must also be ignored.
/* Reminder: do NOT regress CHARACTER SET utf8 anywhere in this repo. */
INSERT INTO app.audit_log (event_type, message)
VALUES ('reminder', 'See block comment above for the utf8 policy');

COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
