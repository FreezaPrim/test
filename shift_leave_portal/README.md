# Shift Outage & Leave Portal

An internal web application for contact-center advisors to report **shift outages** and request **annual leave**, and for admins to review, approve, export and audit them.

Built with Python 3.11+, Streamlit, SQLAlchemy 2 (SQLite), Alembic, Pandas, Pydantic and Argon2id password hashing.

---

## 1. Features

| Advisors | Admins |
|---|---|
| Sign in / sign out, change own password | Same sign-in page; role-based navigation |
| Dashboard: pending count, approved leave days & hours (this year), upcoming leave, recent outages, latest statuses | Dashboard: active advisors, pending outages / leave, approved leave this month, outages by category & impact, submissions by team & over time, average review time |
| Report an outage (date, start/end time incl. past-midnight, reason, impact, channel, ticket, description, PNG/JPG/PDF evidence) | All submissions: date-range, advisor, team, type, status, impact filters + search (employee ID, username, name, ticket, description), pagination |
| Request annual leave (full days or partial-day hours, reason, notes) | Approve / reject / resolve / cancel / reopen with comments, edit any record, delete (typed confirmation) |
| My submissions: search, filters, sort, status badges, details, edit & cancel while **pending** | Export the current filters to **CSV** or **Excel** |
| | User management: create (generated or chosen password), edit, roles, teams, activate/deactivate, reset password, force change at next sign-in, unlock |
| | Audit log viewer |

---

## 2. Architecture

```
            ┌──────────── Streamlit UI (ui/) ────────────┐
 browser ─▶ │ app.py: session checks + role-based nav    │  presentation only
            │ login · advisor_* · admin_* · components   │
            └───────────────────┬────────────────────────┘
                                │ AuthUser (no secrets)
            ┌───────────────────▼────────────────────────┐
            │ services/  auth · user · submission ·      │  business rules,
            │ attachment · dashboard · export · audit    │  authorisation, audit
            └───────────────────┬────────────────────────┘
                                │ SQLAlchemy ORM (parameterised)
            ┌───────────────────▼────────────────────────┐
            │ database/ models · connection · schemas    │  SQLite (WAL) +
            │ migrations/ (Alembic)                      │  Alembic migrations
            └────────────────────────────────────────────┘
 utils/: validators · security (Argon2id) · datetime_utils (timezone) · permissions (RBAC)
```

Key design decisions:

* **Business logic never lives in the UI.** Every service function receives the acting user (`AuthUser`) and enforces permissions itself — hiding a page is defence in depth, not the control.
* **One transaction per action** (`session_scope`). The audit row is written in the same transaction as the change, so you never get one without the other. Write transactions use SQLite `BEGIN IMMEDIATE` so check-then-insert rules (overlaps) cannot race.
* **Time zones:** the database stores UTC; a strict `UTCDateTime` column type rejects naive datetimes. Input and display use `APP_TIMEZONE` (default `Africa/Cairo`, DST-aware).
* **Validation:** Pydantic schemas + `utils/validators.py` (sanitisation, lengths, formats); services add rules that depend on data or the actor (overlaps, time windows, status transitions).

### Project structure

```
shift_leave_portal/
├── app.py                    # Streamlit entry point (navigation, session enforcement)
├── manage.py                 # CLI: init-db, create-admin, seed-demo, backup-db
├── alembic.ini, migrations/  # database migrations
├── requirements.txt, .env.example, .gitignore, pytest.ini
├── .streamlit/config.toml    # hardened Streamlit settings + theme
├── config/settings.py        # env-driven, validated, immutable settings + logging
├── database/
│   ├── connection.py         # engine, SQLite pragmas, transactional sessions
│   ├── models.py             # ORM models, enums, constraints, indexes
│   ├── schemas.py            # Pydantic input schemas, AuthUser, filters
│   └── seed.py               # first admin + development demo data
├── services/
│   ├── auth_service.py       # login, lockout, logout, password change, session re-validation
│   ├── user_service.py       # user management (admin)
│   ├── submission_service.py # outages & leave: create/edit/cancel/review/query
│   ├── attachment_service.py # evidence uploads (signature-checked)
│   ├── dashboard_service.py  # dashboard aggregations
│   ├── export_service.py     # CSV / XLSX exports
│   ├── audit_service.py      # audit trail
│   └── errors.py             # user-safe domain exceptions
├── ui/                       # one module per page + shared components/session
├── utils/                    # validators, security, datetime_utils, permissions
└── tests/                    # pytest suite (auth, permissions, submissions, exports, …)
```

