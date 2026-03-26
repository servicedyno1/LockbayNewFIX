"""
Centralized wallet management utilities for preventing duplicate wallet issues.
This module ensures consistent wallet handling with proper USD currency filtering.
"""

import logging
from decimal import Decimal
from typing import Optional, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from database import managed_session
from models import Wallet, Transaction, TransactionType

logger = logging.getLogger(__name__)

def get_or_create_wallet(user_id: int, session=None, currency: str = "USD") -> Tuple[Wallet, bool]:
    """
    Get or create a wallet for a user with proper currency handling.
    This is the SINGLE source of truth for wallet creation to prevent duplicates.
    
    Args:
        user_id: User database ID
        session: SQLAlchemy session (optional, will create if not provided)
        currency: Wallet currency (default: "USD")
    
    Returns:
        Tuple of (wallet, created_flag) where created_flag is True if wallet was created
    
    CRITICAL: Always use this function instead of direct Wallet() creation
    """
    own_session = session is None
    if own_session:
        # Use async session for async function
        with managed_session() as session:
            return get_or_create_wallet(user_id, session, currency)
    
    # Continue with provided session
    try:
        # CRITICAL: Always include currency in the filter to prevent duplicate issues
        stmt = select(Wallet).where(
            Wallet.user_id == user_id,
            Wallet.currency == currency
        )
        result = session.execute(stmt)
        existing_wallet = result.scalar_one_or_none()
        
        if existing_wallet:
            logger.debug(f"Found existing {currency} wallet for user {user_id}: balance=${existing_wallet.available_balance}")
            return existing_wallet, False
        
        # Create new wallet with explicit currency
        logger.info(f"Creating new {currency} wallet for user {user_id}")
        
        try:
            new_wallet = Wallet(
                user_id=user_id,
                currency=currency,
                available_balance=Decimal("0.00"),
                frozen_balance=Decimal("0.00")
            )
            session.add(new_wallet)
            
            if own_session:
                session.commit()
            else:
                session.flush()
                
            logger.info(f"✅ Successfully created {currency} wallet for user {user_id}")
            
            # Create initial wallet creation transaction for audit trail
            from utils.universal_id_generator import UniversalIDGenerator
            transaction = Transaction(
                transaction_id=UniversalIDGenerator.generate_transaction_id(),
                user_id=user_id,
                transaction_type=TransactionType.WALLET_DEPOSIT.value,
                amount=Decimal("0.00"),
                currency=currency,
                status="completed",
                description=f"Wallet created - {currency}",
                utid=UniversalIDGenerator.generate_id("wallet_deposit")
            )
            session.add(transaction)
            
            if own_session:
                session.commit()
            else:
                session.flush()
                
            return new_wallet, True
            
        except IntegrityError as e:
            # Race condition - another process created the wallet
            logger.warning(f"Race condition detected creating wallet: {e}")
            session.rollback()
            
            # Try to get the wallet again
            stmt = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            )
            result = session.execute(stmt)
            existing_wallet = result.scalar_one_or_none()
            
            if existing_wallet:
                logger.info(f"Retrieved {currency} wallet after race condition for user {user_id}")
                return existing_wallet, False
            else:
                logger.error(f"Failed to retrieve wallet after race condition for user {user_id}")
                raise RuntimeError("Failed to create or retrieve wallet")
                
    except Exception as e:
        logger.error(f"Error in get_or_create_wallet for user {user_id}: {e}")
        if own_session:
            session.rollback()
        raise
    finally:
        # Session cleanup handled by managed_session context manager for own_session
        pass


def get_user_wallet(user_id: int, session=None, currency: str = "USD", create_if_missing: bool = True) -> Optional[Wallet]:
    """
    Get a user's wallet with proper currency filtering.
    
    Args:
        user_id: User database ID
        session: SQLAlchemy session (optional)
        currency: Wallet currency (default: "USD")
        create_if_missing: Whether to create wallet if it doesn't exist (default: True)
    
    Returns:
        Wallet object or None if not found and create_if_missing is False
    """
    own_session = session is None
    if own_session:
        # Use async session for async function
        with managed_session() as session:
            return get_user_wallet(user_id, session, currency, create_if_missing)
    
    # Continue with provided session
    try:
        # CRITICAL: Always include currency in the filter
        stmt = select(Wallet).where(
            Wallet.user_id == user_id,
            Wallet.currency == currency
        )
        result = session.execute(stmt)
        wallet = result.scalar_one_or_none()
        
        if wallet:
            return wallet
        elif create_if_missing:
            wallet, _ = get_or_create_wallet(user_id, session, currency)
            return wallet
        else:
            logger.warning(f"No {currency} wallet found for user {user_id}")
            return None
            
    except Exception as e:
        logger.error(f"Error getting wallet for user {user_id}: {e}")
        return None
    finally:
        # Session cleanup handled by managed_session context manager
        pass


