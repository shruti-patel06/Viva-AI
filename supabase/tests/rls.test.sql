-- Row-level security tests. Run with: supabase test db
-- Each block pretends to be one login, the same way the database API does: it switches
-- to the authenticated role and sets the token claims. Everything is rolled back at the end.

begin;
create extension if not exists pgtap with schema extensions;
select plan(18);

-- Logins. The sign-up trigger creates the public.users rows with roles.
insert into auth.users (id, email, is_anonymous, raw_app_meta_data) values
    ('11111111-0000-0000-0000-000000000001', 't1@test.local', false, '{"role": "teacher"}'),
    ('11111111-0000-0000-0000-000000000002', 't2@test.local', false, '{"role": "teacher"}'),
    ('22222222-0000-0000-0000-00000000000a', null, true, '{}'),
    ('22222222-0000-0000-0000-00000000000b', null, true, '{}'),
    ('22222222-0000-0000-0000-00000000000c', null, true, '{}'),
    ('33333333-0000-0000-0000-000000000001', 'norole@test.local', false, '{}');

insert into public.subjects (id, name) values
    ('aaaaaaaa-0000-0000-0000-000000000001', 'RLS test subject');
insert into public.experiments (id, subject_id, position, title) values
    ('aaaaaaaa-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000001', 1, 'Stacks');

-- Teacher 1 and teacher 2 each have a question and an exam.
insert into public.questions (id, teacher_id, experiment_id, type, difficulty, question_text, reference_answer) values
    ('bbbbbbbb-0000-0000-0000-000000000001', '11111111-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000002', 'concept', 'easy', 'What is a stack?', 'LIFO'),
    ('bbbbbbbb-0000-0000-0000-000000000002', '11111111-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000002', 'concept', 'easy', 'What is a stack?', 'LIFO');
insert into public.exams (id, teacher_id, subject_id, section, title, join_code) values
    ('cccccccc-0000-0000-0000-000000000001', '11111111-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000001', 'A', 'Lab exam A', 'AAA111'),
    ('cccccccc-0000-0000-0000-000000000002', '11111111-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000001', 'B', 'Lab exam B', 'BBB222');

-- Students A and B sit teacher 1's exam together; student C sits teacher 2's exam.
insert into public.sessions (id, exam_id, student_id, roll_number) values
    ('dddddddd-0000-0000-0000-00000000000a', 'cccccccc-0000-0000-0000-000000000001', '22222222-0000-0000-0000-00000000000a', '22CS001'),
    ('dddddddd-0000-0000-0000-00000000000b', 'cccccccc-0000-0000-0000-000000000001', '22222222-0000-0000-0000-00000000000b', '22CS002'),
    ('dddddddd-0000-0000-0000-00000000000c', 'cccccccc-0000-0000-0000-000000000002', '22222222-0000-0000-0000-00000000000c', '22CS003');
insert into public.session_questions (id, session_id, question_id, position) values
    ('eeeeeeee-0000-0000-0000-00000000000a', 'dddddddd-0000-0000-0000-00000000000a', 'bbbbbbbb-0000-0000-0000-000000000001', 1),
    ('eeeeeeee-0000-0000-0000-00000000000b', 'dddddddd-0000-0000-0000-00000000000b', 'bbbbbbbb-0000-0000-0000-000000000001', 1),
    ('eeeeeeee-0000-0000-0000-00000000000c', 'dddddddd-0000-0000-0000-00000000000c', 'bbbbbbbb-0000-0000-0000-000000000002', 1);
insert into public.answers (id, session_question_id, text, idempotency_key) values
    ('ffffffff-0000-0000-0000-00000000000a', 'eeeeeeee-0000-0000-0000-00000000000a', 'Last in, first out', 'key-a'),
    ('ffffffff-0000-0000-0000-00000000000b', 'eeeeeeee-0000-0000-0000-00000000000b', 'LIFO order', 'key-b'),
    ('ffffffff-0000-0000-0000-00000000000c', 'eeeeeeee-0000-0000-0000-00000000000c', 'A pile of plates', 'key-c');
insert into public.scores (answer_id, ai_score, reason) values
    ('ffffffff-0000-0000-0000-00000000000a', 2, 'covered'),
    ('ffffffff-0000-0000-0000-00000000000b', 2, 'covered'),
    ('ffffffff-0000-0000-0000-00000000000c', 1, 'partly');
insert into public.code_submissions (session_id, code) values
    ('dddddddd-0000-0000-0000-00000000000a', 'int main() {}'),
    ('dddddddd-0000-0000-0000-00000000000b', 'int main() {}');
insert into public.jobs (type) values ('score');

-- Student A -----------------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
    '{"sub": "22222222-0000-0000-0000-00000000000a", "role": "authenticated", "user_role": "student"}', true);

select is((select count(*) from public.sessions), 1::bigint, 'student sees only their own session');
select is((select count(*) from public.answers), 1::bigint, 'student sees only their own answer');
select is_empty(
    $$ select 1 from public.answers where id = 'ffffffff-0000-0000-0000-00000000000b' $$,
    'student cannot read a classmate''s answer even by its id');
select is((select count(*) from public.code_submissions), 1::bigint, 'student sees only their own code');
select is((select count(*) from public.exams), 1::bigint, 'student sees only the exam they sit');
select is((select count(*) from public.users), 1::bigint, 'student sees only their own user row');
select is((select count(*) from public.scores), 0::bigint, 'student sees no scores before approval');
select is((select count(*) from public.questions), 0::bigint, 'student cannot read the question pool');
select throws_ok(
    $$ insert into public.answers (session_question_id, text, idempotency_key)
       values ('eeeeeeee-0000-0000-0000-00000000000a', 'full marks please', 'key-x') $$,
    '42501', null, 'student cannot write an answer directly, only through the API');
select throws_ok($$ select * from public.jobs $$, '42501', null, 'student cannot read the job queue');
reset role;

-- Teacher 1 -----------------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
    '{"sub": "11111111-0000-0000-0000-000000000001", "role": "authenticated", "user_role": "teacher"}', true);

select is((select count(*) from public.exams), 1::bigint, 'teacher sees only their own exam');
select is((select count(*) from public.sessions), 2::bigint, 'teacher sees both sessions in their exam, not the other teacher''s');
select is((select count(*) from public.answers), 2::bigint, 'teacher sees answers in their exam only');
select is((select count(*) from public.scores), 2::bigint, 'teacher sees scores in their exam only');
select is((select count(*) from public.questions), 1::bigint, 'teacher sees only their own question pool');
select throws_ok(
    $$ update public.scores set teacher_score = 4 $$,
    '42501', null, 'teacher cannot change a score directly, only through the API');
reset role;

-- A login with no role (self-registered with email) ----------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
    '{"sub": "33333333-0000-0000-0000-000000000001", "role": "authenticated"}', true);

select is((select count(*) from public.subjects), 0::bigint, 'login with no role sees nothing');
reset role;

-- Not logged in -------------------------------------------------------------------------
set local role anon;
select set_config('request.jwt.claims', '{"role": "anon"}', true);

select throws_ok($$ select * from public.sessions $$, '42501', null, 'anonymous caller is refused');
reset role;

select * from finish();
rollback;
