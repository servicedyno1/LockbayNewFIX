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
- `handlers/onboarding_router.py`: Auto-completes onboarding for ALL users (new + existing)
- `handlers/start.py`: All email verification code paths removed; existing users auto-complete

### 2. Group Chat Guard
- `main.py`: `_group_chat_guard` at group `-99` ignores messages in groups/supergroups

### 3. Cashout OTP Removed
- `handlers/wallet_direct.py`: NGN + Crypto cashout proceed directly without OTP

### 4. Group Message Broadcasting
- `handlers/group_handler.py`: NEW - handles bot add/remove from groups
- `models.py`: Added `BotGroup`, `PromoMessageLog`, `PromoOptOut` models
- `handlers/escrow.py`: 4 group event broadcasts (trade created/funded/seller accepted/completed)
- `handlers/user_rating.py`: Rating broadcast
- `main.py`: Registered `register_group_handlers(application)`
- `services/group_event_service.py`: NEW - group broadcast service

### 5. Dead Code Cleanup
- `handlers/start.py`: Removed 1,902 lines of dead email/OTP/TOS code (5132 -> 3202 lines)
  - Removed: `start_onboarding`, `collect_email`, `verify_email_otp_onboarding`, `show_terms_of_service`, `accept_terms`, `complete_onboarding`, `finalize_trade_acceptance`, `onboarding_conversation` ConversationHandler, and 10+ other dead functions
  - Cleaned `OnboardingStates`: removed 25 dead states, kept `ONBOARDING_SHOWCASE` (still used by demo handlers)
- `main.py`: Removed dead imports (`handle_start_email_input`, `handle_invitation_decide_later`, `onboarding_conversation`), removed commented-out conversation handler registration, removed `cancel_email_setup` callback

### 6. DB Migration
- `migrations/add_group_broadcast_tables.sql`: SQL migration for `bot_groups`, `promo_message_logs`, `promo_opt_outs` tables
- Note: `Base.metadata.create_all(checkfirst=True)` in `database.py` will auto-create tables on startup

## Blocker
- `.env` file missing (gitignored, lost during fork). Backend cannot start without credentials.

## Backlog
- P2: Neon DB sync, configure Redis for production
