-- Postgres migration: CREATE INDEX without CONCURRENTLY blocks writes
-- on the base table for the build duration. (TSQL-specific rules like
-- "ONLINE = ON" and "BEGIN TRAN" don't apply here.)

DELETE FROM accounts;

CREATE INDEX idx_account_email ON accounts (email);
