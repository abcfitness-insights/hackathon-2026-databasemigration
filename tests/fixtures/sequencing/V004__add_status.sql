-- Gap! V003 is missing -- should produce a sequencing/version-gap finding.
ALTER TABLE dw.member ADD status NVARCHAR(16) NULL;
GO
