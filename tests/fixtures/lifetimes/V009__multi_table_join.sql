-- Multi-table join with an UNQUALIFIED column reference that happens to share
-- a name with a dropped column. The rule must NOT flag this -- in joins with
-- multiple tables, the unqualified column could belong to either table, so we
-- skip to avoid false positives.
SELECT id FROM app.customer c JOIN app.region r ON c.region_id = r.id;