### Database schema

**users** — `id`, `username` (unique), `full_name`, `employee_id` (unique), `email` (unique, optional), `password_hash` (Argon2id), `role` (`advisor`/`admin`), `team`, `is_active`, `must_change_password`, `allow_overlapping_outages`, `failed_login_attempts`, `locked_until`, `password_changed_at`, `created_at`, `updated_at`, `last_login_at`.

**submissions** — `id`, `user_id` → users, `submission_type` (`outage`/`annual_leave`), `status` (`pending`/`approved`/`rejected`/`resolved`/`cancelled`), `start_datetime`, `end_datetime`, `all_day`, `reason_category`, `description`, `notes`, `impact_level` (`low`/`medium`/`high`, outages), `outage_channel`, `ticket_reference`, `manager_comment`, `admin_comment`, `overlap_override`, `reviewed_by` → users, `reviewed_at`, `created_at`, `updated_at`.
Indexes on `user_id`, `submission_type`, `status`, `start_datetime`, `end_datetime`, `created_at`, plus a composite `(user_id, submission_type, start_datetime, end_datetime)` for overlap checks. CHECK constraints enforce valid enums, `end > start`, non-empty reason/description and "outage ⇒ impact level".

**attachments** — `id`, `submission_id` → submissions (cascade), `original_filename` (sanitised), `stored_filename` (random), `content_type`, `size_bytes`, `sha256`, `uploaded_by`, `created_at`.

**audit_logs** — `id`, `actor_user_id`, `action`, `entity_type`, `entity_id`, `old_values` (JSON), `new_values` (JSON), `ip_address`, `created_at`.

Columns beyond the original brief (`all_day`, `notes`, `overlap_override`, lockout/password fields, `attachments`) exist to support partial-day leave, admin-approved overlaps, account lockout, forced password change and evidence files.

### Business rules (enforced in `services/`)

* End must be after start; outages max `MAX_OUTAGE_HOURS` (24 h), not in the future, not older than `OUTAGE_MAX_PAST_DAYS`; leave max `MAX_LEAVE_DAYS`, not more than `LEAVE_MAX_PAST_DAYS` in the past or one year ahead (advisor submissions).
* Outages need an impact level and a description (≥ 10 chars); leave needs a reason and reason details.
* **No overlapping annual leave** for the same advisor. **No overlapping outages** unless an admin enables *Allow overlapping outages* on the user, or ticks *Allow overlap* when editing/reopening a record. Intervals that only touch (09:00–10:00 and 10:00–11:00) do not overlap.
* `pending`, `approved` and `resolved` records are *active*; **cancelled and rejected records never block** new requests and never count as leave.
* Advisors can edit/cancel only their **own pending** records; admins can update any record. Status transitions are whitelisted (e.g. a rejected record must be reopened before it can be approved; *resolved* applies to outages only). Reopening re-checks overlaps.
* Full-day leave is stored as `[first day 00:00, day after last day 00:00)` in local time and displayed as dates.

---

## 3. Setup

### 3.1 Create a virtual environment

