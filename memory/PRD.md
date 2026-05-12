# Lockbay Telegram Escrow Bot - PRD

## Original Problem Statement
"Analyze /app and set up or the README file" — analyze the existing Lockbay codebase and bring services online in the Emergent preview environment.

## Project Overview
**Lockbay** is a production-grade Telegram-based escrow bot for secure cryptocurrency and NGN cashout transactions. Features automated fee calculation, dispute resolution, multi-currency wallets, real-time exchange rates, auto-cashout, and admin tooling.

## Architecture
- **Telegram Bot**: `python-telegram-bot` 22.7, webhook-only mode
- **Bridge**: `backend/server.py` boots the bot's FastAPI `webhook_server` on port 8001 for the Emergent preview; bot init is skipped in preview mode (server-only) to keep responses fast
- **Database**: SQLAlchemy 2.0 async on Neon PostgreSQL (DATABASE_URL); Railway Postgres for DR backup
- **Frontend**: Lightweight React 18 status page (`/app/frontend`) showing setup checklist & bot health
- **Background**: APScheduler, Redis (optional fallback), webhook intake queue, email queue

## What's Been Set Up (May 12, 2026)
- Installed missing Python deps: `orjson`, `python-telegram-bot==22.7`, `sib-api-v3-sdk`, plus full `backend/requirements.txt` (SQLAlchemy, asyncpg, fastapi, telegram libs, etc.)
- Removed conflicting `telegram==0.0.1` package (was shadowing `python-telegram-bot`)
- Installed frontend yarn dependencies (`react-scripts`)
- Backend running on `:8001` — `GET /api/health` returns `{"status":"ok","service":"LockBay Telegram Bot"}`
- Frontend running on `:3000` — status page renders, fetches `/api/health` from `REACT_APP_BACKEND_URL`
- Preview mode auto-detected → Telegram bot initialization skipped (server-only); set `WEBHOOK_URL` to a non-preview URL to enable the bot

## Core Domain Features (Already in Codebase)
- Escrow state machine with automated fees, disputes, refunds, auto-release
- Multi-currency wallet (available_balance + trading_credit, Decimal precision)
- Exchange engine with 5-tier rate caching, configurable markups
- Dual payment processors (DynoPay primary, BlockBee fallback) with idempotency
- Auto-cashout: crypto via Kraken, NGN via Fincra with PG advisory locks
- 17-event admin notification system (Telegram + email)
- Referral system, public profile slugs, support chat, dispute UI
- Customer landing page (Nov 2025 brand: teal #3BB5C8 + navy #2C3E50)

## Services Status
| Service | State | Port | Notes |
|---|---|---|---|
| backend | RUNNING | 8001 | FastAPI webhook_server bridged via `backend/server.py` |
| frontend | RUNNING | 3000 | React status dashboard |
| mongodb | RUNNING | 27017 | Available; project uses Neon PostgreSQL |
| code-server | STOPPED | — | Not required |

## Next Action Items
- (Optional) Restore full bot in preview by setting `WEBHOOK_URL` away from `preview.emergentagent.com` and providing a valid `TELEGRAM_BOT_TOKEN`
- Add missing tests / iterate on any specific feature the user wants
- Production deployment via Railway / Replit Reserved VM (see `RAILWAY_MIGRATION_GUIDE.md`)

## Future / Backlog
- Optional: surface bot KPIs (active escrows, GMV, dispute rate) on the React status page
- Optional: add Telegram webhook smoke test endpoint to the React UI

## Tech Stack
Python 3.11, FastAPI, SQLAlchemy 2.0, python-telegram-bot 22.7, PostgreSQL (Neon), Redis (optional), APScheduler, React 18, Brevo, Twilio, Fincra, DynoPay, BlockBee, Kraken.
