-- Stage 2: spot abandoned jobs.
-- A worker stamps locked_at when it claims a job. If the worker dies (crash, restart,
-- laptop lid closed), the job stays "running" forever unless something notices.
-- The worker treats a job running far longer than any job should take as abandoned
-- and puts it back in the queue.
alter table public.jobs add column locked_at timestamptz;