```bash
cd shift_leave_portal
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

### 3.2 Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.3 Configure `.env`

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Edit `.env`. The most important settings:

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `production` | `development` enables demo seeding only |
| `DATABASE_URL` | `sqlite:///data/app.db` | relative paths resolve from the project folder |
| `APP_TIMEZONE` | `Africa/Cairo` | any IANA zone |
| `SESSION_TIMEOUT_MINUTES` / `SESSION_MAX_HOURS` | `30` / `12` | idle and absolute session limits |
| `MAX_LOGIN_ATTEMPTS` / `LOCKOUT_MINUTES` | `5` / `15` | per-account lockout |
| `PASSWORD_MIN_LENGTH` | `12` | plus upper, lower, digit, symbol |
| `UPLOAD_DIR`, `MAX_UPLOAD_MB`, `MAX_ATTACHMENTS_PER_SUBMISSION` | `data/uploads`, `5`, `3` | evidence files (keep `maxUploadSize` in `.streamlit/config.toml` equal to `MAX_UPLOAD_MB`) |
| `TRUST_PROXY_HEADERS` | `false` | only `true` behind your own reverse proxy |

Invalid values stop the app with a clear configuration error.

### 3.4 Initialise the database

```bash
python manage.py init-db
```

This runs the Alembic migrations (`alembic upgrade head`). Run it again after every upgrade of the application. For future schema changes: edit `database/models.py`, then `alembic revision --autogenerate -m "describe change"`, review the generated file, and run `python manage.py init-db`.

### 3.5 Create the first admin

```bash
python manage.py create-admin
```

You are prompted for username, full name, employee ID, optional e-mail and a password (entered twice, hidden). For automation you may set `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_FULL_NAME`, `INITIAL_ADMIN_EMPLOYEE_ID`, `INITIAL_ADMIN_EMAIL` and `INITIAL_ADMIN_PASSWORD` — remove the password variable immediately afterwards. The command refuses to run when an admin already exists (use the *User management* page, or `--allow-additional`).

### 3.6 Optional: demo data for local development

```bash
# requires APP_ENV=development in .env
python manage.py seed-demo
```

| Username | Password | Role | Team |
|---|---|---|---|
| `demo.admin` | `Admin#Demo2026!` | admin | Workforce |
| `sara.ali` | `Advisor#Demo2026` | advisor | Billing |
| `omar.hassan` | `Advisor#Demo2026` | advisor | Billing |
| `mona.adel` | `Advisor#Demo2026` | advisor | Technical Support |
| `youssef.nabil` | `Advisor#Demo2026` | advisor | Technical Support |

> ⚠️ These accounts are for local development only. The command refuses to run unless `APP_ENV=development`. Never seed them in a shared environment.

### 3.7 Run the application

```bash
streamlit run app.py
```

Open http://localhost:8501. Use `--server.port 8600` to change the port and `--server.address 0.0.0.0` to expose it on your network (see the security checklist first).

### 3.8 Run the tests

```bash
pytest
```

The suite (114 tests) uses a fresh temporary SQLite database per test and covers authentication and lockout, password policy, permissions/RBAC, validation, overlapping dates, status transitions and audit records, attachments, exports (columns, filters, formula-injection protection, permissions, audit), dashboards and the Alembic migration matching the models.

---

## 4. Backing up the SQLite database

The database runs in WAL mode, so **do not copy `app.db` alone while the app is running** (recent changes may live in `app.db-wal`). Use the built-in online backup instead:

```bash
python manage.py backup-db                  # writes backups/app_YYYYmmdd_HHMMSS.db
python manage.py backup-db --output-dir /secure/backups
```

It uses SQLite's backup API (safe while the app is running) and verifies the copy with `PRAGMA integrity_check`. Also back up `UPLOAD_DIR` (evidence files), e.g. with `robocopy`/`rsync`.

Recommendations: schedule it (Windows Task Scheduler / cron), keep several generations off the server, encrypt backups (they contain personal data), and test a restore regularly.

**Restore:** stop the app, move the current `app.db`, `app.db-wal` and `app.db-shm` aside, copy the backup file to the `DATABASE_URL` path (renamed to `app.db`), restore the uploads folder, then start the app and run `python manage.py init-db` (applies any newer migrations).

---

## 5. Security notes

What is implemented:

