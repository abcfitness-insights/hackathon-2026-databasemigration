UPDATE dw.member
SET status = 'active'
WHERE status IS NULL;
GO
