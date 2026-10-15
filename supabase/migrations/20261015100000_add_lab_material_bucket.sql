-- Stage 2: private storage for uploaded lab manuals.
-- Only the API and worker touch these files, using the service key, so the bucket has no
-- storage policies: browsers can neither list nor download them.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('lab-material', 'lab-material', false, 20971520, array['application/pdf']);
