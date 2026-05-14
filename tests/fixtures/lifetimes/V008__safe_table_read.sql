-- Safe: app.audit_log was recreated in V007. This should NOT fire the rule.
SELECT id FROM app.audit_log WHERE id = 1;
