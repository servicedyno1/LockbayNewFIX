# LockbayNewFIX — Production Analysis & Fixes PRD

## Date: 2026-04-24

## Architecture
- **Platform**: Telegram bot (python-telegram-bot) with Starlette webhook server
- **Database**: PostgreSQL on Railway (roundhouse.proxy.rlwy.net:24637)
- **Payment Providers**: DynoPay (crypto), Fincra (NGN fiat), Kraken (exchange)
- **Deployment**: Railway, service ID 96ee768e-3f4d-49c8-be75-dea30777e890
- **Repo**: servicedyno/LockbayNewFIX (main branch)

## What's Been Implemented

### Session 1: Database Fixes (Apr 24, 2026)
- Created missing `balance_alert_state` table
- Fixed `unified_transactions` status mismatch (pending→completed)
- Cleaned expired `pending_cashouts`

### Session 2: Code Fixes (Apr 24, 2026)
- **handlers/dynopay_webhook.py** — 3 bugs fixed:
  1. Added idempotency service to wallet deposit handler (prevents duplicate processing)
  2. Added FOR UPDATE row locking on wallet balance and transaction duplicate check
  3. Added admin_trade_notifications.notify_wallet_funded() call after deposit credit
- Deleted duplicate transaction record (TX042426F9UK) from production DB

## Prioritized Backlog

### P0 (Deploy Required)
- Push code to main branch to deploy fixes to Railway
- Fix Fincra API key (env var rotation needed)

### P1
- Add unique constraint on transactions.blockchain_tx_hash
- Add balance validation to pending_cashout creation
- Verify wallet deposit webhook delivery from DynoPay for user 5982160502 (BTC)

### P2
- Balance audit log population (currently empty)
- Internal wallets configuration
- Webhook signature verification fix (all DynoPay webhooks fail signature check)

## Key Config
- Railway Project: c23ac3d9-51c5-4242-8776-eed4e3801abe
- Production DB: roundhouse.proxy.rlwy.net:24637
- DynoPay API: dyno.up.railway.app/api
- Admin Telegram ID: 1531772316
