-- Stage 1: the core tables.
-- Row-level security is switched on for every table at the end, with no policies yet,
-- so nothing is readable through the API until stage 1's policies migration allows it.

-- Teachers and students. One row per Supabase login (auth.users).
-- Students sign in anonymously, so a student row only says "this login is a student";
-- the roll number is recorded on the session they sit (see sessions).
create table public.users (
    id          uuid primary key references auth.users (id) on delete cascade,
    role        text not null check (role in ('teacher', 'student', 'admin')),
    full_name   text,
    email       text,
    created_at  timestamptz not null default now()
);

-- Data Structures, DBMS and so on. Subject rules live here as data, not in code.
create table public.subjects (
    id                  uuid primary key default gen_random_uuid(),
    name                text not null unique,
    question_type_mix   jsonb not null default '[]'::jsonb,
    created_at          timestamptz not null default now()
);

-- The units of a subject: searching, sorting, linked lists...
create table public.experiments (
    id          uuid primary key default gen_random_uuid(),
    subject_id  uuid not null references public.subjects (id) on delete cascade,
    position    int  not null,
    title       text not null,
    unique (subject_id, position)
);

-- Uploaded lab material. The file itself lives in storage; this row tracks it.
create table public.documents (
    id            uuid primary key default gen_random_uuid(),
    teacher_id    uuid not null references public.users (id),
    subject_id    uuid not null references public.subjects (id),
    file_name     text not null,
    storage_path  text not null,
    status        text not null default 'uploaded'
                  check (status in ('uploaded', 'processing', 'ready', 'failed')),
    error         text,
    created_at    timestamptz not null default now()
);

-- The question pool. Each teacher owns their pool, and exams draw from it.
-- type is plain text because the allowed types come from subjects.question_type_mix.
create table public.questions (
    id                uuid primary key default gen_random_uuid(),
    teacher_id        uuid not null references public.users (id),
    experiment_id     uuid not null references public.experiments (id),
    type              text not null,
    difficulty        text not null check (difficulty in ('easy', 'medium', 'hard')),
    question_text     text not null,
    reference_answer  text not null,
    key_points        jsonb not null default '[]'::jsonb,
    hints             jsonb not null default '[]'::jsonb,
    audio_path        text,
    status            text not null default 'draft'
                      check (status in ('draft', 'approved', 'rejected')),
    created_at        timestamptz not null default now()
);

-- One lab exam for one subject and section. Students join with join_code.
-- config holds question count, difficulty mix, duration, marks and hint penalty.
create table public.exams (
    id          uuid primary key default gen_random_uuid(),
    teacher_id  uuid not null references public.users (id),
    subject_id  uuid not null references public.subjects (id),
    section     text not null,
    title       text not null,
    join_code   text not null unique,
    config      jsonb not null default '{}'::jsonb,
    state       text not null default 'draft'
                check (state in ('draft', 'open', 'closed', 'approved')),
    created_at  timestamptz not null default now()
);

-- One student's viva. "Take the viva once" is enforced by the unique constraints:
-- one session per login per exam, and one session per roll number per exam.
create table public.sessions (
    id                uuid primary key default gen_random_uuid(),
    exam_id           uuid not null references public.exams (id) on delete cascade,
    student_id        uuid not null references public.users (id),
    roll_number       text not null,
    experiment_id     uuid references public.experiments (id),
    state             text not null default 'joined'
                      check (state in ('joined', 'in_progress', 'completed')),
    current_position  int  not null default 1,
    started_at        timestamptz,
    completed_at      timestamptz,
    created_at        timestamptz not null default now(),
    unique (exam_id, student_id),
    unique (exam_id, roll_number)
);

-- The questions drawn for a session, in the order they are asked.
create table public.session_questions (
    id           uuid primary key default gen_random_uuid(),
    session_id   uuid not null references public.sessions (id) on delete cascade,
    question_id  uuid not null references public.questions (id),
    position     int  not null,
    unique (session_id, position),
    unique (session_id, question_id)
);

-- One submitted answer per drawn question. idempotency_key makes a double submit
-- save once instead of twice.
create table public.answers (
    id                   uuid primary key default gen_random_uuid(),
    session_question_id  uuid not null unique
                         references public.session_questions (id) on delete cascade,
    text                 text not null,
    audio_path           text,
    hints_used           int  not null default 0 check (hints_used >= 0),
    idempotency_key      text not null unique,
    submitted_at         timestamptz not null default now()
);

-- AI and teacher marks, kept in separate columns so both stay on record.
create table public.scores (
    id              uuid primary key default gen_random_uuid(),
    answer_id       uuid not null unique references public.answers (id) on delete cascade,
    key_points_hit  jsonb,
    ai_score        numeric(5, 2),
    confidence      numeric(3, 2) check (confidence between 0 and 1),
    reason          text,
    needs_review    boolean not null default false,
    prompt_version  text,
    teacher_score   numeric(5, 2),
    reviewed_by     uuid references public.users (id),
    reviewed_at     timestamptz,
    created_at      timestamptz not null default now()
);

-- The work queue. The worker picks queued jobs whose run_after has passed.
create table public.jobs (
    id          bigint generated always as identity primary key,
    type        text not null,
    payload     jsonb not null default '{}'::jsonb,
    status      text not null default 'queued'
                check (status in ('queued', 'running', 'done', 'failed')),
    attempts    int  not null default 0,
    run_after   timestamptz not null default now(),
    last_error  text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index jobs_ready_idx on public.jobs (run_after) where status = 'queued';

-- The student's own program (stage 8). It is read by the AI, never run.
create table public.code_submissions (
    id            uuid primary key default gen_random_uuid(),
    session_id    uuid not null unique references public.sessions (id) on delete cascade,
    code          text not null,
    submitted_at  timestamptz not null default now()
);

-- Deny by default: with RLS on and no policies, only the service role can read or write.
alter table public.users             enable row level security;
alter table public.subjects          enable row level security;
alter table public.experiments       enable row level security;
alter table public.documents         enable row level security;
alter table public.questions         enable row level security;
alter table public.exams             enable row level security;
alter table public.sessions          enable row level security;
alter table public.session_questions enable row level security;
alter table public.answers           enable row level security;
alter table public.scores            enable row level security;
alter table public.jobs              enable row level security;
alter table public.code_submissions  enable row level security;
