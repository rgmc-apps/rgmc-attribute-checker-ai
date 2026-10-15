# Handoff

## Goal

Build and maintain the RGMC Attribute Checker — a Flask web service where merchandisers upload (or look up via stock number) clothing product photos, send them to OpenAI's GPT Vision for structured e-commerce descriptions, and download the results as Excel/CSV/text. Admins configure brands, prompt templates, and attribute sets per brand via a Config screen. A companion FastAPI service, `rgmc-gcp-api` (separate repo, shared across RGMC), provides creatives-database access and (as of this session) generic email sending.

**This session's arc**, roughly in order:
1. Verified the stock-search → creatives-DB integration from the prior session was live and working.
2. Added a live, auto-updating Prompt Template preview panel.
3. Made Material Composition / Care Instructions optional (were previously required).
4. Added auto-grouping: photos from the same stock-number search combine into ONE GPT call / ONE description, instead of one description per photo. Manually uploaded photos stay one-per-photo.
5. Confirmed (no code change needed) that processing is already one-GPT-call-per-item, looped across items — never batching multiple products into one prompt.
6. Added an editable "Output File Name" field (names the XLS/CSV and the downloaded zip).
7. **Major feature**: per-description-batch Prompt Template + Required Inputs. Each product searched from the creatives DB now keeps its own Brand/Category/Material/Care/Additional-Info/Prompt-Template snapshot (a "Editing settings for" dropdown lets you switch between them), instead of one shared set that got clobbered every time you searched a new product.
8. Fixed `gpt-5-mini` producing zero output rows: OpenAI's newer reasoning models (GPT-5.x/6.x) reject `max_tokens` — need `max_completion_tokens`. Also fixed a latent MIME-type bug (images were always labeled `image/jpeg` regardless of actual format).
9. Added email-logging: `rgmc-gcp-api` got a new generic `send_generic_mail()` + `POST /notifications/send-mail` route; the Attribute Checker calls it to auto-email a job's log on any failure, plus a manual "Email this log" button always available after a run.
10. **Root-caused and fixed** the "XLS only has images, no title/description/attributes" bug: `gpt-5-mini` (a reasoning model) was sometimes spending its entire `max_completion_tokens` budget on hidden reasoning, returning `choices` with **empty content** but `finish_reason: "length"` — which passed the code's only check (`if not choices`) and silently "succeeded" with blank data. Fixed by raising the token budget to 4096 and adding an explicit empty-content check that now treats this as a proper failure.
11. **Interrupted/unfinished**: a request to look up fabric details from the `Fabric` table in `creative_prod` (joining on fabric code) instead of trusting the flat `fabricName` column on `ProductDesign`, for the Material Composition auto-fill. This was cut off mid-investigation when the user pivoted to the email-logging request (item 9 above) and was never returned to. See "Next Step" below — this is the one real piece of unfinished work.

## Current State

**Everything from items 1–10 above is implemated, tested, and already committed AND pushed to origin** — in both repos. This is a change from earlier in the session: both `rgmc-attribute-checker` and `rgmc-gcp-api` have auto-commit+push hooks that fire on file edits (confirmed by `git log` / `git rev-parse HEAD` == `git rev-parse origin/<branch>` in both repos — see Context & Gotchas). No one ran `git commit` or `git push` explicitly this session; it happened automatically.

- `rgmc-attribute-checker` is on `master`, HEAD = `996aff1` ("DI-0140 added gpt limit"), matches `origin/master` exactly. Working tree is clean.
- `rgmc-gcp-api` is on `main`, HEAD = `ffa7655` ("DI-0143 added email sending"), matches `origin/main` exactly. Working tree is clean.

**Item 11 (Fabric table lookup) is NOT implemented** — investigation was in progress and got interrupted. See Next Step.

