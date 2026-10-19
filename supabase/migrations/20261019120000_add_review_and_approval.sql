-- Stage 7: teacher review and approval.

-- Why the teacher changed a mark, if they say. The AI's score and reason stay untouched
-- beside it, so the record shows both marks and both reasons.
alter table public.scores add column teacher_note text;

-- Who approved the batch, and when. Once approved, marks are locked and the report can be
-- exported.
alter table public.exams
    add column approved_at timestamptz,
    add column approved_by uuid references public.users (id);
