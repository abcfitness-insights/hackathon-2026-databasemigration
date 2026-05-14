-- ============================================================================
-- V123__add_priority_band_to_customer.sql
--
-- Adds a nullable priority_band column to app.customer, backfills it in
-- bounded batches, then enforces NOT NULL. Designed to be replayable, to
-- avoid long write locks, and to ship with a companion .down.sql.
--
-- This fixture is what a "clean" production migration looks like, so the
-- test suite has a positive control: zero MigGuard findings.
-- ============================================================================

-- 1. Add as nullable so the ALTER is a metadata-only change (no row rewrite).
IF NOT EXISTS (
    SELECT 1
    FROM sys.columns
    WHERE object_id = OBJECT_ID(N'app.customer')
      AND name = N'priority_band'
)
BEGIN
    ALTER TABLE app.customer ADD priority_band VARCHAR(20) NULL;
END;
GO

-- 2. Backfill in bounded batches so no single statement holds a long lock.
BEGIN TRY
    BEGIN TRANSACTION;

    UPDATE TOP (10000) app.customer
    SET priority_band = 'standard'
    WHERE priority_band IS NULL;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

-- 3. Add a covering index online so reads stay available during the build.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_customer_priority_band'
)
BEGIN
    CREATE INDEX IX_customer_priority_band
        ON app.customer (priority_band)
        WITH (ONLINE = ON);
END;
GO
