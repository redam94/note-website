# Integrations Plan

Planner / assistant feature: link external services (GitHub, Gmail, Google Drive/Docs, Google Calendar) into the notes-and-graph system so project information and timelines can be managed from one place.

## Ground rules

- **Identity**: single admin. "Multiple accounts" means multiple external accounts linked to the admin, not multi-tenant auth. Linked-account data is admin-only to view by default; admin can flip individual notes to reader-visible.
- **Scope**: this document covers GitHub in detail. Gmail/Drive/Calendar reuse the Phase 0 foundation; their specifics will be planned separately once GitHub Phases 0–3 ship.
- **Next.js 16 caveat**: route handlers (OAuth callbacks) changed shape from older Next versions. Read `node_modules/next/dist/docs/` for the current signature before writing frontend routes (per `AGENTS.md`).

---

## Phase 0 — Foundation (shared across all integrations)

Ship once; reused by every later integration.

- **`connected_accounts` table**
  - Columns: `id`, `integration_type` (`'github' | 'gmail' | 'drive' | 'calendar'`), `label`, `external_account_id`, `oauth_access_token_enc`, `oauth_refresh_token_enc`, `token_expires_at`, `scopes`, `metadata_json`, `created_at`.
  - Admin-owned implicitly (no `user_id`); one row per linked external account.
- **Token encryption utility**
  - Fernet/AES-GCM using `INTEGRATION_ENC_KEY` from env (add to `config.py`).
  - Wrap on write, unwrap on read. Never log plaintext tokens.
- **Visibility on notes**
  - Add `visibility TEXT NOT NULL DEFAULT 'public'` (values: `'admin' | 'public'`).
  - Existing notes migrate to `'public'` (backward compat).
  - Integration-sourced notes default to `'admin'`.
- **Read-path filtering**
  - `notes`, `search`, `graph`, `ask`, `chat` routers filter `visibility = 'public'` for non-admin callers. Admin sees everything.
- **Publish toggle**
  - `PATCH /api/notes/{id}/visibility` — admin-only.
  - Bulk variant later in Phase 7.

**Ships**: nothing user-visible; unblocks every later phase.

---

## Phase 1 — GitHub credential plumbing

- **Auth method**: **GitHub App** (recommended over OAuth App or PAT).
  - Per-repo installation scopes, webhook subscriptions, higher rate limits, refresh-token model.
  - Trade-off: one-time app registration, install-per-account flow.
  - Fallback plan: if webhooks are blocked (e.g. localhost dev), start in polling mode; swap to webhooks once deployed.
- **Routes**
  - `GET /api/integrations/github/install` → redirect to GitHub install URL.
  - `GET /api/integrations/github/callback` → exchange code, persist `connected_accounts` row.
  - `GET /api/integrations/github/accounts` → list linked accounts (admin only).
  - `DELETE /api/integrations/github/accounts/{id}` → disconnect + cascade cleanup.
- **Frontend**
  - `/settings/integrations` page with "Connect GitHub" button and linked-account list.
  - OAuth callback route as a Next.js 16 route handler (check current signature in `node_modules/next/dist/docs/`).
- **Secrets**: `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_WEBHOOK_SECRET` — via `.env`.

**Ships**: admin can link/unlink GitHub accounts; tokens stored encrypted.

---

## Phase 2 — Repo + resource selection

- **`integration_resources` table**
  - Columns: `id`, `account_id` (FK), `resource_type` (`'repo' | 'wiki' | 'issues' | 'prs'`), `external_id`, `name`, `enabled`, `last_synced_at`, `sync_cursor`, `config_json`.
  - Enables per-repo opt-in rather than "sync everything".
- **Discovery endpoint**: `GET /api/integrations/github/accounts/{id}/repos` — lists installation-accessible repos.
- **Selection UI**: admin toggles which repos to track and which of their sub-resources (issues, PRs, wiki) are enabled.
- **Space mapping**: per-repo, admin chooses a target space (existing or auto-created `github/{owner}-{repo}`).

**Ships**: admin picks the tracked surface before any ingest runs.

---

## Phase 3 — Issues + PRs ingest

