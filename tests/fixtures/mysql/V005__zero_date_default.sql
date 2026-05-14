-- Should fire: mysql/zero-date-default
-- '0000-00-00 00:00:00' is rejected by MySQL 5.7+ STRICT mode.

ALTER TABLE app.customer
    ADD updated_at DATETIME NOT NULL DEFAULT '0000-00-00 00:00:00',
    ALGORITHM=INSTANT, LOCK=NONE;
