"""
High-performance async user utilities for OnboardingRouter
Replaces sync run_io_task patterns with pure async implementations
"""

import logging
from typing import Dict, Any, Optional, Tuple
from sqlalchemy import select, func
from database import async_managed_session
from models import User, Wallet, Escrow, EscrowStatus

logger = logging.getLogger(__name__)

async def get_main_menu_data_async(user_id: int) -> Dict[str, Any]:
    """High-performance async main menu data retrieval"""
    async with async_managed_session() as session:
        try:
            # Get wallet balance
            wallet_result = await session.execute(
                select(Wallet).where(Wallet.user_id == user_id, Wallet.currency == "USD")
            )
            wallet = wallet_result.scalar_one_or_none()
            balance = wallet.available_balance if wallet else 0.0
            
            # Get escrow stats (only count COMPLETED trades for accuracy)
            total_trades_result = await session.execute(
                select(func.count(Escrow.id)).where(
                    Escrow.buyer_id == user_id,
                    Escrow.status == EscrowStatus.COMPLETED.value
                )
            )
            total_trades = total_trades_result.scalar() or 0
            
            active_escrows_result = await session.execute(
                select(func.count(Escrow.id)).where(
                    Escrow.buyer_id == user_id,
                    Escrow.status.in_([
                        EscrowStatus.CREATED.value, 
                        EscrowStatus.PAYMENT_PENDING.value, 
                        EscrowStatus.PAYMENT_CONFIRMED.value, 
                        EscrowStatus.PARTIAL_PAYMENT.value, 
                        EscrowStatus.ACTIVE.value
                    ])
                )
            )
            active_escrows = active_escrows_result.scalar() or 0
            
            return {
                "balance": balance,
                "total_trades": total_trades,
                "active_escrows": active_escrows
            }
        except Exception as e:
            logger.error(f"Error fetching user stats for {user_id}: {e}")
            return {"balance": 0.0, "total_trades": 0, "active_escrows": 0}

async def get_or_create_user_async(telegram_user) -> Tuple[Optional[dict], bool]:
    """High-performance async user creation with single session management"""
    if not telegram_user:
        logger.warning("get_or_create_user_async: Invalid telegram_user")
        return None, False
    
    user_id = telegram_user.id
    
    def to_dict_safe(user):
        """Convert User ORM instance to dict - access attributes while session is active"""
        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "is_verified": user.is_verified
        }
    
    async with async_managed_session() as session:
        try:
            # CRITICAL: Check if user is on the blocklist BEFORE allowing onboarding
            from sqlalchemy import text
            blocklist_result = await session.execute(
                text("SELECT id FROM blocked_telegram_ids WHERE telegram_id = :telegram_id"),
                {"telegram_id": user_id}
            )
            if blocklist_result.scalar():
                logger.critical(f"🚫🚫🚫 BLOCKLIST_VIOLATION: User {user_id} ({telegram_user.username}) attempted to onboard but is PERMANENTLY BLOCKED")
                return None, False
            
            # Check if user exists
            existing_user_result = await session.execute(
                select(User).where(User.id == user_id)
            )
            existing_user = existing_user_result.scalar_one_or_none()
            
            if existing_user:
                # Access attributes while session is active
                user_dict = to_dict_safe(existing_user)
                return user_dict, False
            
            # Create new user
            from datetime import datetime
            now = datetime.utcnow()
            new_user = User(
                id=telegram_user.id,
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name,
                email=f"temp_{telegram_user.id}@onboarding.temp",
                is_verified=False,
                created_at=now,
                updated_at=now
            )
            session.add(new_user)
            await session.flush()
            
            # Generate referral code for new user
            try:
                from utils.referral import ReferralSystem
                # After flush, the ID is available as an actual int value
                user_id_value: int = telegram_user.id  # Use telegram_user.id since new_user.id == telegram_user.id
                referral_code = ReferralSystem.generate_referral_code(user_id_value)
                # Direct attribute assignment works with SQLAlchemy ORM
                new_user.referral_code = referral_code  # type: ignore[assignment]
                logger.info(f"Generated referral code {referral_code} for new user {user_id_value}")
            except Exception as e:
                logger.error(f"Error generating referral code for user {telegram_user.id}: {e}")
            
            # Generate profile slug for new user
            try:
                
                # Convert async session to sync for profile_slug_generator
                # We'll use a direct SQL approach to avoid session type mismatch
                import re
                import random
                import string
                
                def slugify_text(text: str) -> str:
                    text = text.lower().strip()
                    text = re.sub(r'[^\w\s-]', '', text)
                    text = re.sub(r'[-\s]+', '_', text)
                    return text[:30]
                
                def gen_suffix(length: int = 6) -> str:
                    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
                
                # Generate base slug
                if telegram_user.username:
                    base_slug = slugify_text(telegram_user.username.lstrip('@'))
                elif telegram_user.first_name:
                    base_slug = slugify_text(telegram_user.first_name)
                else:
                    base_slug = f"user_{telegram_user.id}"
                
                # Check uniqueness and add suffix if needed
                candidate = base_slug
                for attempt in range(10):
                    result = await session.execute(
                        select(User).where(User.profile_slug == candidate)
                    )
                    if not result.scalar_one_or_none():
                        new_user.profile_slug = candidate  # type: ignore[assignment]
                        logger.info(f"Generated profile slug '{candidate}' for new user {telegram_user.id}")
                        break
                    candidate = f"{base_slug}_{gen_suffix()}"
                
            except Exception as e:
                logger.error(f"Error generating profile slug for user {telegram_user.id}: {e}")
            
            # Create USD wallet for new user
            try:
                from decimal import Decimal
                wallet = Wallet(
                    user_id=new_user.id,
                    currency="USD",
                    available_balance=Decimal("0"),
                    frozen_balance=Decimal("0")
                )
                session.add(wallet)
                logger.info(f"Created USD wallet for new user {new_user.id}")
            except Exception as e:
                logger.error(f"Error creating wallet for user {telegram_user.id}: {e}")
            
            # Access all attributes BEFORE commit (while session is active)
            user_dict = to_dict_safe(new_user)
            
            logger.info(f"Created new user {telegram_user.id} async with referral code and wallet")
            await session.commit()
            
            return user_dict, True
            
        except Exception as e:
            from sqlalchemy.exc import IntegrityError
            
            # Handle constraint violations
            if isinstance(e, IntegrityError) or "UNIQUE constraint" in str(e):
                logger.info(f"User {user_id} already exists (constraint), fetching existing")
                await session.rollback()
                
                # Re-fetch the existing user
                existing_user = await session.get(User, user_id)
                if existing_user:
                    logger.info(f"Retrieved existing user {user_id} after constraint violation")
                    user_dict = to_dict_safe(existing_user)
                    return user_dict, False
                else:
                    logger.error(f"Failed to find user {user_id} after constraint violation")
                    return None, False
            else:
                logger.error(f"Unexpected error creating user {user_id}: {e}")
                raise