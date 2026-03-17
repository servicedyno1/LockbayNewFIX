# Lockbay Telegram Escrow Bot - PRD

## Original Problem Statement
1. Analyze and set up the Lockbay Telegram Bot codebase with all environment variables
2. Update webhook URLs to use current pod URL
3. Fix critical wallet deposit bug: User @whyterosecyb deposited ~$250 USD in LTC but only $10 was credited
4. Fix cashout error: User 722865886 (Hack) couldn't cash out $225 USDT-TRC20

## Architecture
- **Backend**: Python FastAPI (webhook_server.py) running on port 8001 via supervisor
- **Bot**: python-telegram-bot v20+ library in webhook mode
- **Database**: PostgreSQL (Railway: yamabiko.proxy.rlwy.net:44505)
- **Queue**: SQLite-backed webhook queue (Redis fallback)
- **Scheduler**: APScheduler (ConsolidatedScheduler)
- **External Services**: DynoPay, Fincra, BlockBee, Kraken, Twilio, Brevo, FastForex/Tatum

## What's Been Implemented

### Session 1 (2026-03-17): Environment Setup
- Created `/app/.env` with 78 environment variables
- Updated WEBHOOK_URL and DYNOPAY_WEBHOOK_URL to pod URL

### Session 2 (2026-03-17): Wallet Deposit Bug Fix
- **Root Cause**: crypto.py hardcoded `amount=10.0` for DynoPay invoice; webhook handler used invoice `base_amount` ($10) instead of `crypto_amount × exchange_rate`
- **Fix**: Webhook now computes actual USD from received crypto × rate
- **Impact**: User 5309762918 balance corrected from $10→$250.11

### Session 3 (2026-03-17): Cashout Error Fix
- **Root Cause**: `handle_wallet_cashout` at line 9124 tried `query.data = f"quick_cashout_all:{balance}"` — but `CallbackQuery.data` is **read-only** in python-telegram-bot v20+
- **Error**: `Attribute 'data' of class 'CallbackQuery' can't be set!`
- **Fix**: Inlined the logic from `handle_quick_cashout_all` directly into `handle_wallet_cashout`, bypassing the need to modify `query.data`. Now calls `show_cashout_method_selection`/`show_crypto_address_selection`/`show_saved_bank_accounts` directly based on user's last cashout method.
- **Testing**: 5/5 tests passed (100%)

## Backlog / Next Tasks
- P0: Deploy fixes to Railway production
- P1: Fix milestone streak tracking error (`'>' not supported between 'int' and 'NoneType'`)
- P1: Fix Fincra authentication (Invalid credentials error in logs)
- P2: Handle Kraken API temporary lockout gracefully
- P2: Add monitoring for cashout flow errors
