-- Run this once against your existing database before deploying the new code.
-- 1) Ensure password hashes are not truncated.
ALTER TABLE `user` MODIFY COLUMN `password` VARCHAR(255) NOT NULL;
