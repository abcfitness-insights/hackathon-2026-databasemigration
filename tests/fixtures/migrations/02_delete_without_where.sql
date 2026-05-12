-- Disaster scenario: DELETE without WHERE on a 47M-row table.
-- Expected MigGuard output: HIGH severity, data_loss/delete-without-where on line 5.

BEGIN TRANSACTION;

DELETE FROM dw.drdr_dmb;

COMMIT TRANSACTION;
GO
