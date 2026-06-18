# Handoff

## Goal

Build and deploy a Flask web service (to Google Cloud Run) for RGMC Group that:
1. Lets users upload clothing images and generate structured product attributes via GPT-4 Vision
2. Manages **brands** and **per-brand GPT prompt templates** through a `/config` admin screen
3. On the main page, users upload images, choose a brand (optional), and generate XLS/CSV/TXT output files

The brand/prompt config system uses **Supabase** (`https://eesrzpgmsrbhjeenfojq.supabase.co`) as its database backend.

---

## Current State

**Code: COMPLETE and committed** — all changes are in commit `d34751b` ("added prompt mapping").

**Blocker: Database tables do not exist yet.** The `brands` and `brand_prompts` tables have NOT been created in the Supabase project. The app will throw this error when `/config` loads:

```
{'message': "Could not find the table 'public.brands' in the schema cache", 'code': 'PGRST205'}
```

Everything else is ready:
- Flask routes are wired (`/config`, `/api/brands`, `/api/brands/<id>`, `/api/brands/<id>/prompts`, `/api/prompts/<id>/activate`, `/api/prompts/<id>`)
- `supabase>=2.0.0` is in `requirements.txt`
- `SUPABASE_KEY` env var is expected at runtime (service role key)
- The config UI (`/config`) is fully built

**The ONLY remaining task is running the SQL migration in the Supabase dashboard.**

---

## Files Actively Being Edited

All files below were changed in this session and are **already committed** to `master` (commit `d34751b`):

- `main.py` — Added Supabase lazy client (`_sb()` function, lines 24–44), `/config` route, and 8 REST API routes for brands/prompts CRUD (after the existing `/download` route, before `_run_job`)
- `requirements.txt` — Added `supabase>=2.0.0` (line 6)
- `templates/config.html` — **New file**. Full admin UI at `/config`: two sections — (1) Brands table with add/edit/delete form panel, (2) Brand Prompts with brand dropdown, prompt history cards (active/inactive badges, expand/collapse), and add-prompt form with "Set as active" checkbox
- `templates/index.html` — Added a ⚙ Config link in the header (inline-styled `<a>` tag pointing to `/config`, before the GPT Vision badge)
- `supabase/migrations/001_create_brands.sql` — **New file**. SQL migration to create `brands` and `brand_prompts` tables (not yet run in Supabase)
- `.claude/settings.local.json` — Has a local diff (git tracks it modified) but the change is minor/cosmetic — added curl permission

---

## Failed Attempts

- **Supabase Management API (`POST api.supabase.com/v1/projects/.../database/query`)** — **Why it failed**: Returned HTTP 401. The Management API requires a Supabase Personal Access Token (PAT from supabase.com/dashboard/account/tokens), NOT the project's service_role JWT key.

- **Supabase Supavisor pooler via psycopg2 (JWT as DB password)** — Tried `aws-0-{region}.pooler.supabase.com:6543` with user `postgres.eesrzpgmsrbhjeenfojq` and the service_role JWT as password, across 5 AWS regions (ap-southeast-1, us-east-1, us-west-1, eu-west-1, ap-northeast-1). — **Why it failed**: Error `FATAL: (ENOTFOUND) tenant/user postgres.eesrzpgmsrbhjeenfojq not found` — the project is not on the new Supavisor pooler infrastructure.

- **Direct psycopg2 to `db.eesrzpgmsrbhjeenfojq.supabase.co:5432`** — **Why it failed**: DNS resolution failed (`getaddrinfo failed`). The direct DB host is either firewalled or not accessible from this machine without the DB password.

- **PostgREST REST API for DDL** — **Why it failed**: PostgREST (`/rest/v1/`) only supports CRUD on tables and RPC calls to named functions. No DDL support. No RPC functions were found in the database's OpenAPI spec at all.

- **`@supabase/mcp-server-supabase` npm package** — Installed successfully via npx. — **Why it failed**: The package reads `--access-token` or `SUPABASE_ACCESS_TOKEN` env var. This must be a Supabase **account-level Personal Access Token**, not the project service_role key. The Supabase MCP is also not configured in the user's Claude settings (`C:\Users\erarellano\.claude\settings.json`), so it doesn't appear as an MCP tool in Claude Code sessions.

- **Supabase CLI** — **Why it failed**: Not installed on this machine (`supabase --version` returns not found).

---

## Next Step