- **Sync worker**: full initial sync on enable, then incremental (`since=last_synced_at`, paginated).
- **Mapping**: each issue/PR → one `notes` row.
  - `source = 'github:issue'` or `'github:pr'`
  - Frontmatter: `repo`, `number`, `state`, `author`, `labels`, `assignees`, `created_at`, `updated_at`, `html_url`, `external_id`
  - Body → note `content`
  - `visibility = 'admin'` by default
  - `tags`: `github/{owner}-{repo}`, `state/{open|closed}`, plus repo labels
- **Dedup**: `(source, external_id)` uniqueness check before insert; update-in-place on re-sync.
- **Rate limits**: honour `x-ratelimit-remaining` / `x-ratelimit-reset`; exponential backoff on 403/429.
- **Webhooks (optional, requires public URL)**: `issues`, `issue_comment`, `pull_request`, `pull_request_review` → trigger targeted re-sync.

**Ships**: GitHub issues/PRs appear as searchable, admin-only notes in the mapped space.

---

## Phase 4 — Wiki ingest (pull direction)

- GitHub wikis are standalone git repos (`{repo}.wiki.git`).
- Shallow-clone to a managed dir under `uploads/`, walk markdown files, one note per page.
  - `source = 'github:wiki'`
  - Frontmatter: `repo`, `page_name`, `html_url`, `external_id` (= page path + sha)
  - Preserve sidebar/footer as separate notes or skip (configurable).
- **Change detection**: compare per-file blob sha; only re-ingest changed pages.
- **Trigger**: webhook `gollum` event (wiki pushes) or periodic poll.

**Ships**: GitHub wiki content is readable/searchable inside the notes app.

---

## Phase 5 — Graph integration

Extend `services/graph_builder.py` edge sources to recognize GitHub cross-refs — no new graph table needed.

- **Cross-ref patterns → edges**
  - `#123` / `owner/repo#123` → link to that issue/PR note
  - `closes #N` / `fixes #N` / `resolves #N` → typed `closes` edge (vs plain mention)
  - Cross-repo refs → link if target note exists; otherwise drop silently (logged)
- **Already-indexed inputs**: GitHub-sourced notes use existing wikilink / frontmatter edge mechanics automatically — no new work for those.

**Ships**: issues, PRs, wiki pages are first-class graph nodes; UI graph view works unchanged.

---

## Phase 6 — Smart spaces (vault filters)

A separate subsystem; ships only after there's varied content to filter.

- Add `filter_query TEXT NULL` to `spaces`. JSON DSL, e.g.:
  ```json
  {"source": "github:issue", "tags": ["bug"], "account_id": 2, "state": "open"}
  ```
- When a space has a `filter_query`, `list_notes_in_space` runs the filter instead of `space_id = ?`. Notes keep their canonical space; filtered spaces are virtual views.
- Filter DSL fields v1: `source`, `tags` (AND/OR), `account_id`, `since`, arbitrary frontmatter key match.

**Ships**: admin can create spaces like "GitHub bugs this week" or "All PRs in repo X" without moving notes.

---

## Phase 7 — Publish workflow (admin → public)

- **Inline toggle**: note viewer has an admin-only "publish to readers" control flipping `visibility`.
- **Bulk publish**: action on a smart space — "publish all notes in this view".
- **Reader view**: existing endpoints already filter on `visibility = 'public'` (Phase 0) — no reader-side code changes needed here.
- **Audit trail (nice-to-have)**: `note_visibility_changes` table with `(note_id, from, to, actor, ts)` for undo / history.

**Ships**: admin can curate what readers see; the default remains private.

---

## Phase 8 — Wiki publishing (push direction: notes → GitHub wiki)

Reverse of Phase 4. The note-generation pipeline already produces well-structured markdown with frontmatter and `[[wikilinks]]`; this phase makes those publishable as a GitHub wiki.

### 8a — Publish target + auth

- Reuse the GitHub App install (Phase 1); add `contents: write` scope to the installation.
- New `integration_publish_targets` table: `id`, `account_id`, `repo_id`, `wiki_branch` (default `master`), `last_published_sha`, `config_json` (path prefix, page-name strategy).
- Per-space "publish target" setting — a space opts in to sync to a specific repo's wiki.

### 8b — Wikilink → GitHub wiki link transform

