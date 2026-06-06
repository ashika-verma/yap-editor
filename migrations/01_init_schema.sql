-- Create projects table
create table projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  data jsonb not null default '{}',
  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

-- Enable RLS
alter table projects enable row level security;

-- Create RLS policies
create policy "Users can read their own projects" on projects
  for select using (auth.uid() = user_id);

create policy "Users can insert their own projects" on projects
  for insert with check (auth.uid() = user_id);

create policy "Users can update their own projects" on projects
  for update using (auth.uid() = user_id);

create policy "Users can delete their own projects" on projects
  for delete using (auth.uid() = user_id);

-- Create index for faster queries
create index projects_user_id_idx on projects(user_id);
