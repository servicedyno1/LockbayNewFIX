#!/usr/bin/env python3
"""
Regional Economic Service
Purchasing power parity and regional economic adjustments for global fairness
"""

import logging
from datetime import datetime
from typing import Dict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from database import async_managed_session
from models import User
from sqlalchemy import select

logger = logging.getLogger(__name__)


@dataclass
class RegionalEconomicProfile:
    """Regional economic data for adjustment calculations"""
    
    region_code: str
    country_code: str
    purchasing_power_index: float  # Relative to USD (1.0 = par with USD)
    cost_of_living_index: float    # Relative to global average
    average_income_usd: Decimal
    currency_code: str
    economic_tier: str  # "developing", "emerging", "developed"
    adjustment_factor: float  # Final multiplier for thresholds


@dataclass
class AdaptedThresholds:
    """Regionally adapted thresholds"""
    
    user_id: int
    region_code: str
    original_threshold_usd: Decimal
    adapted_threshold_usd: Decimal
    adapted_threshold_local: Decimal
    local_currency: str
    adjustment_reason: str
    calculated_at: datetime


class RegionalEconomicService:
    """Service for regional economic adjustments and purchasing power parity"""

    def __init__(self):
        # Regional economic data (simplified - could be from external API)
        self.regional_data = {
            # Africa
            "NG": RegionalEconomicProfile("NG", "NG", 0.3, 0.4, Decimal("2400"), "NGN", "developing", 0.4),
            "KE": RegionalEconomicProfile("KE", "KE", 0.35, 0.45, Decimal("1800"), "KES", "developing", 0.45),
            "ZA": RegionalEconomicProfile("ZA", "ZA", 0.5, 0.55, Decimal("6000"), "ZAR", "emerging", 0.6),
            "GH": RegionalEconomicProfile("GH", "GH", 0.25, 0.35, Decimal("2200"), "GHS", "developing", 0.35),
            
            # Asia
            "IN": RegionalEconomicProfile("IN", "IN", 0.4, 0.35, Decimal("2100"), "INR", "developing", 0.45),
            "PH": RegionalEconomicProfile("PH", "PH", 0.45, 0.4, Decimal("3500"), "PHP", "emerging", 0.5),
            "ID": RegionalEconomicProfile("ID", "ID", 0.4, 0.4, Decimal("4200"), "IDR", "emerging", 0.5),
            "TH": RegionalEconomicProfile("TH", "TH", 0.6, 0.5, Decimal("7000"), "THB", "emerging", 0.65),
            "VN": RegionalEconomicProfile("VN", "VN", 0.35, 0.35, Decimal("2800"), "VND", "developing", 0.4),
            
            # Latin America
            "BR": RegionalEconomicProfile("BR", "BR", 0.55, 0.5, Decimal("8700"), "BRL", "emerging", 0.6),
            "MX": RegionalEconomicProfile("MX", "MX", 0.6, 0.55, Decimal("9600"), "MXN", "emerging", 0.65),
            "AR": RegionalEconomicProfile("AR", "AR", 0.5, 0.6, Decimal("8400"), "ARS", "emerging", 0.55),
            "CO": RegionalEconomicProfile("CO", "CO", 0.45, 0.45, Decimal("6400"), "COP", "emerging", 0.5),
            
            # Eastern Europe
            "UA": RegionalEconomicProfile("UA", "UA", 0.4, 0.45, Decimal("3700"), "UAH", "developing", 0.45),
            "RO": RegionalEconomicProfile("RO", "RO", 0.65, 0.6, Decimal("12000"), "RON", "emerging", 0.7),
            "PL": RegionalEconomicProfile("PL", "PL", 0.75, 0.65, Decimal("15000"), "PLN", "developed", 0.8),
            
            # Developed regions (baseline)
            "US": RegionalEconomicProfile("US", "US", 1.0, 1.0, Decimal("55000"), "USD", "developed", 1.0),
            "CA": RegionalEconomicProfile("CA", "CA", 0.95, 0.95, Decimal("48000"), "CAD", "developed", 0.95),
            "GB": RegionalEconomicProfile("GB", "GB", 0.9, 1.1, Decimal("42000"), "GBP", "developed", 0.9),
            "DE": RegionalEconomicProfile("DE", "DE", 0.85, 1.05, Decimal("47000"), "EUR", "developed", 0.9),
            "AU": RegionalEconomicProfile("AU", "AU", 0.85, 1.15, Decimal("53000"), "AUD", "developed", 0.9),
        }
        
        # Default fallbacks by economic tier
        self.tier_defaults = {
            "developing": 0.4,
            "emerging": 0.6,
            "developed": 1.0
        }
        
        logger.info("Regional economic service initialized")

    async def detect_user_region(self, user_id: int) -> str:
        """Detect user's region from various signals"""
        try:
            # Priority: Explicitly set country preference
            async with async_managed_session() as session:
                stmt = select(User).where(User.id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if user and hasattr(user, 'country_code') and getattr(user, 'country_code', None):
                    return getattr(user, 'country_code').upper()
            
            # TODO: Could add more detection methods:
            # - Phone number country code
            # - IP geolocation
            # - Currency preferences
            # - Time zone analysis
            
            # For now, default to US (no adjustment)
            return "US"
            
        except Exception as e:
            logger.error(f"Error detecting user region for {user_id}: {e}")
            return "US"  # Safe default

    async def get_regional_profile(self, region_code: str) -> RegionalEconomicProfile:
        """Get regional economic profile"""
        try:
            region_code = region_code.upper()
            
            # Direct lookup
            if region_code in self.regional_data:
                return self.regional_data[region_code]
            
            # Fallback by common economic groupings
            developing_countries = ["BD", "PK", "ET", "UG", "TZ", "MW", "NP", "AF", "MM", "KH", "LA"]
            emerging_countries = ["TR", "MY", "EG", "PE", "CL", "HU", "CZ", "SK", "HR", "LV", "LT", "EE"]
            
            if region_code in developing_countries:
                adjustment_factor = self.tier_defaults["developing"]
                tier = "developing"
            elif region_code in emerging_countries:
                adjustment_factor = self.tier_defaults["emerging"]
                tier = "emerging"
            else:
                adjustment_factor = self.tier_defaults["developed"]
                tier = "developed"
            
            # Create synthetic profile
            return RegionalEconomicProfile(
                region_code=region_code,
                country_code=region_code,
                purchasing_power_index=adjustment_factor,
                cost_of_living_index=adjustment_factor,
                average_income_usd=Decimal(str(adjustment_factor * 50000)),  # Scaled from US baseline
                currency_code="USD",  # Default to USD for unknowns
                economic_tier=tier,
                adjustment_factor=adjustment_factor
            )
            
        except Exception as e:
            logger.error(f"Error getting regional profile for {region_code}: {e}")
            # Return US baseline as fallback
            return self.regional_data["US"]

    async def calculate_adapted_threshold(self, user_id: int, original_threshold_usd: Decimal, threshold_type: str = "general") -> AdaptedThresholds:
        """Calculate regionally adapted threshold"""
        try:
            # Detect user's region
            region_code = await self.detect_user_region(user_id)
            regional_profile = await self.get_regional_profile(region_code)
            
            # Apply regional adjustment
            base_adjustment = regional_profile.adjustment_factor
            
            # Different adjustment strategies by threshold type
            if threshold_type == "referral":
                # More aggressive adjustment for referrals (community building)
                final_adjustment = base_adjustment * 0.8  # Extra 20% reduction
                reason = f"Referral threshold adjusted for {regional_profile.economic_tier} economy"
            elif threshold_type == "security":
                # More conservative adjustment for security (fraud prevention)
                final_adjustment = max(0.6, base_adjustment * 1.2)  # Less aggressive, min 60%
                reason = f"Security threshold conservatively adjusted for {regional_profile.economic_tier} economy"
            elif threshold_type == "minimum":
                # Moderate adjustment for minimums (accessibility)
                final_adjustment = base_adjustment
                reason = f"Minimum threshold adjusted for {regional_profile.economic_tier} economy"
            else:
                # General adjustment
                final_adjustment = base_adjustment
                reason = f"Threshold adjusted for {regional_profile.economic_tier} economy"
            
            # Calculate adapted amounts
            adapted_usd = original_threshold_usd * Decimal(str(final_adjustment))
            
            # TODO: Convert to local currency if needed
            adapted_local = adapted_usd  # For now, keep in USD
            local_currency = "USD"
            
            # Apply minimum safety limits (never go below certain absolute minimums)
            safety_minimums = {
                "referral": Decimal("5.00"),    # Never below $5 for referrals
                "security": Decimal("10.00"),   # Never below $10 for security
                "minimum": Decimal("1.00"),     # Never below $1 for minimums
                "general": Decimal("2.00"),     # Never below $2 for general
            }
            
            safety_minimum = safety_minimums.get(threshold_type, Decimal("2.00"))
            adapted_usd = max(safety_minimum, adapted_usd)
            adapted_local = max(safety_minimum, adapted_local)
            
            return AdaptedThresholds(
                user_id=user_id,
                region_code=region_code,
                original_threshold_usd=original_threshold_usd,
                adapted_threshold_usd=adapted_usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                adapted_threshold_local=adapted_local.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                local_currency=local_currency,
                adjustment_reason=reason,
                calculated_at=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error calculating adapted threshold for user {user_id}: {e}")
            # Return no adjustment as fallback
            return AdaptedThresholds(
                user_id=user_id,
                region_code="US",
                original_threshold_usd=original_threshold_usd,
                adapted_threshold_usd=original_threshold_usd,
                adapted_threshold_local=original_threshold_usd,
                local_currency="USD",
                adjustment_reason="Fallback - no adjustment applied",
                calculated_at=datetime.utcnow()
            )

    async def get_regional_referral_threshold(self, user_id: int, base_threshold_usd: Decimal = Decimal("50.00")) -> Decimal:
        """Get regionally adapted referral threshold"""
        try:
            adapted = await self.calculate_adapted_threshold(user_id, base_threshold_usd, "referral")
            return adapted.adapted_threshold_usd
        except Exception as e:
            logger.error(f"Error getting regional referral threshold for user {user_id}: {e}")
            return base_threshold_usd

    async def get_regional_security_threshold(self, user_id: int, base_threshold_usd: Decimal) -> Decimal:
        """Get regionally adapted security threshold"""
        try:
            adapted = await self.calculate_adapted_threshold(user_id, base_threshold_usd, "security")
            return adapted.adapted_threshold_usd
        except Exception as e:
            logger.error(f"Error getting regional security threshold for user {user_id}: {e}")
            return base_threshold_usd

    async def get_regional_minimum_threshold(self, user_id: int, base_threshold_usd: Decimal) -> Decimal:
        """Get regionally adapted minimum threshold"""
        try:
            adapted = await self.calculate_adapted_threshold(user_id, base_threshold_usd, "minimum")
            return adapted.adapted_threshold_usd
        except Exception as e:
            logger.error(f"Error getting regional minimum threshold for user {user_id}: {e}")
            return base_threshold_usd

    async def get_regional_adjustment_summary(self, user_id: int) -> Dict:
        """Get comprehensive regional adjustment summary for user"""
        try:
            region_code = await self.detect_user_region(user_id)
            regional_profile = await self.get_regional_profile(region_code)
            
            # Calculate sample adjustments
            sample_thresholds = {
                "referral_threshold": Decimal("50.00"),
                "security_large_tx": Decimal("1000.00"),
                "minimum_exchange": Decimal("5.00"),
                "auto_cashout": Decimal("25.00")
            }
            
            adjustments = {}
            for name, base_amount in sample_thresholds.items():
                threshold_type = "referral" if "referral" in name else "security" if "security" in name else "minimum"
                adapted = await self.calculate_adapted_threshold(user_id, base_amount, threshold_type)
                adjustments[name] = {
                    "original": float(base_amount),
                    "adapted": float(adapted.adapted_threshold_usd),
                    "savings": float(base_amount - adapted.adapted_threshold_usd),
                    "percentage_reduction": float((base_amount - adapted.adapted_threshold_usd) / base_amount * 100)
                }
            
            return {
                "user_id": user_id,
                "region": {
                    "code": region_code,
                    "economic_tier": regional_profile.economic_tier,
                    "adjustment_factor": regional_profile.adjustment_factor,
                    "purchasing_power_index": regional_profile.purchasing_power_index
                },
                "adjustments": adjustments,
                "total_savings": sum([adj["savings"] for adj in adjustments.values()]),
                "generated_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting regional adjustment summary for user {user_id}: {e}")
            return {"error": str(e), "user_id": user_id}