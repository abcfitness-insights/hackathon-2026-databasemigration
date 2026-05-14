-- Single-file lifetime bug: DROP a column, then reference it later in the
-- same script. Replaying this against a fresh database would fail on the
-- UPDATE. The lifetime rule must flag the UPDATE with
-- `lifetime/dropped-column-referenced`.
ALTER TABLE app.customer DROP COLUMN status_code;

UPDATE app.customer SET status_code = 'A' WHERE id < 1000;
