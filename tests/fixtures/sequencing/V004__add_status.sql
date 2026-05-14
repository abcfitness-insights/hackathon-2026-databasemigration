-- Gap! V003 is missing -- should produce a sequencing/version-gap finding.
ALTER TABLE app.customer ADD status NVARCHAR(16) NULL;
GO
