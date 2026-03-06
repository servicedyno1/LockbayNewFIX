# Lockbay Telegram Escrow Bot - PRD

## Original Problem Statement
Analyze and set up an existing Lockbay Telegram bot project. Compare the local codebase's onboarding flow with the `Groupmessage` branch of `Moxxcompany/LockbayPaymentFixing` GitHub repository, identify differences, and align the local code.

## Architecture
- **Type**: Monolithic Python Telegram bot
- **Root**: `/app`
- **Entrypoint**: `/app/__main__.py`
- **Config**: `/app/config.py` reads from `/app/.env`
- **Webhooks**: FastAPI at `/app/webhook_server.py`
- **Database**: PostgreSQL via `DATABASE_URL`
- **Bot Framework**: python-telegram-bot

## What's Been Implemented (March 6, 2026)

### 1. Email Onboarding Flow Removed
- **`handlers/onboarding_router.py`**: Auto-completes onboarding for ALL users (new + existing), skipping email/OTP/TOS steps
- **`handlers/start.py`**: All 7 email verification code paths removed; existing users auto-complete instead of routing to onboarding flow
- **`services/group_event_service.py`**: Created new service for group event broadcasting

### 2. Group Chat Guard
- **`main.py`**: Added `_group_chat_guard` at handler group `-99` to ignore all messages/commands in groups/supergroups (allows `my_chat_member`/`chat_member` events through)

### 3. Cashout OTP Removed
- **`handlers/wallet_direct.py`**: NGN cashout - OTP removed for ALL users, shows direct confirmation
- **`handlers/wallet_direct.py`**: Crypto cashout - OTP and email verification requirement removed

### 4. Group Message Broadcasting Feature (NEW)
- **`handlers/group_handler.py`**: NEW file - handles bot being added/removed from groups, sends welcome message, registers/unregisters groups
- **`models.py`**: Added `BotGroup`, `PromoMessageLog`, `PromoOptOut` models
- **`handlers/escrow.py`**: Added 4 group event broadcasts:
  - `broadcast_trade_created` (escrow created)
  - `broadcast_trade_funded` (payment confirmed)
  - `broadcast_seller_accepted` (seller accepts)
  - `broadcast_escrow_completed` (funds released)
- **`handlers/user_rating.py`**: Added `broadcast_rating_submitted` (rating submitted)
- **`main.py`**: Registered `group_handler` via `register_group_handlers(application)`

## Blocker
- `.env` file missing (gitignored, lost during fork). Backend cannot start without `DATABASE_URL` and other credentials.

## Backlog
- P2: Clean up dead OnboardingStates email/OTP state definitions in `start.py`
- P2: Clean up commented-out `onboarding_conversation` handler in `main.py`
- P2: Set up Neon DB sync, configure Redis for production
- P3: Database migration for new models (BotGroup, PromoMessageLog, PromoOptOut)
