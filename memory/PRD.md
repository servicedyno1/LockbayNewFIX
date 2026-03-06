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
- **`handlers/onboarding_router.py`**: Auto-completes onboarding for ALL users (new + existing), skipping email/OTP/TOS steps entirely
- **`handlers/start.py`**: All 7 email verification code paths removed; existing users auto-complete instead of routing to onboarding flow
- **`services/group_event_service.py`**: Created new service matching remote repo for group event broadcasting

### 2. Group Chat Guard
- **`main.py`**: Added `_group_chat_guard` at handler group `-99` to ignore all messages/commands in groups/supergroups (allows `my_chat_member` / `chat_member` events through)

### 3. Cashout OTP Removed
- **`handlers/wallet_direct.py`**: NGN cashout - OTP removed for ALL users, shows direct confirmation screen
- **`handlers/wallet_direct.py`**: Crypto cashout - OTP and email verification requirement removed, all users proceed directly to confirmation

## Blocker
- `.env` file missing (gitignored, lost during fork). Backend cannot start without `DATABASE_URL` and other credentials.

## Backlog
- P2: Clean up dead OnboardingStates email/OTP state definitions in `start.py`
- P2: Clean up commented-out `onboarding_conversation` handler in `main.py`
- P2: Set up Neon DB sync, configure Redis for production
