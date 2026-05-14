-- Should fire: mysql/alter-table-without-algorithm
-- ALTER TABLE on a known-large table (app.customer ~ 12.4M rows) with no
-- ALGORITHM= hint. MySQL may pick ALGORITHM=COPY and rewrite the table.

ALTER TABLE app.customer ADD email VARCHAR(255);
