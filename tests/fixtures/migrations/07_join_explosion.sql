-- Join-risk fixture. Exercises every rule in `joins.py`:
--   HIGH    joins/missing-on-clause       (implicit JOIN with no ON)
--   MEDIUM  joins/function-on-key         (UPPER() in ON predicate)
--   LOW     joins/on-nullable-key         (join on column added NULL here)
--   MEDIUM  joins/many-to-many-risk       (app.customer 12.4M x app.event_log 47.2M
--                                          on non-key column `region_code`)

ALTER TABLE app.customer ADD region_code VARCHAR(10);
GO

SELECT c.customer_id, o.order_id
FROM   app.customer c
JOIN   app.staging_inbox o;
GO

SELECT c.customer_id, o.order_id
FROM   app.customer c
JOIN   app.staging_inbox o
  ON   UPPER(c.email) = LOWER(o.contact_email);
GO

SELECT c.customer_id, o.order_id
FROM   app.customer c
JOIN   app.staging_inbox o
  ON   c.region_code = o.region_code;
GO

SELECT c.customer_id, e.event_id
FROM   app.customer c
JOIN   app.event_log e
  ON   c.region_code = e.region_code;
GO