### What's verified working (this session, with real tests):
- Live prompt preview, optional Material/Care, auto-grouping (verified live in Chrome with real stock numbers `W397-00320` and `D002-SMT05`), output filename sanitization, per-batch settings selector (verified switching between two real products' settings, including that manual edits survive switching away and back).
- `gpt-5-mini` end-to-end with a **real OpenAI API key** the user pasted into chat (see security note below) — confirmed a real photo produces a real, correct description.
- The empty-content/reasoning-token bug: reproduced synthetically (mocked GPT response with empty `content` + `finish_reason: "length"`) and confirmed it's now caught as an explicit failure with zero rows written (previously: silent blank row with just an image).
- Email logging: verified against a local mock of the new `/notifications/send-mail` endpoint (the real gcp-api endpoint is pushed but **not yet deployed to Cloud Run** — see below) — both auto-send-on-failure and the manual button correctly POST a well-formed subject/body and the UI reflects "Sent ✓".

### What's NOT yet live:
- `rgmc-gcp-api`'s new `/notifications/send-mail` endpoint is pushed to `origin/main` but **not deployed to Cloud Run**. Until deployed, both the auto-send-on-failure and the manual "Email this log" button in the Attribute Checker will fail (network/404 error), though this fails gracefully — it does not break job processing itself, it just means no email actually goes out yet.

## Files Actively Being Edited

Nothing is mid-edit — everything is committed. For reference, the files touched this session:

**`C:\claude\rgmc-attribute-checker`** (all committed, see commits above):
- `helper/attribute_generator.py` — heavily reworked this session: per-group prompt building (`__resolve_group_settings`, `__build_prompt(group_key)`), groups changed shape to `{'files': [...], 'group_key': str}`, MIME-type detection (`_detect_mime`), `max_completion_tokens` (was `max_tokens`, now 4096, was briefly 1000), empty-content safety check, per-job log transcript (`job_log` / `log_text` in the result dict), `success_count`/`total_groups` returned explicitly, filename sanitization (`_sanitize_filename`).
- `main.py` — `_group_files()` groups by `group_key` with `DEFAULT_GROUP_KEY = '__default__'`; `/process` now parses `group_settings` JSON (per-group Required Inputs + Prompt Template) instead of flat form fields; `output_filename` handling; `_send_job_log_email()` + `POST /api/jobs/<job_id>/email-log` route; `_run_job` auto-emails on any failure (including full job crashes, via `traceback.format_exc()` as the log).
- `templates/index.html` — Live Preview panel (Step 2), Material/Care badges changed to Optional, group badges in the file list ("X photos → 1 description"), the "Editing settings for" selector + all its JS (`groupSettings` map, `currentGroupKey`, `switchToGroup`, `applyFieldsSnapshot`, `currentFieldsSnapshot`, `refreshGroupSettingsSelector`), Output File Name field, "Email this log" buttons on both error and result cards + `emailJobLog()` JS.
- `.github/workflows/dev-item-comment.yml` — **appeared in commit `3999c73` that I did not create**. Likely swept in by whatever auto-commit hook is active (see Gotchas) picking up an untracked file. Worth a quick look next session to confirm it's benign/expected and not something that needs reverting.

**`C:\RGMC\Source\git\rgmc-gcp-api`** (all committed, see commit `ffa7655`):
- `src/services/send_mail.py` — added `send_generic_mail(subject, body, recipients=None)`, generic (not SBIC-branded like the pre-existing `send_mail()`), falls back to the service's own `config.mail_recipient` env var when no explicit recipients given.
- `src/routers/notification_routes.py` (new file) — `POST /notifications/send-mail` wrapping the above.
- `src/routers/__init__.py`, `src/main.py` — registered the new router, following the exact pattern of the existing `mssql_router`.

## Failed Attempts

- **What was tried**: Testing with a PowerShell script that built multipart/form-data bodies via string concatenation (`-join $LF`) and passed the result as a string to `Invoke-WebRequest -Body`. — **Why it failed**: PowerShell re-encodes string bodies (likely as UTF-8) before sending, corrupting binary image bytes >127 that had been embedded via an ISO-8859-1/Latin-1 round-trip. This produced a real-looking but totally bogus "You uploaded an unsupported image" error from OpenAI that had nothing to do with the app code. **Fixed by switching to Python's `requests` library** (`files=[('images', (filename, fileobj, mimetype))]`) for any test that uploads real binary image data — this works correctly and should be the default approach for future local testing of `/process`.
- **What was tried**: Assumed the XLS-only-shows-images bug was a formatting/visual issue (image overlapping text columns, row height too short, etc.) and spent time on pixel/point-conversion theories. — **Why it failed**: Wasted effort — the actual bug was a backend logic gap (empty GPT content silently treated as success), not a spreadsheet formatting issue. Confirmed by direct `openpyxl` inspection of a generated file (showed real None/empty cell *values*, not just invisible-but-present data) and by asking the user to confirm the job's completion message said "no failures" despite blank columns — that combination pointed straight at the real cause. **Lesson: when a `.xlsx` generated by xlsxwriter looks wrong, read it back with `openpyxl` first before theorizing about Excel rendering quirks.**
- **What was tried (interrupted, not a failure, just incomplete)**: Looked up `Fabric` table by `fabricCode` from a `ProductDesign` row (`LH25805`) — returned `{"record_count":0,"data":[]}`. Then fetched `Fabric`'s latest rows to see its real schema: `fabricId, fabricCode, name, remark, isActive, createDate, ...` with `fabricCode` values like `FR0140`, `FR0139`, `FR0138`. — **Why it's unresolved, not failed**: The code *formats* don't match (`LH25805` vs `FR0140`-style), so either (a) that specific ProductDesign's fabric genuinely has no `Fabric` table entry, (b) `fabricCode` on `ProductDesign` isn't actually comparable to `Fabric.fabricCode` as text (could be two different coding schemes — e.g. a supplier/swatch code vs. an internal RGMC code), or (c) the real FK column on `ProductDesign` is something else entirely (e.g. a numeric `fabricId`) that hasn't been checked yet. See Next Step.

## Next Step

**Resume the Fabric table lookup task** (user's request, verbatim): *"for the items searched from creatives, look up for the fabric details on the fabric table (on creative_prod) using the indicated fabric code and indicate the fabric name on the material composition field."*

1. First, resolve the join-key mystery before writing any code. Fetch a **raw, full** `ProductDesign` row (not just the fields `main.py` currently extracts) to check for a `fabricId` column alongside `fabricCode` — mirrors the `brandId`/`categoryId`/`designerId`/`supplierId` FK pattern used everywhere else in this integration:
   ```
   curl -s -G "https://rgmc-gcp-api-935246372408.asia-southeast1.run.app/creative/by_table/value" --data-urlencode "table_name=ProductDesign" --data-urlencode "where_column=stockNumber" --data-urlencode "where_value=W397-00320"
   ```
   Look at **every key** in the returned row, not just the ones `main.py` currently reads. If there's a `fabricId`, that's almost certainly the correct FK into `Fabric.fabricId` — use that instead of text-matching `fabricCode`.
2. If there's genuinely no `fabricId` and `fabricCode` really is the only link, test whether `ProductDesign.fabricCode` values match `Fabric.fabricCode` for *other* known products (the earlier single test used `LH25805` which didn't match — try a few more stock numbers' fabric codes against `Fabric` before concluding the join doesn't work via `fabricCode`).
3. Once the correct join is confirmed, wire it into `main.py`'s `/api/creative/search` route (around line 684-685, currently `'fabric_code': row.get('fabricCode'), 'fabric_name': row.get('fabricName')`) — add a `_creative_lookup_name('Fabric', '<correct_fk_column>', <value>, name_column='name')` call (reusing the existing helper, same pattern as Brand/Category/Designer/Supplier) and use **that** looked-up name for `fabric_name` instead of the flat `row.get('fabricName')`.
4. The frontend already consumes `data.fabric_name` for Material Composition auto-fill (`templates/index.html`, `fillRequiredInputsFromCreative` → `buildSettingsFromCreative`, the `material` field) — no frontend change should be needed once the backend returns the correct looked-up name under the same `fabric_name` key.
5. Test live against the creatives API with a few real stock numbers before considering this done (this whole integration has a documented history of camelCase/PascalCase and wrong-default-casing bugs — see repo's own `CLAUDE.md`/prior handoffs — always verify against the live API, never assume).

## Context & Gotchas

- **Both repos auto-commit AND auto-push on file edits.** Confirmed this session for `rgmc-attribute-checker` (previously only suspected) in addition to the already-known `rgmc-gcp-api` behavior. Every `Edit`/`Write` call in this session ended up as a real commit on `origin/master` / `origin/main` with no explicit `git commit`/`git push` from Claude. **Treat every file edit in either repo as effectively public/shared immediately** — there is no "uncommitted draft" safety net in this environment.
- **`rgmc-gcp-api` is actively used by others.** Between this session's last known commit (`d794bb1`) and the new email-sending commit, an unrelated commit (`76d5d04`, "fix: stop routing Manila Taste Curators rows to a nonexistent 'MTC' BC company") landed from someone/something else. Don't assume you have the repo to yourself.
- **⚠️ Security: a real, live OpenAI API key was pasted directly into this chat conversation** (user's choice, after being offered a safer `setx`-based alternative). It was used only in-memory for local dev-server testing this session and never written to disk, but it now exists in plaintext in this conversation's transcript/history. **The user was told to revoke/rotate it at platform.openai.com** — unknown whether they've done so. If this comes up again, don't re-paste or echo the old key; confirm it's been rotated.
- **`gpt-5-mini` (and GPT-5.x/6.x generally) are reasoning models.** They consume part of their `max_completion_tokens` budget on hidden reasoning before any visible output. Too-low a budget (this app started at `1000`) can produce a response with non-empty `choices` but empty `content` and `finish_reason: "length"` — a "successful-looking" failure that's easy to miss. Current fix: budget raised to 4096 (gpt-5-mini's actual ceiling) + an explicit empty-content check. If a future model has a much higher ceiling and still hits this, raise the constant further; it's currently hardcoded, not an env var, in `helper/attribute_generator.py`'s `__get_gpt_response`.
- **PowerShell + binary multipart bodies don't mix** (see Failed Attempts) — always use Python `requests` for any future local test of file-upload endpoints in this app.
- **To test locally**: `PORT=8888 API_KEY=<key> py -3 main.py` (ports 5000/5050 are blocked on this machine; `python`/`python3` aren't on PATH, must use `py -3`). For testing the email feature without the real gcp-api deployed, a tiny Flask mock server on port 8899 implementing `POST /notifications/send-mail` works (see the now-deleted `mock_mail_server.py` pattern — recreate if needed, it was ~15 lines).
- **Design register**: this is a `product` (internal tool) per `PRODUCT.md` — workflow clarity over decoration. All new UI this session reused existing CSS tokens/classes rather than introducing new visual patterns.
- **The `.github/workflows/dev-item-comment.yml` file** that appeared in a commit this session was not intentionally created — worth a quick sanity check next session that it's expected/harmless.
