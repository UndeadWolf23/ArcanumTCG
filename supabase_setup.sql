-- ============================================================================
-- ARCANUM · Supabase setup & hardening (idempotent — safe to run repeatedly)
-- Run in: Supabase Dashboard -> SQL Editor -> New query -> Run
-- ============================================================================

-- ---------------------------------------------------------------- 1) CARDS
-- The official card library. Everyone can READ (the game fetches it with the
-- publishable key); nobody can WRITE except the service role (Card Designer).
create table if not exists public.cards (
  id          text primary key,
  name        text not null,
  data        jsonb not null default '{}',   -- full CardSpec (stats, keywords, image, ...)
  collectible boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

alter table public.cards enable row level security;

drop policy if exists "cards are readable by everyone" on public.cards;
create policy "cards are readable by everyone"
  on public.cards for select using (true);
-- No insert/update/delete policies: only the service_role key (which
-- bypasses RLS) can write. The publishable key in the client CANNOT.

-- keep updated_at honest
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists cards_touch on public.cards;
create trigger cards_touch before update on public.cards
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------- 2) DECKS
-- Player deck saves. Each player sees and edits only their own.
create table if not exists public.decks (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  name        text not null default 'New Deck',
  cards       jsonb not null default '{}',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists decks_user_idx on public.decks (user_id);
alter table public.decks enable row level security;

drop policy if exists "players read own decks"   on public.decks;
drop policy if exists "players insert own decks" on public.decks;
drop policy if exists "players update own decks" on public.decks;
drop policy if exists "players delete own decks" on public.decks;
create policy "players read own decks"
  on public.decks for select using (auth.uid() = user_id);
create policy "players insert own decks"
  on public.decks for insert with check (auth.uid() = user_id);
create policy "players update own decks"
  on public.decks for update using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
create policy "players delete own decks"
  on public.decks for delete using (auth.uid() = user_id);

drop trigger if exists decks_touch on public.decks;
create trigger decks_touch before update on public.decks
  for each row execute function public.touch_updated_at();

-- ------------------------------------------------------------- 3) PROFILES
-- Public-facing player identity + soft currency. Players may edit their
-- username; ONLY the game server (service role) may change coins, so the
-- currency chip in the hub can become real without being client-forgeable.
create table if not exists public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  username    text unique not null,
  coins       int not null default 200 check (coins >= 0),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "profiles are readable by everyone" on public.profiles;
drop policy if exists "players create own profile"        on public.profiles;
drop policy if exists "players rename themselves"         on public.profiles;
create policy "profiles are readable by everyone"
  on public.profiles for select using (true);
create policy "players create own profile"
  on public.profiles for insert with check (auth.uid() = id);
create policy "players rename themselves"
  on public.profiles for update using (auth.uid() = id)
  with check (auth.uid() = id and coins = (select p.coins from public.profiles p where p.id = auth.uid()));
-- (the coins equality check stops a player updating their own balance)

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

-- auto-create a profile row on signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, username)
  values (new.id,
          coalesce(new.raw_user_meta_data->>'username',
                   'Mage-' || left(new.id::text, 6)))
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------- 4) COLLECTIONS
-- Real card ownership for the pack economy. Players can READ their own;
-- ONLY the server (service role) writes — pack results are granted by the
-- game server, never by the client, or players could gift themselves cards.
create table if not exists public.collections (
  user_id     uuid not null references auth.users (id) on delete cascade,
  card_id     text not null,
  count       int  not null default 0 check (count >= 0),
  updated_at  timestamptz not null default now(),
  primary key (user_id, card_id)
);

alter table public.collections enable row level security;

drop policy if exists "players read own collection" on public.collections;
create policy "players read own collection"
  on public.collections for select using (auth.uid() = user_id);
-- no client write policies on purpose.

-- -------------------------------------------------------- 5) MATCH RESULTS
-- Server-written match history (service role only; players read their own).
create table if not exists public.match_results (
  id          uuid primary key default gen_random_uuid(),
  played_at   timestamptz not null default now(),
  mode        text not null default 'pvp',
  winner_name text not null,
  loser_name  text not null,
  winner_id   uuid references auth.users (id),
  loser_id    uuid references auth.users (id),
  turns       int not null default 0
);

alter table public.match_results enable row level security;

drop policy if exists "players read their matches" on public.match_results;
create policy "players read their matches"
  on public.match_results for select
  using (auth.uid() = winner_id or auth.uid() = loser_id);

-- ---------------------------------------------------------- 6) STORAGE
-- card-art bucket: public READ, service-role-only WRITE.
insert into storage.buckets (id, name, public)
  values ('card-art', 'card-art', true)
  on conflict (id) do update set public = true;

drop policy if exists "card art public read"  on storage.objects;
create policy "card art public read"
  on storage.objects for select
  using (bucket_id = 'card-art');
-- no insert/update/delete policies for card-art: service role only.

-- ============================================================== VERIFY ====
-- After running, these should all say rowsecurity = true:
select tablename, rowsecurity from pg_tables
 where schemaname = 'public'
   and tablename in ('cards','decks','profiles','collections','match_results');
