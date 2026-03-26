#!/usr/bin/env python3
"""
Rollback Duplicate Overpayment Credits
Safely deducts excess credits from user wallets and marks duplicate transactions
"""

import asyncio
import sys
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import select, text
from database import async_managed_session
from models import Transaction, Wallet
from utils.universal_id_generator import UniversalIDGenerator

async def rollback_duplicate_overpayments(dry_run=True, days_back=None, force=False):
    """
    Rollback duplicate overpayment credits from user wallets
    
    Args:
        dry_run: If True, only show what would be done without making changes
        days_back: Number of days to look back (None = all history)
        force: If True, skip confirmation prompt (for automated execution)
    """
    mode = "DRY RUN" if dry_run else "LIVE EXECUTION"
    print("=" * 80)
    print(f"DUPLICATE OVERPAYMENT ROLLBACK - {mode}")
    print(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    if days_back:
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        print(f"Scanning Period: Last {days_back} days (from {cutoff_date.strftime('%Y-%m-%d')})")
        date_filter = f"AND created_at >= '{cutoff_date.isoformat()}'"
    else:
        print("Scanning Period: ALL HISTORY")
        date_filter = ""
    
    print("=" * 80)
    print()
    
    if not dry_run and not force:
        confirm = input("⚠️  This will modify wallet balances. Type 'CONFIRM' to proceed: ")
        if confirm != 'CONFIRM':
            print("❌ Rollback cancelled.")
            return
        print()
    elif not dry_run and force:
        print("⚠️  Running in FORCE mode - skipping confirmation")
        print()
    
    async with async_managed_session() as session:
        # Find all duplicate overpayment groups
        query = text(f"""
            WITH overpayment_groups AS (
                SELECT 
                    user_id,
                    escrow_id,
                    amount,
                    currency,
                    COUNT(*) as occurrence_count,
                    ARRAY_AGG(id ORDER BY created_at) as transaction_ids,
                    ARRAY_AGG(transaction_id ORDER BY created_at) as tx_uuids,
                    MIN(created_at) as first_occurrence
                FROM transactions
                WHERE transaction_type = 'escrow_overpayment'
                {date_filter}
                GROUP BY user_id, escrow_id, amount, currency
                HAVING COUNT(*) > 1
            )
            SELECT 
                og.user_id,
                u.username,
                e.escrow_id,
                e.id as escrow_db_id,
                og.amount,
                og.currency,
                og.occurrence_count,
                (og.amount * (og.occurrence_count - 1)) as excess_credited,
                og.transaction_ids,
                og.tx_uuids
            FROM overpayment_groups og
            LEFT JOIN users u ON og.user_id = u.id
            LEFT JOIN escrows e ON og.escrow_id = e.id
            ORDER BY og.user_id, e.escrow_id
        """)
        
        result = await session.execute(query)
        duplicates = result.fetchall()
        
        if not duplicates:
            print("✅ No duplicate overpayment transactions found!")
            return
        
        print(f"🔍 Found {len(duplicates)} groups of duplicate overpayments to rollback\n")
        
        rollback_actions = []
        total_to_deduct = Decimal('0')
        
        for idx, dup in enumerate(duplicates, 1):
            user_id, username, escrow_id, escrow_db_id, amount, currency, occurrence_count, \
            excess_credited, transaction_ids, tx_uuids = dup
            
            total_to_deduct += Decimal(str(excess_credited))
            
            print(f"[{idx}] Processing User: {username} (ID: {user_id})")
            print(f"    Escrow: {escrow_id}")
            print(f"    Excess Credited: ${excess_credited} {currency}")
            print(f"    Duplicate Transaction IDs: {transaction_ids[1:]}")  # Skip first (original)
            
            # Get user's current wallet balance
            wallet_query = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            ).with_for_update()
            
            wallet_result = await session.execute(wallet_query)
            wallet = wallet_result.scalar_one_or_none()
            
            if not wallet:
                print(f"    ❌ ERROR: No {currency} wallet found for user {user_id}")
                continue
            
            print(f"    Current Balance: ${wallet.available_balance}")
            
            if wallet.available_balance < Decimal(str(excess_credited)):
                print("    ⚠️  WARNING: Insufficient balance to deduct full amount!")
                print(f"    Available: ${wallet.available_balance}, Need: ${excess_credited}")
                deduct_amount = wallet.available_balance  # Deduct what's available
            else:
                deduct_amount = Decimal(str(excess_credited))
            
            print(f"    Will Deduct: ${deduct_amount}")
            new_balance = wallet.available_balance - deduct_amount
            print(f"    New Balance: ${new_balance}")
            
            if not dry_run:
                # Deduct from wallet
                wallet.available_balance = new_balance
                
                # Create reversal transaction for audit trail
                reversal_tx_id = UniversalIDGenerator.generate_id("transaction", custom_prefix="ROLLBACK")
                reversal_tx = Transaction(
                    transaction_id=reversal_tx_id,
                    user_id=user_id,
                    transaction_type="admin_adjustment",  # Required for constraint compliance
                    amount=deduct_amount,  # Positive amount (DB constraint requirement)
                    currency=currency,
                    status="completed",
                    description=f"🔄 Rollback: Duplicate overpayment reversal for trade #{escrow_id} (-${deduct_amount})",
                    extra_data={
                        "rollback_reason": "duplicate_overpayment",
                        "escrow_id": escrow_id,
                        "duplicate_tx_ids": transaction_ids[1:],
                        "original_tx_id": transaction_ids[0],
                        "rollback_timestamp": datetime.utcnow().isoformat()
                    }
                )
                session.add(reversal_tx)
                
                # Mark duplicate transactions with metadata
                for tx_id in transaction_ids[1:]:  # Skip first (original)
                    tx_query = select(Transaction).where(Transaction.id == tx_id)
                    tx_result = await session.execute(tx_query)
                    duplicate_tx = tx_result.scalar_one_or_none()
                    
                    if duplicate_tx:
                        if duplicate_tx.extra_data:
                            duplicate_tx.extra_data['marked_as_duplicate'] = True
                            duplicate_tx.extra_data['rollback_tx_id'] = reversal_tx_id
                        else:
                            duplicate_tx.extra_data = {
                                'marked_as_duplicate': True,
                                'rollback_tx_id': reversal_tx_id
                            }
                        duplicate_tx.description += " [DUPLICATE - ROLLED BACK]"
                
                rollback_actions.append({
                    'user_id': user_id,
                    'username': username,
                    'escrow_id': escrow_id,
                    'deducted': deduct_amount,
                    'reversal_tx_id': reversal_tx_id
                })
                
                print("    ✅ Wallet balance adjusted")
                print(f"    ✅ Reversal transaction created: {reversal_tx_id}")
            else:
                print(f"    [DRY RUN] Would deduct ${deduct_amount} and create reversal transaction")
            
            print()
        
        if not dry_run:
            await session.commit()
            print("=" * 80)
            print("✅ ROLLBACK COMPLETED SUCCESSFULLY")
            print("=" * 80)
            print(f"Total Actions: {len(rollback_actions)}")
            print(f"Total Deducted: ${total_to_deduct}")
            print()
            print("ROLLBACK SUMMARY:")
            for action in rollback_actions:
                print(f"  • User {action['username']}: ${action['deducted']} deducted (Escrow: {action['escrow_id']})")
                print(f"    Reversal TX: {action['reversal_tx_id']}")
            print()
        else:
            print("=" * 80)
            print("DRY RUN COMPLETE - NO CHANGES MADE")
            print("=" * 80)
            print(f"Total Would Deduct: ${total_to_deduct}")
            print()
            print("To execute rollback, run:")
            print("  python scripts/rollback_duplicate_overpayments.py --live")
            print()

if __name__ == "__main__":
    # Parse command line arguments
    dry_run = "--live" not in sys.argv
    force = "--force" in sys.argv
    days_back = None
    
    for arg in sys.argv[1:]:
        if arg in ["--live", "--force"]:
            continue
        elif arg == "--all":
            days_back = None
        else:
            try:
                days_back = int(arg)
            except ValueError:
                print("Usage: python rollback_duplicate_overpayments.py [--live] [--force] [days_back|--all]")
                print("  --live: Execute rollback (default is dry-run)")
                print("  --force: Skip confirmation prompt (for automated execution)")
                print("  days_back: Number of days to look back (default: scan all history)")
                print("  --all: Explicitly scan all history")
                sys.exit(1)
    
    asyncio.run(rollback_duplicate_overpayments(dry_run=dry_run, days_back=days_back, force=force))
