-- Stage 4: the typed viva session.

-- Hints taken on a drawn question, counted the moment each one is shown. They live on the
-- drawn question rather than the answer because the answer does not exist until submit:
-- a student who takes a hint and then refreshes the page still has it counted.
alter table public.session_questions
    add column hints_used int not null default 0 check (hints_used >= 0);

-- When the student's time runs out, fixed the moment they start. Storing it (instead of
-- working it out from started_at each time) means changing an exam's duration later never
-- moves the deadline of someone already answering.
alter table public.sessions add column ends_at timestamptz;

-- The draw counts how often each question has been given out in an exam, to spread the
-- pool evenly across students.
create index session_questions_question_idx on public.session_questions (question_id);
