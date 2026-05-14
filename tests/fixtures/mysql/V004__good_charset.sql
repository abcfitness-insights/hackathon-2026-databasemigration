-- Should NOT fire: utf8mb4 is the correct 4-byte encoding.

CREATE TABLE app.audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    payload TEXT,
    created_at DATETIME NOT NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;
