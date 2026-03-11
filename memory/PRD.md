# LockBay Telegram Escrow Bot - PRD

## Original Problem Statement
Analyze and set up the LockBay Telegram bot, updating `.env` files and ensuring the current pod URL is used for the Telegram webhook.

## Architecture
- **Platform**: Python Telegram Bot with FastAPI webhook server
- **Database**: PostgreSQL (Railway + Neon)
- **Bot Framework**: python-telegram-bot v22
- **Webhook Server**: FastAPI with uvicorn
- **Caching**: SQLite-backed webhook queue (Redis optional)
- **Email**: Brevo (Sendinblue)
- **Payments**: DynoPay, Fincra, BlockBee, Kraken
- **SMS**: Twilio

## What's Been Implemented (2026-03-11)
- Set up all environment variables in `/app/.env` and `/app/backend/.env`
- Updated WEBHOOK_URL to use current pod URL: `https://setup-analyze-pod.preview.emergentagent.com/api/webhook`
- Updated DYNOPAY_WEBHOOK_URL to: `https://setup-analyze-pod.preview.emergentagent.com/api/webhook/dynopay`
- Installed all Python dependencies from requirements.txt
- Started backend (FastAPI webhook server on port 8001) and frontend services
- Verified Telegram webhook registration with Telegram API
- Confirmed health endpoint accessible externally

### Bug Fix: Persistent Email Flood (2026-03-11)
**Root cause**: Three compounding issues:
1. `balance_guard.py` `should_send_alert()` defaulted to `return True` on DB errors → every 5-min reconciliation cycle sent emails when DB was unreachable
2. Balance alert cooldowns were set to 12 hours → 2 alerts/day per provider
3. Daily financial reports ran at 8 AM + 8 PM UTC → 2 report emails/day

**Fixes applied**:
- `services/balance_guard.py`: Changed fallback from `True` to `False` — suppresses alerts during DB outages
- `services/balance_guard.py`: Added in-memory cooldown dict as backup even if DB write fails
- `config.py`: All balance alert cooldowns changed from 12h to **24h** (once daily)
- `jobs/consolidated_scheduler.py`: Financial reports changed from `hour="8,20"` to `hour=8` (once daily at 8 AM UTC)

## Webhook URLs Configured
- Telegram: `https://setup-analyze-pod.preview.emergentagent.com/api/webhook`
- DynoPay: `https://setup-analyze-pod.preview.emergentagent.com/api/webhook/dynopay`
- BlockBee: `https://setup-analyze-pod.preview.emergentagent.com/api/blockbee/callback`
- Fincra: `https://setup-analyze-pod.preview.emergentagent.com/api/webhook/api/fincra/webhook`

## Known Issues
- Railway PostgreSQL connection intermittently drops (expected for remote DB from this pod)
- Database connection pool recovers automatically via circuit breaker

## Backlog
- P0: Monitor database connectivity stability
- P1: Consider using Neon PostgreSQL as primary DB for lower latency
- P2: Enable REDIS for production caching instead of DB_BACKED fallback