def ensure_single_wallet_per_currency(user_id: int, session=None) -> bool:
    """
    Validate that a user has at most one wallet per currency.
    This is a safety check to detect any duplicate wallet issues.
    
    Args:
        user_id: User database ID
        session: SQLAlchemy session (optional)
    
    Returns:
        True if validation passes, False if duplicates found
    """
    own_session = session is None
    if own_session:
        # Use async session for async function
        with managed_session() as session:
            return ensure_single_wallet_per_currency(user_id, session)
    
    # Continue with provided session
    try:
        # Group wallets by currency and check for duplicates
        from sqlalchemy import func
        
        stmt = select(
            Wallet.currency,
            func.count(Wallet.id).label('count')
        ).where(
            Wallet.user_id == user_id
        ).group_by(
            Wallet.currency
        ).having(
            func.count(Wallet.id) > 1
        )
        result = session.execute(stmt)
        duplicate_check = result.all()
        
        if duplicate_check:
            for currency, count in duplicate_check:
                logger.error(f"❌ CRITICAL: User {user_id} has {count} {currency} wallets (should be 1)")
            return False
            
        return True
        
    except Exception as e:
        logger.error(f"Error checking wallet duplicates for user {user_id}: {e}")
        return False
    finally:
        # Session cleanup handled by managed_session context manager
        pass


def migrate_duplicate_wallets(user_id: int, session=None) -> bool:
    """
    Merge duplicate wallets for a user if they exist.
    This consolidates balances and deletes duplicates.
    
    Args:
        user_id: User database ID
        session: SQLAlchemy session (optional)
    
    Returns:
        True if migration successful or no duplicates found
    """
    own_session = session is None
    if own_session:
        # Use async session for async function
        with managed_session() as session:
            return migrate_duplicate_wallets(user_id, session)
    
    # Continue with provided session
    try:
        # Find all USD wallets for the user
        stmt = select(Wallet).where(
            Wallet.user_id == user_id,
            Wallet.currency == "USD"
        ).order_by(Wallet.created_at)
        result = session.execute(stmt)
        usd_wallets = list(result.scalars())
        
        if len(usd_wallets) <= 1:
            return True  # No duplicates
            
        logger.warning(f"Found {len(usd_wallets)} USD wallets for user {user_id}, merging...")
        
        # Keep the oldest wallet, merge balances from others
        primary_wallet = usd_wallets[0]
        total_balance = Decimal("0.00")
        total_frozen = Decimal("0.00")
        Decimal("0.00")
        
        for wallet in usd_wallets:
            total_balance += Decimal(str(wallet.available_balance))
            total_frozen += Decimal(str(wallet.frozen_balance))
            # Note: locked_balance doesn't exist in Wallet model, using frozen_balance only
        
        # Update primary wallet with combined balances
        primary_wallet.available_balance = total_balance
        primary_wallet.frozen_balance = total_frozen
        # Note: locked_balance field doesn't exist in Wallet model
        
        # Delete duplicate wallets
        for wallet in usd_wallets[1:]:
            logger.info(f"Deleting duplicate wallet {wallet.id} for user {user_id}")
            session.delete(wallet)
        
        if own_session:
            session.commit()
            
        logger.info(f"✅ Successfully merged {len(usd_wallets)} wallets for user {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error migrating duplicate wallets for user {user_id}: {e}")
        if own_session:
            session.rollback()
        return False
    finally:
        # Session cleanup handled by managed_session context manager
        pass


# Export the main functions for easy import
__all__ = [
    'get_or_create_wallet',
    'get_user_wallet',
    'ensure_single_wallet_per_currency',
    'migrate_duplicate_wallets'
]