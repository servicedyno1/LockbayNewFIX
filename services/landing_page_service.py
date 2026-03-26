"""
Landing Page Service
Fetches referrer data and platform information for universal landing page
"""

import logging
import os
from typing import Dict, Optional, Any
from decimal import Decimal
from sqlalchemy import func

from database import SessionLocal
from models import User
from utils.decimal_precision import MonetaryDecimal
from config import Config

logger = logging.getLogger(__name__)


class LandingPageService:
    """Service for landing page data aggregation"""
    
    # Platform features for non-referred users
    PLATFORM_FEATURES = [
        {
            'icon': '🔒',
            'title': 'Secure Escrow',
            'description': 'Your funds are protected by our secure escrow system until both parties confirm'
        },
        {
            'icon': '💱',
            'title': 'Multi-Currency Support',
            'description': 'Trade in USD, NGN, BTC, ETH, USDT, and more with instant conversions'
        },
        {
            'icon': '⚡',
            'title': 'Instant Settlements',
            'description': 'Fast payment processing and immediate fund releases upon confirmation'
        },
        {
            'icon': '🛡️',
            'title': 'Dispute Protection',
            'description': 'Fair resolution system with admin support for any transaction disputes'
        },
        {
            'icon': '📊',
            'title': 'Trade Analytics',
            'description': 'Track your trading history, volume, and reputation scores'
        },
        {
            'icon': '⭐',
            'title': 'Reputation System',
            'description': 'Build trust with verified ratings and reviews from real traders'
        }
    ]
    
    # Trust indicators
    TRUST_INDICATORS = [
        {'icon': '✅', 'text': 'Bank-Grade Security'},
        {'icon': '🔐', 'text': 'Encrypted Transactions'},
        {'icon': '⚡', 'text': '24/7 Support'},
        {'icon': '🌍', 'text': 'Global Trading Network'}
    ]
    
    @classmethod
    def get_bonus_amount(cls) -> Decimal:
        """Get current welcome bonus amount from environment"""
        try:
            bonus_str = os.getenv("REFEREE_REWARD_USD", "5.0")
            return MonetaryDecimal.to_decimal(bonus_str, "referee_bonus")
        except Exception as e:
            logger.error(f"Error parsing REFEREE_REWARD_USD: {e}")
            return Decimal("5.0")
    
    @classmethod
    def get_referrer_data(cls, referral_code: str) -> Optional[Dict[str, Any]]:
        """
        Fetch referrer user data by referral code
        
        Args:
            referral_code: The referral code from URL parameter
            
        Returns:
            Dictionary with referrer data or None if not found
        """
        if not referral_code:
            return None
        
        session = SessionLocal()
        try:
            # Find referrer by code (case-insensitive)
            result = session.query(User).filter(
                func.upper(User.referral_code) == referral_code.upper()
            ).first()
            
            if not result:
                logger.warning(f"Referral code not found: {referral_code}")
                return None
            
            # Get referrer's stats
            from services.enhanced_reputation_service import EnhancedReputationService
            from models import Escrow
            
            reputation = EnhancedReputationService.get_comprehensive_reputation(result.id, session)
            
            # Get total completed trades
            completed_buyer = session.query(Escrow).filter(
                Escrow.buyer_id == result.id,
                Escrow.status == 'completed'
            ).count()
            completed_seller = session.query(Escrow).filter(
                Escrow.seller_id == result.id,
                Escrow.status == 'completed'
            ).count()
            total_completed = completed_buyer + completed_seller
            
            referrer_data = {
                'user_id': result.id,
                'display_name': result.first_name or result.username or f"User{result.id}",
                'username': result.username,
                'trust_level': reputation.trust_level.title() if reputation else 'New',
                'total_trades': total_completed,
                'rating': f"{reputation.overall_rating:.1f}" if reputation else "New",
                'trust_icon': cls._get_trust_icon(reputation.trust_level if reputation else 'new')
            }
            
            logger.info(f"✅ Fetched referrer data for code: {referral_code} (user: {result.id})")
            return referrer_data
            
        except Exception as e:
            logger.error(f"❌ Error fetching referrer data: {e}", exc_info=True)
            return None
        finally:
            session.close()
    
    @classmethod
    def _get_trust_icon(cls, trust_level: str) -> str:
        """Get emoji icon for trust level"""
        icons = {
            'new': '🌱',
            'bronze': '🥉',
            'silver': '🥈',
            'gold': '🥇',
            'platinum': '💎',
            'diamond': '👑'
        }
        return icons.get(trust_level.lower(), '🌱')
    
    @classmethod
    def get_landing_page_data(cls, referral_code: Optional[str] = None) -> Dict[str, Any]:
        """
        Get complete landing page data for template rendering
        
        Args:
            referral_code: Optional referral code from URL
            
        Returns:
            Dictionary with all landing page data
        """
        # Base data for all users
        data = {
            'platform_name': 'LockBay',
            'platform_tagline': 'Secure Peer-to-Peer Trading with Escrow Protection',
            'features': cls.PLATFORM_FEATURES,
            'trust_indicators': cls.TRUST_INDICATORS,
            'bot_link': f"https://t.me/{Config.BOT_USERNAME.replace('@', '')}",
            'has_referral': False,
            'referrer': None,
            'bonus_amount': str(cls.get_bonus_amount()),
            'bonus_amount_display': f"${cls.get_bonus_amount()}"
        }
        
        # Add referral-specific data if referral code provided
        if referral_code:
            referrer_data = cls.get_referrer_data(referral_code)
            if referrer_data:
                data['has_referral'] = True
                data['referrer'] = referrer_data
                data['bot_link'] = f"{data['bot_link']}?start=ref_{referral_code}"
        
        return data
