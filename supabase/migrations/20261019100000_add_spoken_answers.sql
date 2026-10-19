-- Stage 6: spoken answers.

-- What speech to text heard for the current question, gathered on the server as each
-- recording is transcribed (a long answer can be recorded in several parts). It lives on the
-- drawn question until submit, like hints_used, so a refresh does not lose it.
alter table public.session_questions add column transcript text;

-- On submit, what speech to text heard is kept beside what the student confirmed. Comparing
-- the two shows how often students had to correct the transcript, which measures speech
-- quality. Null for an answer that was typed.
--
-- The recording itself is not stored. Whether answer audio is kept, and for how long, is
-- still to be agreed with faculty, so only the text is kept for now (answers.audio_path
-- stays empty).
alter table public.answers add column spoken_transcript text;
