# Lockbay Telegram Bot - PRD

## Original Problem Statement
Analyze and setup the Lockbay Telegram bot codebase. Update .env with all required environment variables. Ensure the current pod URL is used for the Telegram webhook. Then fix all 7 bugs identified from Railway deployment log analysis that caused the bot to become unresponsive.

## Architecture
- **Backend**: FastAPI webhook server (Python) running on port 8001 via uvicorn
- **Bot Framework**: python-telegram-bot v22.x
- **Database**: PostgreSQL (Railway hosted) with SQLAlchemy async/sync engines
- **Webhook Server**: FastAPI app at `/app/webhook_server.py` bootstrapped by `/app/backend/server.py`
- **Scheduler**: APScheduler for 5 core background jobs
- **Payment Providers**: Fincra, DynoPay, BlockBee, Kraken
- **Rate APIs**: Tatum (primary), FastForex (legacy fallback)
- **Email**: Brevo (Sendinblue)
- **SMS**: Twilio

## User Personas
- **Buyers**: Create escrows, fund via crypto/bank, track trades
- **Sellers**: Accept trades, mark delivered, receive payments
- **Admins**: Monitor trades, manage disputes, process refunds

## Core Requirements (Static)
- Telegram bot webhook processing
- Escrow creation, funding, delivery, release flow
- Multi-currency support (USD, NGN, crypto)
- Payment webhook processing (DynoPay, Fincra, BlockBee)
- Admin dashboard via Telegram commands
- Email notifications via Brevo
- Rating system, referral system

## What's Been Implemented

### Session 1 (2026-03-26) - Environment Setup
- [x] Root `.env` created with all 80+ environment variables
- [x] Backend `.env` updated with key variables + webhook URLs
- [x] Telegram webhook registered with pod URL
- [x] All Python dependencies installed
- [x] Backend and frontend running successfully

### Session 2 (2026-03-26) - Railway Log Analysis & Bug Fixes
Analyzed 500+ error logs from Railway deployment. Fixed all 7 identified bugs:

#### Bug #1 (CRITICAL): Database Connection Pool Exhaustion - FIXED
- **File**: `database.py`
- **Fix**: Increased sync pool_size 3→10, max_overflow 5→15. Async pool_size 5→12, max_overflow 10→20
- **Fix**: Reduced pool_recycle 1800→600 (Railway proxy compatibility)
- **Fix**: Reduced pool_timeout 30→10 (fail fast)
- **Fix**: Added `is_pool_healthy()` function for pool utilization monitoring

#### Bug #2 (CRITICAL): /start Handler Cascade Failure - FIXED
- **Files**: `handlers/start.py`, `handlers/onboarding_router.py`
- **Fix**: Added `asyncio.wait_for(timeout=15)` on DB operations in start handler
- **Fix**: Added `asyncio.timeout(15)` on entire existing user flow
- **Fix**: Added graceful "service busy" message when DB is exhausted
- **Fix**: Added `_send_service_busy_message()` helper

#### Bug #3 (HIGH): asyncpg connection_lost() - FIXED
- **File**: `database.py`
- **Fix**: pool_recycle reduced to 600s (Railway proxy kills idle conns at ~30min)
- **Fix**: keepalives_idle reduced to 15s (was 30s)
- **Fix**: statement_timeout reduced to 30s, idle_in_transaction to 120s

#### Bug #4 (HIGH): Fincra Auth Failure Circuit Breaker - FIXED
- **File**: `services/fincra_service.py`
- **Fix**: Added circuit breaker (3 failures → 30 min cooldown)
- **Fix**: Stops wasting connections on repeated 401 auth failures
- **Fix**: Auth failure count resets on success

#### Bug #5 (HIGH): FastForex Subscription Expired Circuit Breaker - FIXED
- **File**: `services/fastforex_service.py`
- **Fix**: Added circuit breaker (3 failures → 30 min cooldown)
- **Fix**: Added 5s aggressive timeout on all FastForex legacy calls
- **Fix**: Applied to _make_request, _fetch_fastforex_single_rate, and get_crypto_to_usd_rate

#### Bug #6 (MEDIUM): Telegram TimedOut Handling - FIXED
- **File**: `handlers/onboarding_router.py`
- **Fix**: Added TimedOut detection in safe_reply_text (no retry on timeout)
- **Fix**: DB timeout causes graceful error message instead of hanging

#### Bug #7 (MEDIUM): Kraken API Failures Circuit Breaker - FIXED
- **File**: `services/kraken_service.py`
- **Fix**: Added circuit breaker (5 failures → 5 min cooldown)
- **Fix**: Failure count resets on success

#### Background Job Pool Guards - ADDED
- **Files**: `jobs/core/reconciliation.py`, `jobs/core/retry_engine.py`, `jobs/core/workflow_runner.py`
- **Fix**: Background jobs skip execution when pool utilization >75-80%
- **Fix**: Preserves connections for user-facing handlers

#### Connection Leak Killer - FIXED
- **File**: `jobs/consolidated_scheduler.py`
- **Fix**: Uses dedicated fresh engine instead of competing for the main pool

## Testing Results
- Iteration 6: 100% (9/9 tests) - Environment setup
- Iteration 7: 100% (22/22 tests) - All bug fixes verified

## Prioritized Backlog
### P0 (Critical) - None

### P1 (High)
- Renew FastForex subscription or fully deprecate in favor of Tatum
- Fix Fincra API credentials (authentication failing on LIVE mode)
- Fund Kraken/Fincra accounts for live operations

### P2 (Medium)
- Add connection pool utilization metrics endpoint
- Add alerting when pool utilization stays >70% for >5 minutes
- Web-based admin monitoring dashboard

## Next Tasks
1. Fix Fincra API credentials or switch to test mode
2. Test bot interaction end-to-end via Telegram
3. Verify payment webhook processing under load
