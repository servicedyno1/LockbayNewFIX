#!/usr/bin/env python3
"""
Regional Referral Service
Integrates regional economic adjustments with referral system configuration
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict
from datetime import datetime

from services.referral_admin_service import ReferralAdminService
from services.regional_economic_service import RegionalEconomicService

logger = logging.getLogger(__name__)


class RegionalReferralService:
    """Service for regionally-aware referral threshold management"""

    def __init__(self):
        self.referral_admin = ReferralAdminService()
        self.regional_service = RegionalEconomicService()
        logger.info("Regional referral service initialized")

    async def get_regional_referral_threshold(self, user_id: int) -> Decimal:
        """Get regionally adapted referral threshold for user"""
        try:
            # Get current referral configuration
            config = self.referral_admin.get_current_config()
            
            # Check if regional adjustments are enabled
            if not config.get("enable_regional_adjustments", True):
                logger.info(f"Regional adjustments disabled - using base threshold for user {user_id}")
                return Decimal(str(config["min_activity_for_reward"]))
            
            # Get base threshold
            base_threshold = Decimal(str(config["min_activity_for_reward"]))
            
            # Get user's regional profile
            region_code = await self.regional_service.detect_user_region(user_id)
            regional_profile = await self.regional_service.get_regional_profile(region_code)
            
            # Apply tier-specific multiplier from configuration
            tier_multipliers = {
                "developing": config.get("developing_tier_multiplier", 0.4),
                "emerging": config.get("emerging_tier_multiplier", 0.6),
                "developed": config.get("developed_tier_multiplier", 1.0)
            }
            
            tier_multiplier = tier_multipliers.get(regional_profile.economic_tier, 1.0)
            
            # Apply referral-specific adjustment (extra reduction for community building)
            referral_multiplier = config.get("regional_adjustment_multiplier", 0.8)
            
            # Calculate final threshold
            final_multiplier = tier_multiplier * referral_multiplier
            adapted_threshold = base_threshold * Decimal(str(final_multiplier))
            
            # Apply safety minimum from configuration
            minimum_floor = Decimal(str(config.get("minimum_threshold_floor_usd", 5.0)))
            adapted_threshold = max(minimum_floor, adapted_threshold)
            
            # Round to 2 decimal places
            adapted_threshold = adapted_threshold.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            logger.info(
                f"Regional referral threshold for user {user_id} ({region_code}, {regional_profile.economic_tier}): "
                f"${base_threshold} → ${adapted_threshold} (reduction: {(1-final_multiplier)*100:.1f}%)"
            )
            
            return adapted_threshold
            
        except Exception as e:
            logger.error(f"Error calculating regional referral threshold for user {user_id}: {e}")
            # Return base threshold as fallback
            config = self.referral_admin.get_current_config()
            return Decimal(str(config["min_activity_for_reward"]))

    async def get_referral_rewards_info(self, user_id: int) -> Dict:
        """Get complete referral rewards information for user including regional adjustments"""
        try:
            config = self.referral_admin.get_current_config()
            
            # Get regional threshold
            regional_threshold = await self.get_regional_referral_threshold(user_id)
            base_threshold = Decimal(str(config["min_activity_for_reward"]))
            
            # Calculate savings
            savings_amount = base_threshold - regional_threshold
            savings_percentage = float((savings_amount / base_threshold) * 100) if base_threshold > 0 else 0
            
            # Get user's region info
            region_code = await self.regional_service.detect_user_region(user_id)
            regional_profile = await self.regional_service.get_regional_profile(region_code)
            
            return {
                "user_id": user_id,
                "regional_info": {
                    "region_code": region_code,
                    "economic_tier": regional_profile.economic_tier,
                    "adjustments_enabled": config.get("enable_regional_adjustments", True)
                },
                "thresholds": {
                    "base_threshold_usd": float(base_threshold),
                    "regional_threshold_usd": float(regional_threshold),
                    "savings_amount_usd": float(savings_amount),
                    "savings_percentage": savings_percentage
                },
                "rewards": {
                    "referrer_reward_usd": config["referrer_reward_usd"],
                    "referee_welcome_bonus_usd": config["referee_reward_usd"],
                    "total_value_generated": config["referrer_reward_usd"] + config["referee_reward_usd"]
                },
                "system_status": {
                    "enabled": config["system_enabled"],
                    "fraud_detection": config["enable_fraud_detection"],
                    "daily_limit": config["max_referrals_per_day"]
                },
                "calculated_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting referral rewards info for user {user_id}: {e}")
            return {"error": str(e), "user_id": user_id}

    async def check_referral_eligibility(self, referrer_id: int, referee_trade_amount: Decimal) -> Dict:
        """Check if a referral qualifies for reward based on regional threshold"""
        try:
            # Get regional threshold for referrer
            required_threshold = await self.get_regional_referral_threshold(referrer_id)
            
            # Check if trade amount meets threshold
            qualifies = referee_trade_amount >= required_threshold
            
            config = self.referral_admin.get_current_config()
            
            return {
                "qualifies": qualifies,
                "required_threshold_usd": float(required_threshold),
                "actual_trade_amount_usd": float(referee_trade_amount),
                "shortfall_usd": float(max(Decimal("0"), required_threshold - referee_trade_amount)),
                "reward_amount_usd": config["referrer_reward_usd"] if qualifies else 0,
                "regional_adjustment_applied": config.get("enable_regional_adjustments", True),
                "checked_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error checking referral eligibility for referrer {referrer_id}: {e}")
            return {
                "error": str(e),
                "referrer_id": referrer_id,
                "qualifies": False
            }

    def get_configuration_summary(self) -> Dict:
        """Get complete configuration summary for admin dashboard"""
        try:
            config = self.referral_admin.get_current_config()
            
            return {
                "system_overview": {
                    "status": "✅ Enabled" if config["system_enabled"] else "❌ Disabled",
                    "regional_adjustments": "✅ Enabled" if config.get("enable_regional_adjustments", True) else "❌ Disabled",
                    "fraud_detection": "✅ Enabled" if config["enable_fraud_detection"] else "❌ Disabled"
                },
                "reward_structure": {
                    "referrer_reward": f"${config['referrer_reward_usd']:.2f}",
                    "referee_bonus": f"${config['referee_reward_usd']:.2f}",
                    "base_threshold": f"${config['min_activity_for_reward']:.2f}"
                },
                "regional_multipliers": {
                    "developing_economies": f"{config.get('developing_tier_multiplier', 0.4)*100:.0f}% of base",
                    "emerging_markets": f"{config.get('emerging_tier_multiplier', 0.6)*100:.0f}% of base", 
                    "developed_markets": f"{config.get('developed_tier_multiplier', 1.0)*100:.0f}% of base",
                    "referral_extra_reduction": f"{(1-config.get('regional_adjustment_multiplier', 0.8))*100:.0f}% additional"
                },
                "safety_limits": {
                    "minimum_threshold_floor": f"${config.get('minimum_threshold_floor_usd', 5.0):.2f}",
                    "max_referrals_per_day": config["max_referrals_per_day"],
                    "min_account_age": f"{config['min_account_age_hours']}h",
                    "fraud_risk_threshold": f"{config.get('fraud_risk_threshold', 70.0):.0f}%"
                },
                "performance_settings": {
                    "batch_processing": "✅ Enabled" if config.get("batch_reward_processing", True) else "❌ Disabled",
                    "processing_delay": f"{config.get('reward_processing_delay_hours', 24)}h",
                    "code_expiry": f"{config.get('inactive_code_expiry_days', 90)} days",
                    "conversion_tracking": "✅ Enabled" if config.get("track_conversion_funnel", True) else "❌ Disabled"
                },
                "last_updated": config.get("updated_at", "Never"),
                "updated_by": f"Admin {config.get('updated_by_admin_id', 'System')}"
            }
            
        except Exception as e:
            logger.error(f"Error getting configuration summary: {e}")
            return {"error": str(e)}