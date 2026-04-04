# LockBay - Telegram Escrow Bot PRD

## Original Problem Statement
User requested setup and analysis of existing LockBay codebase, then reported a bug: BTC Bech32 addresses (bc1...) being rejected during cashout with "BTC address must be 26-35 characters" error.

## Architecture
- **Backend**: FastAPI (Python) serving as Telegram bot webhook server on port 8001
- **Frontend**: React status page on port 3000
- **Database**: PostgreSQL (Railway-hosted)
- **Bot Framework**: python-telegram-bot v22.7
- **Messaging**: Telegram Bot API (webhook mode)

## What's Been Implemented (Existing)
- Full Telegram escrow bot with webhook-based architecture
- PostgreSQL database with 57+ tables
- Wallet system (crypto funding, bank deposits, cashouts)
- Escrow creation, payment, release, dispute flows
- Admin dashboard, broadcast, analytics
- Rating system, referral system
- DynoPay and Fincra payment integrations
- BlockBee crypto payment integration

## Setup Completed (Jan 2026)
- Fixed missing Python dependencies (orjson, python-telegram-bot, etc.)
- Installed frontend node_modules
- Fixed .env URL configuration (REACT_APP_BACKEND_URL, WEBHOOK_URL, DYNOPAY_WEBHOOK_URL)
- All services running: backend, frontend, mongodb

## Bug Fix: BTC Bech32 Address Validation (Jan 2026)
- **Root Cause**: `validate_crypto_address()` in `wallet_direct.py` enforced 26-35 char limit for ALL BTC addresses, but Bech32 (bc1q...) addresses are 42 chars and Taproot (bc1p...) are 62 chars
- **Files Fixed**:
  - `handlers/wallet_direct.py` - Main validation function, validation tips text
  - `handlers/messages_hub.py` - Crypto address detection logic
  - `handlers/escrow.py` - Crypto address detection in escrow flow
  - `services/qr_generator.py` - QR code address validation
- **Also fixed**: LTC Bech32 (ltc1...) address support in validation

## Current Status
- Backend: RUNNING
- Frontend: RUNNING
- Database: Connected
- Bot: Initialized with webhook registered
- BTC Bech32 validation: FIXED

## Prioritized Backlog
- P1: Brevo API key not configured (email notifications disabled)
- P2: Redis not configured (using fallback)

## Next Tasks
- User to test BTC cashout with Bech32 addresses
- User to specify additional features/changes
