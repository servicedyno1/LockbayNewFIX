"""
NGN Cashout Performance Optimization Utilities

Provides high-performance caching and optimization specifically for NGN cashout flows
to reduce response times and improve user experience.

Key optimizations:
- Cached saved bank account loading
- Optimized bank verification data
- Fast context processing for confirmations
- Efficient rate data management
"""

import logging
import asyncio
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from decimal import Decimal

from database import SessionLocal
from models import User, SavedBankAccount
from utils.production_cache import get_cached, set_cached, delete_cached

logger = logging.getLogger(__name__)


class NGNCashoutPerformanceOptimizer:
    """
    High-performance utilities for NGN cashout flow optimization
    
    Provides caching and optimization specifically designed to reduce response times
    in NGN cashout interactions while maintaining data accuracy and security.
    """
    
    # Cache TTL settings optimized for NGN cashout flow
    SAVED_BANKS_CACHE_TTL = 300  # 5 minutes - balance between performance and freshness
    BANK_VERIFICATION_CACHE_TTL = 1800  # 30 minutes - longer cache for verified accounts
    RATE_DISPLAY_CACHE_TTL = 60  # 1 minute - short cache for rate display data
    USER_CONTEXT_CACHE_TTL = 180  # 3 minutes - context data caching
    
    @classmethod
    def get_cached_saved_banks(cls, user_id: int) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached saved bank accounts for faster loading
        
        Returns:
            List of saved bank account dictionaries or None if not cached
        """
        cache_key = f"ngn_saved_banks_user_{user_id}"
        cached_banks = get_cached(cache_key)
        
        if cached_banks:
            logger.debug(f"💨 Cache HIT for saved banks - User: {user_id}")
            return cached_banks
        
        return None
    
    @classmethod
    def cache_saved_banks(cls, user_id: int, saved_banks: List[Dict[str, Any]]) -> None:
        """
        Cache saved bank accounts for faster subsequent loads
        
        Args:
            user_id: User ID
            saved_banks: List of saved bank account dictionaries
        """
        cache_key = f"ngn_saved_banks_user_{user_id}"
        set_cached(cache_key, saved_banks, ttl=cls.SAVED_BANKS_CACHE_TTL)
        logger.debug(f"💾 Cached {len(saved_banks)} saved banks for user {user_id}")
    
    @classmethod
    def invalidate_saved_banks_cache(cls, user_id: int) -> None:
        """
        Invalidate saved banks cache when accounts are added/removed/modified
        
        Args:
            user_id: User ID
        """
        cache_key = f"ngn_saved_banks_user_{user_id}"
        delete_cached(cache_key)
        logger.debug(f"🗑️ Invalidated saved banks cache for user {user_id}")
    
    @classmethod
    async def load_saved_banks_optimized(cls, telegram_user_id: int) -> Optional[List[Dict[str, Any]]]:
        """
        Load saved bank accounts with optimal performance using caching
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            List of saved bank account dictionaries or None if error
        """
        try:
            # Try cache first for massive performance boost
            cached_banks = cls.get_cached_saved_banks(telegram_user_id)
            if cached_banks is not None:
                return cached_banks
            
            # Cache miss - load from database with optimized query
            session = SessionLocal()
            try:
                # Single optimized query to get user and banks
                user = session.query(User).filter(
                    User.telegram_id == str(telegram_user_id)
                ).first()
                
                if not user:
                    logger.warning(f"User not found for telegram_id: {telegram_user_id}")
                    return None
                
                # Load saved bank accounts with optimized query
                saved_accounts = session.query(SavedBankAccount).filter(
                    SavedBankAccount.user_id == user.id
                ).order_by(SavedBankAccount.last_used.desc()).all()
                
                # Convert to optimized dictionary format for fast access
                saved_banks = []
                for account in saved_accounts:
                    bank_data = {
                        'id': account.id,
                        'bank_name': account.bank_name,
                        'account_number': account.account_number,
                        'account_name': account.account_name,
                        'bank_code': account.bank_code,
                        'is_verified': getattr(account, 'is_verified', False),
                        'last_used': account.last_used.isoformat() if account.last_used else None,
                        'created_at': account.created_at.isoformat() if hasattr(account, 'created_at') else None
                    }
                    saved_banks.append(bank_data)
                
                # Cache the results for faster subsequent access
                cls.cache_saved_banks(telegram_user_id, saved_banks)
                
                logger.info(f"✅ Loaded {len(saved_banks)} saved banks for user {telegram_user_id} (cached)")
                return saved_banks
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Error loading saved banks for user {telegram_user_id}: {e}")
            return None
    
    @classmethod
    def _get_bank_verification_cache_key(cls, user_id: int, bank_code: str, account_number: str) -> str:
        """
        Generate secure cache key for bank verification to prevent collisions
        
        Args:
            user_id: User ID for secure binding
            bank_code: Bank code
            account_number: Full account number
            
        Returns:
            Secure cache key that prevents collisions
        """
        # Create secure hash including user_id to prevent cross-user collisions
        data = f"{user_id}:{bank_code}:{account_number}"
        hash_obj = hashlib.sha256(data.encode('utf-8'))
        return f"bank_verification_{hash_obj.hexdigest()[:16]}"
    
    @classmethod
    def get_cached_bank_verification(cls, user_id: int, bank_code: str, account_number: str) -> Optional[Dict[str, Any]]:
        """
        Get cached bank verification data to avoid duplicate API calls
        
        Args:
            user_id: User ID for secure cache binding
            bank_code: Bank code
            account_number: Account number
            
        Returns:
            Cached verification data or None if not cached
        """
        # Use secure cache key that includes user binding
        cache_key = cls._get_bank_verification_cache_key(user_id, bank_code, account_number)
        cached_verification = get_cached(cache_key)
        
        if cached_verification:
            logger.debug(f"💨 Cache HIT for bank verification - User: {user_id}, Bank: {bank_code}")
            return cached_verification
        
        return None
    
    @classmethod
    def cache_bank_verification(cls, user_id: int, bank_code: str, account_number: str, verification_data: Dict[str, Any]) -> None:
        """
        Cache bank verification data for faster subsequent verifications
        
        Args:
            user_id: User ID for secure cache binding
            bank_code: Bank code
            account_number: Account number
            verification_data: Verification result data
        """
        # Use secure cache key that prevents collisions
        cache_key = cls._get_bank_verification_cache_key(user_id, bank_code, account_number)
        set_cached(cache_key, verification_data, ttl=cls.BANK_VERIFICATION_CACHE_TTL)
        logger.debug(f"💾 Cached bank verification for User: {user_id}, Bank: {bank_code} ••••{account_number[-4:]}")
    
    @classmethod
    def get_cached_rate_display_data(cls, usd_amount: Decimal) -> Optional[Dict[str, Any]]:
        """
        Get cached rate display data for confirmation screens
        
        Args:
            usd_amount: USD amount for rate calculation
            
        Returns:
            Cached rate display data or None if not cached
        """
        # Round amount for cache key to group similar amounts
        amount_rounded = str(usd_amount.quantize(Decimal('0.01')))
        cache_key = f"ngn_rate_display_{amount_rounded}"
        cached_data = get_cached(cache_key)
        
        if cached_data:
            # Check if cached data is still fresh (within rate display cache TTL)
            cached_time = datetime.fromisoformat(cached_data.get('cached_at', '2000-01-01'))
            if datetime.utcnow() - cached_time < timedelta(seconds=cls.RATE_DISPLAY_CACHE_TTL):
                logger.debug(f"💨 Cache HIT for rate display - Amount: ${amount_rounded}")
                return cached_data
        
        return None
    
    @classmethod
    def cache_rate_display_data(cls, usd_amount: Decimal, ngn_amount: Decimal, rate: float, rate_display: str) -> None:
        """
        Cache rate display data for faster confirmation screen loading
        
        Args:
            usd_amount: USD amount
            ngn_amount: NGN amount
            rate: Exchange rate
            rate_display: Formatted rate display string
        """
        amount_rounded = str(usd_amount.quantize(Decimal('0.01')))
        cache_key = f"ngn_rate_display_{amount_rounded}"
        
        cache_data = {
            'usd_amount': str(usd_amount),
            'ngn_amount': str(ngn_amount),
            'rate': rate,
            'rate_display': rate_display,
            'cached_at': datetime.utcnow().isoformat()
        }
        
        set_cached(cache_key, cache_data, ttl=cls.RATE_DISPLAY_CACHE_TTL)
        logger.debug(f"💾 Cached rate display data for ${amount_rounded}")
    
    @classmethod
    def optimize_user_context(cls, context_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Optimize user context data by removing unnecessary fields and compressing data
        
        Args:
            context_data: Raw context data
            
        Returns:
            Optimized context data
        """
        try:
            # Create lightweight context with only essential fields
            optimized_context = {}
            
            # Essential cashout data
            if 'cashout_data' in context_data:
                cashout_data = context_data['cashout_data']
                optimized_context['cashout_data'] = {
                    'amount': cashout_data.get('amount'),
                    'method': cashout_data.get('method'),
                    'verified_account': cashout_data.get('verified_account')
                }
            
            # Essential wallet state
            if 'wallet_state' in context_data:
                optimized_context['wallet_state'] = context_data['wallet_state']
            
            # Essential rate lock data (if present)
            if 'rate_lock' in context_data:
                rate_lock = context_data['rate_lock']
                optimized_context['rate_lock'] = {
                    'token': rate_lock.get('token'),
                    'locked_rate': rate_lock.get('locked_rate'),
                    'locked_ngn_amount': rate_lock.get('locked_ngn_amount'),
                    'expires_at': rate_lock.get('expires_at')
                }
            
            return optimized_context
            
        except Exception as e:
            logger.error(f"Error optimizing user context: {e}")
            return context_data  # Return original if optimization fails
    
    @classmethod
    def get_cached_user_context(cls, user_id: int, context_type: str) -> Optional[Dict[str, Any]]:
        """
        Get cached user context for faster state restoration
        
        Args:
            user_id: User ID
            context_type: Type of context (e.g., 'ngn_cashout', 'bank_selection')
            
        Returns:
            Cached context or None if not cached
        """
        cache_key = f"user_context_{user_id}_{context_type}"
        cached_context = get_cached(cache_key)
        
        if cached_context:
            logger.debug(f"💨 Cache HIT for user context - User: {user_id}, Type: {context_type}")
            return cached_context
        
        return None
    
    @classmethod
    def cache_user_context(cls, user_id: int, context_type: str, context_data: Dict[str, Any]) -> None:
        """
        Cache user context for faster state restoration
        
        Args:
            user_id: User ID
            context_type: Type of context
            context_data: Context data to cache
        """
        cache_key = f"user_context_{user_id}_{context_type}"
        optimized_context = cls.optimize_user_context(context_data)
        set_cached(cache_key, optimized_context, ttl=cls.USER_CONTEXT_CACHE_TTL)
        logger.debug(f"💾 Cached user context - User: {user_id}, Type: {context_type}")
    
    @classmethod
    async def preload_user_data(cls, telegram_user_id: int) -> Dict[str, Any]:
        """
        Preload frequently used user data for faster cashout flow
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            Dictionary with preloaded user data
        """
        try:
            preload_tasks = [
                cls.load_saved_banks_optimized(telegram_user_id)
            ]
            
            # Execute all preload tasks concurrently
            results = await asyncio.gather(*preload_tasks, return_exceptions=True)
            
            saved_banks = results[0] if not isinstance(results[0], Exception) else []
            
            preloaded_data = {
                'saved_banks': saved_banks or [],
                'has_saved_banks': bool(saved_banks),
                'preload_timestamp': datetime.utcnow().isoformat()
            }
            
            # Cache preloaded data for subsequent use
            cache_key = f"preloaded_user_data_{telegram_user_id}"
            set_cached(cache_key, preloaded_data, ttl=120)  # 2 minute cache
            
            logger.info(f"🚀 Preloaded user data for {telegram_user_id}: {len(saved_banks or [])} banks")
            return preloaded_data
            
        except Exception as e:
            logger.error(f"❌ Error preloading user data for {telegram_user_id}: {e}")
            return {}


