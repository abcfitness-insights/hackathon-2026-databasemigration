-- Bad naming all around: CamelCase, reserved words, overlong identifier.
-- Expected MigGuard findings:
--   LOW  naming/non-snake-case  on "MemberLoyalty" (CamelCase)
--   MED  naming/reserved-word   on "user" (reserved keyword as column name)
--   LOW  naming/non-snake-case  on "Email_Address" (mixed case)
--   LOW  naming/too-long        on the 70-char column name

IF OBJECT_ID(N'dw.MemberLoyalty', 'U') IS NULL
BEGIN
    CREATE TABLE dw.MemberLoyalty (
        member_id INT NOT NULL,
        [user] NVARCHAR(64) NOT NULL,
        Email_Address NVARCHAR(255) NOT NULL,
        a_column_with_a_name_that_is_intentionally_far_too_long_for_postgres_yes NVARCHAR(16) NULL
    );
END
GO
