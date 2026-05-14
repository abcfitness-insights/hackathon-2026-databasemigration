-- Clean migration: NULLable column add, idempotent, transactional.
-- Expected MigGuard output: 0 findings (or INFO-only).

BEGIN TRY
    BEGIN TRANSACTION;

    IF NOT EXISTS (
        SELECT 1
        FROM   sys.columns
        WHERE  object_id = OBJECT_ID(N'app.customer')
        AND    name = N'preferred_contact_method'
    )
    BEGIN
        ALTER TABLE app.customer
            ADD preferred_contact_method NVARCHAR(32) NULL;
    END

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
