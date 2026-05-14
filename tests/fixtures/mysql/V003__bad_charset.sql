-- Should fire: mysql/utf8-not-utf8mb4
-- DEFAULT CHARSET=utf8 is the 3-byte legacy alias. Can't store emoji.

CREATE TABLE app.audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    payload TEXT,
    created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
