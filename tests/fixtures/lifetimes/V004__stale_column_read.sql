-- BUG: this references status_code, which V003 just dropped.
-- The lifetime rule must flag this with `lifetime/dropped-column-referenced`.
SELECT status_code FROM app.customer WHERE id = 1;
