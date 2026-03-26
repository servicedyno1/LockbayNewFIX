#!/usr/bin/env python3
"""
Adaptive Security Service
Dynamic security thresholds based on user behavior patterns and real-time analysis
"""

import logging
from datetime import datetime, timedelta
from typing import List, Tuple
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import statistics

from sqlalchemy import and_
from database import SessionLocal
from models import Transaction, Cashout
from utils.dynamic_minimum_validator import DynamicMinimumValidator
from services.blockbee_service import BlockBeeService

logger = logging.getLogger(__name__)


@dataclass
class UserBehaviorProfile:
    """User behavior analysis profile"""
    
    user_id: int
    avg_transaction_amount: Decimal
    median_transaction_amount: Decimal
    std_dev_transaction: Decimal
    avg_cashout_amount: Decimal
    median_cashout_amount: Decimal
    std_dev_cashout: Decimal
    transaction_frequency_per_day: float
    cashout_frequency_per_day: float
    preferred_trading_hours: List[int]  # Hours 0-23
    risk_tolerance_score: float  # 0-100
    last_updated: datetime


@dataclass
class AdaptiveThresholds:
    """Dynamic security thresholds for a user"""
    
    user_id: int
    large_transaction_threshold: Decimal
    large_cashout_threshold: Decimal
    suspicious_cashout_percentage: float
    rapid_transaction_count: int
    rapid_transaction_window: int  # seconds
    auto_cashout_minimum: Decimal
    calculated_at: datetime


