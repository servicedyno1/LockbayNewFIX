-- Migration: Add BotGroup, PromoMessageLog, PromoOptOut tables
-- Date: 2026-03-06
-- Description: Adds tables for group event broadcasting and promotional message tracking

-- BotGroup: Tracks Telegram groups the bot has been added to
CREATE TABLE IF NOT EXISTS bot_groups (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    chat_title VARCHAR(255),
    chat_type VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    events_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_bot_groups_chat_id ON bot_groups (chat_id);
CREATE INDEX IF NOT EXISTS ix_bot_groups_active ON bot_groups (is_active);

-- PromoMessageLog: Tracks promotional messages sent to users
CREATE TABLE IF NOT EXISTS promo_message_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    telegram_id BIGINT NOT NULL,
    promo_key VARCHAR(100) NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_promo_logs_user_id ON promo_message_logs (user_id);
CREATE INDEX IF NOT EXISTS ix_promo_logs_telegram_id ON promo_message_logs (telegram_id);
CREATE INDEX IF NOT EXISTS ix_promo_logs_promo_key ON promo_message_logs (promo_key);
CREATE UNIQUE INDEX IF NOT EXISTS ix_promo_user_key ON promo_message_logs (user_id, promo_key);

-- PromoOptOut: Users who have opted out of promotional messages
CREATE TABLE IF NOT EXISTS promo_opt_outs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    telegram_id BIGINT NOT NULL,
    opted_out_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_promo_optout_user_id ON promo_opt_outs (user_id);
CREATE INDEX IF NOT EXISTS ix_promo_optout_telegram_id ON promo_opt_outs (telegram_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_promo_optout_user ON promo_opt_outs (user_id);
