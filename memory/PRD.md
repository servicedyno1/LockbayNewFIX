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
- Updated WEBHOOK_URL to use current pod URL: `https://ceba0ef3-e714-478f-a7c0-5e95b357de30.preview.emergentagent.com/api/webhook`
- Updated DYNOPAY_WEBHOOK_URL to: `https://ceba0ef3-e714-478f-a7c0-5e95b357de30.preview.emergentagent.com/api/webhook/dynopay`
- Installed all Python dependencies from requirements.txt
- Started backend (FastAPI webhook server on port 8001) and frontend services
- Verified Telegram webhook registration with Telegram API
- Confirmed health endpoint accessible externally

## Webhook URLs Configured
- Telegram: `https://ceba0ef3-e714-478f-a7c0-5e95b357de30.preview.emergentagent.com/api/webhook`
- DynoPay: `https://ceba0ef3-e714-478f-a7c0-5e95b357de30.preview.emergentagent.com/api/webhook/dynopay`
- BlockBee: `https://ceba0ef3-e714-478f-a7c0-5e95b357de30.preview.emergentagent.com/api/blockbee/callback`
- Fincra: `https://ceba0ef3-e714-478f-a7c0-5e95b357de30.preview.emergentagent.com/api/webhook/api/fincra/webhook`

## Known Issues
- Railway PostgreSQL connection intermittently drops (expected for remote DB from this pod)
- Database connection pool recovers automatically via circuit breaker

## Backlog
- P0: Monitor database connectivity stability
- P1: Consider using Neon PostgreSQL as primary DB for lower latency
- P2: Enable REDIS for production caching instead of DB_BACKED fallback
