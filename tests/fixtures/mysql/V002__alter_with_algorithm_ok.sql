-- Should NOT fire: explicit ALGORITHM + LOCK clauses present.

ALTER TABLE app.customer
    ADD email VARCHAR(255),
    ALGORITHM=INSTANT, LOCK=NONE;
