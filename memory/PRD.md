# Lockbay - Telegram Escrow Bot PRD

## Overview
Lockbay is a Telegram-based cryptocurrency escrow platform enabling secure peer-to-peer trades. It supports USDT (TRC20), BTC, ETH, LTC cashouts, NGN bank transfers, and integrates with Kraken, BlockBee, Fincra, DynoPay, and Flutterwave.

## Architecture
- **Runtime**: Python (Telegram Bot via python-telegram-bot)
- **Database**: PostgreSQL (Railway + Neon)
- **Deployment**: Railway
- **Key Components**: Route guard, wallet handlers, support chat, escrow system, dispute resolution

## Bug Fix: Stale Support Session Hijacking Cashout Flow (March 5, 2026)

### Problem
User @technine1738 (ID: 5336660667) could not submit USDT TRC20 address for cashout. Address was silently swallowed by a stale support chat session from 4 days earlier. User @onarrival1 (ID: 5590563715) had no such issue because they had no active support session.

### Root Cause
In `utils/route_guard.py`, support chat session check had **SECOND priority** in the routing chain, while crypto address detection was at **FOURTH-D priority**. A stale support session (from March 1) persisted in the in-memory `active_support_sessions` dict and intercepted all text messages for 4+ days.

### Fix Applied (3 files modified)
1. **`utils/route_guard.py`**: Reordered routing priorities - crypto address detection, wallet_input state, and active cashout checks now come BEFORE support chat check. Also auto-clears stale support sessions when wallet/cashout takes priority.
2. **`handlers/text_router.py`**: Added exclusive wallet state bypass - when user is in an exclusive wallet state (entering_crypto_address, verifying_otp, etc.), routes directly to wallet handler without going through RouteGuard.
3. **`handlers/wallet_direct.py`**: Added proactive support session cleanup in `start_cashout()` - clears any stale support session when user enters cashout flow.

### New Routing Priority Order
1. Messages hub (trade chat)
2. Crypto address detection → wallet (NEW - was #8)
3. Wallet input state → wallet (NEW - was #7)
4. Active cashout/OTP → wallet (NEW - was #9)
5. Support chat sessions (DEMOTED - was #2)
6. Trade review + amount
7. Rating session
8. Escrow conversation
9. Dispute session
10. Admin states
11. Onboarding
12. Fallback

### Testing Status
- Fix has been applied to codebase on `main` branch
- Needs deployment to Railway to take effect on production bot

## Backlog
- P0: Deploy fix to Railway production
- P1: Add TTL/auto-expiry for support sessions (prevent 4-day stale sessions)
- P2: Fincra API key authentication failure (recurring every 30min)
