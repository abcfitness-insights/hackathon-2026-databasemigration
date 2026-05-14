-- Should produce ZERO findings. Positive control for the MySQL rule pack:
-- explicit ALGORITHM hint, utf8mb4, NULLable timestamp (no zero-date).

ALTER TABLE app.customer
    ADD priority_band VARCHAR(20) NULL,
    ALGORITHM=INSTANT, LOCK=NONE;

CREATE TABLE app.message (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    body TEXT NOT NULL,
    sent_at DATETIME NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;