* **Passwords:** Argon2id (argon2-cffi), automatic re-hash on parameter upgrades, policy (length, character classes, not containing the username, not a common password), temporary passwords generated with `secrets`, shown once, forced change at first sign-in. Hashes are never displayed, exported or logged.
* **Login:** identical, non-revealing error for unknown user / wrong password / locked / inactive account; dummy hash verification for unknown users (timing); per-account lockout after `MAX_LOGIN_ATTEMPTS`; growing per-browser-session delay after repeated failures; malformed usernames are not written to the audit log (people sometimes type their password there).
* **Sessions:** idle timeout and absolute lifetime; the session state is cleared at sign-in (no state carried across users) and sign-out; on **every interaction** the user is re-loaded from the database, so deactivation, role changes and password resets take effect immediately (resetting a password ends that user's other sessions).
* **Authorisation:** checked in the service layer for every operation; advisors' queries are always forced to their own `user_id`, and someone else's record answers "not found" rather than "forbidden". Only pages for the current role are registered with `st.navigation`, and each page re-checks the role.
* **Data handling:** all database access through the SQLAlchemy ORM with bound parameters (no string-built SQL; `LIKE` searches are auto-escaped); input sanitisation (Unicode normalisation, control characters, length limits); user text is escaped before Markdown rendering and the app never uses `unsafe_allow_html` for user data; CSV/Excel **formula injection** is neutralised in exports.
* **Uploads:** PNG/JPEG/PDF only, verified by file signature *and* extension, size and count limits, random server-side names outside any web-served path, permission-checked and audited downloads, files cleaned up if the transaction fails or the record is deleted.
* **Audit:** sign-in success/failure, sign-out, session expiry, password change/reset, submission create/update/cancel/status change/delete, attachment download, user create/update/activate/deactivate/unlock and every data export (with its filters and row count). Sensitive keys are stripped defensively.
* **Errors:** users only see friendly messages; details go to the server log; `showErrorDetails = "none"` hides tracebacks.
* **Caching:** only the database engine/bootstrap is cached (`st.cache_resource`). No user data, query results or exports are cached across users.

**"Remember me" is intentionally not implemented.** Streamlit cannot set `HttpOnly`/`Secure` cookies from the app, and storing a long-lived token in browser storage readable by JavaScript would weaken security. If you need single sign-on or persistent login, put the app behind your company's identity provider (see below).

### Checklist before deploying beyond your own PC

1. Set `APP_ENV=production`; never run `seed-demo`; delete any demo accounts.
2. Serve over **HTTPS only**, behind a reverse proxy (IIS, nginx, Caddy) on the internal network or VPN; do not expose Streamlit directly to the internet. Consider adding company SSO / Windows authentication at the proxy.
3. If behind a proxy, set `TRUST_PROXY_HEADERS=true` (only then) so the audit log records real client IPs, and make the proxy overwrite `X-Forwarded-For`.
4. Restrict file-system permissions on the project folder, `data/` (database + uploads), `backups/` and `.env` to the service account. Consider disk encryption.
5. Keep `.streamlit/config.toml` settings (`enableXsrfProtection`, `showErrorDetails = "none"`); keep `maxUploadSize` aligned with `MAX_UPLOAD_MB`.
6. Schedule and test backups (see section 4); define a retention period for audit logs and personal data that meets company policy and Egyptian data-protection law (Law No. 151 of 2020).
7. Keep dependencies patched (`pip list --outdated`, `pip-audit`).
8. Review the audit log regularly for repeated failed sign-ins and unusual exports.
9. SQLite fits one server with a modest number of concurrent users. For larger loads or multiple app servers, move to PostgreSQL (change `DATABASE_URL`, install the driver, re-run migrations — the code uses only portable SQLAlchemy features apart from the SQLite pragmas in `database/connection.py`).
10. Run Streamlit as a non-privileged service account and keep `LOG_FILE` outside any web-served folder.

---

## 6. Troubleshooting

* **"The database has not been initialised"** — run `python manage.py init-db`.
* **`APP_TIMEZONE ... is not a valid IANA timezone` on Windows** — `pip install tzdata` (already in `requirements.txt`).
* **A user is locked out** — wait `LOCKOUT_MINUTES`, or an admin uses *Unlock sign-in* in User management.
* **Lost the only admin password** — on the server run `python manage.py create-admin --allow-additional`, sign in with the new admin and reset the old account.
