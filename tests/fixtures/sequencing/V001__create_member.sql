-- Flyway-style first migration.
CREATE TABLE dw.member (
    member_id INT PRIMARY KEY,
    email NVARCHAR(255) NOT NULL
);
GO