GitHub wiki (Gollum) link conventions:
- Page file on disk: `Page-Name.md` (spaces → dashes; URL-unsafe chars stripped or replaced).
- Link syntax: `[[Page Name]]` (renders to the page above) or `[[Display Text|Page Name]]` for aliases.
- All pages live in a flat namespace at wiki root; subfolders exist but cross-folder linking is awkward — v1 flattens.

Transform layer (runs at publish time, not at note-save time):

1. **Resolve each `[[target]]`** in the note to its canonical note (by slug or title).
2. **Filter by visibility**: if the target note is `visibility = 'admin'` (i.e., not being published), either:
   - rewrite `[[X]]` to plain italic text `_X_` (default), or
   - drop the link and keep display text, controlled by a `unpublished_link_strategy` config.
3. **Compute wiki page name** for every published note:
   - Start from `note.title`.
   - Strip/replace GitHub-wiki-unsafe chars: `/ \ : * ? " < > |` → removed; non-ASCII → transliterated.
   - Collapse whitespace → single space (GitHub converts to `-` on save).
   - Disambiguate collisions by appending ` (N)`.
4. **Emit** `[[Display|Page Name]]` when the resolved page name differs from the display text; otherwise `[[Page Name]]`.
5. **External links** (http/https) pass through unchanged.

The transform is pure — takes `(note_body, note_registry, visibility_map)` and returns transformed markdown + list of orphaned link warnings.

### 8c — Prompt updates

Two prompt changes so generated notes are publish-friendly from the start (not just patched at publish time):

- **`create_note.yaml` / `insert_links.yaml`**: add explicit guidance that wikilink targets should use the note's canonical title (human-readable), not its slug. Reason: the publish transform maps title → wiki page name; slugs produce ugly wiki URLs.
- **New prompt `wiki_polish.yaml`**: optional pre-publish pass that, for each note selected for wiki publish, rewrites:
  - Section headings to be H1-free at the top (GitHub wiki auto-adds page title).
  - Dangling wikilinks (targets not in the publish set) into prose references or footnotes with source URLs.
  - Internal image refs to public URLs if images are also published, else to a note explaining the image is internal-only.
- **Quality-check extension**: `quality_check.yaml` gains a "wiki-ready" mode that flags links to unpublished notes, absolute-path image refs, and headings that would collide with the implicit page title.

### 8d — Publish pipeline

1. Admin selects a space (and optionally a smart-space filter) to publish.
2. For each included note: run the transform (8b), optionally the `wiki_polish` prompt (8c).
3. Clone/pull the wiki repo (`{repo}.wiki.git`) into a working dir.
4. Write each transformed note to `Page-Name.md`.
5. Build/update `_Sidebar.md` from the space's note hierarchy (level-1 notes as top entries, children nested).
6. Build/update `_Footer.md` with a generated-by notice.
7. Commit with a structured message (`chore(wiki): sync N pages from {space}`); push.
8. Record `last_published_sha` per target; only re-push on diff.

### 8e — Endpoints

- `POST /api/integrations/github/publish` — body: `{ space_id, target_id, dry_run }`. Dry-run returns the diff + orphan-link report without pushing.
- `GET /api/integrations/github/publish/history` — last N publish runs + their outcomes.

### 8f — Safety / guardrails

- Never push a note with `visibility = 'admin'`.
- Warn (dry-run fails loud) if >30% of links become orphaned — likely the admin accidentally left most targets private.
- First publish to a non-empty wiki requires an explicit `--overwrite` flag; otherwise abort to protect hand-written wiki content.

**Ships**: admin picks a space, clicks "Publish to wiki", and selected notes appear as a navigable wiki on GitHub with working cross-links.

---

## Deferred (not in this plan)

- GitHub Projects v2 (GraphQL-only; shape differs). Candidate Phase 9.
- Commits as graph nodes (high volume, low signal by default).
- Write-back from notes → GitHub issues/PRs (read first, write only once trust is established).
- Gmail / Drive / Docs / Calendar specifics — separate plan once GitHub 0–3 have landed and the foundation is validated.

---

## Suggested ship order

Phase 0 → 1 → 2 → 3. Pause here: you now have GitHub issues/PRs as searchable, graphed notes. Use it for a week.

Then 4 → 5 → 7 (skip 6 initially unless filtering pain is real).

Then 8 once there's curated, published content worth mirroring to a wiki.

Phase 6 (smart spaces) slots in wherever filtering pain bites — likely between 5 and 7.
