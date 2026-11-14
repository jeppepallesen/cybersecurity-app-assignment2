# Security Remediation Summary

Scope of this update: T1, T2, T3, T6/T13, T7, T14, and T15.

## Final audit evidence and current status

The final documentation audit was read-only. The current SQLite command,
`python -m unittest discover -s tests -v`, ran **24 tests: 23 passed and 1
PostgreSQL-only integration test was skipped**. This is distinct from the
previously successful PostgreSQL integration and Docker verification described
in the relevant sections below; those checks were not rerun during the final
audit. No new tests or verification are claimed here.

Read-only inspection found two database accounts, `alice` and `bob`, each with
two notes and two tasks whose sample content matches the demo seeder. Their
exact provenance and password validity are unknown. Neither account nor its
data was deleted or modified. An operator must review the accounts and decide
whether password rotation or deactivation is appropriate.

Git diff inspection was unavailable because this workspace is not a Git
repository. Therefore, this summary does not claim that a Git worktree diff was
reviewed or that no unrelated changes exist.

## T1 — Hardcoded `SECRET_KEY` / session forgery

- **Severity:** High
- **Attack:** When the application starts with a known fallback signing key,
  an attacker who knows that key can forge Flask session cookies and impersonate
  users.
- **Before:**

  ```python
  SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-key-change-me")
  ```

  ```yaml
  SECRET_KEY: ${SECRET_KEY:-replace-this-for-any-shared-deployment}
  ```

- **After:** Application startup requires a non-empty `SECRET_KEY` from the
  environment. A test configuration may provide one only when `TESTING` is
  enabled. Docker Compose requires the environment variable and has no fallback.

  ```python
  SECRET_KEY=os.environ.get("SECRET_KEY")
  # after config overrides:
  if not app.config["SECRET_KEY"] or (
      not os.environ.get("SECRET_KEY") and not app.config.get("TESTING")
  ):
      raise RuntimeError(...)
  ```

  ```yaml
  SECRET_KEY: ${SECRET_KEY:?Set SECRET_KEY in the environment before starting the application}
  ```

- **Verification evidence:** `python -m unittest discover -s tests -v` passed
  all 11 tests. Regression tests verify startup rejects a missing environment
  key and rejects a production-style configuration-only key. `docker compose
  config` succeeded with a temporary test key and rejected configuration when
  `SECRET_KEY` was absent.
- **Why the regression tests close the default-key path:** Without an environment
  key, the app now raises before initializing the database or serving requests;
  passing only an application config key cannot restore a production fallback.
- **OWASP:** A02:2025 — Security Misconfiguration.
- **CIS Controls:** 4.1 — Establish and Maintain a Secure Configuration Process.
- **Secure Development Lifecycle:** Secure design and configuration management
  establish secret provisioning requirements; implementation enforces them at
  startup; regression tests verify the missing-secret failure path.

## T2 — Demo credentials and default seeding

- **Severity:** High
- **Attack:** Public demo passwords and automatic creation of accounts make
  credential guessing unnecessary: an attacker can sign in as a known account
  and access its data.
- **Before:**

  ```python
  SEED_DEMO_DATA=os.environ.get("SEED_DEMO_DATA", "true").lower() == "true"
  ```

  Compose explicitly set `SEED_DEMO_DATA: "true"`, the login template displayed
  both demo usernames and passwords, and the seeder embedded their passwords.
- **After:** Seeding defaults to disabled and Compose no longer enables it. The
  login page no longer displays demo credentials. If seeding is deliberately
  enabled, the seeder requires `DEMO_ALICE_PASSWORD` and `DEMO_BOB_PASSWORD`
  from the environment rather than using passwords stored in source.

  ```python
  SEED_DEMO_DATA=os.environ.get("SEED_DEMO_DATA", "false").lower() == "true"
  ```

  ```python
  alice_password = os.environ.get("DEMO_ALICE_PASSWORD")
  bob_password = os.environ.get("DEMO_BOB_PASSWORD")
  if not alice_password or not bob_password:
      raise RuntimeError(...)
  ```

