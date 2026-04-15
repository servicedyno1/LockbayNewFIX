# LockBay - Telegram Escrow Bot

## Application Overview
LockBay is a comprehensive Telegram-based escrow trading platform that enables secure peer-to-peer transactions via a Telegram bot. The platform handles cryptocurrency and fiat (NGN) payments, wallet management, dispute resolution, and more.

## Architecture
- **Backend**: FastAPI (Python) serving as both a webhook server for Telegram and REST API
- **Database**: PostgreSQL (Neon/Railway hosted) with SQLAlchemy ORM - 57+ tables
- **Frontend**: React status/landing page (minimal - bot is primary interface)
- **Bot Framework**: python-telegram-bot v22.7 (webhook mode)
- **Background Jobs**: APScheduler (consolidated scheduler with 5 core jobs)
- **Payment Providers**: BlockBee, DynoPay, Fincra, Kraken
- **Email**: Brevo (SendinBlue) for notifications and OTP
- **SMS**: Twilio for trade invitations
- **Caching**: Redis (optional, with fallback)

## Environment Setup (Jan 2026)
- Installed missing Python dependencies, fixed package conflicts
- Fixed `load_dotenv(override=True)` → `override=False`
- Made bot initialization non-blocking in preview environment
- Updated `.env` files with correct URLs

## Bug Fix: Escrow Creation Intermittent Failures (Apr 15, 2026)

### Root Causes Found (via Railway production log analysis)

**Bug 1 (CRITICAL) - Support chat hijacks escrow flow:**
- Stale support chat sessions in `active_support_sessions` in-memory dict intercept ALL text messages
- Route guard checks support chat (priority 3) BEFORE escrow states (priority 5)
- Users stuck at `seller_input` have messages silently swallowed by support handler
- **Fix**: Added escrow flow states check (priority 2D) BEFORE support chat in `route_guard.py`

**Bug 2 (CRITICAL) - Delivery time clears state, restarting flow:**
- After entering delivery time, `escrow_direct.py` calls `clear_user_state()` instead of mapping to next state
- Handler returns `EscrowStates.FEE_SPLIT_OPTION` but routing code ignores it
- Empty state causes `start_secure_trade` to fire again, resetting to `seller_input`
- **Fix**: Added proper state mapping for delivery_time handler (like amount/description handlers)

**Bug 3 (MODERATE) - Seller input always transitions to `amount_input`:**
- Routing code unconditionally sets state to `amount_input` after seller input
- Doesn't check if handler returned `SELLER_INPUT` (validation error) vs `AMOUNT_INPUT` (success)
- **Fix**: Added proper state mapping checking actual return value

### Files Changed
- `/app/utils/route_guard.py` - Added escrow flow priority before support chat
- `/app/handlers/escrow_direct.py` - Fixed delivery_time and seller_input state mapping

## Backlog
- P0: Deploy fixes to Railway production
- P1: Optimize background jobs to not block event loop (use async DB queries in APScheduler)
- P2: Deduplicate start_secure_trade handler registration
- P3: Add monitoring for route_guard decisions to track future routing conflicts
