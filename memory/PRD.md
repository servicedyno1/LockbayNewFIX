# Lockbay Telegram Escrow Bot - PRD

## Original Problem Statement
Analyze and set up an existing Lockbay Telegram bot project. Compare the local codebase's onboarding flow with the `Groupmessage` branch of `Moxxcompany/LockbayPaymentFixing` GitHub repository, identify differences, and align the local code.

## Architecture
- **Type**: Monolithic Python Telegram bot
- **Root**: `/app`
- **Entrypoint**: `/app/__main__.py`
- **Config**: `/app/config.py` reads from `/app/.env`
- **Webhooks**: FastAPI at `/app/webhook_server.py`
- **Database**: PostgreSQL (Railway) via `DATABASE_URL`
- **Bot Framework**: python-telegram-bot

## What's Been Implemented (March 6, 2026)

### 1. Email Onboarding Flow Removed
- `handlers/onboarding_router.py`: Auto-completes onboarding for ALL users (new + existing)
- `handlers/start.py`: All email verification code paths removed

### 2. Group Chat Guard
- `main.py`: `_group_chat_guard` at group `-99` ignores messages in groups/supergroups

### 3. Cashout OTP Removed
- `handlers/wallet_direct.py`: NGN + Crypto cashout proceed directly without OTP

### 4. Group Message Broadcasting
- `handlers/group_handler.py`: NEW - handles bot add/remove from groups
- `models.py`: Added `BotGroup`, `PromoMessageLog`, `PromoOptOut` models (aligned to existing DB schema)
- `handlers/escrow.py`: 4 group event broadcasts
- `handlers/user_rating.py`: Rating broadcast
- `main.py`: Registered group handlers
- `services/group_event_service.py`: NEW - group broadcast service

### 5. Dead Code Cleanup
- `handlers/start.py`: 1,902 lines removed (5,132 → 3,202)
- `main.py`: Dead imports and commented-out handlers removed

### 6. DB Migration
- `migrations/add_group_broadcast_tables.sql`: Indexes added to existing tables
- 839 duplicate rows cleaned from `promo_message_logs`
- All tables verified: `bot_groups`, `promo_message_logs`, `promo_opt_outs`

### 7. Environment
- `.env` created with all credentials
- `WEBHOOK_URL` pointed to current pod
- Backend running and healthy

## Status
- Backend: Running, health check passing
- All files compile without errors
- DB migration complete
- No broken imports or references

## Backlog
- P2: Neon DB sync, configure Redis for production
