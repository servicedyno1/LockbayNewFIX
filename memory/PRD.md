# LockBay Telegram Escrow Bot - PRD

## Original Problem Statement
1. Update backend .env with full production environment variables and ensure WEBHOOK_URL uses the current pod URL for the Telegram webhook.
2. Audit all Brevo emails being sent and reduce frequency of balance guard and hourly report emails.

## Architecture
- Python Telegram Bot (python-telegram-bot) running as FastAPI webhook server
- PostgreSQL (Railway + Neon) for database
- SQLite for webhook queue
- Supervisor-managed uvicorn process on port 8001

## What's Been Implemented

### 2026-03-07 — Session 1: Env Setup
- Updated `/app/backend/.env` with 80 environment variables
- Set `WEBHOOK_URL` to pod URL for Telegram webhook
- Set `DYNOPAY_WEBHOOK_URL` to pod URL
- Installed missing dependencies

### 2026-03-07 — Session 2: Email Frequency Fixes
- **Balance Guard cooldowns**: Changed all 4 alert levels (Warning, Critical, Emergency, Operational) from graduated (1h–12h) to flat **12 hours** — max 2 emails/day per provider per level
- **Per-operation balance alerts**: Added 12h DB-backed cooldown to `_send_operation_proceeding_with_low_balance_alert()` and `_send_operation_blocked_admin_alert()` — previously had NO cooldown
- **Hourly report → daily**: Changed `run_admin_dashboards` scheduler from `IntervalTrigger(hours=1)` to `CronTrigger(hour=6)` — runs once daily at 6 AM UTC

### Files Modified
- `/app/config.py` — cooldown defaults all set to 12h
- `/app/services/balance_guard.py` — per-operation alert cooldown added
- `/app/jobs/consolidated_scheduler.py` — hourly → daily schedule

## Backlog
- P0: None
- P1: Add digest mode to admin trade notifications (batch per-event emails into hourly/daily summary)
- P1: Add cooldown to admin funding notifications (per cashout ID)
- P2: Persist alert_manager cooldowns to DB (currently in-memory, resets on restart)
- P2: Fix retention email potential duplicate sends (overlapping time window)
