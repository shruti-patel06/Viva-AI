-- Stage 8: questions about the student's own code, job priority and AI call logging.

-- A question written live from one student's program. It belongs to that session, skips the
-- teacher's approval (nobody can prepare it in advance), and goes when the session goes.
alter table public.questions drop constraint questions_status_check;
alter table public.questions add constraint questions_status_check
    check (status in ('draft', 'approved', 'rejected', 'live'));
alter table public.questions
    add column session_id uuid references public.sessions (id) on delete cascade;

-- Higher runs first. A question a student is about to be asked outranks scoring, which
-- nobody is waiting for.
alter table public.jobs add column priority int not null default 0;
drop index public.jobs_ready_idx;
create index jobs_ready_idx on public.jobs (priority desc, run_after) where status = 'queued';

-- One row per call to an AI service: what it was for, how long it took, what it cost and how
-- it ended. Only the API and worker write it; nobody reads it through the database API.
create table public.ai_calls (
    id           bigint generated always as identity primary key,
    at           timestamptz not null default now(),
    service      text not null,   -- groq, gemini, sarvam
    model        text,
    purpose      text not null,   -- answer_score, question_pool, own_code_question, tts, stt
    outcome      text not null check (outcome in ('ok', 'rate_limited', 'error')),
    duration_ms  int  not null,
    waited_ms    int  not null default 0,  -- time spent waiting for the rate limit first
    tokens       int,
    error        text
);
create index ai_calls_at_idx on public.ai_calls (at);
alter table public.ai_calls enable row level security;
