-- Index lifecycle fixture. Exercises `rollback/index-drop-without-recreate`.
--   MEDIUM  rollback/index-drop-without-recreate  (DROP INDEX ix_customer_email,
--                                                  not recreated anywhere)
--
-- The second pair (DROP + CREATE of ix_staging_status) should NOT fire the rule,
-- since the index is recreated in the same migration.

DROP INDEX ix_customer_email ON app.customer;
GO

DROP INDEX ix_staging_status ON app.staging_inbox;
GO

CREATE NONCLUSTERED INDEX ix_staging_status
    ON app.staging_inbox (status);
GO
