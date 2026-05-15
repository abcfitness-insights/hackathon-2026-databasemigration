-- Cross-rule false-negative sweep for the comment + string-literal scrub.
--
-- Each real operation below LACKS its safety guard, but the guard keyword
-- is mentioned only in a comment or string literal. The relevant rule must
-- still fire — comment text is not a real guard.
--
-- Wrapped in BEGIN TRAN to silence transaction/missing-tran-wrapper.

BEGIN TRY
BEGIN TRANSACTION;

-- idempotency/drop-without-if-exists: comment mentions IF EXISTS, but the
-- real DROP has no guard. Rule MUST fire.
-- TODO: someday wrap this in IF EXISTS for safety
DROP TABLE app.legacy_thing;

-- idempotency/alter-add-column-without-guard: comment mentions sys.columns,
-- but the real ALTER has no guard. Rule MUST fire on T-SQL.
-- We should add a sys.columns guard around this once we have time
ALTER TABLE app.customer ADD legacy_flag BIT NULL;

-- locking/create-index-without-online: comment mentions ONLINE = ON, but
-- the real CREATE INDEX doesn't use it. Rule MUST fire on T-SQL.
-- Consider ONLINE = ON for the prod rollout
CREATE INDEX ix_customer_email_v2 ON app.customer (email);

COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
