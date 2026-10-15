-- Stage 3: the question pool.

-- Where each question came from, so it can be explained and regenerated:
-- the manual it was written from, the prompt version and the model that wrote it.
alter table public.questions
    add column source_document_id uuid references public.documents (id) on delete set null,
    add column prompt_version     text,
    add column model              text,
    add column updated_at         timestamptz not null default now();

create index questions_pool_idx on public.questions (teacher_id, experiment_id, status);

-- Spoken questions, made once when a question is approved and played on exam day.
-- Private: the API hands out short-lived signed links to whoever may hear a question.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('question-audio', 'question-audio', false, 2097152, array['audio/mpeg']);
