-- Destructive rollback: removes these tables and all their rows.
BEGIN;
DROP TABLE public.transactions;
DROP TABLE public.services;
DROP TABLE public.accounts;
COMMIT;
