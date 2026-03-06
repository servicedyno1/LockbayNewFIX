-- Migration: Add indexes for BotGroup, PromoMessageLog, PromoOptOut tables
-- Date: 2026-03-06
-- Description: Adds missing indexes to existing tables

-- BotGroup indexes (table already exists)
CREATE INDEX IF NOT EXISTS ix_bot_groups_chat_id ON bot_groups (chat_id);
CREATE INDEX IF NOT EXISTS ix_bot_groups_active ON bot_groups (is_active);

-- PromoMessageLog indexes (table already exists)
CREATE INDEX IF NOT EXISTS ix_promo_logs_user_id ON promo_message_logs (user_id);
CREATE INDEX IF NOT EXISTS ix_promo_logs_message_key ON promo_message_logs (message_key);
CREATE UNIQUE INDEX IF NOT EXISTS ix_promo_user_key ON promo_message_logs (user_id, message_key);

-- PromoOptOut indexes (table already exists)
CREATE INDEX IF NOT EXISTS ix_promo_optout_user_id ON promo_opt_outs (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_promo_optout_user ON promo_opt_outs (user_id);
