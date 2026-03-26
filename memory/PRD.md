# Lockbay Telegram Bot - PRD

## Original Problem Statement
Analyze and setup the Lockbay Telegram bot codebase. Update .env with all required environment variables. Ensure the current pod URL is used for the Telegram webhook. Fix all 7 bugs identified from Railway deployment logs. Fix all pre-existing code quality issues.

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

## What's Been Implemented

### Session 1 (2026-03-26) - Environment Setup
- [x] Root `.env` created with 80+ environment variables
- [x] Telegram webhook registered with pod URL

### Session 2 (2026-03-26) - Railway Bug Fixes (7 bugs)
- [x] DB pool exhaustion: pool_size 3→10, async 5→12
- [x] /start cascade failure: timeout + graceful degradation
- [x] asyncpg connection_lost: pool_recycle 1800→600
- [x] Fincra auth circuit breaker (3 failures → 30min cooldown)
- [x] FastForex circuit breaker (5s timeout + 30min cooldown)
- [x] Telegram TimedOut handling
- [x] Kraken circuit breaker (5 failures → 5min cooldown)
- [x] Pool guards on background jobs
- [x] Connection leak killer: dedicated engine

### Session 3 (2026-03-26) - Pre-existing Lint Fixes (~3000 issues)
- [x] F401: 2089 unused imports removed
- [x] F541: 608 empty f-strings fixed
- [x] F841: 383 unused variables cleaned
- [x] F811: 251 duplicate imports/definitions resolved
- [x] F821: ~112 undefined names fixed (actual runtime bugs)
- [x] E722: 21 bare excepts → except Exception
- [x] F601: 3 duplicate dict keys removed
- [x] F823: 10 referenced-before-assignment fixes
- [x] F402: 1 import shadow fixed

Key F821 fixes (runtime bugs):
- handlers/admin_transactions.py: Missing `timezone` import
- handlers/missing_handlers.py: Missing `safe_edit_message_text`, `telegram`, `InlineKeyboardButton/Markup` imports
- handlers/refund_command_registry.py: Missing `InlineKeyboardButton` import
- handlers/dynopay_webhook.py: `crypto_amount`/`paid_currency` undefined in cancel handler
- handlers/fincra_payment.py: `AmountValidationError`/`SecureAmountParser` undefined
- handlers/start.py: `start_onboarding` undefined → routed to `onboarding_router`
- handlers/dispute_chat.py: `dispute.id` → `dispute_id`
- handlers/wallet_direct.py: Missing `get_kraken_withdrawal_service` import
- handlers/admin.py: `monthly_revenue_query` undefined variable removed
- services/payment_routing_security.py: Missing `datetime`, `DirectExchange` imports
- services/receipt_generation_service.py: Missing `or_` import
- services/wallet_notification_service.py: Missing `EmailService` import
- services/security.py: Missing `CryptoServiceAtomic` import
- services/wallet_service_enhancements.py: Missing 14+ imports (full lazy-loading)
- services/notification_delivery_tracker.py: Missing `SessionLocal` import
- services/kraken_address_verification_service.py: `production_cache_service` → `delete_cached`
- services/overpayment_service.py: `tolerance` undefined in f-string
- utils/conversation_protection.py: Missing `ConversationHandler` import
- utils/exchange_prefetch.py: Missing `timezone` import
- utils/startup_reliability_checker.py: `engine` → `sync_engine`
- utils/status_update_facade.py: Missing `WalletHoldStatus` import
- utils/trace_system_initializer.py: Missing `List` import
- jobs/consolidated_scheduler.py: Dead code removed
- jobs/core/reconciliation.py: `kraken_service` → `kraken_adapter`
- jobs/exchange_monitor.py: Multiple undefined service references (lazy-loaded)
- jobs/scheduler.py: `minutes_overdue` undefined

## Testing Results
- Iteration 6: 100% (9/9 tests) - Environment setup
- Iteration 7: 100% (22/22 tests) - Bug fixes
- Iteration 8: 100% (11/11 tests) - Lint fixes + regression

## Prioritized Backlog
### P0 (Critical) - None
### P1 (High)
- Fix Fincra API credentials (auth failing on LIVE mode)
- Renew FastForex subscription
### P2 (Medium)
- Web-based admin monitoring dashboard
- Connection pool utilization metrics endpoint

## Next Tasks
1. Fix Fincra API credentials or switch to test mode
2. Test bot interaction end-to-end via Telegram
