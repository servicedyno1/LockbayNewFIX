#!/usr/bin/env python3
"""
ESCROW PAYMENT RECOVERY SCRIPT

PURPOSE: Detect and fix orphaned escrow payments caused by async session bugs
- Escrows with status "payment_confirmed" but no EscrowHolding records
- Escrows with status "payment_confirmed" but no Transaction records

USAGE:
    python scripts/recover_orphaned_escrows.py --list              # List orphaned escrows
    python scripts/recover_orphaned_escrows.py --recover ES123     # Recover specific escrow
    python scripts/recover_orphaned_escrows.py --recover-all       # Recover all orphaned escrows
    python scripts/recover_orphaned_escrows.py --dry-run           # Show what would be recovered
"""

import sys
import os
import asyncio
import argparse
from decimal import Decimal
from datetime import datetime
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import async_managed_session
from models import Escrow, EscrowHolding, Transaction, TransactionType
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class OrphanedEscrowRecovery:
    """Service to detect and recover orphaned escrow payments"""
    
    @staticmethod
    async def find_orphaned_escrows() -> List[Dict]:
        """Find escrows with payment_confirmed status but missing holdings or transactions"""
        async with async_managed_session() as session:
            # Query for escrows with payment_confirmed status
            stmt = select(Escrow).where(
                Escrow.status == 'payment_confirmed'
            ).options(
                joinedload(Escrow.transactions)
            )
            
            result = await session.execute(stmt)
            escrows = result.unique().scalars().all()
            
            orphaned = []
            for escrow in escrows:
                # Check if holdings exist by querying directly
                holdings_stmt = select(EscrowHolding).where(
                    EscrowHolding.escrow_id == escrow.escrow_id
                )
                holdings_result = await session.execute(holdings_stmt)
                holdings = holdings_result.scalars().all()
                holdings_exist = len(holdings) > 0
                
                # Check if deposit transaction exists
                deposit_exists = any(
                    t.transaction_type == TransactionType.DEPOSIT.value 
                    for t in escrow.transactions
                )
                
                if not holdings_exist or not deposit_exists:
                    orphaned.append({
                        'escrow_id': escrow.escrow_id,
                        'utid': escrow.utid,
                        'buyer_id': escrow.buyer_id,
                        'seller_id': escrow.seller_id,
                        'amount': float(escrow.amount),
                        'currency': escrow.currency,
                        'status': escrow.status,
                        'created_at': escrow.created_at,
                        'missing_holdings': not holdings_exist,
                        'missing_deposit': not deposit_exists,
                        'transaction_count': len(escrow.transactions),
                        'holding_count': len(holdings)
                    })
            
            return orphaned
    
    @staticmethod
    async def recover_escrow(escrow_id: str, dry_run: bool = False) -> Dict:
        """Recover a specific orphaned escrow by creating missing records"""
        async with async_managed_session() as session:
            # Get escrow with all related data
            stmt = select(Escrow).where(
                Escrow.escrow_id == escrow_id
            ).options(
                joinedload(Escrow.transactions)
            )
            
            result = await session.execute(stmt)
            escrow = result.unique().scalar_one_or_none()
            
            if not escrow:
                return {'success': False, 'error': f'Escrow {escrow_id} not found'}
            
            if escrow.status != 'payment_confirmed':
                return {'success': False, 'error': f'Escrow {escrow_id} status is {escrow.status}, not payment_confirmed'}
            
            recovery_actions = []
            
            # Check if holdings exist by querying directly
            holdings_stmt = select(EscrowHolding).where(
                EscrowHolding.escrow_id == escrow.escrow_id
            )
            holdings_result = await session.execute(holdings_stmt)
            holdings = holdings_result.scalars().all()
            holdings_exist = len(holdings) > 0
            
            deposit_exists = any(
                t.transaction_type == TransactionType.DEPOSIT.value 
                for t in escrow.transactions
            )
            
            if not holdings_exist:
                recovery_actions.append('create_holding')
            
            if not deposit_exists:
                recovery_actions.append('create_deposit_transaction')
            
            if not recovery_actions:
                return {'success': True, 'message': 'No recovery needed - escrow is healthy'}
            
            if dry_run:
                return {
                    'success': True,
                    'dry_run': True,
                    'escrow_id': escrow_id,
                    'actions_needed': recovery_actions,
                    'escrow_amount': float(escrow.amount),
                    'currency': escrow.currency
                }
            
            # RECOVERY: Create missing records
            from services.fee_transparency import FeeTransparencyService
            
            # Calculate fee breakdown
            fee_calc = FeeTransparencyService.calculate_escrow_fees(
                escrow.amount,
                escrow.buyer_id,
                escrow.seller_id
            )
            
            platform_fee = Decimal(str(fee_calc['platform_fee']))
            base_amount = Decimal(str(fee_calc['base_amount']))
            total_amount = Decimal(str(fee_calc['total_amount']))
            
            # Create missing holding
            if 'create_holding' in recovery_actions:
                holding = EscrowHolding(
                    escrow_id=escrow.escrow_id,
                    amount_held=total_amount,
                    currency="USD",
                    created_at=datetime.utcnow(),
                    status="held"
                )
                session.add(holding)
                logger.info(f"✅ Created missing EscrowHolding for {escrow_id}")
            
            # Create missing deposit transaction
            if 'create_deposit_transaction' in recovery_actions:
                transaction = Transaction(
                    transaction_id=f"RECOVERED_{escrow_id}_{int(datetime.utcnow().timestamp())}",
                    user_id=escrow.buyer_id,
                    transaction_type=TransactionType.DEPOSIT.value,
                    amount=total_amount,
                    currency=escrow.currency,
                    status='completed',
                    escrow_id=escrow.id,
                    description=f"Recovered deposit for escrow {escrow.utid} - Auto-recovered by script",
                    created_at=escrow.created_at or datetime.utcnow(),
                    confirmed_at=datetime.utcnow()
                )
                session.add(transaction)
                logger.info(f"✅ Created missing Transaction for {escrow_id}")
            
            # Get values before commit to avoid detached instance errors
            result_data = {
                'success': True,
                'escrow_id': escrow_id,
                'utid': escrow.utid,
                'actions_taken': recovery_actions,
                'base_amount': float(base_amount),
                'platform_fee': float(platform_fee),
                'total_amount': float(total_amount),
                'currency': escrow.currency
            }
            
            await session.commit()
            
            return result_data
    
    @staticmethod
    async def recover_all_orphaned(dry_run: bool = False) -> Dict:
        """Recover all orphaned escrows"""
        orphaned = await OrphanedEscrowRecovery.find_orphaned_escrows()
        
        if not orphaned:
            return {'success': True, 'message': 'No orphaned escrows found'}
        
        results = []
        for escrow_data in orphaned:
            result = await OrphanedEscrowRecovery.recover_escrow(
                escrow_data['escrow_id'],
                dry_run=dry_run
            )
            results.append(result)
        
        return {
            'success': True,
            'total_orphaned': len(orphaned),
            'results': results
        }


