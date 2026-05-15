-- Regression fixture for ManyToManyJoinRule's guard fix.
--
-- Tables referenced with no schema prefix (`FROM customer c JOIN event_log e ...`).
-- Under T-SQL semantics these resolve to dbo.customer / dbo.event_log. The
-- many-to-many heuristic should still consult the schema-facts snapshot --
-- the previous `all(left_tbl)` guard silently skipped it because the AST
-- reports schema=None for unqualified table refs.

INSERT INTO app.report_lookup (customer_email, event_summary)
SELECT c.email, e.payload
FROM customer c
JOIN event_log e
    ON c.region_code = e.region_code;
GO
