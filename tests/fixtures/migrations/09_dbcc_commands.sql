-- DBCC fixture. Exercises `compatibility/dbcc-command` at both severities:
--   MEDIUM  DBCC CHECKDB                          (diagnostic — not for migrations)
--   MEDIUM  DBCC OPENTRAN                         (diagnostic — not for migrations)
--   HIGH    DBCC SHRINKDATABASE                   (causes severe fragmentation)
--   HIGH    DBCC CHECKDB ... REPAIR_ALLOW_DATA_LOSS (can delete data)

DBCC CHECKDB ('AppDb');
GO

DBCC OPENTRAN;
GO

DBCC SHRINKDATABASE ('AppDb', 10);
GO

DBCC CHECKDB ('AppDb', REPAIR_ALLOW_DATA_LOSS);
GO
