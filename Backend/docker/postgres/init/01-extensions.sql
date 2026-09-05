-- Runs once, on first initialisation of an empty data directory.
--
-- citext gives us a case-insensitive text type. Email addresses are
-- case-insensitive in practice, and a UNIQUE index on a citext column is what
-- actually prevents "Ana@x.com" and "ana@x.com" from both registering — an
-- application-level `.lower()` cannot, because two concurrent inserts can pass
-- the check simultaneously. The constraint has to live in the database.
CREATE EXTENSION IF NOT EXISTS citext;

-- pgcrypto provides gen_random_uuid() for database-side UUID defaults.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
