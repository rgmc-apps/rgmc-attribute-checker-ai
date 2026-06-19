# Handoff

## Goal

A fully functional internal GPT Vision web tool for RGMC that converts clothing product photos into structured product descriptions. Users upload images, the tool sends them to OpenAI GPT-4V with a configurable prompt per brand, and returns formatted Excel/CSV/text files with product attributes. Deployed as a Flask service on Google Cloud Run with Supabase as the database backend.

Acceptance criteria:
- Main page 4-step workflow: Upload → Prompt → Attributes → Generate
- Config page 4-section admin: Brands → Brand Prompts → Attributes → Brand Attribute Mapping
- Brand-specific prompts loadable on main page
- Brand-specific attribute sets loadable on main page
- Output formats: .xlsx (with embedded thumbnails), .csv, .txt
- All config data persisted in Supabase

---

## Current State

**The app code is complete.** All Flask routes, both HTML templates, and all CSS/JS are implemented. The app cannot be fully used until the Supabase database tables are created.

**CRITICAL BLOCKER: Supabase DB tables do not exist yet.**
- `brands` table — does not exist
- `brand_prompts` table — does not exist
- `attributes` table — does not exist
- `brand_attributes` table — does not exist

Every API call from the Config page will return a 500 error until the migrations are run. The main page works with default prompt and default attributes (no Supabase call needed for those), but brand prompt loading and brand attribute loading will fail silently.

**What is working locally:**
- All Flask Python routes (`main.py`) are complete
- `templates/index.html` — fully implemented with all features and onboarding UI
- `templates/config.html` — fully implemented with all features and onboarding UI
- `helper/attribute_generator.py` — existing GPT processor (not changed this session)
- Git is clean, working tree matches latest commit

---

## Files Actively Being Edited

- `templates/index.html` — All changes committed. Contains:
  - How-it-works first-run banner (`#hiw-banner`, localStorage key `rgmc-hiw-seen`)
  - Hint tooltips (`?` buttons) on "Prompt source" and "Attribute source" toggles
  - Format cards redesigned from 3-column equal grid to stacked horizontal cards (icon-left, content-center, checkbox-right)
  - Step number circles 30px with orange drop shadow
  - Stagger entrance animation (`@keyframes fadeSlideUp` + `--si` CSS custom property set by JS)
  - Section `focus-within` orange border highlight when a field inside is active
  - Process button shine sweep animation (`@keyframes btnShine` on `::after` pseudo)

- `templates/config.html` — All changes committed. Contains:
  - Setup guide first-run card (`#setup-guide`, localStorage key `rgmc-sg-seen`)
  - Enhanced empty states for Brands, Attributes, and Prompts (icon + title + description + CTA buttons)
  - CTA buttons in empty states are wired to trigger the add-form toggle buttons via `.click()`
  - Step number circles 30px with orange drop shadow
  - Same stagger entrance animation as index.html
  - Section `focus-within` orange border highlight
  - Primary button lift + tactile feedback on `:active`

- `supabase/migrations/001_create_brands.sql` — **PENDING execution.** Creates `brands` and `brand_prompts` tables. Must be run first.

- `supabase/migrations/002_create_attributes.sql` — **PENDING execution.** Creates `attributes` (with seeded defaults) and `brand_attributes` tables. References `brands` via FK so 001 must run first.

---

## Failed Attempts

- **What was tried**: Running Supabase DDL via CLI — **Why it failed**: Supabase CLI not installed; Management API requires a Personal Access Token (not the service role key that's available); direct DB connection is firewalled. DDL must be run manually in the Supabase Dashboard SQL editor only.

- **What was tried**: Using `python` to run the dev server — **Why it failed**: On this Windows machine Python is only available as `py` (Windows Python Launcher). `python` is not on PATH.

---

## Next Step

**Run the two SQL migrations in Supabase Dashboard.**

1. Go to: `https://supabase.com/dashboard/project/eesrzpgmsrbhjeenfojq/sql/new`
2. Paste and run the full contents of `supabase/migrations/001_create_brands.sql` first.
3. Then paste and run the full contents of `supabase/migrations/002_create_attributes.sql`.
4. Verify in Supabase Table Editor that all four tables exist: `brands`, `brand_prompts`, `attributes`, `brand_attributes`.
5. Set the `SUPABASE_KEY` env var, then run `py main.py` and test the Config page end-to-end (create a brand, add a prompt, set it active, create attributes, map them to the brand). Then test the main page (select Brand Prompt source, select the brand, verify prompt loads; switch Attribute source to Brand, verify attributes load).

---

## Context & Gotchas

**Environment:**
- Python command: `py` (Windows Launcher), not `python`
- Dev server: `py main.py` starts on port 8080 by default
- `SUPABASE_KEY` must be set as an environment variable (service role JWT). Never hardcoded.
- `SUPABASE_URL` is hardcoded in `main.py` line 29: `'https://eesrzpgmsrbhjeenfojq.supabase.co'`
- OpenAI API key: `API_KEY` env var, read inside `helper/attribute_generator.py`

**Supabase schema notes:**
- `brands.brand_id` — BIGSERIAL PK
- `brand_prompts` — enforces one active prompt per brand via partial unique index on `(brand_id) WHERE is_active = TRUE`
- `brand_attributes` — junction table with `sort_order INT` for ordering attributes per brand
- `attributes.attribute_name` — UNIQUE constraint; table is seeded with 6 defaults (Material, Style, Fit, Details, Sleeves, Design) via the migration

**Main page JS architecture:**
- `DEFAULT_PROMPT` — captured once at load from Jinja-rendered textarea value; restored when switching back to "Default" source
- `DEFAULT_ATTRS` — injected via Jinja from `main.py DEFAULT_ATTRIBUTES` constant
- `brandsLoaded` flag — prevents redundant `/api/brands` fetches on repeated radio toggles
- Brand dropdown uses the `makeSearchableSelect` component wrapping `#brand-ss`
- When brand prompt source = "Brand", selecting a brand fires `loadBrandPrompt(brandId)` — if also attr source = "Brand", also fires `loadBrandAttributes(brandId)`

**Config page JS architecture:**
- Three SS instances: `configBrandSS` (section 2, prompt management), `mapBrandSS` (section 4, attribute mapping)
- Both are updated together in `renderBrandDropdown()` after `loadBrands()` resolves
- `show(id)` / `hide(id)` helpers set `style.display = ''` / `'none'` — `.empty-state` CSS provides `display:flex` as the shown state
- CTA buttons: `#brands-empty-cta` triggers `#toggle-brand-form-btn`, `#attrs-empty-cta` triggers `#toggle-attr-form-btn`

**Onboarding localStorage keys:**
- `rgmc-hiw-seen` — dismisses how-it-works banner on main page (set on "Got it" click)
- `rgmc-sg-seen` — dismisses setup guide on config page (set on "Got it" click)
- To re-test onboarding: open DevTools → Application → Local Storage → delete these keys

**Format cards structural change:**
- `.format-check` is now the last child of `.format-card-inner` (order: icon → .format-card-content → check)
- No longer `position: absolute`; part of flex row
- Checked state CSS still works: `input:checked ~ .format-card-inner .format-check`

**Cloud Run deployment env vars:**
- `SUPABASE_KEY` — Supabase service role JWT
- `API_KEY` — OpenAI API key
- `GPT_VERSION` — e.g. `gpt-4.1` (default in main.py)
- `SCRIPT_VERSION` — e.g. `2` (shown as a badge, default in main.py)
- `PORT` — Cloud Run sets this automatically; main.py reads it with default 8080
