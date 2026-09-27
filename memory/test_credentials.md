# Test Credentials — Lockbay

This project has no username/password web login. Access control is via Telegram admin IDs.

## Telegram Admin
- ADMIN_USER_IDS / ADMIN_IDS = `1531772316`
- Bot: @lockbaybot (token in /app/.env as TELEGRAM_BOT_TOKEN / BOT_TOKEN)

## Database (production, from /app/.env)
- DATABASE_URL (Railway): postgresql://postgres:***@roundhouse.proxy.rlwy.net:24637/railway
- Backup: RAILWAY_BACKUP_DB_URL (switchyard.proxy.rlwy.net)
- Neon (PG* vars): ep-purple-frog-af1vlofq.c-2.us-west-2.aws.neon.tech / neondb

## Preview status API
- GET {REACT_APP_BACKEND_URL}/api/status → configuration/health JSON (read-only)
