# LockBay Telegram Escrow Bot - PRD

## Problem Statement
1. Update .env with all required environment variables and ensure Telegram webhook uses the current pod URL
2. Bug fix: User @ilovemoney34 canceled escrow ES031126HXF8 but refund didn't go back into wallet

## Architecture
- **Backend**: FastAPI (Python) running on port 8001 via supervisor
- **Bot Framework**: python-telegram-bot v22+
- **Database**: PostgreSQL (Railway-hosted)
- **Webhook Mode**: FastAPI receives Telegram updates via POST /webhook
- **Kubernetes Ingress**: Routes /api/* to backend port 8001

## What's Been Implemented

### Session 1 (2026-03-11) - Environment Setup
1. Created `/app/.env` with all 75+ environment variables
2. Updated `WEBHOOK_URL` to use current pod URL
3. Updated `DYNOPAY_WEBHOOK_URL` to use current pod URL
4. Installed all missing Python dependencies
5. Verified backend starts successfully with webhook registered

### Session 2 (2026-03-11) - Escrow Refund Bug Fix
**Root Cause Analysis:**
- User @ilovemoney34 created escrow ES031126HXF8 ($350 + $35 fee) with BTC payment
- DynoPay received BTC (~$387) but webhook failed due to missing reference_id
- User cancelled escrow while it was still in `payment_pending`
- Cancel handler didn't process refunds, DynoPay retries were rejected

**Bug 1 - DynoPay Webhook Reference ID (dynopay_webhook.py ~line 147):**
- DynoPay `payment.underpaid` events send `transaction_reference` but code only checked `meta_data.refId` and `customer_reference`
- Also `meta_data` could be `null` causing NoneType AttributeError
- Fix: Added `transaction_reference` to extraction chain, used `or {}` for None meta_data, added address-based fallback lookup

**Bug 2 - Cancelled Escrow Webhook Rejection (dynopay_webhook.py ~line 734):**
- When payment received for cancelled escrow, handler just rejected it (lost funds)
- Fix: Added auto-refund logic that credits buyer wallet with escrow base amount (without platform fee) using CryptoServiceAtomic.credit_user_wallet_atomic
- Sends Telegram notification to buyer and admin notification about the auto-refund
- Updates escrow status to `refunded`

**Testing**: All 6 backend tests passed (100%)

## Webhook URLs (Current Pod)
- Telegram: `https://ee7ea911-f525-4def-949e-8758f2117e9c.preview.emergentagent.com/api/webhook`
- DynoPay: `https://ee7ea911-f525-4def-949e-8758f2117e9c.preview.emergentagent.com/api/webhook/dynopay`

## Backlog
- P0: None
- P1: Monitor if DynoPay retries the webhook for ES031126HXF8 and auto-refund triggers
- P2: Consider adding `amount_received` field mapping for DynoPay `payment.underpaid` events in more places
- P2: Review MANUAL_REFUNDS_ONLY config flag interaction with new auto-refund logic
