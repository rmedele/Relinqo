# PostgreSQL Migration

Relinqo supports SQLite for local development and PostgreSQL for production.

## New Production Deployments

Use a PostgreSQL database URL:

```env
DATABASE_URL=postgresql+psycopg://leadrelay:strong-password@postgres:5432/leadrelay
```

Hosted providers sometimes give URLs that start with `postgres://` or
`postgresql://`. The app normalizes both forms to the `psycopg` driver.

Run migrations before starting the app:

```bash
alembic upgrade head
```

The Docker image already does this in its startup command.

## Docker Compose

The root `docker-compose.yml` now starts:

- `postgres` for the production-style database
- `leadrelay` for the FastAPI app
- `inbox-poller` for inbox polling

Start it with:

```bash
docker compose up -d --build
```

The Postgres data lives in the `leadrelay-postgres` Docker volume. The
`leadrelay-data` volume is still used for photos and local artifacts.

## Migrating Existing SQLite Data

1. Back up the current SQLite file:

```bash
copy data\leadrelay.db data\leadrelay.db.pre-postgres.bak
```

2. Create an empty Postgres database.

3. Point `DATABASE_URL` at the Postgres database and run:

```bash
alembic upgrade head
```

4. Copy data from SQLite into Postgres:

```bash
python scripts/migrate_sqlite_to_postgres.py ^
  --sqlite-url sqlite:///./data/leadrelay.db ^
  --postgres-url postgresql+psycopg://leadrelay:strong-password@localhost:5432/leadrelay
```

If the target database has rows and you intentionally want to replace them:

```bash
python scripts/migrate_sqlite_to_postgres.py ^
  --sqlite-url sqlite:///./data/leadrelay.db ^
  --postgres-url postgresql+psycopg://leadrelay:strong-password@localhost:5432/leadrelay ^
  --truncate
```

5. Start the app against Postgres and verify:

```bash
pytest -q
```

Then log in, check `/health`, load `/review`, and confirm recent leads,
settings, bookings, phone numbers, and templates are present.
