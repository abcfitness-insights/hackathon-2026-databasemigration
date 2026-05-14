-- Realistic "messy PR" — mix of issues across categories.
-- Expected MigGuard output (representative):
--   HIGH   data_loss/truncate                  line 6
--   HIGH   permissions/grant-without-review    line 19
--   MEDIUM compatibility/merge-on-synapse      line 9-15
--   MEDIUM transaction/missing-tran-wrapper    (file-level)
--   LOW    naming/non-conventional-prefix      line 22

TRUNCATE TABLE app.staging_inbox;
GO

MERGE INTO app.customer AS target
USING #incoming_changes AS source
    ON target.customer_id = source.customer_id
WHEN MATCHED THEN UPDATE SET target.email = source.email
WHEN NOT MATCHED THEN INSERT (customer_id, email) VALUES (source.customer_id, source.email);
GO

UPDATE app.customer
SET    status = 'INACTIVE'
WHERE  last_login_date < '2020-01-01';
GO

GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::app TO [analytics_reader];
GO

CREATE TABLE app.tmp_scratch (id INT);
GO
