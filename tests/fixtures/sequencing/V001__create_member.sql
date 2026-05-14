-- Flyway-style first migration.
CREATE TABLE app.customer (
    customer_id INT PRIMARY KEY,
    email NVARCHAR(255) NOT NULL
);
GO
