# LockBay - Telegram Escrow Bot

## Application Overview
LockBay is a comprehensive Telegram-based escrow trading platform that enables secure peer-to-peer transactions via a Telegram bot. The platform handles cryptocurrency and fiat (NGN) payments, wallet management, dispute resolution, and more.

## Architecture
- **Backend**: FastAPI (Python) serving as both a webhook server for Telegram and REST API
- **Database**: PostgreSQL (Neon/Railway hosted) with SQLAlchemy ORM - 57+ tables
- **Frontend**: React status/landing page (minimal - bot is primary interface)
- **Bot Framework**: python-telegram-bot v22.7 (webhook mode)
- **Background Jobs**: APScheduler (consolidated scheduler with 5 core jobs)
- **Payment Providers**: BlockBee, DynoPay, Fincra, Kraken
- **Email**: Brevo (SendinBlue) for notifications and OTP
- **SMS**: Twilio for trade invitations
- **Caching**: Redis (optional, with fallback)

## Key Services
- Escrow creation, payment, delivery, release, and cancellation
- Wallet funding (crypto + NGN), cashout (crypto + bank transfer)
- Quick exchange (crypto-to-crypto, NGN-to-crypto)
- Dispute resolution with admin panel
- Rating system for traders
- Referral program
- Admin dashboard (Telegram-based)
- Webhook processing for payment confirmations

## Environment Setup (Jan 2026)
### What was done:
1. Installed missing Python dependencies (`orjson`, `python-telegram-bot`, full `requirements.txt`)
2. Installed frontend npm packages (`yarn install`)
3. Fixed `telegram` package conflict (bare `telegram` vs `python-telegram-bot`)
4. Fixed `load_dotenv(override=True)` → `override=False` to prevent env var overwriting in production
5. Made bot initialization non-blocking in preview environment (bot's background jobs were overwhelming the event loop)
6. Updated `.env` files with correct preview domain URLs
7. Verified backend health endpoint, database connection, and frontend rendering

### Current State:
- Backend: Running on port 8001, health endpoint responsive
- Frontend: Running on port 3000, status page shows all systems operational
- Database: Connected to Railway PostgreSQL (57+ tables)
- Telegram Bot: Initialization skipped in preview (requires production webhook URL)
- Redis: Not connected (fallback active)

## Configuration Files
- `/app/backend/.env`: Backend environment variables (DB, Telegram token, webhook URLs)
- `/app/frontend/.env`: Frontend REACT_APP_BACKEND_URL
- `/app/config.py`: Main configuration class with extensive settings
- `/app/backend/server.py`: Bridge server bootstrapping bot + webhook FastAPI app

## Deployment Notes
- Backend uses `load_dotenv(override=False)` to allow K8s env vars to take precedence
- Preview environment skips Telegram bot init to keep server responsive
- `payment-config-14.preview.emergentagent.com` routes frontend only; UUID domain handles API routing

## Backlog
- P0: None (system operational)
- P1: The Telegram bot background jobs need optimization to not block the event loop (use async DB queries)
- P2: Consider making bot initialization timeout configurable
- P2: Redis setup for state management in production
- P3: Frontend could be enhanced beyond status page
