-- Add a status_code column to the customer table.
-- This is the column whose lifetime the test suite tracks.
ALTER TABLE app.customer ADD COLUMN status_code VARCHAR(8);
