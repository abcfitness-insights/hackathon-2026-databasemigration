-- Reads status_code AFTER V001 added it. This is safe and should NOT trigger
-- the lifetime rule.
UPDATE app.customer SET status_code = 'A' WHERE id < 100;
