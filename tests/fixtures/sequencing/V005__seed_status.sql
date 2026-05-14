UPDATE app.customer
SET status = 'active'
WHERE status IS NULL;
GO