- **Verification evidence:** The same 11-test suite passed. Regression tests
  verify the default config creates no demo users, explicit seeding refuses to
  run without environment-supplied passwords, and the login response does not
  contain the old demo passwords. The existing note-ownership and privacy tests
  also passed.
- **Why the regression tests close the default path:** In the default environment,
  the app does not invoke seeding and the test verifies the database contains no
  seeded users. The login template no longer discloses a credential pair, while
  the opt-in seeder cannot silently fall back to fixed source-code passwords.
- **OWASP:** A07:2025 — Authentication Failures.
- **CIS Controls:** 5.2 — Use Unique Passwords.
- **Secure Development Lifecycle:** Account-provisioning requirements are
  reflected in secure defaults; implementation removes displayed and
  source-embedded credentials; regression tests cover default provisioning and
  login-page disclosure.

## T3 — Login brute force

- **Severity:** Medium
- **Attack:** Repeated password guesses can be used to discover a user's password
  and gain access to their private notes and tasks.
- **Before:** The login route checked the submitted username and password on
  every POST without tracking attempts or limiting retries.
- **After:** Failed attempts are limited to five per 60-second fixed window,
  independently by HMAC-hashed username and client IP. The persistent
  SQLAlchemy counter table is stored in the application database, so limits are
  shared across app workers. PostgreSQL advisory transaction locks serialize
  requests for the same buckets; expired rows are pruned on login requests.
  Successful logins do not consume the failed-attempt allowance. Invalid
  credentials and requests over the limit use the same generic message and
  response status.

  ```python
  LOGIN_RATE_LIMIT = 5
  LOGIN_RATE_WINDOW_SECONDS = 60
  ```

  ```python
  username_attempts = _record_login_attempt(username_bucket, now, window_start)
  ip_attempts = _record_login_attempt(ip_bucket, now, window_start)
  if max(username_attempts, ip_attempts) > LOGIN_RATE_LIMIT:
      flash("Check your username and password, then try again.", "error")
      return render_template("login.html")
  ```

- **Verification evidence:** `python -m unittest discover -s tests -v` passed
  all 13 tests. Added regression tests submit five bad passwords, verify the
  next attempt is throttled with the same generic failure, verify an unknown
  username receives the same generic message, and verify a normal successful
  login still redirects to the home page. Existing owner-based note privacy
  tests also passed.
- **Why the regression tests demonstrate the behavior:** The test crosses the
  exact configured boundary (five allowed failures, sixth request throttled),
  checks the same non-enumerating message for unknown and throttled usernames,
  and separately verifies normal authentication/session behavior.
- **OWASP:** A07:2025 — Authentication Failures.
- **CIS Controls:** No direct safeguard mapping for login rate limiting is
  claimed. CIS Control 6.3 is **Require MFA for Externally-Exposed Applications**;
  it addresses MFA, not rate limiting, so it is not presented as an equivalent
  control. Verify any assignment-specific mapping against the applicable
  rubric.
- **Secure Development Lifecycle:** Authentication abuse is addressed during
  secure design; persistent rate controls are implemented in the login flow;
  boundary, generic-response, and successful-login behavior are checked in
  regression testing.

## T7 — Logout session replay

- **Severity:** High
- **Attack:** Flask's default signed client-side cookie remains cryptographically
  valid until it expires. Clearing a browser's copy at logout does not revoke a
  copied cookie, so an attacker holding the copy can continue to replay the
  authenticated session.
- **Before:** Flask used its default client-side session interface. Logout called
  `session.clear()`, which only caused the current browser to receive a cleared
  cookie; no server-side session record existed to revoke.
- **After:** Flask-Session stores session data in a SQLAlchemy-backed database
  table, shared across application workers. Logout's `session.clear()` marks the
  server-side session for deletion, so the copied session identifier no longer
  resolves to authenticated data. Successful login regenerates the session ID.
  Sessions expire after eight hours; Flask-Session checks expiration on access
  and removes expired records periodically (configured to run cleanup on about
  one in every 100 requests). Cookies are HttpOnly, SameSite=Lax, and Secure by
  default. Secure cookies require HTTPS; local HTTP Compose development must
  explicitly set `APP_ENV=development` and `SESSION_COOKIE_SECURE=false`.
  The application rejects disabling Secure unless it is in testing or
  development mode.

  ```python
  SESSION_TYPE="sqlalchemy"
  SESSION_SQLALCHEMY=db
  SESSION_SQLALCHEMY_TABLE="flask_sessions"
  PERMANENT_SESSION_LIFETIME=timedelta(hours=8)
  SESSION_COOKIE_HTTPONLY=True
  SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
  SESSION_COOKIE_SAMESITE="Lax"
  ```