async def main():
    parser = argparse.ArgumentParser(description='Recover orphaned escrow payments')
    parser.add_argument('--list', action='store_true', help='List orphaned escrows')
    parser.add_argument('--recover', type=str, help='Recover specific escrow by ID')
    parser.add_argument('--recover-all', action='store_true', help='Recover all orphaned escrows')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    
    args = parser.parse_args()
    
    if args.list:
        orphaned = await OrphanedEscrowRecovery.find_orphaned_escrows()
        if not orphaned:
            print("✅ No orphaned escrows found!")
        else:
            print(f"\n⚠️  Found {len(orphaned)} orphaned escrow(s):\n")
            for escrow in orphaned:
                print(f"Escrow ID: {escrow['escrow_id']} (UTID: {escrow['utid']})")
                print(f"  Amount: {escrow['amount']} {escrow['currency']}")
                print(f"  Status: {escrow['status']}")
                print(f"  Created: {escrow['created_at']}")
                print(f"  Missing Holdings: {escrow['missing_holdings']}")
                print(f"  Missing Deposit: {escrow['missing_deposit']}")
                print(f"  Transactions: {escrow['transaction_count']}, Holdings: {escrow['holding_count']}")
                print()
    
    elif args.recover:
        result = await OrphanedEscrowRecovery.recover_escrow(args.recover, dry_run=args.dry_run)
        if result['success']:
            if result.get('dry_run'):
                print(f"\n🔍 DRY RUN - Would recover {args.recover}:")
                print(f"  Actions: {result['actions_needed']}")
                print(f"  Amount: {result['escrow_amount']} {result['currency']}")
            elif result.get('message'):
                print(f"\n✅ {result['message']}")
            else:
                print(f"\n✅ Successfully recovered {args.recover}")
                print(f"  UTID: {result['utid']}")
                print(f"  Actions: {result['actions_taken']}")
                print(f"  Base Amount: {result['base_amount']} {result['currency']}")
                print(f"  Platform Fee: {result['platform_fee']} {result['currency']}")
        else:
            print(f"\n❌ Recovery failed: {result.get('error')}")
    
    elif args.recover_all:
        result = await OrphanedEscrowRecovery.recover_all_orphaned(dry_run=args.dry_run)
        if result.get('total_orphaned', 0) == 0:
            print("✅ No orphaned escrows found!")
        else:
            if args.dry_run:
                print(f"\n🔍 DRY RUN - Would recover {result['total_orphaned']} escrow(s)")
            else:
                print(f"\n✅ Recovered {result['total_orphaned']} escrow(s)")
            
            for r in result['results']:
                if r['success']:
                    print(f"  ✅ {r['escrow_id']}: {r.get('actions_taken', r.get('actions_needed', []))}")
                else:
                    print(f"  ❌ {r.get('error')}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
