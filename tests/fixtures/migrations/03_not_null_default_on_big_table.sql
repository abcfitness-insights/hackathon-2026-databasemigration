-- Subtle locking risk: NOT NULL + DEFAULT on a large table.
-- On Synapse and older SQL Server this rewrites the entire table
-- and holds a SCH-M lock, blocking all readers.
-- Expected MigGuard output:
--   HIGH locking/not-null-default-large-table on line 6
--   Schema context: "app.customer has 12.4M rows, 3 indexes"
--   Suggestion: split into ADD NULL -> backfill -> ALTER COLUMN NOT NULL

ALTER TABLE app.customer
    ADD priority_band VARCHAR(20) NOT NULL DEFAULT 'standard';
GO
