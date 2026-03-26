# Railway Deployment Log Analysis - Lockbay Bot Unresponsiveness Report
## Deployment: 63946390-f960-4cc6-a08c-8121065e14ac
## Date Range: 2026-03-19 to 2026-03-26

---

## EXECUTIVE SUMMARY

The bot became **completely unresponsive** due to a **cascading database connection pool exhaustion**. The root cause is a **PostgreSQL connection leak** caused by background jobs (schedulers, health checks, balance monitors) consuming ALL available database connections, leaving ZERO connections available for user-facing operations like `/start`.

---

## BUG #1 (CRITICAL): Database Connection Pool Exhaustion (ROOT CAUSE)

**Error:** `QueuePool limit of size 3 overflow 5 reached, connection timed out, timeout 30.00`

**Impact:** This is the #1 reason the bot was unresponsive. With only 8 total connections (pool_size=3, max_overflow=5), background jobs consume all of them, leaving none for user requests.

**Evidence from logs (500+ occurrences, continuous from 05:47 to 08:30+ UTC):**
- Every 30 seconds, health checks, retry services, balance guards, auto-release services, and notification queues ALL compete for the same tiny pool
- The `CONNECTION_LEAK_KILLER` job itself fails because it can't get a connection: `CONNECTION_LEAK_KILLER error: QueuePool limit of size 3 overflow 5 reached`
- This creates an ironic death spiral: the tool designed to kill leaks is itself blocked by the leak

**Affected services (all starved of connections):**
- `handlers.start` - `/start` command handler
- `handlers.onboarding_router` - New user onboarding
- `utils.referral` - Referral code generation
- `services.unified_retry_service` - Payment retry processing
- `services.admin_notification_queue` - Admin notifications
- `services.standalone_auto_release_service` - Auto-release of funds
- `jobs.consolidated_scheduler` - All scheduled jobs
- `config` - Even maintenance mode checks fail

**Fix Required:**
1. Increase `pool_size` from 3 to at least 10 and `max_overflow` from 5 to 15 in `database.py`
2. Add connection timeouts and proper cleanup to background jobs
3. Implement connection borrowing priority (user-facing > background jobs)
4. Add `pool_reset_on_return='rollback'` is already present but connections aren't being returned fast enough

---

## BUG #2 (CRITICAL): /start Handler Cascade Failure for User 5864480192

**Error chain for user 5864480192 (repeated 20+ times from 07:53 to 08:16):**
1. User sends `/start`
2. `handlers.start` fails: `Error checking pending invitations by telegram ID` (no DB connection)
3. `handlers.onboarding_router` tries to create/lookup user: `Unexpected error creating user 5864480192`
4. `async_user_utils` fails: `Error generating profile slug for user 5864480192: QueryCanceledError`
5. Bot tries to send error message: `safe_reply_text EXCEPTION for user 5864480192: Timed out`
6. Telegram API call times out: `telegram.error.TimedOut: Timed out`
7. The user's `/start` command generates a retry from Telegram, creating MORE load
8. `onboarding_prefetch` times out after 30-120 seconds: `Failed to prefetch context in 120010.8ms`

