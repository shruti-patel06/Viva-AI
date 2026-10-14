-- Stage 1: teacher and student login.
-- Every Supabase login with a role gets a matching public.users row, and that role is
-- copied into the login token so the API and RLS policies can read it without a query.

-- Create or update the public.users row when a login is created or its role changes.
-- Students sign in anonymously, so an anonymous login is always a student.
-- Teachers are created by an admin, who sets the role in app_metadata. app_metadata
-- can only be written with the service key; user_metadata can be edited by the user
-- themselves, so it must never decide a role.
-- The admin API creates the login first and sets app_metadata in a second step, which
-- is why this also runs on update. A login with no valid role gets no row, and so no access.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    new_role text;
begin
    if new.is_anonymous then
        new_role := 'student';
    else
        new_role := new.raw_app_meta_data ->> 'role';
    end if;

    if new_role is null or new_role not in ('teacher', 'student', 'admin') then
        return new;
    end if;

    insert into public.users (id, role, email, full_name)
    values (new.id, new_role, new.email, new.raw_user_meta_data ->> 'full_name')
    on conflict (id) do update set role = excluded.role;

    return new;
end;
$$;

create trigger on_auth_user_saved
    after insert or update of raw_app_meta_data on auth.users
    for each row execute function public.handle_new_user();

-- Add the role to every token Supabase issues, as the user_role claim.
-- Supabase calls this before signing a token (see [auth.hook.custom_access_token]).
create function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
    claims     jsonb;
    user_role  text;
begin
    select role into user_role
    from public.users
    where id = (event ->> 'user_id')::uuid;

    claims := jsonb_set(event -> 'claims', '{user_role}', coalesce(to_jsonb(user_role), 'null'::jsonb));
    return jsonb_set(event, '{claims}', claims);
end;
$$;

-- Only the auth service may run the hook and read roles for it.
grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook from authenticated, anon, public;
grant select on table public.users to supabase_auth_admin;

create policy "auth service reads roles for tokens"
    on public.users
    for select
    to supabase_auth_admin
    using (true);