class NGNConfirmationScreenOptimizer:
    """
    Specialized optimizer for NGN confirmation screen performance
    
    Focuses on reducing the time between user bank selection and confirmation display
    """
    
    @classmethod
    async def get_optimized_confirmation_data(cls, usd_amount: Decimal, bank_account: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get all confirmation screen data with optimal performance
        
        Args:
            usd_amount: USD amount for cashout
            bank_account: Selected bank account data
            
        Returns:
            Dictionary with all confirmation screen data
        """
        try:
            # Check if rate display data is cached
            cached_rate_data = NGNCashoutPerformanceOptimizer.get_cached_rate_display_data(usd_amount)
            
            if cached_rate_data:
                # Use cached rate data for faster display
                logger.debug("💨 Using cached rate data for confirmation screen")
                return {
                    'usd_amount': Decimal(cached_rate_data['usd_amount']),
                    'ngn_amount': Decimal(cached_rate_data['ngn_amount']),
                    'exchange_rate': cached_rate_data['rate'],
                    'rate_display': cached_rate_data['rate_display'],
                    'bank_account': bank_account,
                    'data_source': 'cached'
                }
            
            # Cache miss - fetch fresh data but optimize the process
            from handlers.wallet_direct import get_dynamic_ngn_amount
            from services.fastforex_service import FastForexService
            
            # Execute rate fetching operations concurrently for speed
            fastforex = FastForexService()
            
            # Run both operations in parallel
            ngn_amount_task = get_dynamic_ngn_amount(usd_amount)
            rate_task = fastforex.get_usd_to_ngn_rate_with_wallet_markup()
            
            # Wait for both to complete
            ngn_amount, exchange_rate = await asyncio.gather(ngn_amount_task, rate_task)
            
            # Format rate display
            rate_display = f"₦{exchange_rate:,.2f}"
            
            # Cache this data for subsequent use
            NGNCashoutPerformanceOptimizer.cache_rate_display_data(
                usd_amount, ngn_amount, exchange_rate, rate_display
            )
            
            return {
                'usd_amount': usd_amount,
                'ngn_amount': ngn_amount,
                'exchange_rate': exchange_rate,
                'rate_display': rate_display,
                'bank_account': bank_account,
                'data_source': 'fresh'
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting optimized confirmation data: {e}")
            raise e


# Global performance optimizer instance
ngn_performance = NGNCashoutPerformanceOptimizer()
ngn_confirmation_optimizer = NGNConfirmationScreenOptimizer()