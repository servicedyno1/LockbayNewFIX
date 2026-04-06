# Railway App - Dispute Resolution Token Expiry Fix

## Problem Statement
Dispute resolution email action tokens were expiring after only 2 hours, making them unusable by the time admin reviewed them. User wants at least 1 week.

## Root Cause
- `TOKEN_VALIDITY_HOURS = 2` hardcoded in two classes:
  - `AdminEmailActionService` (line 24) — cashout tokens
  - `AdminDisputeEmailService` (line 1663) — dispute tokens
- Email HTML templates had mismatched messaging (some said 24h, some said 2h)

## Fix Applied (Jan 2026)
- Changed `TOKEN_VALIDITY_HOURS` from `2` to `168` (7 days) in both classes
- Updated all email HTML templates to consistently say "7 days"
- File changed: `/app/services/admin_email_actions.py`

## Deployment Notes
- Code change needs to be deployed to Railway to take effect
- After deployment, a new dispute message in dispute #12 will trigger new token generation with 7-day expiry
- Alternatively, database `admin_action_tokens` table can be updated directly to extend `expires_at` for existing tokens

## Backlog
- P0: Deploy code change to Railway
- P1: For immediate use of dispute 12, either trigger new tokens via dispute message or update DB directly
- P2: Consider making TOKEN_VALIDITY_HOURS configurable via environment variable instead of hardcoded
