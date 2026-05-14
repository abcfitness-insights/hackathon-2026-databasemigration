-- Two problems in one migration:
-- 1. DROP without IF EXISTS guard -> migration fails on rerun (idempotency)
-- 2. DROP TABLE on a customer-data table -> irreversible data loss (rollback)
-- Expected MigGuard output:
--   HIGH   data_loss/drop-table on line 8
--   MEDIUM idempotency/missing-if-exists on line 8
--   MEDIUM rollback/no-down-script on line 8

DROP TABLE app.legacy_archive;
GO

DROP INDEX IX_customer_email ON app.customer;
GO
