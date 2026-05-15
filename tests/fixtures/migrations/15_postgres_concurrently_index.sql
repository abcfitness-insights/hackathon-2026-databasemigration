-- Postgres CONCURRENTLY regression fixture for
-- rollback/index-drop-without-recreate.
--
-- Postgres syntax (per the docs):
--   DROP   INDEX [ CONCURRENTLY ] [ IF EXISTS ]     name
--   CREATE [UNIQUE] INDEX [ CONCURRENTLY ] [ [ IF NOT EXISTS ] name ] ON ...
--
-- Without the CONCURRENTLY slot in the regex, every variant below would
-- capture the literal string "CONCURRENTLY" as the index name -- silently
-- no-op'ing the rule for any Postgres shop that uses the idiomatic form.

-- Real DROP with no matching CREATE -- MUST fire.
DROP INDEX CONCURRENTLY ix_alpha;

-- Real DROP + matching CREATE in the same migration -- MUST NOT fire.
DROP INDEX CONCURRENTLY ix_beta;
CREATE INDEX CONCURRENTLY ix_beta ON app.customer (email);

-- IF EXISTS interleave after CONCURRENTLY, no recreate -- MUST fire.
DROP INDEX CONCURRENTLY IF EXISTS ix_gamma;
