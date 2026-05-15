-- Comment-handling regression fixture for rollback/index-drop-without-recreate.
--
-- Three statements, three independent scenarios. The rule must look at the
-- SQL only, not the surrounding prose.

-- Scenario A — comment mentions a future CREATE INDEX, but the only real SQL
-- here is a DROP. The rule MUST still flag the DROP.
--   TODO(team): once we replace the lookup table, do
--   CREATE INDEX ix_alpha ON app.customer (status);
DROP INDEX ix_alpha ON app.customer;
GO

-- Scenario B — comment contains a reverted DROP INDEX. The real SQL is a
-- harmless CREATE TABLE. The rule MUST NOT fire on the prose.
--   We considered DROP INDEX ix_beta ON app.event_log here but pulled it
--   out after code review.
CREATE TABLE app.placeholder_b (id INT PRIMARY KEY);
GO

-- Scenario C — block-comment variant of the false positive: a multi-line
-- /* ... */ block describing what we *didn't* do. The rule MUST NOT fire.
/*
   Earlier draft of this migration included:
       DROP INDEX ix_gamma ON app.event_log;
   We removed it because the index is still needed by the nightly report.
*/
CREATE TABLE app.placeholder_c (id INT PRIMARY KEY);
GO