- Secure-cookie override validation rejects `SESSION_COOKIE_SECURE=false`
  outside testing or `APP_ENV=development`, so the local HTTP exception is not
  silently accepted in production configuration.

  ```python
  session.clear()
  session["user_id"] = user.id
  current_app.session_interface.regenerate(session)
  ```

- **Verification evidence:** `python -m unittest discover -s tests -v` passed
  all 18 tests. The logout regression test logs in, copies the authenticated
  cookie value into a separate test client, logs out normally, and confirms that
  replaying the copied cookie redirects to `/login`. It also verifies the
  original client is signed out. Additional tests verify successful login
  changes the session ID, an eight-hour expiry is stored and expired rows are
  rejected and removed, Secure cannot be disabled outside development/testing,
  and existing note/task authorization behavior.
- A PostgreSQL integration test ran inside the built Docker web image against
  the Compose PostgreSQL service. It verified that login created a persistent
  session row, logout deleted that row, and replaying the exact copied
  pre-logout cookie redirected to `/login`. The integration test passed.
- Docker successfully built the app image and installed Flask-Session from
  `requirements.txt` using the project's Python 3.12 base image.
- Installing all requirements with `python -m pip install -r requirements.txt`
  failed because the existing `psycopg2-binary==2.9.10` dependency attempted a
  source build under Python 3.14 and requires Microsoft Visual C++ 14.0.
  Flask-Session was installed directly, and the complete SQLite test suite then
  ran successfully. The session extension emits Python 3.14 deprecation
  warnings for its internal use of `datetime.utcnow()`; these did not fail
  tests.
- **Why the copied-cookie test demonstrates revocation:** The replay client
  retains the exact cookie issued before logout and does not receive the logout
  response's cookie deletion. Its redirect after logout therefore shows that
  the old server-side session record is no longer accepted.
- **OWASP:** A07:2025 — Authentication Failures (session invalidation and
  management).
- **CIS Controls:** 6.2 — Establish an Access Revoking Process (revoke
  authenticated server-side sessions at logout).
- **Secure Development Lifecycle:** Session revocation and expiry are defined
  during secure design; database-backed sessions and ID rotation are implemented
  in the authentication flow; copied-cookie replay and normal authorization are
  covered in regression testing.

## T6/T13 — Security headers, cache controls, and production server

- **Severity:** Medium
- **Attack:** Without browser security headers, the application has fewer
  protections against framing and content-type confusion. A permissive CSP can
  allow injected content to execute. Shared caches may retain private notes or
  account responses. Flask's development server is not appropriate for
  production.
- **Before:** Responses had no configured security-header policy or
  authenticated-response cache controls. The Docker image launched
  `flask run --host=0.0.0.0`, and Compose did not explicitly pin Flask debug
  off.
- **After:** Application responses now include `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy:
  strict-origin-when-cross-origin`, a restrictive CSP, and a restrictive
  Permissions Policy. Dynamic responses receive `Cache-Control: private,
  no-store`, `Pragma: no-cache`, and `Expires: 0`; static assets retain their
  normal cache behavior. HSTS is opt-in and is sent only when Flask sees an
  HTTPS request. Production config forces Flask debug off; Compose sets
  `FLASK_DEBUG=0`; Docker starts Gunicorn with two workers. Local development
  can still use the Flask CLI `--debug` command.

  ```text
  default-src 'self'; base-uri 'self'; object-src 'none';
  frame-ancestors 'none'; form-action 'self'; script-src 'self';
  style-src 'self' https://fonts.googleapis.com;
  font-src 'self' https://fonts.gstatic.com; img-src 'self' data:
  ```

- **Verification evidence:** `python -m unittest discover -s tests -v` passed
  all 22 tests. New tests assert security headers and a CSP without
  `unsafe-inline`/`unsafe-eval`, no-store behavior for login and authenticated
  notes, HSTS only when explicitly enabled and Flask sees HTTPS, and production
  debug forced off even when a test config attempts to enable it. Docker Compose
  built and started the production app. A live `GET /login` returned status 200,
  `Server: gunicorn`, all configured headers, `Cache-Control: private,
  no-store`, a Secure/HttpOnly/SameSite=Lax session cookie, and no HSTS when
  HSTS was disabled. The running process was confirmed as Gunicorn with
  `APP_ENV=production`, `DEBUG=False`, and `FLASK_DEBUG=0`.
- **CSP compatibility:** Existing templates load scripts and app CSS from the
  same origin; the stylesheet imports Google Fonts. The policy permits that
  font stylesheet and its font origin, while excluding inline scripts/styles
  and `unsafe-eval`.
- **OWASP:** A02:2025 — Security Misconfiguration.
- **CIS Controls:** 4.1 — Establish and Maintain a Secure Configuration Process.
- **Secure Development Lifecycle:** Secure architecture and configuration
  design establishes browser and cache controls; implementation adds response
  policies and production serving; regression tests and live container checks
  verify the response and server configuration.

## T14 — Container running as root

- **Severity:** Medium
- **Attack:** A process running as container root has unnecessary privilege if
  the application or a dependency is compromised, increasing the possible
  impact within the container.
- **Before:** The Dockerfile installed dependencies and copied the app, but did
  not set a runtime `USER`; Docker therefore ran Gunicorn and its workers as
  root (UID 0).
- **After:** The image creates a dedicated system account/group `appuser` with
  UID/GID 10001 and executes Gunicorn as `10001:10001`. Dependencies remain
  installed during the build as root. App files are copied root-owned and
  readable, not writable by the runtime user. `HOME` is the account's home,
  temporary files use the standard writable `/tmp`, and Gunicorn logs to
  stdout/stderr. The app persists sessions in PostgreSQL and does not require
  write access to its source tree.

  ```dockerfile
  RUN groupadd --system --gid 10001 appuser \
      && useradd --system --uid 10001 --gid appuser ...
  COPY --chown=root:root . .
  USER 10001:10001
  ```

- **Verification evidence:** The full SQLite suite passed all 22 tests:
  `python -m unittest discover -s tests -v`. The production Compose app image
  built and started. Docker inspection of the running container reported
  configured user `10001:10001`; `/proc/1/status` and both Gunicorn worker
  processes reported effective UID 10001, not 0. The running Gunicorn app
  returned HTTP 200 on `/login` with its existing security headers and
  `Cache-Control: private, no-store`.
- **OWASP:** A02:2025 — Security Misconfiguration.
- **CIS Controls:** 4.1 — Establish and Maintain a Secure Configuration Process.
- **Secure Development Lifecycle:** Container least privilege is set during
  deployment design; image identity and file permissions are configured at
  build time; image build, runtime process identity, endpoint reachability, and
  retained response controls are verified before release.

## Changed files

- `app/__init__.py` — require the signing key and turn demo seeding off by
  default; configure database-backed server-side sessions, response headers,
  cache behavior, production debug settings, environment-only database
  credentials, and test-only automatic schema creation.
- `app/models.py` — add the persistent login-attempt counter model.
- `app/routes.py` — enforce username- and IP-based login throttling; rotate
  session identifiers after login.
- `docker-compose.yml` — require `SECRET_KEY`, remove automatic demo seeding,
  pass through secure-cookie settings, configure production debug/HSTS
  defaults, and use the restricted runtime role with environment-supplied DB
  credentials.
- `app/seed.py` — require environment-provided passwords for deliberate seeding.
- `app/templates/login.html` — remove the demo credential disclosure.
- `tests/test_app.py` — add T1/T2/T3/T6/T13/T7 regression coverage.
- `tests/test_postgres_role.py` — add opt-in PostgreSQL verification for
  runtime role privileges, CRUD, and server-side session revocation.
- `schema.sql` — provision login-throttle and Flask-Session tables without
  requiring runtime DDL privileges.
- `database/init/00-create-runtime-role.sql` and
  `database/init/02-grant-runtime-role.sql` — create/restrict the app role,
  rotate the bootstrap credential from environment, and grant precise
  table/sequence privileges.
- `requirements.txt` — add Flask-Session for persistent SQLAlchemy sessions and
  Gunicorn for production WSGI serving.
- `README.md` — document required secret, no-seeding default, and session/cookie
  configuration, response protections, server modes, and non-root container
  identity.
- `Dockerfile` — run Gunicorn instead of Flask's development server and create
  and run as `appuser` UID/GID 10001; retain root-owned readable application
  code and use `/tmp` for temporary files.

No existing dependencies were upgraded; Flask-Session 0.8.0 and Gunicorn 23.0.0
were added.
No dependencies were added for T15. Existing ownership/access checks were not
modified.

## Residual risks and manual checks

- The application has no account-registration or administrative provisioning
  workflow. With demo seeding disabled, an operator must provision real accounts
  separately before users can sign in.
- Do not enable `SEED_DEMO_DATA` in production. If it is intentionally enabled
  for development, supply strong, unique values for both demo password
  environment variables.
- Rate limiting trusts `request.remote_addr`; deployments behind a reverse proxy
  must configure trusted client-address forwarding carefully. Distributed
  attacks from many IPs, account recovery after the one-minute lock window, and
  PostgreSQL concurrent behavior were not exercised in a live deployment.
- HTTPS must be configured for production because session cookies are Secure by
  default. Disabling Secure is restricted to testing or the explicit development
  environment; deployments must not mark production as development.
- Expired session cleanup is periodic and lazy rather than immediate; records
  remain unusable after their expiry but may remain stored until a cleanup run.
- PostgreSQL session storage and logout deletion were verified against the
  Compose service, but multi-worker concurrency and scheduled cleanup behavior
  were not load-tested. T15 now provisions the session table before runtime and
  keeps table creation in the separate bootstrap/migration role.
- HSTS remains disabled by default. If TLS terminates before Flask, HSTS must
  be configured at the trusted TLS proxy or Flask must be configured to trust
  only that proxy's forwarded protocol information.
- CSP permits the existing Google Fonts origins; this is an external
  third-party dependency and may be removed by self-hosting fonts if a stricter
  policy is desired.
- T14 was verified for the Compose image and its running Gunicorn master and
  workers. Other deployment platforms and runtime overrides were not checked;
  operators should ensure they do not override the image's non-root user.

## T15 — PostgreSQL application role has excessive privileges

- **Severity:** High
- **Attack:** The application connected as `student`, the Compose bootstrap
  account. On the inspected PostgreSQL instance this role had SUPERUSER,
  CREATEDB, CREATEROLE, REPLICATION, and BYPASSRLS. Compromise of the web
  application therefore exposed database administration and bypassed row-level
  security safeguards.
- **Before:** Compose used the same `student` login for database initialization
  and Flask, with its password embedded in Compose. The Flask configuration
  also had a source-coded database URL fallback. The application called
  `db.create_all()` at every startup, so the runtime account required
  schema-creation privileges.
- **After:** PostgreSQL initialization creates a separate `student_app` LOGIN
  role. Compose constructs the web service's `DATABASE_URL` with that restricted
  identity and reads both the runtime and bootstrap passwords from environment
  variables. `DATABASE_URL` is mandatory with no source-coded credential
  fallback. The privileged bootstrap role remains separate and is used only for
  initialization/schema migration; it is not passed to the web service. Runtime
  startup no longer creates tables. Existing named volumes are migrated with
  idempotent scripts and are not deleted or reset.

  ```sql
  ALTER ROLE student_app LOGIN
      NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS NOINHERIT;
  ```

  ```sql
  REVOKE CONNECT, TEMPORARY ON DATABASE student_notes FROM PUBLIC;
  GRANT CONNECT ON DATABASE student_notes TO student_app;
  GRANT USAGE ON SCHEMA public TO student_app;
  GRANT SELECT, INSERT ON TABLE "user" TO student_app;
  GRANT SELECT, INSERT, UPDATE, DELETE
      ON TABLE note, task, login_attempt_bucket, flask_sessions TO student_app;
  GRANT USAGE ON SEQUENCE user_id_seq, note_id_seq,
      task_id_seq, flask_sessions_id_seq TO student_app;
  ```

