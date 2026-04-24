# LockbayNewFIX — Production Analysis PRD

## Date: 2026-04-24

## Architecture
- **Platform**: Telegram bot (python-telegram-bot) with Starlette webhook server
- **Database**: PostgreSQL on Railway (roundhouse.proxy.rlwy.net:24637)
- **Payment Providers**: DynoPay (crypto), Fincra (NGN fiat), Kraken (exchange)
- **Deployment**: Railway, service ID 96ee768e-3f4d-49c8-be75-dea30777e890
- **Environment**: production (889fd56a-720a-4020-884c-034784992666)

## Core Requirements
- Crypto escrow trades (BTC, ETH, USDT, etc.)
- Wallet deposits via DynoPay
- Admin-approved crypto cashouts
- NGN bank payouts via Fincra
- Balance guard monitoring

## What's Been Implemented (Analysis Session)

### Database Fixes (Apr 24, 2026)
- Created missing `balance_alert_state` table (62nd table)
- Fixed unified_transaction status mismatch (pending→completed for CO042326X6MC)
- Cleaned expired pending_cashout records

### Issues Identified
1. **DynoPay wallet webhook not arriving** — escrow webhooks work, wallet deposit webhooks never received
2. **Fincra API key unauthorized** — needs rotation
3. **BalanceGuard operations_blocked** — fincra_NGN and kraken_USD both blocked
4. **Pending cashout validation gap** — cashout queued with $0 balance

## Prioritized Backlog

### P0
- Investigate DynoPay wallet webhook delivery (ref: WALLET-20260424-014548-5982160502)
- Fix Fincra API key

### P1  
- Add balance validation to pending_cashout creation
- Ensure unified_transaction status syncs with cashout status

### P2
- Balance audit log population (currently empty)
- Wallet balance snapshots
- Internal wallets configuration

## User Personas
- Bot users (Telegram): Create escrows, deposit crypto, cashout
- Admin (Telegram ID 1531772316): Approve cashouts, manage disputes

## Key Credentials/Config
- Railway Project: c23ac3d9-51c5-4242-8776-eed4e3801abe
- LockbayNewFIX Service: 96ee768e-3f4d-49c8-be75-dea30777e890
- Production DB: roundhouse.proxy.rlwy.net:24637
- DynoPay API: dyno.up.railway.app/api
- Webhook endpoints: /webhook/dynopay/escrow, /webhook/dynopay/wallet
