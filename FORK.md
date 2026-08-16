# FORK.md

**Fork of [FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack)** (AGPL-3.0).

- **Upstream** (`upstream` remote): `FuzzyGrim/Yamtrack`, pinned at commit `9063ad43` (`dev` @ v0.25.3). Read-only from this fork's perspective — do not push there.
- **Fork** (`origin` remote): `rpmalouin/yamtrack` (public).
- This fork is **22 commits ahead** of the fork point (see `git log --reverse HEAD --not 9063ad43`).

This document explains *why* the fork exists and *what* we've added on top of the
original. It complements the operator-facing deployment notes in
`MEMORY.md` at the deployment root and the `INSTALLATION.md` build guide.

---

## Why we forked

We run Yamtrack as a self-hosted media tracker over plain HTTP on a local homelab,
backed by shared Postgres/Redis containers. Upstream Yamtrack is excellent but is
developed with different priorities (public CI, prebuilt images, upstream roadmap),
so we maintain a long-lived fork to:

1. **Zero in on self-hosting realities.** Security hardening that matters on a
   self-hosted box with self-signed/local endpoints, plain-HTTP serving, and
   many services sharing one Docker network.
2. **Add features upstream does not have** for *physical* media management — a
   shelf-location field, CLZ movie-export import, a printable full-list report —
   and for *local (Plex/Emby) server* import + an "Unwatched" review flow.
3. **Keep the repo buildable and CI-clean on a public fork** without upstream's
   private CI secrets (provider API keys).
4. **Stabilize the live deployment** against shared-resource failure modes
   (shared Redis `celery` queue wedges, dead workers, duplicate-media rows).

We intentionally do **not** chase full upstream alignment; the fork stays pinned to
a known-good base and carries only changes we actually need.

---

## What we added

### 1. Self-hosting security hardening

Removed the hardcoded default `SECRET_KEY` and hardcoded third-party API keys from
`src/config/settings.py` — all secrets now load from environment/secret files only.

- `SECRET` **fails closed** for server processes (gunicorn/celery/supervisord);
  dev & management commands (`runserver`/`test`/`collectstatic`/`migrate`/…) get a
  throwaway dev key so image builds don't break.
- **Celery:** task serializer = `json`; `accept_content = ["json", "application/x-python-serialize"]`
  (pickle retained only because internal control/pidbox relies on it).
- **SSRF guard** for Apprise notification URLs (`src/app/apprise_guard.py`) —
  blocks private/loopback/link-local targets.
- **Secure-cookie / SameSite / HSTS / SSL-redirect** settings, all env-overridable,
  so a plain-HTTP deployment stays functional out of the box while HTTPS is possible.
- **CSV export** formula-injection sanitization (`src/integrations/exports.py`).
- **Open-redirect fix** on `HX-Redirect`; `@require_POST` on import endpoints;
  **staff-only** `clear_search_cache`; `delete_history_record` media-type validation;
  `api_request` http(s)-only scheme check.
- Added **`.env.example`** template (`cp .env.example .env`) documenting required vars.

### 2. Operational / Celery reliability

- **Dedicated `yamtrack` Celery queue** — `CELERY_TASK_DEFAULT_QUEUE` /
  `task_default_queue` in `config/celery.py` — instead of the shared default
  `celery` queue. This fixed a worker wedge where tasks got reserved (`PENDING`)
  but never executed while other apps (e.g. paperless-ngx) shared the same Redis
  queue. Depth is now `redis-cli LLEN yamtrack`.
- **Worker/beat auto-restart** — `autorestart=true`/`startretries=10` on the
  `celery` and `celery-beat` supervisord programs, so a crashed worker respawns.
- Default runtime timezone changed **Berlin → New York** (`TIME_ZONE` in settings).

### 3. Home screen: Completed section

