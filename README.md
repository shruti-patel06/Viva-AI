# VivaAI

VivaAI gives every student an individual viva during a lab exam, and gives the teacher suggested scores with evidence to approve.

## Layout

```
backend/
  app/
    core/        settings, database, login token check, file storage
    modules/     one folder per module: auth, exams, ingestion, questions, sessions, scoring, reports
    ai/          provider interfaces: embeddings, language model, speech; prompts/ holds versioned prompts
    jobs/        the jobs table queue
    worker/      worker loop that runs ingest and score jobs
  tests/
supabase/
  migrations/    database tables, row-level security, storage bucket
  seed.sql       local test subject and experiments
  tests/         row-level security tests
web/             Next.js app for teachers and students
docs/decisions/  one decision log per stage
scripts/load_test/  60-student load test (stage 8)
```

## Setup

Copy `.env.example` to `.env` and `web/.env.example` to `web/.env.local`, then fill in the keys
(`supabase status` prints the local ones). Question writing needs `GROQ_API_KEY` and question audio
needs `SARVAM_API_KEY`. Never commit either file.

Start the local database (needs Docker Desktop running):

```
supabase start
```

`supabase db reset` rebuilds the database from the migrations and `seed.sql`.

Install the backend and the web app once:

```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

cd ../web
npm install
```

Create a teacher login (asks for a password):

```
python scripts/create_teacher.py teacher@college.edu "Full Name"
```

## Running

Three processes, each in its own terminal:

```
# 1. API (from backend/, venv active)
uvicorn app.main:app --reload

# 2. Worker: processes uploads, writes questions, makes question audio and scores answers (from backend/, venv active)
python -m app.worker

# 3. Web app (from web/), then open http://localhost:3000
npm run dev
```

Teachers sign in at `/login`. Under Exams, create an exam, open it once every question slot has an
approved question, and give students the join code. Students open `/join` (no account needed), enter
the code, their roll number and the experiment they performed, and take the viva at `/viva`.

Questions are read aloud from the audio made when they were approved. Students can speak an answer
(in parts of up to 30 seconds), check the transcript, and correct it before submitting; typing always
works too. Browsers allow the microphone only on `https` or `localhost`, so lab machines reaching a dev
server by its network address (`http://192.168...`) get typing only until the app is served over https.

Each submitted answer is scored by the worker a few seconds later, at the rate the AI service
allows, and the suggested marks appear on the Exams page. Answers saved before scoring existed can
be queued with `python -m app.modules.scoring.requeue` (from backend/).

After closing an exam, choose Review marks. Flagged answers come first; keep the AI's mark or give your
own, with a note. Approve once every answer has a mark, then download the report as Excel or PDF. The
AI's mark and the teacher's mark are both kept and both appear in the report.

When the subject's mix has an own-code slot (Data Structures does), students paste the program they
wrote before starting. The worker writes one question about it while they answer the others; if it is
not ready 45 seconds after they reach it, a ready-made question about their program is used. The
program is read, never run. These questions skip approval, so the review page lists them as
"generated live".

## Checking the system

Every AI call is recorded (time, tokens, outcome). From backend/:

```
python -m app.ai.calls --hours 24
```

The load test plays 60 students through a typed viva at once against the running API, with double
clicks and resends, then checks nothing was lost or saved twice. It builds and removes its own exam
and makes no AI calls:

```
python ../scripts/load_test/run.py
```

The scoring study checks the AI marks like a teacher. Export a blind sample, mark it without looking
at the AI's scores, then compare (target: within 1 mark on at least 80% of answers):

```
python -m app.modules.reports.study export <exam id> --count 30 --out study.csv
python -m app.modules.reports.study compare study.csv
```

`study.csv` holds student answers: it is git-ignored, and should be deleted once the result is noted.

Search a processed manual by experiment (from backend/):

```
python -m app.modules.ingestion.search "Stack Using Arrays" "what happens when you pop an empty stack"
```

`python -m tests.sample_manual sample.pdf` writes a made-up manual to try uploading.

## Tests

```
supabase test db
```

runs the row-level security tests in `supabase/tests/`.

```
cd backend
python -m pytest
```

runs the queue, ingestion, question pool, exam, viva session and scoring tests (needs `supabase start`).
The scoring tests use a stand-in for the AI service, so they cost no API credits.