class AdaptiveSecurityService:
    """Intelligent security service with behavior-based thresholds"""

    def __init__(self):
        self.blockbee_service = BlockBeeService()
        self.dynamic_validator = DynamicMinimumValidator()
        
        # Base security multipliers (configurable)
        self.security_multipliers = {
            "large_transaction": 3.0,      # 3x user's average
            "large_cashout": 2.5,       # 2.5x user's average cashout
            "suspicious_percentage": 0.6,   # 60% of balance (adaptive)
            "rapid_count_multiplier": 1.5,  # 1.5x user's normal frequency
        }
        
        # Minimum safety thresholds (absolute minimums)
        self.safety_minimums = {
            "large_transaction": Decimal("100.00"),    # Never below $100
            "large_cashout": Decimal("50.00"),      # Never below $50
            "auto_cashout": Decimal("2.00"),           # Never below $2
        }
        
        logger.info("Adaptive security service initialized")

    async def get_user_behavior_profile(self, user_id: int) -> UserBehaviorProfile:
        """Analyze user behavior patterns over last 90 days"""
        session = SessionLocal()
        try:
            # Calculate date range for analysis
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=90)
            
            # Get transaction history
            transactions = session.query(Transaction).filter(
                and_(
                    Transaction.user_id == user_id,
                    Transaction.created_at >= start_date,
                    Transaction.status == "completed"
                )
            ).all()
            
            # Get cashout history
            cashouts = session.query(Cashout).filter(
                and_(
                    Cashout.user_id == user_id,
                    Cashout.created_at >= start_date,
                    Cashout.status.in_(["completed", "processing"])
                )
            ).all()
            
            # Calculate transaction statistics
            if transactions:
                tx_amounts = [float(getattr(tx, 'amount_usd', getattr(tx, 'amount', 0))) for tx in transactions]
                avg_tx = Decimal(str(statistics.mean(tx_amounts)))
                median_tx = Decimal(str(statistics.median(tx_amounts)))
                std_dev_tx = Decimal(str(statistics.stdev(tx_amounts) if len(tx_amounts) > 1 else 0))
                tx_frequency = len(transactions) / 90.0  # per day
                
                # Analyze preferred trading hours
                trading_hours = [tx.created_at.hour for tx in transactions]
                hour_counts = {}
                for hour in trading_hours:
                    hour_counts[hour] = hour_counts.get(hour, 0) + 1
                preferred_hours = sorted(hour_counts.keys(), key=lambda x: hour_counts[x], reverse=True)[:8]
            else:
                avg_tx = median_tx = std_dev_tx = Decimal("0")
                tx_frequency = 0.0
                preferred_hours = list(range(9, 17))  # Default business hours
            
            # Calculate cashout statistics
            if cashouts:
                wd_amounts = [float(getattr(wd, 'amount_usd', getattr(wd, 'amount', 0))) for wd in cashouts]
                avg_wd = Decimal(str(statistics.mean(wd_amounts)))
                median_wd = Decimal(str(statistics.median(wd_amounts)))
                std_dev_wd = Decimal(str(statistics.stdev(wd_amounts) if len(wd_amounts) > 1 else 0))
                wd_frequency = len(cashouts) / 90.0  # per day
            else:
                avg_wd = median_wd = std_dev_wd = Decimal("0")
                wd_frequency = 0.0
            
            # Calculate risk tolerance score (0-100)
            # Higher score = more risk tolerant (larger amounts, more frequent)
            risk_score = min(100.0, (
                float(avg_tx) / 100.0 * 20 +  # Transaction size factor
                tx_frequency * 10 +            # Activity frequency factor  
                float(std_dev_tx) / 50.0 * 15 + # Variance tolerance factor
                len(preferred_hours) / 24.0 * 10  # Time flexibility factor
            ))
            
            return UserBehaviorProfile(
                user_id=user_id,
                avg_transaction_amount=avg_tx,
                median_transaction_amount=median_tx,
                std_dev_transaction=std_dev_tx,
                avg_cashout_amount=avg_wd,
                median_cashout_amount=median_wd,
                std_dev_cashout=std_dev_wd,
                transaction_frequency_per_day=tx_frequency,
                cashout_frequency_per_day=wd_frequency,
                preferred_trading_hours=preferred_hours,
                risk_tolerance_score=risk_score,
                last_updated=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error analyzing user behavior profile for {user_id}: {e}")
            # Return safe defaults for new users
            return UserBehaviorProfile(
                user_id=user_id,
                avg_transaction_amount=Decimal("50.00"),
                median_transaction_amount=Decimal("25.00"),
                std_dev_transaction=Decimal("25.00"),
                avg_cashout_amount=Decimal("100.00"),
                median_cashout_amount=Decimal("50.00"),
                std_dev_cashout=Decimal("50.00"),
                transaction_frequency_per_day=0.1,
                cashout_frequency_per_day=0.05,
                preferred_trading_hours=list(range(9, 17)),
                risk_tolerance_score=30.0,
                last_updated=datetime.utcnow()
            )
        finally:
            session.close()

    async def calculate_adaptive_thresholds(self, user_id: int) -> AdaptiveThresholds:
        """Calculate dynamic security thresholds for user"""
        try:
            # Get user behavior profile
            profile = await self.get_user_behavior_profile(user_id)
            
            # Calculate large transaction threshold
            # Use 3x average or median (whichever is higher) + 1 std deviation
            large_tx_base = max(profile.avg_transaction_amount, profile.median_transaction_amount)
            large_tx_threshold = max(
                self.safety_minimums["large_transaction"],
                large_tx_base * Decimal(str(self.security_multipliers["large_transaction"])) + profile.std_dev_transaction
            )
            
            # Calculate large cashout threshold  
            large_wd_base = max(profile.avg_cashout_amount, profile.median_cashout_amount)
            large_wd_threshold = max(
                self.safety_minimums["large_cashout"],
                large_wd_base * Decimal(str(self.security_multipliers["large_cashout"])) + profile.std_dev_cashout
            )
            
            # Calculate suspicious cashout percentage (adaptive based on risk tolerance)
            # Higher risk tolerance = higher percentage allowed
            base_suspicious_pct = self.security_multipliers["suspicious_percentage"]
            risk_adjustment = (profile.risk_tolerance_score / 100.0) * 0.2  # 0-20% adjustment
            suspicious_pct = min(0.9, base_suspicious_pct + risk_adjustment)  # Max 90%
            
            # Calculate rapid transaction limits (adaptive to user's normal frequency)
            normal_daily_tx = max(1, profile.transaction_frequency_per_day)
            rapid_count = max(3, int(normal_daily_tx * self.security_multipliers["rapid_count_multiplier"]))
            rapid_window = max(300, int(3600 / normal_daily_tx))  # Adaptive time window
            
            # Calculate network-aware auto-cashout minimum
            auto_cashout_min = await self._calculate_network_aware_minimum()
            
            return AdaptiveThresholds(
                user_id=user_id,
                large_transaction_threshold=large_tx_threshold.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                large_cashout_threshold=large_wd_threshold.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                suspicious_cashout_percentage=suspicious_pct,
                rapid_transaction_count=rapid_count,
                rapid_transaction_window=rapid_window,
                auto_cashout_minimum=auto_cashout_min,
                calculated_at=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error calculating adaptive thresholds for user {user_id}: {e}")
            # Return safe default thresholds
            return AdaptiveThresholds(
                user_id=user_id,
                large_transaction_threshold=Decimal("150.00"),
                large_cashout_threshold=Decimal("100.00"),
                suspicious_cashout_percentage=0.7,
                rapid_transaction_count=5,
                rapid_transaction_window=300,
                auto_cashout_minimum=Decimal("10.00"),
                calculated_at=datetime.utcnow()
            )

    async def _calculate_network_aware_minimum(self) -> Decimal:
        """Calculate intelligent auto-cashout minimum based on current network fees"""
        try:
            # Get current network fees for major networks
            network_fees = {}
            
            # Check major networks (BTC, ETH, TRC20)
            for network in ["BTC", "ETH", "TRC20"]:
                try:
                    # Get current network fee estimate
                    # Get network fee estimate (placeholder implementation)
                    fee_info = {"fee_usd": 5.0}  # Default fallback
                    if fee_info and "fee_usd" in fee_info:
                        network_fees[network] = Decimal(str(fee_info["fee_usd"]))
                except Exception as e:
                    logger.warning(f"Could not get fee for {network}: {e}")
            
            # Use lowest available network fee + 50% buffer
            if network_fees:
                min_network_fee = min(network_fees.values())
                intelligent_minimum = min_network_fee * Decimal("1.5")  # 50% buffer
                
                # Ensure it's above absolute safety minimum
                return max(self.safety_minimums["auto_cashout"], intelligent_minimum)
            else:
                # Fallback to conservative estimate
                return Decimal("8.00")  # Safe default
                
        except Exception as e:
            logger.error(f"Error calculating network-aware minimum: {e}")
            return Decimal("10.00")  # Safe fallback

    async def is_transaction_suspicious(self, user_id: int, amount_usd: Decimal, transaction_type: str = "transaction") -> Tuple[bool, str]:
        """Check if transaction amount is suspicious for this user"""
        try:
            thresholds = await self.calculate_adaptive_thresholds(user_id)
            
            if transaction_type == "cashout":
                if amount_usd > thresholds.large_cashout_threshold:
                    return True, f"Large cashout: ${amount_usd} exceeds personal threshold of ${thresholds.large_cashout_threshold}"
            else:
                if amount_usd > thresholds.large_transaction_threshold:
                    return True, f"Large transaction: ${amount_usd} exceeds personal threshold of ${thresholds.large_transaction_threshold}"
            
            return False, "Amount within normal range for user"
            
        except Exception as e:
            logger.error(f"Error checking transaction suspicion for user {user_id}: {e}")
            # Fallback to conservative check
            return amount_usd > Decimal("500.00"), "Fallback security check"

    async def check_rapid_transaction_pattern(self, user_id: int, timeframe_minutes: int = 10) -> Tuple[bool, str]:
        """Check for rapid transaction patterns based on user's normal behavior"""
        session = SessionLocal()
        try:
            thresholds = await self.calculate_adaptive_thresholds(user_id)
            
            # Check recent transactions within adaptive window
            recent_cutoff = datetime.utcnow() - timedelta(seconds=thresholds.rapid_transaction_window)
            
            recent_count = session.query(Transaction).filter(
                and_(
                    Transaction.user_id == user_id,
                    Transaction.created_at >= recent_cutoff
                )
            ).count()
            
            if recent_count >= thresholds.rapid_transaction_count:
                return True, f"Rapid transactions: {recent_count} in {thresholds.rapid_transaction_window}s (threshold: {thresholds.rapid_transaction_count})"
            
            return False, "Transaction frequency within normal range"
            
        except Exception as e:
            logger.error(f"Error checking rapid transaction pattern for user {user_id}: {e}")
            return False, "Error in pattern analysis"
        finally:
            session.close()

    async def get_user_auto_cashout_minimum(self, user_id: int) -> Decimal:
        """Get intelligent auto-cashout minimum for user"""
        try:
            thresholds = await self.calculate_adaptive_thresholds(user_id)
            return thresholds.auto_cashout_minimum
        except Exception as e:
            logger.error(f"Error getting auto-cashout minimum for user {user_id}: {e}")
            return Decimal("10.00")  # Safe fallback

    async def analyze_cashout_risk(self, user_id: int, cashout_amount: Decimal, user_balance: Decimal) -> Tuple[int, List[str]]:
        """Analyze cashout risk with adaptive thresholds (returns risk_score 0-100, reasons)"""
        try:
            risk_score = 0
            risk_factors = []
            
            thresholds = await self.calculate_adaptive_thresholds(user_id)
            
            # Check large cashout threshold (adaptive)
            if cashout_amount > thresholds.large_cashout_threshold:
                risk_score += 30
                risk_factors.append(f"Large cashout: ${cashout_amount} exceeds personal threshold ${thresholds.large_cashout_threshold}")
            
            # Check suspicious percentage (adaptive based on user risk tolerance)
            if user_balance > 0:
                cashout_percentage = float(cashout_amount) / float(user_balance)
                if cashout_percentage > thresholds.suspicious_cashout_percentage:
                    risk_score += 40
                    risk_factors.append(f"High balance percentage: {cashout_percentage:.1%} of balance (threshold: {thresholds.suspicious_cashout_percentage:.1%})")
            
            # Check rapid pattern
            is_rapid, rapid_reason = await self.check_rapid_transaction_pattern(user_id)
            if is_rapid:
                risk_score += 25
                risk_factors.append(f"Rapid pattern: {rapid_reason}")
            
            # Outside preferred hours check
            current_hour = datetime.utcnow().hour
            profile = await self.get_user_behavior_profile(user_id)
            if current_hour not in profile.preferred_trading_hours:
                risk_score += 10
                risk_factors.append(f"Outside normal trading hours (usual: {profile.preferred_trading_hours})")
            
            return min(100, risk_score), risk_factors
            
        except Exception as e:
            logger.error(f"Error analyzing cashout risk for user {user_id}: {e}")
            return 20, ["Error in risk analysis - applying conservative score"]