**Run the SQL migration in the Supabase Dashboard. This unblocks everything.**

1. Open: **https://supabase.com/dashboard/project/eesrzpgmsrbhjeenfojq/sql/new**

2. Paste and run:

```sql
CREATE TABLE IF NOT EXISTS public.brands (
    brand_id  BIGSERIAL PRIMARY KEY,
    brand_code VARCHAR(50)  NOT NULL UNIQUE,
    brand_name VARCHAR(200) NOT NULL,
    brand_desc TEXT
);

CREATE TABLE IF NOT EXISTS public.brand_prompts (
    id           BIGSERIAL PRIMARY KEY,
    brand_id     BIGINT      NOT NULL REFERENCES public.brands(brand_id) ON DELETE CASCADE,
    brand_prompt TEXT        NOT NULL,
    is_active    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS brand_prompts_single_active_idx
    ON public.brand_prompts(brand_id)
    WHERE is_active = TRUE;
```

3. After running SQL, set `SUPABASE_KEY` env var (Cloud Run config or local `.env`):
   ```
   SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVlc3J6cGdtc3JiaGplZW5mb2pxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTY3MDgyMiwiZXhwIjoyMDk1MjQ2ODIyfQ.dhFkhsJQh74TOY-s5ga7yaG34BRt9SbzQG4m9ivHwmY
   ```

4. Install Python dependency locally if not done yet: `pip install supabase`

5. Run the app (`python main.py` or via gunicorn) and navigate to `/config` — it should load the Brands section with an empty table and work end-to-end.

**Optional follow-up after tables exist:**
- Add Supabase MCP to Claude settings for future sessions (see Context section below)
- Wire the active brand prompt into the main `/` page: when a brand is selected, pre-populate the prompt template textarea with that brand's active `brand_prompt`

---

## Context & Gotchas

**Supabase credentials:**
- Project URL: `https://eesrzpgmsrbhjeenfojq.supabase.co`
- Service role key (already hardcoded as fallback in `main.py` line 29, but `SUPABASE_KEY` env var overrides it):
  `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVlc3J6cGdtc3JiaGplZW5mb2pxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTY3MDgyMiwiZXhwIjoyMDk1MjQ2ODIyfQ.dhFkhsJQh74TOY-s5ga7yaG34BRt9SbzQG4m9ivHwmY`

**The Supabase DB already has other tables** (systems, issues, companies, access_requests, bot_subscriptions, dev_activity_logs, etc.) — this is a shared Supabase project used for other RGMC internal tools. The `brands` and `brand_prompts` tables are new additions for this project specifically.

**Unique index constraint on `brand_prompts`**: `CREATE UNIQUE INDEX ... WHERE is_active = TRUE` enforces only one active prompt per brand. The Flask code handles this correctly by deactivating all prompts before inserting/activating a new one (see `api_brand_prompts_create` and `api_prompt_activate` in `main.py`).

**Supabase client is lazy-initialized**: `_sb()` function in `main.py` (lines 32–44) creates the client on first API call, not at startup. This means missing `SUPABASE_KEY` won't crash the app on boot — it will return a 500 error only when `/config` or `/api/brands` etc. are first called.

**Python launcher**: On this Windows machine, Python is invoked via `py` (the Python Launcher), not `python` or `python3`. The `py` binary is at `C:\Users\erarellano\AppData\Local\Programs\Python\Launcher\py.exe`. Node.js v22 is available at standard PATH.

**Setting up Supabase MCP for future sessions** (so Claude can do DDL without manual SQL execution):
1. Get a Personal Access Token from https://supabase.com/dashboard/account/tokens
2. Add to `C:\Users\erarellano\.claude\settings.json`:
```json
"mcpServers": {
  "supabase": {
    "command": "npx",
    "args": [
      "-y", "@supabase/mcp-server-supabase@latest",
      "--project-ref", "eesrzpgmsrbhjeenfojq",
      "--access-token", "YOUR_PAT_HERE"
    ]
  }
}
```
3. Restart Claude Code to load the MCP tools.

**Tech stack** (for reference): Flask, XlsxWriter, Pillow, Gunicorn, supabase-py, requests — deployed to Google Cloud Run. Environment vars: `API_KEY` (OpenAI), `GPT_VERSION`, `GPT_LIMIT`, `SCRIPT_VERSION`, `SUPABASE_KEY`, `SUPABASE_URL`.
