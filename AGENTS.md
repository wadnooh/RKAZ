# AGENTS.md

## Cursor Cloud specific instructions

### What this is
Single Flask app: **ركاز (Rakaz)** — an Arabic-first, right-to-left (RTL) business/works tracking system. Server-rendered Jinja2 templates in `webapp/templates`, backed by an embedded **SQLite** database (no separate DB server). The app package lives in `webapp/`; run it via the `webapp.app` module. There is only one service to run.

### Running the app (development)
- Start the dev server: `python3 -m webapp.app` (serves on `127.0.0.1:5070` by default). Set `HOST=0.0.0.0` to expose it, and `PORT` to change the port.
- Entry points: `Procfile` (`web: python -m webapp.app`) for dev/PaaS, `wsgi.py` (`create_app()`) for production. Setting `USE_WAITRESS=1` or `RENDER=1` switches to the production Waitress server; for development leave these unset so the Flask dev server is used.
- Health check: `GET /health` returns `200`.

### Login / testing
- The SQLite DB auto-creates and seeds default users on first startup, so no manual DB setup is needed. Default admin login: `admin` / `admin123`.
- The home route `/` redirects to `/ops` after login. Create a work ticket via the `+ عطل جديد` button on `/ops`; the only required field is `رقم العطل` (ticket number) — everything else is optional. On save the app auto-generates a "rekaz code" (e.g. `ER-2`).

### Gotchas
- The DB path defaults to `instance/rakaz.db` (created fresh on first run), NOT the committed root-level `rakaz.db`. Override the data directory with `RAKAZ_DATA_DIR`. `instance/` and `*.db` are gitignored.
- Passwords are stored/compared as plaintext (no hashing) — this is existing behavior, not a bug to "fix".
- Amazon S3 backups (`boto3`) are optional and only activate when `AWS_S3_BUCKET` + AWS credentials are set; otherwise auto-backup silently writes local ZIPs. Disable auto-backup with `AUTO_BACKUP=0`.
- There is no test framework, linter, or build step configured in this repo. `_smoke_backup.py` is an ad-hoc smoke script, not a test suite.
- Python venv creation (`python3 -m venv`) is unavailable in this environment (no `ensurepip`/`python3-venv` package). Dependencies are installed into the user site with `pip install --break-system-packages -r requirements.txt` (handled by the startup update script).
