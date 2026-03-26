"""
Unified ID Migration Utility
===========================

Safe migration utility to populate UTID fields for existing database records.
Ensures all existing data gets unified IDs without breaking current functionality.

Usage:
    python -m utils.unified_id_migration --dry-run  # Preview changes
    python -m utils.unified_id_migration --execute  # Apply migration
"""

import logging
import argparse
from datetime import datetime
from typing import Dict
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Escrow, Transaction, Cashout, Refund, Wallet, PaymentAddress, SavedAddress
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


class UnifiedIDMigration:
    """Safe migration utility for populating UTID fields"""
    
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.stats = {
            'escrows': {'total': 0, 'updated': 0, 'skipped': 0},
            'transactions': {'total': 0, 'updated': 0, 'skipped': 0},
            'cashouts': {'total': 0, 'updated': 0, 'skipped': 0},
            'refunds': {'total': 0, 'updated': 0, 'skipped': 0},
            'wallets': {'total': 0, 'updated': 0, 'skipped': 0},
            'payment_addresses': {'total': 0, 'updated': 0, 'skipped': 0},
            'saved_addresses': {'total': 0, 'updated': 0, 'skipped': 0},
        }
    
    def migrate_all(self) -> Dict:
        """
        Migrate all entities to unified ID system
        
        Returns:
            Migration statistics
        """
        logger.info(f"Starting unified ID migration (dry_run={self.dry_run})")
        
        try:
            session = SessionLocal()
            
            # Migrate each entity type
            self._migrate_escrows(session)
            self._migrate_transactions(session)
            self._migrate_cashouts(session)
            self._migrate_refunds(session)
            self._migrate_wallets(session)
            self._migrate_payment_addresses(session)
            self._migrate_saved_addresses(session)
            
            if not self.dry_run:
                session.commit()
                logger.info("Migration committed successfully")
            else:
                session.rollback()
                logger.info("Dry run completed - no changes committed")
                
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            session.rollback()
            raise
        finally:
            session.close()
        
        return self.stats
    
    def _migrate_escrows(self, session: Session) -> None:
        """Migrate escrow records to unified IDs"""
        logger.info("Migrating escrows...")
        
        # Get all escrows without UTID
        escrows = session.query(Escrow).filter(
            (Escrow.utid is None) | (Escrow.utid == '')
        ).all()
        
        self.stats['escrows']['total'] = len(escrows)
        
        for escrow in escrows:
            try:
                # Generate unified ID maintaining date consistency
                if hasattr(escrow, 'created_at') and escrow.created_at:
                    # Use existing creation date for consistency
                    unified_id = self._generate_date_consistent_id('escrow', escrow.created_at)
                else:
                    # Fallback to new ID
                    unified_id = UniversalIDGenerator.generate_escrow_id()
                
                # Check for conflicts
                existing = session.query(Escrow).filter(Escrow.utid == unified_id).first()
                if existing:
                    # Generate fresh ID if conflict
                    unified_id = UniversalIDGenerator.generate_escrow_id()
                
                if not self.dry_run:
                    escrow.utid = unified_id
                
                self.stats['escrows']['updated'] += 1
                logger.debug(f"Escrow {escrow.escrow_id} → UTID: {unified_id}")
                
            except Exception as e:
                logger.error(f"Failed to migrate escrow {escrow.escrow_id}: {e}")
                self.stats['escrows']['skipped'] += 1
    
    def _migrate_transactions(self, session: Session) -> None:
        """Migrate transaction records to unified IDs"""
        logger.info("Migrating transactions...")
        
        # Get all transactions without UTID
        transactions = session.query(Transaction).filter(
            (Transaction.utid is None) | (Transaction.utid == '')
        ).all()
        
        self.stats['transactions']['total'] = len(transactions)
        
        for transaction in transactions:
            try:
                # Generate unified ID maintaining date consistency
                if hasattr(transaction, 'created_at') and transaction.created_at:
                    unified_id = self._generate_date_consistent_id('transaction', transaction.created_at)
                else:
                    unified_id = UniversalIDGenerator.generate_transaction_id()
                
                # Check for conflicts
                existing = session.query(Transaction).filter(Transaction.utid == unified_id).first()
                if existing:
                    unified_id = UniversalIDGenerator.generate_transaction_id()
                
                if not self.dry_run:
                    transaction.utid = unified_id
                
                self.stats['transactions']['updated'] += 1
                logger.debug(f"Transaction {transaction.transaction_id} → UTID: {unified_id}")
                
            except Exception as e:
                logger.error(f"Failed to migrate transaction {transaction.transaction_id}: {e}")
                self.stats['transactions']['skipped'] += 1
    
    def _migrate_cashouts(self, session: Session) -> None:
        """Migrate cashout records to unified IDs"""
        logger.info("Migrating cashouts...")
        
        # Get all cashouts without UTID
        cashouts = session.query(Cashout).filter(
            (Cashout.utid is None) | (Cashout.utid == '')
        ).all()
        
        self.stats['cashouts']['total'] = len(cashouts)
        
        for cashout in cashouts:
            try:
                # Generate unified ID maintaining date consistency
                if hasattr(cashout, 'created_at') and cashout.created_at:
                    unified_id = self._generate_date_consistent_id('cashout', cashout.created_at)
                else:
                    unified_id = UniversalIDGenerator.generate_cashout_id()
                
                # Check for conflicts
                existing = session.query(Cashout).filter(Cashout.utid == unified_id).first()
                if existing:
                    unified_id = UniversalIDGenerator.generate_cashout_id()
                
                if not self.dry_run:
                    cashout.utid = unified_id
                
                self.stats['cashouts']['updated'] += 1
                logger.debug(f"Cashout {cashout.cashout_id} → UTID: {unified_id}")
                
            except Exception as e:
                logger.error(f"Failed to migrate cashout {cashout.cashout_id}: {e}")
                self.stats['cashouts']['skipped'] += 1
    
    def _migrate_refunds(self, session: Session) -> None:
        """Migrate refund records to unified IDs"""
        logger.info("Migrating refunds...")
        
        # Get all refunds without UTID
        refunds = session.query(Refund).filter(
            (Refund.utid is None) | (Refund.utid == '')
        ).all()
        
        self.stats['refunds']['total'] = len(refunds)
        
        for refund in refunds:
            try:
                # Generate unified ID maintaining date consistency
                if hasattr(refund, 'created_at') and refund.created_at:
                    unified_id = self._generate_date_consistent_id('refund', refund.created_at)
                else:
                    unified_id = UniversalIDGenerator.generate_refund_id()
                
                # Check for conflicts
                existing = session.query(Refund).filter(Refund.utid == unified_id).first()
                if existing:
                    unified_id = UniversalIDGenerator.generate_refund_id()
                
                if not self.dry_run:
                    refund.utid = unified_id
                
                self.stats['refunds']['updated'] += 1
                logger.debug(f"Refund {refund.refund_id} → UTID: {unified_id}")
                
            except Exception as e:
                logger.error(f"Failed to migrate refund {refund.refund_id}: {e}")
                self.stats['refunds']['skipped'] += 1
    
    def _migrate_wallets(self, session: Session) -> None:
        """Migrate wallet records to unified IDs"""
        logger.info("Migrating wallets...")
        
        # Get all wallets without UTID
        wallets = session.query(Wallet).filter(
            (Wallet.utid is None) | (Wallet.utid == '')
        ).all()
        
        self.stats['wallets']['total'] = len(wallets)
        
        for wallet in wallets:
            try:
                # Generate unified ID for wallet
                unified_id = UniversalIDGenerator.generate_id('wallet_deposit')
                
                # Check for conflicts
                existing = session.query(Wallet).filter(Wallet.utid == unified_id).first()
                if existing:
                    unified_id = UniversalIDGenerator.generate_id('wallet_deposit')
                
                if not self.dry_run:
                    wallet.utid = unified_id
                
                self.stats['wallets']['updated'] += 1
                logger.debug(f"Wallet {wallet.id} → UTID: {unified_id}")
                
            except Exception as e:
                logger.error(f"Failed to migrate wallet {wallet.id}: {e}")
                self.stats['wallets']['skipped'] += 1
    
    def _migrate_payment_addresses(self, session: Session) -> None:
        """Migrate payment address records to unified IDs"""
        logger.info("Migrating payment addresses...")
        
        # Get all payment addresses without UTID
        addresses = session.query(PaymentAddress).filter(
            (PaymentAddress.utid is None) | (PaymentAddress.utid == '')
        ).all()
        
        self.stats['payment_addresses']['total'] = len(addresses)
        
        for address in addresses:
            try:
                # Generate unified ID for payment address
                unified_id = UniversalIDGenerator.generate_id('payment')
                
                # Check for conflicts
                existing = session.query(PaymentAddress).filter(PaymentAddress.utid == unified_id).first()
                if existing:
                    unified_id = UniversalIDGenerator.generate_id('payment')
                
                if not self.dry_run:
                    address.utid = unified_id
                
                self.stats['payment_addresses']['updated'] += 1
                logger.debug(f"PaymentAddress {address.id} → UTID: {unified_id}")
                
            except Exception as e:
                logger.error(f"Failed to migrate payment address {address.id}: {e}")
                self.stats['payment_addresses']['skipped'] += 1
    
    def _migrate_saved_addresses(self, session: Session) -> None:
        """Migrate saved address records to unified IDs"""
        logger.info("Migrating saved addresses...")
        
        # Get all saved addresses without UTID
        addresses = session.query(SavedAddress).filter(
            (SavedAddress.utid is None) | (SavedAddress.utid == '')
        ).all()
        
        self.stats['saved_addresses']['total'] = len(addresses)
        
        for address in addresses:
            try:
                # Generate unified ID for saved address
                unified_id = UniversalIDGenerator.generate_id('external_ref')
                
                # Check for conflicts
                existing = session.query(SavedAddress).filter(SavedAddress.utid == unified_id).first()
                if existing:
                    unified_id = UniversalIDGenerator.generate_id('external_ref')
                
                if not self.dry_run:
                    address.utid = unified_id
                
                self.stats['saved_addresses']['updated'] += 1
                logger.debug(f"SavedAddress {address.id} → UTID: {unified_id}")
                
            except Exception as e:
                logger.error(f"Failed to migrate saved address {address.id}: {e}")
                self.stats['saved_addresses']['skipped'] += 1
    
    def _generate_date_consistent_id(self, entity_type: str, created_at: datetime) -> str:
        """
        Generate ID using creation date for consistency
        
        Args:
            entity_type: Type of entity
            created_at: Original creation timestamp
            
        Returns:
            Unified ID with consistent date
        """
        try:
            prefix = UniversalIDGenerator.ENTITY_PREFIXES[entity_type]
            date_part = created_at.strftime("%m%d%y")
            
            # Generate random suffix for uniqueness
            import secrets
            clean_alphabet = UniversalIDGenerator.CLEAN_ALPHABET
            random_suffix = ''.join(secrets.choice(clean_alphabet) for _ in range(4))
            
            return f"{prefix}{date_part}{random_suffix}"
            
        except Exception:
            # Fallback to standard generation
            return UniversalIDGenerator.generate_id(entity_type)
    
    def print_statistics(self) -> None:
        """Print migration statistics"""
        print("\n" + "="*60)
        print("UNIFIED ID MIGRATION STATISTICS")
        print("="*60)
        
        total_records = 0
        total_updated = 0
        total_skipped = 0
        
        for entity_type, stats in self.stats.items():
            print(f"\n{entity_type.upper()}:")
            print(f"  Total: {stats['total']}")
            print(f"  Updated: {stats['updated']}")
            print(f"  Skipped: {stats['skipped']}")
            
            total_records += stats['total']
            total_updated += stats['updated']
            total_skipped += stats['skipped']
        
        print("\nOVERALL SUMMARY:")
        print(f"  Total Records: {total_records}")
        print(f"  Successfully Updated: {total_updated}")
        print(f"  Skipped (Errors): {total_skipped}")
        print(f"  Success Rate: {(total_updated/total_records*100):.1f}%" if total_records > 0 else "  Success Rate: N/A")
        
        if self.dry_run:
            print("\n⚠️  DRY RUN MODE - No changes were committed to database")
        else:
            print("\n✅ MIGRATION COMPLETED - All changes committed to database")
        
        print("="*60)