The Home page now renders a third section — **Completed** (completed movies +
completed TV seasons) — alongside the existing **In Progress** and **Planning**
sections. It honors the same per-user sort, Hide-unreleased, and load-more
controls (#`home` view, `home_section.html`).

### 4. Plex integration

- **Plex library import** (Settings → Import → Plex): pulls watched movies and
  fully-watched shows as **Completed** via the Plex server HTTP API
  (`integrations/imports/plex.py`, `import_plex` task/view/URL, `plex` source
  display + logo, tests in `test_plex.py`).
  - Handles two upstream quirks: TLS skipped only for the user-supplied Plex URL
    via a new keyword-only `verify` param in `app/providers/services.py`, and
    `?includeGuids=1` appended for `tmdb://` `<Guid>` ids.
- **Unwatched review queue** — a new `Status.UNWATCHED` value, a review page at
  `/unwatched` (sidebar "Unwatched"), `plex.unwatched_importer`, and the
  `import_plex_unwatched` task. Lets you scan a Plex server's library for titles
  you haven't watched and queue/track them.
- **Persisted Plex connection** — the user's `plex_server_url` and Fernet-encrypted
  `plex_api_token` are saved on the `User` model and reused/prefilled by the
  Unwatched page and the completed import (`users/0058`).
- **Plex webhook username logging** bumped to `warning` so a payload-username
  mismatch (`poppabear1950` ≠ Yamtrack username `ron`) is visible in logs.

> Deployment note (not in the repo): Plex runs host-net `network_mode: host` and
> only answers loopback-sourced connections, so it is unreachable from inside the
> yamtrack container. A host TCP forwarder (`plex-forward`) relays
> `172.21.0.1:32421 → 127.0.0.1:32400`, and both Plex flows point at
> `http://172.21.0.1:32421`. See `MEMORY.md` at the deployment root.

### 5. Emby integration

- **Emby library import** source (`integrations/imports/emby.py`) added alongside
  Plex, and the two now share a common **importer core**
  (`integrations/imports/` shared helpers, incl. `should_process_media`) so both
  correctly reuse existing media rows instead of duplicating them.

### 6. Physical media: location field

- A **`location`** field on the abstract `Media` base (`app/models.py`), so Movie,
  TV, Anime, Manga, etc. all carry a physical-shelf location (migration `app/0065`,
  incl. the `Historical*` tables).
- **Editable inline on the detail page** — a `LOCATION` text card with a *Save
  location* button on every tracked item; htmx-POSTs to a new
  `update_media_location` view/route (`update-location/<media_type>/<int:instance_id>`).
- **CLZ Movies import** — `python manage.py import_locations [movies.json]`
  (`app/management/commands/import_locations.py`) matches a CLZ export to tracked
  media by TMDB-id then normalized title, and writes `location`. Multiple physical
  copies join into one field (e.g. `"A-131, A-132"`). Flags: `--user X`, `--dry-run`,
  `--skip-title`. Tests in `test_import_locations.py` (9 tests).

### 7. List view: Location column + printable report

- **Location column** in the media list **table** view, next to Title; grid cards
  show a pin+location line under the title. Layout remains per-user/per-type.
- **`Location` sort option** (`MediaSortChoices.LOCATION`, `users/0059` migration);
  ascending sort pushes empty locations to the end.
- **Print button** (table view only) renders the *entire filtered list* — not just
  buffered pages — as a clean black-on-white columnar report
  (`Title | Location | Score | Status | Start Date | End Date`). It htmx-GETs
  `?print=1&layout=table` (preserving sort/status/search) into the table body, then
  calls `window.print()`. `?print=1` mode returns every matching row in one page.

### 8. Bug fix: media "stuck" in a status (duplicate rows)

`media_save` (`app/views.py`) with no `instance_id` **always created a new row** for
an item the user might already own, so multiple rows for the same `item` existed
with different statuses — a movie could show in **Planning** and **Completed**
simultaneously and refuse to move. Fix:

- `media_save` now **reuses** the user's existing row when `instance_id` is absent
  (creates only if none exists).
- Hardened `Media.process_status`/`process_progress` so a provider/metadata failure
  can't abort a save (status still persists).
- One-time cleanup: `python manage.py dedupe_media [--user X] [--dry-run]` (keeps
  highest-status row, merges score/notes/dates, deletes stale duplicates; TV guarded).

### 9. CI / public-fork friendliness

- **Skip live-provider tests without configured keys**: `test_metadata.py`,
  `test_search.py`, and importer/webhook integration tests are decorated with
  `@requires(...)` (helper in `src/app/tests/providers/_skip.py`) so they **skip**
  when the corresponding API key is missing — keeping public CI green absent
  upstream's secrets (`TMDB_API`, `MAL_API`, `IGDB_ID/SECRET`, `HARDCOVER_API`,
  `COMICVINE_API`, `SECRET`).
- **`sync_metadata` refactor** to satisfy the repo `C901` complexity rule.

### 10. Docs & build

- **`INSTALLATION.md`** — a guide for building and deploying the fork locally
  (the `docker-compose.yaml` builds `./Yamtrack` locally rather than pulling the
  prebuilt `ghcr.io/yamtrack/yamtrack` image).
- **README** — fork attribution + AGPL-3.0 credit to the original upstream,
  removal of upstream CI badges and the demo section, and documentation of the
  fork-specific security changes.

---

## Staying in sync with upstream

Fork point: upstream `dev` at commit `9063ad43`. To pull upstream changes later:

```bash
git fetch upstream
# review / rebase your work onto the new upstream head, then:
git push origin main
```

> `.env` is gitignored/untracked in this repo, and the deployment `MEMORY.md`
> lives *outside* the git repo — neither is ever pushed. Use `.env.example` for
> the secret-free template.

## License

AGPL-3.0, inherited from [FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack).