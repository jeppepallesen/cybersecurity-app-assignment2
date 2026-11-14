# Security Review and Hardening of an AI-Generated Flask App

Threat modelling, security review and remediation of a Flask + PostgreSQL
"Student Notes and Tasks" app that was generated with AI assistance.
Course project (IBE321), Høgskolen i Molde.

**Key result:** Remediated the top-priority findings (hard-coded
SECRET_KEY, demo credentials, login brute force, session handling,
cache control, database privileges) and added regression tests:
23 of 24 tests pass, with 1 PostgreSQL-only test skipped in the
SQLite run (it passed in a separate PostgreSQL run).

> ⚠️ `v0-vulnerable` is intentionally insecure. Do not deploy it.

## What this project covers
- Decomposing the system (data-flow diagram, trust boundaries, assets)
- STRIDE threat model with 20 scored threats (likelihood × impact)
  and an attack tree
- Two review passes against OWASP Top 10:2025: a manual pass and an
  AI-assisted pass, with every AI finding treated as unconfirmed
  until tested
- Remediation with AI assistance under written rules (small changes,
  no unverified claims, regression test for every fix)

## Selected findings and fixes
| Threat | Problem | Fix | Verified by |
|---|---|---|---|
| T1 | Default SECRET_KEY allows session forgery | Startup fails without key in environment | Regression tests |
| T2 | Demo credentials shown on login page | Seeding off by default, credentials removed | Regression tests |
| T3 | No login rate limiting | 5 failed attempts per 60 s | Regression tests |
| T10 | Over-privileged database role | Separate low-privilege runtime role | PostgreSQL test |

## Repository structure
v0-vulnerable/   original AI-generated app
v1-remediated/   hardened version, tests, REMEDIATION-SUMMARY.md
docs/            threat model summary

## How to run (v1)
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
docker compose up --build
python -m unittest discover -s tests -v

## Limitations
- Not all threats were remediated (see REMEDIATION-SUMMARY.md)
- No TLS in the Compose setup; Secure cookies need a proxy
- Timing-based username enumeration was not tested
- Existing demo accounts in a database need operator review

## AI use
The app, the second review pass and the remediation were done with AI
assistance. Prompts and session logs are documented in docs/.

## Credits
Course: IBE321, Høgskolen i Molde.
