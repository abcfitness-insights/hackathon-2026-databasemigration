-- BUG: this references app.audit_log, which V005 just dropped.
-- The lifetime rule must flag this with `lifetime/dropped-table-referenced`.
SELECT id FROM app.audit_log WHERE id = 1;
