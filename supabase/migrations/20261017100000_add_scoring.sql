-- Stage 5: scoring.

-- Which model produced an AI score (null when the mark was worked out in code, such as an
-- exact match). With prompt_version, this records exactly how every mark was reached.
-- review_reason says why an answer is flagged "needs review", so the teacher knows what to check.
alter table public.scores
    add column model         text,
    add column review_reason text,
    add column updated_at    timestamptz not null default now();

-- Slots whose answer is one exact output are marked by comparing it in code, with no AI call.
-- New databases get the flag from seed.sql; this sets it on a Data Structures subject that
-- already exists. Exams copy the mix when they are created, so older exams keep AI marking.
update public.subjects
set question_type_mix = (
    select jsonb_agg(
               case when slot ->> 'type' = 'trace_output' then slot || '{"exact_match": true}'::jsonb else slot end
               order by n)
    from jsonb_array_elements(question_type_mix) with ordinality as t (slot, n)
)
where name = 'Data Structures';
