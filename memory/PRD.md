# Lockbay Telegram Escrow Bot - PRD

## Original Problem Statement
1. Analyze and set up the Lockbay Telegram Bot codebase with all environment variables
2. Update webhook URLs to use current pod URL
3. Fix critical wallet deposit bug: User @whyterosecyb deposited ~$250 USD in LTC but only $10 was credited

## Architecture
- **Backend**: Python FastAPI (webhook_server.py) running on port 8001 via supervisor
- **Bot**: python-telegram-bot library in webhook mode
- **Database**: PostgreSQL (Railway: yamabiko.proxy.rlwy.net:44505)
- **Queue**: SQLite-backed webhook queue (Redis fallback)
- **Scheduler**: APScheduler (ConsolidatedScheduler)
- **External Services**: DynoPay, Fincra, BlockBee, Kraken, Twilio, Brevo, FastForex/Tatum

## Core Requirements (Static)
- Telegram bot for escrow/payment operations
- Webhook-based architecture for receiving Telegram updates
- Payment processing via DynoPay, Fincra, BlockBee
- User wallet management and crypto exchange
- Admin dashboard and notification system

## What's Been Implemented

### Session 1 (2026-03-17): Environment Setup
- Created `/app/.env` with 78 environment variables
- Updated `WEBHOOK_URL` to pod URL: `https://lockbay-setup.preview.emergentagent.com/api/webhook`
- Updated `DYNOPAY_WEBHOOK_URL` to pod URL
- Installed all Python dependencies, backend fully running

### Session 2 (2026-03-17): Wallet Deposit Bug Fix
**Root Cause**: Two-part bug causing wallet deposits to always credit only $10 regardless of actual crypto value sent:
1. `crypto.py:160` hardcoded `amount=10.0` when creating DynoPay invoice
2. `dynopay_webhook.py:1970-1972` used DynoPay's `base_amount` (= invoice minimum, $10) instead of computing actual USD from `crypto_amount × exchange_rate`

**Fix Applied**:
- `crypto.py`: Changed hardcoded `amount=10.0` to `amount=1.0` (signal minimum for open deposits)
- `dynopay_webhook.py`: Rewrote USD computation to use `crypto_amount × exchange_rate` from DynoPay webhook, with fallback chain: rate calc → base_amount → raw crypto

**Impact**: 
- User @whyterosecyb (5309762918): Sent 4.32717222 LTC × $57.8 = $250.11, only got $10 → shortfall: $240.11
- User 6556421274: Sent 0.00546914 BTC × $71,034.44 = $388.35, only got $10 → shortfall: $378.35
- **MANUAL BALANCE CORRECTION REQUIRED** for both users

**Testing**: 10/10 DynoPay webhook tests passed, all edge cases covered

## Affected Users Requiring Manual Balance Correction
| User Telegram ID | Crypto Sent | Rate | Actual USD | Credited | Shortfall |
|---|---|---|---|---|---|
| 5309762918 (@whyterosecyb) | 4.32717222 LTC | $57.8 | $250.11 | $10.00 | $240.11 |
| 6556421274 | 0.00546914 BTC | $71,034.44 | $388.35 | $10.00 | $378.35 |

## Backlog / Next Tasks
- P0: Manually correct wallet balances for affected users (see table above)
- P1: Add webhook payload logging for audit trail on future deposits
- P1: Fix potential duplicate webhook processing (user 6556421274 got webhook processed twice)
- P2: Configure Redis for persistent state management
- P2: Add monitoring/alerting for deposit amount discrepancies
