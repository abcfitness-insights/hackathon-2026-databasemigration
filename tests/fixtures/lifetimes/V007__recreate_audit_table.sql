-- Re-creates app.audit_log after V005 dropped it.
-- The lifetime state should clear, so subsequent references are safe.
CREATE TABLE app.audit_log (id INT NOT NULL, customer_id INT NOT NULL);
