-- Local development data, loaded by `supabase db reset`. Not applied to a hosted project.
-- Made-up data only: never put real student names, roll numbers or answers here.

-- The 5-question viva mix from the spec's Data Structures pilot.
insert into public.subjects (name, question_type_mix) values (
    'Data Structures',
    '[
        {"position": 1, "type": "concept",         "difficulty": "easy",   "answer_mode": "voice"},
        {"position": 2, "type": "trace_output",    "difficulty": "easy",   "answer_mode": "typed"},
        {"position": 3, "type": "complexity",      "difficulty": "medium", "answer_mode": "voice"},
        {"position": 4, "type": "compare_what_if", "difficulty": "medium", "answer_mode": "voice"},
        {"position": 5, "type": "own_code",        "difficulty": "hard",   "answer_mode": "voice_or_typed"}
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