def run_migration(dry_run: bool = True) -> Dict:
    """
    Run the unified ID migration
    
    Args:
        dry_run: If True, preview changes without committing
        
    Returns:
        Migration statistics
    """
    migration = UnifiedIDMigration(dry_run=dry_run)
    stats = migration.migrate_all()
    migration.print_statistics()
    return stats


def main():
    """Command line interface for migration"""
    parser = argparse.ArgumentParser(description='Unified ID Migration Utility')
    parser.add_argument(
        '--execute', 
        action='store_true', 
        help='Execute migration (default is dry-run)'
    )
    parser.add_argument(
        '--dry-run', 
        action='store_true', 
        help='Preview changes without committing (default)'
    )
    parser.add_argument(
        '--verbose', 
        action='store_true', 
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Determine if dry run
    dry_run = not args.execute
    if args.dry_run:
        dry_run = True
    
    if dry_run:
        print("🔍 Running in DRY RUN mode - previewing changes only")
    else:
        print("⚡ Running in EXECUTE mode - changes will be committed")
        confirmation = input("Are you sure you want to proceed? (yes/no): ")
        if confirmation.lower() != 'yes':
            print("Migration cancelled")
            return
    
    try:
        stats = run_migration(dry_run=dry_run)
        print("\nMigration completed successfully")
        return stats
    except Exception as e:
        print(f"\nMigration failed: {e}")
        logger.exception("Migration error details")
        return None


if __name__ == "__main__":
    main()