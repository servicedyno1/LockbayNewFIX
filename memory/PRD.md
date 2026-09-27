# Lockbay — Telegram Escrow Bot (Setup Record)

## What this project is
Lockbay is a production Telegram escrow/crypto platform (Python + FastAPI webhook server + PostgreSQL).
- Entry point (Railway/production): `python production_start.py` → `main.py` (webhook mode).
- Emergent bridge: `backend/server.py` imports the FastAPI `app` from `webhook_server.py` and runs it on port 8001 under supervisor.
- Frontend: `frontend/` React "configuration status" dashboard (port 3000).

## Architecture in the Emergent preview
- The preview is detected via `APP_URL` containing `preview.emergentagent.com`.
- In preview, `backend/server.py` runs in **server-only mode**: it serves the FastAPI app and the status API but SKIPS full Telegram bot initialization, so it never hijacks the production Telegram webhook or runs schedulers against the production DB.
- The real bot runs on the user's Railway deployment (`WEBHOOK_URL=https://lockbay1.up.railway.app/webhook`).

## Setup completed (2026-09-27)
- Wrote `/app/.env` with all provided production credentials (DB, Telegram, Brevo, Tatum, Kraken, Fincra, BlockBee, DynoPay, Twilio, admin IDs, financial config).
- Wrote `/app/frontend/.env` with `REACT_APP_BACKEND_URL` (preview URL) + `WDS_SOCKET_PORT=443`.
- Installed Python deps into `/root/.venv` (pinned `SQLAlchemy==2.0.48` to keep the psycopg2 driver default; installed orjson, asyncpg, greenlet, pytz, etc.).
- Ran `yarn install` in `frontend/`.
- Fixed preview detection in `backend/server.py` to use `APP_URL` (not `WEBHOOK_URL`) so the preview stays server-only even though `WEBHOOK_URL` points to Railway.
- Added `/status` endpoint (served as `/api/status` after the app's `/api` prefix-strip middleware) that read-only confirms DB connectivity, table count, bot token, admin IDs, and integrations.
- Rewrote `frontend/src/App.js` into a dynamic configuration dashboard driven by `/api/status`.

## Verified
- `/api/status` → 200 JSON: DB **connected**, **62 tables**, bot token configured (@lockbaybot), admin configured, all 7 integrations detected, mode `preview-server-only`.
- Headless-Chrome render confirms the dashboard populates ("Connected", "62 tables", "configured").
- Backend logs: "Emergent preview detected - skipping Telegram bot initialization (server-only mode)".

## Railway deploy crash — diagnosed & fixed (2026-09-27)
- Railway project = `zippy-radiance`, service = `LockbayNewFIX`, env = `production`. Latest deployment `efdfc379-b0e6-4a6d-94be-49ab23164683` = **CRASHED**.
- Root cause (from runtime logs): `ModuleNotFoundError: No module named 'greenlet'` → `sqlalchemy.ext.asyncio` import at `database.py:14` fails → `production_start.py`/`main.py` bot startup crashes.
- Why: `requirements.txt` had `sqlalchemy>=2.0.41` without the `[asyncio]` extra and no `greenlet`, so the Railway build never installed greenlet.
- Fix: `requirements.txt` → `sqlalchemy[asyncio]>=2.0.41,<2.1` + `greenlet>=3.0.0`. The `<2.1` pin also keeps the `postgresql://` sync driver on psycopg2 (avoids the 2.1 psycopg-v3 default-driver break).
- Verified: clean-venv `pip install` reproduction (greenlet 3.5.6, sqlalchemy 2.0.54, driver=psycopg2) + testing_agent app-level smoke (100% backend/frontend).
- **Next step to go live:** push the updated `requirements.txt` to GitHub (`servicedyno1/LockbayNewFIX`) via "Save to Github" → Railway auto-redeploys. Redeploying the current crashed commit alone won't help; the fix must be pushed first.

## Notes / Backlog
- Database in use = `DATABASE_URL` (Railway `roundhouse.proxy.rlwy.net`). PG* vars point to a separate Neon DB; `RAILWAY_BACKUP_DB_URL` is the backup.
- Several provided values are LIVE production secrets — recommend rotating anything that has been shared in plaintext.
