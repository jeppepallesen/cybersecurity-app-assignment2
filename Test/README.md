# Student Notes and Tasks

A small Flask app for keeping personal study notes and a due-date task list. Each account can only access its own records. PostgreSQL stores user accounts, notes, and tasks; passwords are stored as Werkzeug hashes.

## Run with Docker Compose

From this directory:

```sh
docker compose up --build
```

Provide `SECRET_KEY`, `POSTGRES_ADMIN_PASSWORD`, and `APP_DB_PASSWORD` through a secret manager or the shell environment before starting Compose; none has a source-code fallback. Use a URL-safe high-entropy value for `APP_DB_PASSWORD` because Compose also inserts it into the SQLAlchemy connection URL. The database bootstrap account is separate from the application's `student_app` role; the web service receives only the runtime role's connection URL. Do not use the bootstrap account in application configuration.

On a new empty volume, PostgreSQL initialization creates `student_app`, creates the application tables, and grants the runtime role access. For an existing volume, initialization scripts are intentionally not rerun by PostgreSQL. Preserve the volume and apply the three idempotent migrations explicitly after setting the environment values:

```powershell
docker compose up -d db
docker compose exec -T db psql -U $env:POSTGRES_ADMIN_USER -d student_notes -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/00-create-runtime-role.sql
docker compose exec -T db psql -U $env:POSTGRES_ADMIN_USER -d student_notes -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/01-schema.sql
docker compose exec -T db psql -U $env:POSTGRES_ADMIN_USER -d student_notes -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/02-grant-runtime-role.sql
docker compose up --build -d web
```

Set `POSTGRES_ADMIN_USER` to the bootstrap role that owns the existing database (the current development volume uses `student`). The migration retains the existing database and table owners, rotates the bootstrap password to `POSTGRES_ADMIN_PASSWORD`, and sets `student_app`'s password from `APP_DB_PASSWORD`. Back up the database according to your normal operational procedure before changing production role credentials. Do not run `docker compose down -v`; it deletes the named database volume.

The bootstrap role remains a PostgreSQL administrator used only for database initialization and schema migrations; its password is supplied through `POSTGRES_ADMIN_PASSWORD` and is never passed to the web service. The runtime `student_app` role has `CONNECT` on `student_notes`, `USAGE` on `public`, `SELECT`/`INSERT` on `"user"`, and `SELECT`/`INSERT`/`UPDATE`/`DELETE` on `note`, `task`, `login_attempt_bucket`, and `flask_sessions`. It has `USAGE` on the four corresponding serial sequences, no database `CREATE`/`TEMPORARY`, no schema `CREATE`, no object ownership, no role memberships, and no elevated role attributes. Runtime startup does not create schema; apply later schema changes with the bootstrap/migration role and update the grants migration for any new application tables.

For local HTTP Compose development, set `APP_ENV=development` and `SESSION_COOKIE_SECURE=false`; the app rejects that cookie override in other environments. HTTPS deployments should leave the secure-cookie default enabled. Open <http://localhost:5000> after the services start. The application does not create schema at runtime and does not seed demo users by default.

The Compose database uses a named volume. To remove the database and all stored data, run `docker compose down -v`.

## Run locally

Python 3.10+ and a running PostgreSQL server are required.

1. Provision the PostgreSQL schema as an administrator/migration role, then configure `DATABASE_URL` to use the restricted `student_app` runtime role.
2. Create and activate a virtual environment, then install dependencies:

   ```sh
   python -m venv .venv
   # Windows PowerShell: .venv\Scripts\Activate.ps1
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL, for example `postgresql+psycopg2://student:student@localhost:5432/student_notes`. Set `SECRET_KEY` to a private random value; the app will not start without it. For local HTTP development, also set `APP_ENV=development` and `SESSION_COOKIE_SECURE=false`.
4. Start the app:

   ```sh
   flask --app wsgi:app run --debug
   ```

`schema.sql` contains the application and Flask-Session tables and is mounted into the PostgreSQL image for initialization on an empty data volume. Ordinary application startup does not create or alter tables. Run schema changes as the separate database bootstrap/migration role; the application role has no schema `CREATE` privilege. Demo-user seeding is disabled by default.

Authenticated sessions are stored in the application database and expire after eight hours. The session cookie is Secure by default and is intended for HTTPS deployments. For local HTTP development only, set both `APP_ENV=development` and `SESSION_COOKIE_SECURE=false`.

Docker Compose runs Gunicorn as the non-root `appuser` (UID/GID 10001), with Flask debug disabled. Application files remain root-owned and readable; Gunicorn logs to stdout/stderr and temporary files use `/tmp`. The application adds a restrictive Content Security Policy compatible with its same-origin assets and Google Fonts, standard browser security headers, and no-store caching for dynamic responses. HSTS is disabled by default; enable `ENABLE_HSTS=true` only when Flask receives HTTPS requests. If TLS terminates at a reverse proxy, configure trusted forwarded-protocol handling or set HSTS at that proxy. Local development may continue to use Flask's development server with `--debug`.

## Tests

Install the requirements and run `python -m unittest discover -s tests -v`. The regular regression suite uses an in-memory SQLite database. To run the PostgreSQL runtime-role integration test against the Compose web container, pass the bootstrap credentials to the test process for fixture cleanup only (they are not part of the Gunicorn environment):

```powershell
docker compose exec -T -e RUN_POSTGRES_INTEGRATION=1 -e POSTGRES_ADMIN_USER=$env:POSTGRES_ADMIN_USER -e POSTGRES_ADMIN_PASSWORD=$env:POSTGRES_ADMIN_PASSWORD -e POSTGRES_HOST=db web python -m unittest discover -s tests -p test_postgres_role.py -v
```

The integration test verifies actual PostgreSQL role flags and grants, denied elevated operations, note/task CRUD, and server-side session creation/revocation.

## Project layout

- `app/models.py`: SQLAlchemy user, note, and task models
- `app/routes.py`: authentication, home, CRUD, task completion, and note search
- `app/templates/`: Jinja pages and shared navigation
- `app/static/`: application styles and small client-side enhancements
- `schema.sql`: PostgreSQL tables and user-record relationships
- `docker-compose.yml`: web and PostgreSQL services