- **Roles and exact permissions:** The inspected database bootstrap role is
  `student` and remains the database/table owner and administrator; its
  environment-provided password is rotated by the migration. `student_app` is
  the sole application login. It has CONNECT on `student_notes`, USAGE on
  `public`, SELECT/INSERT on the user table, full DML on the four tables used
  for notes, tasks, login throttling, and Flask sessions, and USAGE on the four
  serial sequences. Public CONNECT/TEMPORARY grants are revoked on the
  application database. It has no database CREATE/TEMPORARY, schema CREATE, no
  ownership, no default grants on future objects, no role memberships, and no
  elevated attributes. The user table deliberately withholds UPDATE and DELETE
  because the application has no such user-management operation.
- **Verification evidence:** The current final-audit SQLite run completed
  **24 tests: 23 passed, 1 PostgreSQL-only integration test skipped** because
  the SQLite run did not opt in to PostgreSQL. Separately, the previously run
  opt-in integration regression against the actual Compose PostgreSQL service
  passed (**1 test**); it was not rerun during the read-only final audit. It
  connected through
  `DATABASE_URL`, confirmed every elevated role flag false and required
  privileges true/false as expected, verified `ALTER SYSTEM` is denied, logged
  in, exercised note and task create/edit/toggle/delete operations, created and
  deleted a server-side session on logout, and verified a copied pre-logout
  cookie was rejected. The test removed its own fixture through a separate
  bootstrap connection; the bootstrap credential was supplied only to the
  test process. The actual catalog query showed
  `student_app` has all five elevated flags false; `student` remains the
  separate administrator. The note/task counts remained four each. The two
  existing accounts, `alice` and `bob`, each have two notes and two tasks whose
  sample content matches the demo seeder. Their exact provenance and password
  validity are unknown. No `t15_` fixture remains. These accounts and their
  records were not deleted or modified; an operator must review them before
  deployment. An intermediate integration run
  used a stale image and failed an outdated privilege assertion; the image and
  fixture-cleanup path were corrected, and the final PostgreSQL integration
  run passed. The production Compose image built and ran with Gunicorn;
  `/login` returned HTTP
  200 with `Cache-Control: private, no-store`, `nosniff`, and `X-Frame-Options:
  DENY`. Gunicorn master/workers remained UID/GID 10001:10001.
- **OWASP:** A02:2025 — Security Misconfiguration is directly relevant to
  overly permissive database-role configuration. A06:2025 is not used as a
  mapping: its official category is **Insecure Design**, not vulnerable or
  outdated components. Confirm any additional category mapping against the
  assignment rubric.
- **CIS Controls:** CIS Control 5 — Account Management, Safeguard 5.4 —
  **Restrict Administrator Privileges to Dedicated Administrator Accounts** is
  related to separating the bootstrap administrator from the runtime account,
  but is not claimed as a direct mapping for every PostgreSQL grant. CIS
  Control 4.8 — **Uninstall or Disable Unnecessary Services on Enterprise
  Assets and Software** is not a least-privilege database-role safeguard and is
  not claimed as a mapping. Verify the assignment-specific safeguard mapping
  against its rubric.
- **Secure Development Lifecycle:** Define separate runtime and migration
  trust boundaries during secure architecture; provision roles and schema
  permissions through deployment/database migration code; validate effective
  privileges, denied administrative operations, application CRUD, and session
  behavior in automated and PostgreSQL integration testing.
- **Limitations:** The bootstrap `student` role remains a PostgreSQL
  administrator and must be protected in a secret manager; it is not used by
  Flask. A pre-existing volume does not execute image initialization scripts
  automatically, so operators must apply the documented migration commands
  once. Future schema additions must be created by the migration role and
  receive explicit grants in the provisioning SQL. SQLite unit tests do not
  replace the separately run PostgreSQL integration check. The existing
  `alice` and `bob` accounts and their demo-seed-matching records require
  operator review; their provenance and password validity are unknown, and
  they were not deleted or modified.
