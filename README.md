<!-- --8<-- [start:docs-index-intro] -->

# Yamtrack

![GitHub](https://img.shields.io/badge/license-AGPL--3.0-blue)

Yamtrack is a self hosted media tracker for movies, tv shows, anime, manga, video games, books, comics, and board games.

<!-- --8<-- [end:docs-index-intro] -->

> **Fork / attribution**
> This repository is a fork of and derivative of **[FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack)** by **FuzzyGrim**.
> The original project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** and this fork is distributed under the **same license**. The full license text is in [`LICENSE`](LICENSE). All upstream copyright and license notices are retained. See the license for your rights and obligations when using, modifying, or redistributing this software.

## 🔒 Changes vs. upstream

This fork focuses on **hardening the default configuration** of the upstream
Yamtrack. Notable differences (beyond the smaller fixes below, the feature set is
mostly the same):

- **Home page `Completed` section.** Beyond upstream's *In Progress* and
  *Planning*, the Home page now also shows a **Completed** section listing
  finished movies and TV seasons (emerald accent, per-status icon/sort).
- **No hardcoded secrets.** All credentials (Django `SECRET`, TMDB/TVDB/MAL/IGDB/
  BGG/Hardcover/ComicVine/Trakt/Simkl/AniList API keys) must now come from
  environment variables or secret files — the baked-in defaults were removed.
  `SECRET` **fails closed** for server processes (gunicorn/celery), so a
  production start without one is refused instead of silently using a weak key.
- **Celery uses JSON serialization.** Tasks are sent as JSON with JSON-safe
  arguments (file content/IDs instead of pickled objects). `application/x-python-serialize`
  remains in `accept_content` for Celery's internal control channel.
- **SSRF guard for notifications.** Apprise notification URLs are validated
  against private/loopback/link-local/cloud-metadata addresses, enforced on save
  and on send, so a notification URL can't be pointed at internal services.
- **Secure transport defaults** (env-overridable): `SESSION_COOKIE_SECURE` /
  `CSRF_COOKIE_SECURE` default to on in production, plus `SameSite`, HSTS and
  SSL-redirect settings.
- **Smaller fixes:** CSV export formula-injection sanitization, open-redirect
  fix on `HX-Redirect`, `clear_search_cache` restricted to staff, `@require_POST`
  on CSV imports, media-type validation on history deletion, and `api_request`
  restricted to `http(s)` schemes.
- **Safer defaults:** new-user `REGISTRATION` defaults to `false` and
  `SOCIALACCOUNT_LOGIN_ON_GET` defaults to `false` (login CSRF protection).
- Added a **`.env.example`** configuration template.

## 📚 Documentation

The full documentation is available at [fuzzygrim.github.io/Yamtrack](https://fuzzygrim.github.io/Yamtrack/).

<!-- --8<-- [start:docs-index-body] -->

## ✨ Features

- 🎬 Track movies, tv shows, anime, manga, games, books, comics, and board games.
- 📺 Track each season of a tv show individually and episodes watched.
- ⭐ Save score, status, progress, repeats (rewatches, rereads...), start and end dates, or write a note.
- 📈 Keep a tracking history with each action with a media, such as when you added it, when you started it, when you started watching it again, etc.
- ✏️ Create custom media entries, for niche media that cannot be found by the supported APIs.
- 📂 Create personal lists to organize your media for any purpose, add other members to collaborate on your lists.
- 📅 Keep up with your upcoming media with a calendar, which can be subscribed to in external applications using a iCalendar (.ics) URL.
- 🔔 Receive notifications of upcoming releases via Apprise (supports Discord, Telegram, ntfy, Slack, email, and many more).
- 🐳 Easy deployment with Docker via docker-compose with SQLite or PostgreSQL.
- 👥 Multi-users functionality allowing individual accounts with personalized tracking.
- 🔑 Flexible authentication options including OIDC and 100+ social providers (Google, GitHub, Discord, etc.) via django-allauth.
- 🦀 Integration with [Jellyfin](https://jellyfin.org/), [Plex](https://plex.tv/) and [Emby](https://emby.media/) to automatically track new media watched.
- 📥 Import from [Trakt](https://trakt.tv/), [Simkl](https://simkl.com/), [MyAnimeList](https://myanimelist.net/), [AniList](https://anilist.co/) and [Kitsu](https://kitsu.app/) with support for periodic automatic imports.
- 📊 Export all your tracked media to a CSV file and import it back.

## 📱 Screenshots

| Homepage                                                                                       | Calendar                                                                                    |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/homepage.png?v2" alt="Homepage" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/calendar.png" alt="calendar" /> |

| Media List Grid                                                                                    | Media List Table                                                                                     |
| -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/medialist_grid.png" alt="List Grid" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/medialist_table.png" alt="List Table" /> |

| Media Details                                                                                         | Tracking                                                                                    |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/media_details.png" alt="Media Details" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/tracking.png" alt="Tracking" /> |

| Season Details                                                                                          | Tracking Episodes                                                                                            |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/season_details.png" alt="Season Details" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/tracking_episode.png" alt="Tracking Episodes" /> |

| Lists                                                                                 | Statistics                                                                                      |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/lists.png" alt="Lists" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/statistics.png" alt="Statistics" /> |

| Create Manual Entries                                                                                         | Import Data                                                                                       |
| ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/create_custom.png" alt="Create Manual Entries" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/import_data.png" alt="Import Data" /> |

## 🐳 Installing with Docker

Download the default `docker-compose.yml` file from the repository, update the environment values, and start Yamtrack:

```bash
docker compose up -d
```

The default Compose file uses SQLite, which is enough for most personal installs. For full SQLite, PostgreSQL, and reverse proxy setup instructions, see the [Setup documentation](https://fuzzygrim.github.io/Yamtrack/release/setup/).

## 💻 Development

Development instructions are available in the [Development documentation](https://fuzzygrim.github.io/Yamtrack/release/development/).

## 💪 Support the Project

There are many ways you can support Yamtrack's development:

### ⭐ Star the Project

The simplest way to show your support is to star the repository on GitHub. It helps increase visibility and shows appreciation for the work.

### 🐛 Bug Reports

Found a bug? Open an [issue](https://github.com/FuzzyGrim/Yamtrack/issues) on GitHub with detailed steps to reproduce it. Quality bug reports are incredibly valuable for improving stability.

### 💡 Feature Suggestions

Have ideas for new features? Share them through [GitHub issues](https://github.com/FuzzyGrim/Yamtrack/issues). Your feedback helps shape the future of Yamtrack.

### 🧪 Contributing

Pull requests are welcome! Whether it's fixing typos, improving documentation, or adding new features, your contributions help make Yamtrack better for everyone.

### ☕ Donate

If you'd like to support the project financially:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/fuzzygrim)
