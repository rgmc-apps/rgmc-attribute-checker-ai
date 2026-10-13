# Handoff

## Goal

Build and maintain an internal GPT Vision web service for RGMC Group merchandisers (the "Attribute Checker"). Users upload clothing product photos (or look one up by stock number from the creatives database), the tool sends them to OpenAI GPT-4V with a configurable, structured prompt, and generates product descriptions downloadable as Excel, CSV, or text files. Admins configure brands, prompt templates, and attribute sets per brand via a Config screen.

**Deployment target:** Google Cloud Run
**Tech stack:** Flask (Python 3.12), Supabase (Postgres via REST) for brand/prompt/attribute config, `rgmc-gcp-api` (FastAPI, separate shared repo) for creatives DB access, xlsxwriter, Pillow, gunicorn

**Requirements established this session:**
1. Replace the old flat "attribute list" prompt with a fully structured 10-section prompt (title/intro/details/fit/care/support/tags rules) the user supplied verbatim, adapted to use placeholders.
2. Add "Required Inputs" fields (Brand Name, Product Category, Description Type PD/AE, Material Composition, Care/Wash Instructions, Additional Info) above the prompt editor — Material and Care are required, the rest optional.
3. Add a **stock-number search** option on Step 1 that looks up a product in `creative_prod` (via the shared `rgmc-gcp-api` service) and displays its photo(s) + production details.
4. Users can add the product's existing photos to the processing batch, and add **supplementary** photos of their own — explicitly **NOT** saved back to the creatives database, purely to help GPT produce a more accurate description (this was a reversal of an earlier design — see Failed Attempts).
5. **Wire the looked-up creative fields into the Required Inputs form automatically** (this session's final ask) — Brand Name, Product Category, Material Composition should auto-populate from the lookup, with leftover useful fields (Item ID, Designer, Season, Supplier, etc.) folded into Additional Product Information. Care/Wash Instructions has no equivalent field in creatives data and is deliberately left for manual entry.

---

## Current State

**Everything described above is implemented, tested against live production data, and working.** Git working tree has uncommitted changes (see below) — nothing has been committed in this repo during this session.

### What works (verified live against real stock number `D002-SMT05`, Dansen brand / Keywest One International Corp):
- Step 1 "Image source" toggle: **Upload Files** vs **Search Creatives DB** (`templates/index.html` ~line 1024 onward).
- Search panel calls `GET /api/creative/search?stock_number=...` (`main.py:596`), which:
  - Looks up `ProductDesign` by `stockNumber` via `rgmc-gcp-api`'s generic `/creative/by_table/value` endpoint.
  - Resolves `brandId → Brand.name`, `categoryId → Category.name`, `itemGroupId → ItemGroup.name`, `designerId → Designer.name`, `designStatusCode → DesignStatus.name`, `supplierId → Supplier.name`, `seasonId → Season.name/year` via `_creative_lookup_name()` (`main.py:82`), degrading gracefully (returns `null` for that field, logs a warning) if any sub-lookup fails.
  - Fetches `ProductDesignPicture` rows for the product and returns them as `data:image/png;base64,...` URIs — **this part depends on the `rgmc-gcp-api` fix described below**.
- Result card (`#stock-result-card`) renders: title, image thumbnails (each with its own "add to batch" button), a detail grid (Stock Number, Item ID, Brand, Category, Item Group, Designer, Season, Design Status, Fabric Code/Name, Supplier, Supplier Stock No, Description), "Add Existing Photos to Batch", "Add Supplementary Photos", and a "Search another" reset button.
- `fillRequiredInputsFromCreative(data)` (`templates/index.html:1733`) runs automatically on every successful search, before the result card is shown, and:
  - Fills `#req-brand` ← `brand`, `#req-category` ← `category` (fallback `item_group`), `#req-material` ← `fabric_name` (fallback `fabric_code`).
  - Stuffs everything else (Item ID, Item Group if not already used, Designer, Season+Year, Fabric Code if not already used, Supplier, Supplier Stock No, Design Status, Description, Remark) into `#req-additional` as `Label: value` lines, skipping anything already used to avoid duplication.
  - Leaves `#req-care` (Care/Wash Instructions) untouched — no source field exists for it.
  - Shows a green confirmation note (`#sr-fill-note`) listing which fields were filled.
- "Add Supplementary Photos" (`#sr-add-new-input` change handler, `templates/index.html:1841`) is purely client-side now: picked files go straight into the same `files[]` batch array via `addFileItems()` — **no network call**, no write to creatives DB.
- "Add Existing Photos to Batch" and per-thumbnail "+" buttons convert the `data:` URI photos to `File` objects (`dataURItoFile()`) and add them the same way.

### What is NOT live yet (known, intentional gap):
- `rgmc-gcp-api`'s `src/routers/mssql_routes/mssql_routes.py` has a fix (base64-encode binary columns before JSON serialization) that is **required** for the `ProductDesignPicture` image read to work. This fix has been **committed and pushed to `origin/main` on GitHub** (commit `d794bb1 "added creatives route"`) by an apparent auto-commit+auto-push hook in that environment — **I did not run `git push` myself**, and did not expect it to be pushed automatically (see Context & Gotchas). I re-tested the live Cloud Run service (`https://rgmc-gcp-api-935246372408.asia-southeast1.run.app`) immediately after discovering this and confirmed **the fix is NOT yet deployed to Cloud Run** — querying `ProductDesignPicture` still returns `500 Internal Server Error`. So: source is pushed, production is not yet redeployed. Someone needs to either manually deploy (`gcloud run deploy` per that repo's README) or confirm/trigger whatever Cloud Build setup exists.
- Until that deploy happens, every search result's `images` array will be empty (the app degrades gracefully — shows "No photos on file for this product yet." — it does not crash), even for products that do have photos in the DB.

