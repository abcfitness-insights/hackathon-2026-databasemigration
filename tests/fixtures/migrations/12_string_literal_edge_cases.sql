-- String-literal regression fixture for raw-SQL keyword rules.
--
-- Real SQL keywords appearing inside string literals are payload data, not
-- syntax. None of these statements should fire the DBCC or
-- index-drop-without-recreate rules.

-- Scenario A — DBCC inside a string literal.
-- The DBCC rule MUST NOT fire (even at HIGH severity).
INSERT INTO app.audit_log (event_type, message)
VALUES ('maintenance', 'Ran DBCC SHRINKDATABASE on staging last night');
GO

-- Scenario B — DROP INDEX inside a string literal.
-- The index rule MUST NOT fire.
INSERT INTO app.audit_log (event_type, message)
VALUES ('change-log', 'Engineer note: DROP INDEX ix_phantom ON app.customer was avoided');
GO

-- Scenario C — escaped quote inside a string ('it''s'). Confirms the
-- universal SQL `''`-escape form is handled correctly: the string
-- spans the whole single-quoted region, so the DBCC inside it is hidden.
INSERT INTO app.audit_log (event_type, message)
VALUES ('post-mortem', 'It''s tempting to call DBCC CHECKDB here, but we don''t');
GO
