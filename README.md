# VivaAI

VivaAI gives every student an individual viva during a lab exam, and gives the teacher suggested scores with evidence to approve.

## Layout

```
backend/
  app/
    core/        config, database, auth dependencies
    modules/     one folder per module: auth, exams, ingestion, questions, sessions, scoring, reports
    ai/          provider interfaces (language model, speech); prompts/ holds versioned prompts
    jobs/        the jobs table queue
    worker/      worker loop that runs ingest and score jobs
  tests/
supabase/
  migrations/    database tables and row-level security
web/             student and teacher web app
docs/decisions/  one decision log per stage
scripts/load_test/  60-student load test (stage 8)
```

## Setup

Copy `.env.example` to `.env` and fill in the keys. Never commit `.env`.

Start the local database (needs Docker Desktop running):

```
supabase start
```

`supabase status` prints the local URLs and keys. `supabase db reset` rebuilds the database from the migrations.

Create a teacher login (asks for a password):

```
python scripts/create_teacher.py teacher@college.edu "Full Name"
```

## Tests

```
supabase test db
```

runs the row-level security tests in `supabase/tests/`.