---

## Files Actively Being Edited

**`C:\claude\rgmc-attribute-checker` (this repo, all uncommitted):**
- `main.py` — Added: `CREATIVE_API_BASE_URL` env var (default `https://rgmc-gcp-api-935246372408.asia-southeast1.run.app`, line 52), `CreativeApiError`, `_creative_lookup()`, `_creative_lookup_name()` (lines 57–91), the full 10-section `PROMPT_TEMPLATE_DEFAULT` replacing the old simple template (lines 101–232), new `DEFAULT_ATTRIBUTES` (Silhouette & Length / Design Details / Closure / Stretch & Fit / Functional Features — Material and Care moved out since they're now required inputs, line 233), `/process` route now validates and forwards `brand_name`, `product_category`, `description_type`, `material`, `care_instructions`, `additional_info` (material/care required server-side), and `GET /api/creative/search` (line 596). The `POST /api/creative/pictures` write endpoint that existed briefly was **removed** this session (see Failed Attempts) along with the now-unused `base64` import.
- `helper/attribute_generator.py` — `__build_prompt()` now substitutes `{{BRAND_NAME}}`, `{{PRODUCT_CATEGORY}}`, `{{DESCRIPTION_TYPE}}`, `{{MATERIAL}}`, `{{CARE_INSTRUCTIONS}}`, `{{ADDITIONAL_INFO}}` in addition to `{{ATTRIBUTES}}`; constructor reads these from `settings`; `process()` raises `ValueError` early if material/care are empty; `__parse_response()` rewritten to strip markdown bold/bullet markers and find the title under a `**PRODUCT TITLE**` heading (old prompts' "title on line one" still works as a fallback) — also added earlier this session, unaffected by the creatives work.
- `templates/index.html` — Largest file of changes (~500 lines added total across the whole session):
  - CSS: `.req-inputs-grid`, `.text-input`, `.field-badge`, `.field-label-row`, `.btn-text-sm` (Required Inputs styling, earlier in session), and `.stock-search-row`, `.stock-status`, `#stock-result-card`, `.stock-result-*`, `.sr-thumb*` (stock search UI, this session) — inserted right before the `PROMPT SECTION` comment block.
  - HTML: Image-source toggle + `#search-mode-panel` (search box, result card with images/details/actions) inserted at the top of Step 1's `.section-body`, wrapping the old drop-zone content in `#upload-mode-panel` (around line 1024+). Required Inputs fields inserted in Step 2 before the prompt textarea.
  - JS (inside the main IIFE): DOM refs for both the Required Inputs fields and the stock-search elements; `dataURItoFile()`, `renderCreativeDetails()` (line 1681), `renderCreativeImages()` (line 1708), `fillRequiredInputsFromCreative()` (line 1733), `runStockSearch()` (line 1790), plus click handlers for search/clear/add-existing/add-supplementary. `startProcessing()` now validates Material/Care are filled and appends all six Required Inputs fields to the `FormData` sent to `/process`.
- `templates/config.html` — One-line fix: the brand-prompt textarea's `placeholder` attribute now wraps its `{{ATTRIBUTES}}` etc. mentions in `{% raw %}...{% endraw %}` so Jinja doesn't silently swallow them (this was a pre-existing bug, fixed incidentally while updating the placeholder text to mention the new placeholders).
- `.claude/settings.local.json`, `handoff.md` — pre-existing working-tree changes from before this session started, untouched by this session's work (visible in `git status` but not something this session modified further beyond this current handoff.md rewrite).

**`C:\RGMC\Source\git\rgmc-gcp-api` (separate repo — already committed AND pushed, see Current State):**
- `src/routers/mssql_routes/mssql_routes.py` — Added `_serialize_value()` / `_serialize_row()` helpers (lines 13–21) that base64-encode `bytes`/`bytearray`/`memoryview` column values before the generic `GET /{db_name}/by_table/value` and `GET /{db_name}/by_table/latest` endpoints return them as JSON. This is the only change remaining in this repo — the write endpoint (`POST /creative/product-design-pictures`) that was added and then explicitly asked to be removed is gone; `src/main.py`'s tag metadata was reverted back to its original state (no "Creative" tag).
- Committed as `d794bb1 "added creatives route"`, confirmed via `git fetch origin main` to be present on `origin/main` on GitHub. **Not yet deployed to the live Cloud Run service** (verified by live curl test — still 500s on binary columns).

---

## Failed Attempts

- **What was tried**: Assumed `creative_prod` table/column names matched the C# EF Core model property names exactly (PascalCase: `ProductDesignId`, `StockNumber`, `BrandId`, etc.), based on reading `RGMCCreative`'s `Models/*.cs` files. — **Why it failed**: Live testing against the real `rgmc-gcp-api` endpoint revealed the actual SQL Server columns are **camelCase** (`productDesignId`, `stockNumber`, `brandId`, etc.) while table names remain PascalCase (`ProductDesign`, `Brand`, etc.) — a mixed convention not visible from the C# code alone (EF Core must have an explicit column-naming convention configured somewhere not read). Fixed by testing every table/column via `curl` against the live API before trusting it, and rewriting all field-name references in `main.py` to camelCase. **Lesson for future work on this integration: always verify column names against the live API, never trust the C# model names.**
- **What was tried**: A secondary bug from the same root cause — `_creative_lookup_name()`'s default `name_column` parameter was `'Name'` (capital N). — **Why it failed**: Silently returned `None` for every lookup (Brand, Category, ItemGroup, Designer, Supplier, DesignStatus) because `.get('Name')` on a dict with lowercase keys just returns `None`, no exception raised — this masked the bug until a live test showed `brand: null` etc. despite no errors in the server log. Fixed by changing the default to `'name'` (lowercase, line `main.py:82`).
- **What was tried**: Built a full "insert new photo into creatives DB" feature: a new `POST /creative/product-design-pictures` endpoint in `rgmc-gcp-api` (SQLAlchemy INSERT with a Pydantic request model), plus a `POST /api/creative/pictures` proxy route in this app's `main.py`, plus JS that uploaded "Add More Photos" selections to both the creatives DB and the local batch. — **Why it failed**: Not a technical failure — the user explicitly said afterward "the insertion of image to the productdesignpicture is not needed... the additional image is for supplementary image to make the ai produce more accurate description." Reverted: removed the write endpoint and its Pydantic model + now-unused imports (`base64`, `datetime`/`timedelta`, `BaseModel`/`Field`) from `mssql_routes.py`, removed the "Creative" tag from `main.py`'s `tags_metadata`, removed `/api/creative/pictures` from this app's `main.py` (and its now-unused `base64` import), and simplified the JS handler to just call `addFileItems()` locally with no `fetch()` call. Relabeled the button "Add More Photos" → "Add Supplementary Photos" and updated the helper text accordingly.
- **What was tried**: Clicking browser-automation element refs (e.g. `ref_32` for the "Search Creatives DB" label) directly via `mcp__claude-in-chrome__computer` with `ref:` parameter, and via `file_upload` on a scratchpad-generated test image. — **Why it failed**: Ref-based clicks on the radio-toggle labels silently did nothing (page state didn't change) — had to fall back to coordinate-based clicks taken from a screenshot captured immediately beforehand. `file_upload` refused a scratchpad path ("only files this session is allowed to read can be uploaded") — worked around by code-reviewing the upload handler instead of a live UI test, since the handler was simple enough (no `fetch()` call) to verify by inspection.
- **What was tried (from the previous session, still relevant)**: Writing literal `{{ATTRIBUTES}}` etc. directly in Jinja template source (`index.html`, `config.html`) expecting it to render as literal text. — **Why it failed**: Jinja interprets `{{ }}` as an expression and silently renders undefined variables as empty string — this was a **pre-existing bug** discovered and fixed by wrapping in `{% raw %}...{% endraw %}`.

---

## Next Step

**Tell the user clearly (in the next message, if not already done) that the `rgmc-gcp-api` fix was auto-committed AND auto-pushed to `origin/main` on GitHub without an explicit `git push` from Claude**, and that production (Cloud Run) does not yet have it deployed. Concretely:

1. Confirm with the user whether they want to deploy `rgmc-gcp-api` now. If yes, the command (from that repo's README/handoff.md) is along the lines of:
   ```
   cd "C:\RGMC\Source\git\rgmc-gcp-api"
   gcloud builds submit --tag gcr.io/<PROJECT_ID>/rgmc-gcp-api
   gcloud run deploy rgmc-gcp-api --image gcr.io/<PROJECT_ID>/rgmc-gcp-api --region asia-southeast1 --platform managed --allow-unauthenticated
   ```
   (exact project ID / flags not yet confirmed — read `C:\RGMC\Source\git\rgmc-gcp-api\README.md` "Deploying to Cloud Run" section and `handoff.md` for the precise invocation before running anything).
2. Investigate *why* the auto-push happened — check for a Claude Code hook (e.g. in that repo's `.claude/settings.json` or a global hook) that runs `git commit` + `git push` after edits, since this could surprise a future session working in that repo. This weirdness is specific to `C:\RGMC\Source\git\rgmc-gcp-api`, not this repo.
3. Once deployed, re-run the live smoke test to confirm images now load:
   ```
   curl -s -G "https://rgmc-gcp-api-935246372408.asia-southeast1.run.app/creative/by_table/latest" --data-urlencode "table_name=ProductDesignPicture" --data-urlencode "date_column=createDate" --data-urlencode "number_of_rows=1" -w "\nHTTP_STATUS:%{http_code}\n"
   ```
   Expect `200` with base64 `imageData` instead of `500 Internal Server Error`.
4. Then do one more live UI pass in this app: search a stock number known to have photos (need to find one — `D002-SMT05` currently has zero), confirm thumbnails render and "Add Existing Photos to Batch" produces real images in the Step 1 file list (not just the title/detail text).

No code changes are required for this next step unless the deploy surfaces a new bug — the `rgmc-attribute-checker` side is complete and already handles the "no photos yet" state gracefully.

---

## Context & Gotchas

- **Auto-commit/push hook in `rgmc-gcp-api`, NOT in this repo**: `C:\RGMC\Source\git\rgmc-gcp-api` apparently has a hook that commits AND pushes automatically (confirmed via `git log`, `git fetch origin main`, and `git rev-parse HEAD` == `git rev-parse origin/main`). This repo (`rgmc-attribute-checker`) only auto-commits locally per the original handoff note — unclear if it also auto-pushes; nothing was pushed from this repo this session since there's no remote-ahead evidence to check (verify with `git status -sb` next session). **Be very careful editing files in `rgmc-gcp-api` going forward — assume any edit may be committed and pushed to the shared team repo without an explicit git command.**
- **`creative_prod` schema convention**: table names PascalCase, column names camelCase. Verified tables so far: `ProductDesign`, `Brand`, `Category`, `ItemGroup`, `DesignStatus`, `Season`, `Designer`, `Supplier`, `ProductDesignPicture`. All have a `name` column (lowercase) except `ProductDesign` itself. `Designer` conveniently already has a pre-combined `name` field (`"KHEITH LHYNNE CRUZ"`) — no need to concatenate `firstName`/`lastName` manually.
- **`rgmc-gcp-api`'s generic `/{db_name}/by_table/value` and `/by_table/latest` endpoints** (`src/routers/mssql_routes/mssql_routes.py`) do `SELECT * FROM {table_name} WHERE {where_column} = :where_value` with the table/column names **string-interpolated** (not parameterized) — acceptable for this internal, unauthenticated, read-only service per existing convention, but this is why adding a raw SQL write endpoint (now reverted) felt riskier and the user vetoed it.
- **No auth** on `rgmc-gcp-api` at all — confirmed via code reading and live testing. Treat it as an open internal service.
- **Real test data used for verification** (safe, read-only): stock number `D002-SMT05` / `productDesignId=28032` / `productDesignCode=KL002-MT05`, Brand "DANSEN" (Keywest One International Corp — men's formal wear, per org context), zero photos on file. Do not assume this product has photos — pick a different one when testing the image path after deploy.
- **Flask dev server on this machine**: always `PORT=8888 py -3 main.py` (optionally `API_KEY=test` to bypass the "API_KEY not set" error during UI-only testing) — ports 5000/5050 are blocked by Windows, `python`/`python3` aren't on PATH.
- **Browser automation gotcha**: element refs from `read_page`/`find` can silently no-op when clicked on this app's custom radio-toggle labels (`.ps-option`) — always fall back to coordinate clicks from a screenshot taken immediately before clicking, since the page can scroll/reflow between calls.
- **`file_upload` tool sandbox**: cannot upload from the session's scratchpad directory — only files the user has explicitly shared. Generated a throwaway test image there for this purpose and had to abandon the live upload test as a result; relied on code review instead (justified here since the handler is a 3-line no-network function).
- **Design register**: this is a `product` (internal tool) register per `PRODUCT.md` — workflow clarity over decoration, desktop-first, WCAG AA. All new UI (search panel, result card, required-inputs grid) was built to reuse existing CSS tokens/classes (`--orange`, `.ps-toggle`, `.field-label`, `.upload-btn`, etc.) rather than introducing new visual patterns.
- **Nothing in this repo has been committed this session** — all changes listed above are sitting in the working tree. The user has not asked for a commit yet.
