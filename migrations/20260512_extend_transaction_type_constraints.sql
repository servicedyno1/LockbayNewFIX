-- ============================================================================
-- Migration: Extend transactions.transaction_type allowed values
-- Date:      2026-05-12
-- Reason:    Production CHECK constraint `ck_transaction_type_valid` was
--            blocking `escrow_overpayment` (and several other types the code
--            already uses), causing webhook handler crashes and stranding
--            real payments. See incident on escrow ES0512264NC9.
--
-- Safe to run multiple times (drops + re-creates the two constraints).
-- ============================================================================

BEGIN;

ALTER TABLE transactions DROP CONSTRAINT IF EXISTS ck_transaction_type_valid;
ALTER TABLE transactions DROP CONSTRAINT IF EXISTS ck_transaction_entity_link_required;

ALTER TABLE transactions
  ADD CONSTRAINT ck_transaction_type_valid CHECK (
    transaction_type IN (
      'deposit','withdrawal',
      'escrow_payment','escrow_release','escrow_refund',
      'escrow_overpayment','escrow_underpay_refund',
      'wallet_transfer','wallet_deposit','wallet_payment','wallet_credit',
      'cashout','cashout_debit','cashout_hold','cashout_hold_release',
      'frozen_balance_consume',
      'exchange_hold','exchange_debit','exchange_hold_release','exchange_overpayment',
      'refund','fee','admin_adjustment'
    )
  );

ALTER TABLE transactions
  ADD CONSTRAINT ck_transaction_entity_link_required CHECK (
    (transaction_type IN (
      'escrow_payment','escrow_release','escrow_refund',
      'escrow_overpayment','escrow_underpay_refund'
    ) AND escrow_id IS NOT NULL)
    OR (transaction_type IN (
      'cashout','cashout_debit','cashout_hold','cashout_hold_release'
    ) AND cashout_id IS NOT NULL)
    OR (transaction_type IN (
      'deposit','withdrawal','wallet_transfer','wallet_deposit','wallet_payment','wallet_credit',
      'frozen_balance_consume','exchange_hold','exchange_debit','exchange_hold_release','exchange_overpayment',
      'refund','fee','admin_adjustment'
    ))
  );

COMMIT;