**Root Cause:** DB pool exhaustion (Bug #1) means the user can never be created or looked up, and the bot can never respond.

---

## BUG #3 (HIGH): asyncpg Connection Drops - `connection_lost()` Errors

**Error:** `ConnectionError: unexpected connection_lost() call`

**44 occurrences** concentrated in two time windows:
- 2026-03-25 20:40-20:43 (3 events)
- 2026-03-26 07:07-08:30 (41 events - massive cluster)

**Cause:** The PostgreSQL server (Railway-hosted) is dropping idle connections, likely due to:
- Railway's proxy layer (`yamabiko.proxy.rlwy.net`) terminating idle connections
- The `pool_recycle=1800` (30 min) is too long for Railway's proxy timeout
- `keepalives_idle=30` may not be enough to keep connections alive through the Railway proxy

**Fix Required:**
1. Reduce `pool_recycle` from 1800 to 600 (10 minutes)
2. Add `pool_pre_ping=True` (already present) but also handle asyncpg-specific reconnection
3. Consider adding `connect_args` with shorter `keepalives_idle` (e.g., 15s)

---

## BUG #4 (HIGH): Fincra Authentication Failure - Credentials Mismatch

**Error:** `Fincra authentication failed: {'message': 'Invalid authentication credentials'}`

**80+ occurrences** (every 5-10 minutes from 04:50 to 08:38 UTC) - this is a CONTINUOUS failure.

**Impact:**
- Balance checks fail repeatedly, consuming DB connections and network resources
- Every failed attempt spawns error handling that further loads the system
- `BALANCE_FETCH_FAILED: No cached Fincra data available and fresh fetch failed`

**Root Cause:** The `FINCRA_API_KEY` (`e0lKniBvM1GeaIt4Kn48D7HjprGRDzyr`) or `FINCRA_SECRET_KEY` is invalid or expired for the LIVE environment (FINCRA_TEST_MODE=false).

**Fix Required:**
1. Regenerate or verify Fincra API credentials
2. Or set `FINCRA_TEST_MODE=true` while debugging
3. Add circuit breaker: after 3 consecutive auth failures, stop retrying for 30+ minutes

---

## BUG #5 (HIGH): FastForex API Subscription Expired

**Error:** `FastForex API error: 403 - {"error":"No active subscription"}`

**Also:** `Network error fetching [BTC|ETH|DOGE|LTC|BCH|TRX|BNB] rate: Connection timeout to host api.fastforex.io`

**Impact:**
- 14 out of 19 rate refreshes fail every cycle (rate refresh takes 240-690 seconds instead of expected <10s)
- NGN rate completely unavailable: `All rate sources failed for USD-NGN`
- The 690-second rate refresh cycle is consuming connections and async resources the entire time

**Fix Required:**
1. Renew FastForex subscription at https://console.fastforex.io/billing/upgrade
2. The Tatum fallback works for major crypto pairs but NOT for NGN
3. Add aggressive timeout (5s) on FastForex API calls to prevent resource hogging

---

## BUG #6 (MEDIUM): Telegram API Timeouts

**Error:** `telegram.error.TimedOut: Timed out`

**120 occurrences** across the deployment period.

**Cause:** When the bot is under DB connection stress:
1. Handler takes 30+ seconds waiting for DB connection
2. By the time it gets one and tries to reply, the Telegram API timeout (30s) has already expired
3. The reply fails with `TimedOut`
4. Telegram re-delivers the update, creating more load (amplification loop)

**This is a SYMPTOM of Bug #1**, not an independent bug. Fix the connection pool, and Telegram timeouts will resolve.

---

## BUG #7 (MEDIUM): Kraken API Failures

**Error:** `Kraken API request failed` / `Failed to get Kraken account balance`

**Impact:** Balance monitoring fails, admin alerts cannot fire correctly.

**Likely Cause:** Network timeouts when the system is under load (cascade from Bug #1), or Kraken rate limiting.

---

## TIMELINE OF FAILURE CASCADE

```
05:47 UTC - QueuePool errors begin (background jobs consuming connections)
05:47-07:00 - Steady degradation, only background jobs affected
07:04 UTC - First Telegram TimedOut errors appear
07:07 UTC - connection_lost() errors begin (asyncpg connections dying)
07:12-07:30 - Heavy connection_lost events (DB proxy killing idle connections)
07:45 UTC - User 5864480192 sends /start, enters failure loop
07:53 UTC - Full cascade: DB exhausted + connection drops + Telegram timeouts
07:53-08:16 - User 5864480192 stuck in retry loop (20+ failed attempts)
08:00 UTC - FastForex rate refresh completely timeout (690 seconds!)
08:00-08:30 - System effectively dead: every service failing
```

---

## RECOMMENDED FIXES (Priority Order)

### P0 - Immediate (Fix Connection Pool)
1. **Increase pool sizes** in `database.py`:
   - Sync: `pool_size=5, max_overflow=10`
   - Async: `pool_size=8, max_overflow=15`
2. **Reduce pool_recycle** to 600s (from 1800s) for Railway proxy compatibility
3. **Add connection cleanup** - ensure background jobs properly release connections with `try/finally`

### P1 - Short Term (Stop Resource Waste)
4. **Circuit-break Fincra** - Stop retrying after 3 auth failures (save connections + network)
5. **Disable or fix FastForex** - 403 subscription error means it's wasting resources on every call
6. **Add aggressive timeouts** to all external API calls (5s for rate APIs, 10s for payment APIs)
7. **Rate-limit background jobs** - Don't let them run when pool utilization > 80%

### P2 - Medium Term (Prevent Recurrence)
8. **Implement connection pool monitoring** - Alert when pool utilization > 70%
9. **Add graceful degradation** - When pool is exhausted, prioritize user-facing handlers
10. **Fix the retry amplification** - When /start fails, don't let Telegram keep retrying endlessly

---

## KEY METRICS FROM LOGS
- **500+** QueuePool exhaustion errors (continuous for 3+ hours)
- **120** Telegram TimedOut errors
- **44** asyncpg connection_lost events
- **80+** Fincra auth failures (continuous)
- **14/19** rate refreshes failing per cycle
- **User 5864480192** stuck for 23+ minutes (07:53 - 08:16+) without any response
- Rate refresh taking **690 seconds** (should be <10s)
