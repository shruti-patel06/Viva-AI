-- Stage 1: row-level security.
-- Rule for the whole app: a logged-in user may READ their own rows directly from the
-- database API. Every WRITE goes through our FastAPI app, which checks the session
-- state first, so the authenticated role is given SELECT and nothing else.
-- anon (not logged in) gets nothing. jobs is never readable; only the worker touches it.

-- Helpers live in a schema the database API does not expose.
-- They are security definer so a policy on one table can look at another table without
-- that table's own policies running again (exams -> sessions -> exams would recurse).
create schema private;
grant usage on schema private to authenticated;

-- The role copied into the token by custom_access_token_hook. Null for a login with no role.
create function private.user_role()
returns text
language sql
stable
set search_path = ''
as $$
    select auth.jwt() ->> 'user_role';
$$;

-- True when the logged-in teacher created this exam.
create function private.teaches_exam(target_exam_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.exams
        where id = target_exam_id and teacher_id = auth.uid()
    );
$$;

-- True when the logged-in student is sitting this session.
create function private.sits_session(target_session_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.sessions
        where id = target_session_id and student_id = auth.uid()
    );
$$;

-- True when the logged-in teacher owns the exam this session belongs to.
create function private.teaches_session(target_session_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.sessions s
        join public.exams e on e.id = s.exam_id
        where s.id = target_session_id and e.teacher_id = auth.uid()
    );
$$;

-- The session a drawn question belongs to, so answers can be traced back to a session.
create function private.session_of(target_session_question_id uuid)
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
    select session_id from public.session_questions where id = target_session_question_id;
$$;

revoke execute on all functions in schema private from public, anon;
grant execute on all functions in schema private to authenticated;

grant select on
    public.users, public.subjects, public.experiments, public.documents, public.questions,
    public.exams, public.sessions, public.session_questions, public.answers, public.scores,
    public.code_submissions
to authenticated;

-- users: everyone sees only their own row.
create policy "read own user row" on public.users
    for select to authenticated
    using (id = (select auth.uid()));

-- subjects and experiments are shared reference data, readable by any login with a role.
create policy "read subjects" on public.subjects
    for select to authenticated
    using ((select private.user_role()) is not null);

create policy "read experiments" on public.experiments
    for select to authenticated
    using ((select private.user_role()) is not null);

-- documents and questions belong to the teacher who made them.
-- Students never read questions directly: RLS filters rows, not columns, so a student
-- who could read a question row would also see its reference answer and key points.
-- The API serves the current question's text instead.
create policy "teacher reads own documents" on public.documents
    for select to authenticated
    using (teacher_id = (select auth.uid()));

create policy "teacher reads own questions" on public.questions
    for select to authenticated
    using (teacher_id = (select auth.uid()));

-- exams: the teacher who made it, and students who have a session in it.
create policy "teacher reads own exams" on public.exams
    for select to authenticated
    using (teacher_id = (select auth.uid()));

create policy "student reads exam they sit" on public.exams
    for select to authenticated
    using (exists (
        select 1 from public.sessions
        where exam_id = exams.id and student_id = (select auth.uid())
    ));

-- sessions: the student sitting it, and the teacher of its exam.
create policy "student reads own session" on public.sessions
    for select to authenticated
    using (student_id = (select auth.uid()));

create policy "teacher reads sessions in own exams" on public.sessions
    for select to authenticated
    using (private.teaches_exam(exam_id));

-- session_questions: which questions were drawn, by id and position only.
create policy "student reads own drawn questions" on public.session_questions
    for select to authenticated
    using (private.sits_session(session_id));

create policy "teacher reads drawn questions in own exams" on public.session_questions
    for select to authenticated
    using (private.teaches_session(session_id));

-- answers: the student who gave it, and the teacher of the exam.
create policy "student reads own answers" on public.answers
    for select to authenticated
    using (private.sits_session(private.session_of(session_question_id)));

create policy "teacher reads answers in own exams" on public.answers
    for select to authenticated
    using (private.teaches_session(private.session_of(session_question_id)));

-- scores: teacher only. Students see no score before approval (and whether they see it
-- after approval is still an open question for faculty).
create policy "teacher reads scores in own exams" on public.scores
    for select to authenticated
    using (exists (
        select 1 from public.answers a
        join public.session_questions sq on sq.id = a.session_question_id
        where a.id = scores.answer_id and private.teaches_session(sq.session_id)
    ));

-- code_submissions: the student who pasted it, and the teacher of the exam.
create policy "student reads own code" on public.code_submissions
    for select to authenticated
    using (private.sits_session(session_id));

create policy "teacher reads code in own exams" on public.code_submissions
    for select to authenticated
    using (private.teaches_session(session_id));
