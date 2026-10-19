-- Local development data, loaded by `supabase db reset`. Not applied to a hosted project.
-- Made-up data only: never put real student names, roll numbers or answers here.

-- The 5-question viva mix from the spec's Data Structures pilot. Each slot is one
-- (type, difficulty) pair; the question pool holds several questions per slot, and every
-- student gets one question from each slot. "description" tells the question writer what
-- that type means. "live" slots are written during the exam (own code, stage 8), so they
-- are not part of the pool. "exact_match" slots have one correct output, so answers are
-- marked by comparing it in code instead of by the AI.
insert into public.subjects (name, question_type_mix) values (
    'Data Structures',
    '[
        {"position": 1, "type": "concept", "difficulty": "easy", "answer_mode": "voice",
         "description": "Ask why or how a core idea of the experiment works. Answerable in two or three spoken sentences."},
        {"position": 2, "type": "trace_output", "difficulty": "easy", "answer_mode": "typed", "exact_match": true,
         "description": "Give a short, concrete sequence of operations or a tiny input and ask for the exact resulting output or final state. The reference answer is only that exact output, written compactly (for example: 3, 7), so it can be checked by exact match."},
        {"position": 3, "type": "complexity", "difficulty": "medium", "answer_mode": "voice",
         "description": "Ask for the time or space complexity of an operation and the case or reason behind it."},
        {"position": 4, "type": "compare_what_if", "difficulty": "medium", "answer_mode": "voice",
         "description": "Ask the student to compare two approaches from the experiment, or what happens if a condition or step is changed."},
        {"position": 5, "type": "own_code", "difficulty": "hard", "answer_mode": "voice_or_typed", "live": true,
         "description": "A question about the student''s own program, written during the exam."}
    ]'::jsonb
);

-- Replace these with the exact experiment titles from your own lab manual: the ingestion
-- worker splits the manual by finding these titles as headings.
insert into public.experiments (subject_id, position, title)
select s.id, e.position, e.title
from public.subjects s
cross join (values
    (1, 'Linear and Binary Search'),
    (2, 'Sorting Algorithms'),
    (3, 'Singly Linked List'),
    (4, 'Stack Using Arrays'),
    (5, 'Queue Using Arrays'),
    (6, 'Binary Search Tree'),
    (7, 'Graph Traversal')
) as e (position, title)
where s.name = 'Data Structures';
