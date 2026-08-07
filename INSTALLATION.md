# Installation

This guide covers running **your fork** of Yamtrack built **locally from source**
instead of pulling the published upstream image. Because this fork adds its own
features (Plex/Emby library import, the Unwatched queue, etc.), the Compose files
here build the local source into a `yamtrack:local` image — they do **not** use
`ghcr.io/fuzzygrim/yamtrack`.

## Prerequisites

- Docker and Docker Compose (v2+) installed.
- A clone of **this fork**:

```bash
git clone https://github.com/rpmalouin/yamtrack.git
cd yamtrack
```

## 1) Configure the environment

Copy the template and set real values. **Never commit `.env`** (it is gitignored).

```bash
cp .env.example .env
```

At minimum, set a strong `SECRET`:

```bash
# .env
SECRET=$(openssl rand -base64 48)
```

Every other value is optional with a sensible default. Set any metadata-provider
keys you want (e.g. `TMDB_API`). See `docs/env-variables.md` for the full reference,
and `.env.example` for every supported variable.

## 2) Choose a database / Redis setup

### Option A — SQLite (bundled Redis, simplest)

```bash
docker compose up -d --build
```

Data is stored in `./db`.

### Option B — PostgreSQL (bundled Postgres + Redis)

```bash
docker compose -f docker-compose.postgres.yml up -d --build
```

### Option C — Existing external Postgres + Redis (shared network)

Use this when Redis/PostgreSQL already run elsewhere and both are bridged onto one
Docker network (for example `appnet`).

```bash
docker compose -f docker-compose.external.yml up -d --build
```

Set in `.env`:

```bash
DATABASE_URL=postgres://USER:PASSWORD@HOST:5432/yamtrack   # URL-encode special chars, e.g. @ -> %40
REDIS_URL=redis://redis:6379
EXTERNAL_NETWORK=appnet                                      # existing Docker network
YAMTRACK_PORT=8094                                           # host port (default 8000)
```

## 3) Verify it is running

- Health check: `http://<host>:8000/health/` (or `<host>:<YAMTRACK_PORT>/health/`)
- Logs: `docker compose logs -f yamtrack`

Migrations run automatically on first start (`entrypoint.sh`), so no manual
`migrate` is needed.

## 4) Create your first user

If registration is disabled (`REGISTRATION=false`), create an account manually:

```bash
docker compose exec yamtrack python manage.py createsuperuser
```

## Rebuilding after source changes

Because the image is built from source, apply code changes with a rebuild:

```bash
docker compose up -d --build
```

## Notes

- These Compose files compile the **local source** (`yamtrack:local`); they are
  already configured this way in `docker-compose.yml`, `docker-compose.postgres.yml`,
  and `docker-compose.external.yml`.
- The `SECRET` must stay stable — changing it invalidates sessions and encrypted
  tokens. Do not regenerate it once in use.
- For the upstream environment-variable reference, see `docs/env-variables.md`.
