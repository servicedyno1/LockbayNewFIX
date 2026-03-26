#!/usr/bin/env python3
"""
Duplicate Overpayment Audit Script
Identifies all duplicate overpayment credits and generates detailed reports
"""

import asyncio
import sys
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import select, text
from database import async_managed_session
from models import User, Wallet

async def audit_duplicate_overpayments(days_back=None):
    """Audit all duplicate overpayment transactions and generate report
    
    Args:
        days_back: Number of days to look back (None = all history)
    """
    print("=" * 80)
    print("DUPLICATE OVERPAYMENT AUDIT REPORT")
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
    
    async with async_managed_session() as session:
        # Find all duplicate overpayment groups
        query = text(f"""
            WITH overpayment_groups AS (
                SELECT 
                    user_id,
                    escrow_id,
                    amount,
                    currency,
                    description,
                    COUNT(*) as occurrence_count,
                    ARRAY_AGG(id ORDER BY created_at) as transaction_ids,
                    ARRAY_AGG(transaction_id ORDER BY created_at) as tx_uuids,
                    ARRAY_AGG(created_at ORDER BY created_at) as timestamps,
                    MIN(created_at) as first_occurrence,
                    MAX(created_at) as last_occurrence
                FROM transactions
                WHERE transaction_type = 'escrow_overpayment'
                {date_filter}
                GROUP BY user_id, escrow_id, amount, currency, description
                HAVING COUNT(*) > 1
            )
            SELECT 
                og.user_id,
                u.username,
                u.email,
                e.escrow_id,
                og.amount,
                og.currency,
                og.occurrence_count,
                og.occurrence_count - 1 as duplicate_count,
                (og.amount * (og.occurrence_count - 1)) as excess_credited,
                og.transaction_ids,
                og.tx_uuids,
                og.timestamps,
                og.first_occurrence,
                og.last_occurrence
            FROM overpayment_groups og
            LEFT JOIN users u ON og.user_id = u.id
            LEFT JOIN escrows e ON og.escrow_id = e.id
            ORDER BY (og.amount * (og.occurrence_count - 1)) DESC
        """)
        
        result = await session.execute(query)
        duplicates = result.fetchall()
        
        if not duplicates:
            print("✅ No duplicate overpayment transactions found!")
            return
        
        total_excess = Decimal('0')
        affected_users = set()
        
        print(f"🚨 Found {len(duplicates)} groups of duplicate overpayments\n")
        
        for idx, dup in enumerate(duplicates, 1):
            user_id, username, email, escrow_id, amount, currency, occurrence_count, \
            duplicate_count, excess_credited, transaction_ids, tx_uuids, timestamps, \
            first_occurrence, last_occurrence = dup
            
            affected_users.add(user_id)
            total_excess += Decimal(str(excess_credited))
            
            print(f"[{idx}] DUPLICATE GROUP")
            print(f"    User: {username} (ID: {user_id}, Email: {email})")
            print(f"    Escrow: {escrow_id}")
            print(f"    Original Amount: ${amount} {currency}")
            print(f"    Occurrences: {occurrence_count} (should be 1)")
            print(f"    Duplicate Count: {duplicate_count}")
            print(f"    Excess Credited: ${excess_credited}")
            print(f"    Transaction IDs: {transaction_ids}")
            print("    Timestamps:")
            for i, ts in enumerate(timestamps):
                marker = "✅ ORIGINAL" if i == 0 else f"❌ DUPLICATE {i}"
                print(f"        {marker}: {ts}")
            print(f"    Time Span: {first_occurrence} → {last_occurrence}")
            print()
        
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total Affected Users: {len(affected_users)}")
        print(f"Total Duplicate Groups: {len(duplicates)}")
        print(f"Total Excess Credited: ${total_excess}")
        print()
        
        # Get current wallet balances for affected users
        print("AFFECTED USER WALLET BALANCES")
        print("-" * 80)
        
        for user_id in affected_users:
            wallet_query = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == 'USD'
            )
            wallet_result = await session.execute(wallet_query)
            wallet = wallet_result.scalar_one_or_none()
            
            user_query = select(User).where(User.id == user_id)
            user_result = await session.execute(user_query)
            user = user_result.scalar_one_or_none()
            
            if wallet and user:
                print(f"User: {user.username} (ID: {user_id})")
                print(f"  Current USD Balance: ${wallet.available_balance}")
                print(f"  Available: ${wallet.available_balance}")
                print(f"  Frozen: ${wallet.frozen_balance}")
                print()
        
        print("=" * 80)
        print("RECOMMENDED ACTIONS")
        print("=" * 80)
        print("1. Review the duplicate transactions above")
        print("2. Run the rollback script to deduct excess credits from wallets")
        print("3. Add idempotency protection to prevent future duplicates")
        print("4. Monitor for any new duplicate overpayments")
        print()
        print("Script: python scripts/rollback_duplicate_overpayments.py")
        print("=" * 80)

if __name__ == "__main__":
    # Parse command line arguments
    days_back = None
    if len(sys.argv) > 1:
        if sys.argv[1] == "--all":
            days_back = None
        else:
            try:
                days_back = int(sys.argv[1])
            except ValueError:
                print("Usage: python audit_duplicate_overpayments.py [days_back|--all]")
                print("  days_back: Number of days to look back (default: scan all history)")
                print("  --all: Explicitly scan all history")
                sys.exit(1)
    
    asyncio.run(audit_duplicate_overpayments(days_back=days_back))
