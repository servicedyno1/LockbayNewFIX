#!/usr/bin/env python3
"""
BalanceGuard: Unified Balance Monitoring System

This service replaces 7 overlapping balance monitoring services with a single,
streamlined system that eliminates configuration duplication and provides
consistent alerts across all financial service providers.

Replaces:
- services/enhanced_balance_protection.py
- services/balance_monitor.py  
- jobs/enhanced_balance_monitor.py
- jobs/balance_monitor.py
- jobs/unified_monitor_jobs.py
- And other overlapping monitoring components

Architecture:
- Provider adapters for clean service abstraction
- Centralized policy configuration with tier derivation
- Consolidated notifier with cooldown logic and deduplication
- Single source of truth for all balance monitoring decisions
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, List, Tuple, Protocol
from enum import Enum
from dataclasses import dataclass, asdict
import json

from sqlalchemy import text
from database import managed_session, sync_managed_session
from config import Config
from services.fincra_service import FincraService
from services.kraken_service import get_kraken_service
from services.email import EmailService
from services.fastforex_service import FastForexService
from utils.admin import get_admin_user_ids

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Balance alert severity levels with numeric priority for comparison"""
    WARNING = 1          # 75% of threshold
    CRITICAL = 2         # 50% of threshold  
    EMERGENCY = 3        # 25% of threshold
    OPERATIONAL_DANGER = 4  # Below minimum operational threshold (blocks operations)


@dataclass
class BalanceSnapshot:
    """Normalized balance information from any provider"""
    provider: str
    currency: str
    balance: Decimal
    formatted_balance: str
    timestamp: datetime
    threshold_base: Decimal
    threshold_warning: Decimal
    threshold_critical: Decimal
    threshold_emergency: Decimal
    threshold_operational: Decimal
    alert_level: Optional[AlertLevel] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging and storage"""
        data = asdict(self)
        # Convert non-serializable types
        data['balance'] = float(self.balance)
        data['timestamp'] = self.timestamp.isoformat()
        data['threshold_base'] = float(self.threshold_base)
        data['threshold_warning'] = float(self.threshold_warning)
        data['threshold_critical'] = float(self.threshold_critical)
        data['threshold_emergency'] = float(self.threshold_emergency)
        data['threshold_operational'] = float(self.threshold_operational)
        data['alert_level'] = self.alert_level.name if self.alert_level else None
        return data


@dataclass
class ProtectionStatus:
    """Operation protection decision with detailed reasoning"""
    operation_allowed: bool
    alert_level: Optional[AlertLevel]
    balance_check_passed: bool  # Required by financial_operation_protection
    insufficient_services: List[str]  # Required by auto_cashout (alias for blocking_providers)
    blocking_providers: List[str]
    warning_providers: List[str]
    balance_snapshots: List[BalanceSnapshot]
    warning_message: Optional[str] = None  # Required by financial_operation_protection
    blocking_reason: Optional[str] = None  # Required by financial_operation_protection
    protection_reason: Optional[str] = None
    recommendation: Optional[str] = None


class BalanceProvider(Protocol):
    """Protocol defining the interface for balance providers"""
    
    async def get_balance_snapshot(self) -> Optional[BalanceSnapshot]:
        """Get normalized balance snapshot from this provider"""
        ...
    
    def get_provider_name(self) -> str:
        """Get the human-readable provider name"""
        ...


class FincraBalanceProvider:
    """Fincra NGN balance provider adapter"""
    
    def __init__(self, policy: 'BalanceGuardPolicy'):
        from services.fincra_service import get_fincra_service
        self.fincra_service = get_fincra_service()
        self.policy = policy
    
    def get_provider_name(self) -> str:
        return "Fincra NGN"
    
    async def get_balance_snapshot(self, force_fresh_for_critical: bool = False) -> Optional[BalanceSnapshot]:
        """Get Fincra NGN balance as normalized snapshot"""
        try:
            # CRITICAL FIX: Use cached balance call instead of direct call (mirrors Kraken optimization)
            balance_info = await self.fincra_service.get_cached_account_balance(force_fresh_for_critical=force_fresh_for_critical)
            if not balance_info:
                logger.warning("Could not retrieve Fincra balance for BalanceGuard")
                return None
            
            balance = Decimal(str(balance_info.get("available_balance", 0)))
            
            # Get policy thresholds for Fincra
            thresholds = self.policy.get_fincra_thresholds()
            
            # Determine alert level
            alert_level = None
            if balance <= thresholds['operational']:
                alert_level = AlertLevel.OPERATIONAL_DANGER
            elif balance <= thresholds['emergency']:
                alert_level = AlertLevel.EMERGENCY
            elif balance <= thresholds['critical']:
                alert_level = AlertLevel.CRITICAL
            elif balance <= thresholds['warning']:
                alert_level = AlertLevel.WARNING
            
            return BalanceSnapshot(
                provider="fincra",
                currency="NGN",
                balance=balance,
                formatted_balance=f"₦{balance:,.2f}",
                timestamp=datetime.now(timezone.utc),
                threshold_base=thresholds['base'],
                threshold_warning=thresholds['warning'],
                threshold_critical=thresholds['critical'],
                threshold_emergency=thresholds['emergency'],
                threshold_operational=thresholds['operational'],
                alert_level=alert_level
            )
            
        except Exception as e:
            logger.error(f"Error getting Fincra balance snapshot: {e}")
            return None


class KrakenBalanceProvider:
    """Kraken multi-currency balance provider adapter with performance optimizations"""
    
    def __init__(self, policy: 'BalanceGuardPolicy'):
        self.kraken_service = get_kraken_service()
        self.fastforex_service = FastForexService()
        self.policy = policy
        
        # Monitored currencies for combined balance calculation (USDT TRC20/ERC20 + BTC + ETH + LTC + USD)
        self.monitored_currencies = [
            "BTC", "ETH", "LTC", "USDT", "USD"  # Core currencies for operational balance monitoring
        ]
        
        # CRITICAL FIX: Remove instance-level cache - now handled by kraken_service shared cache
        # This eliminates cache duplication and ensures proper sharing across operations
        
        # Note: kraken_service already handles currency mapping from Kraken symbols to standard names
        # So we don't need a separate currency_mapping here
    
    def get_provider_name(self) -> str:
        return "Kraken Crypto"
    
    async def _convert_to_usd(self, currency: str, amount: Decimal) -> Optional[Decimal]:
        """Convert cryptocurrency to USD equivalent using FastForex"""
        try:
            if currency in ["USDT", "USD"]:
                return amount  # 1:1 conversion for USDT and USD
                
            usd_rate = await self.fastforex_service.get_crypto_to_usd_rate(currency)
            if usd_rate is None or usd_rate <= 0:
                logger.warning(f"Could not get {currency}/USD rate for BalanceGuard")
                return None
                
            return amount * Decimal(str(usd_rate))
            
        except Exception as e:
            logger.error(f"Error converting {currency} to USD: {e}")
            return None
    
    async def get_balance_snapshot(self, target_currency: Optional[str] = None, force_fresh_for_critical: bool = False) -> Optional[BalanceSnapshot]:
        """Get aggregated Kraken balance as USD-equivalent snapshot
        
        Args:
            target_currency: If specified, only process this specific currency instead of all monitored currencies.
                           This provides massive performance optimization for currency-specific operations.
            force_fresh_for_critical: Force fresh fetch for critical operations near thresholds
        """
        try:
            if not self.kraken_service.is_available():
                logger.warning("Kraken service not available for BalanceGuard")
                return None
            
            # CRITICAL SAFEGUARD: Force fresh fetch for critical operations near thresholds
            # This prevents stale balance decisions when balances are close to operational limits
            force_fresh = force_fresh_for_critical
            
            # Get initial balance data (cached or fresh)
            balance_result = await self.kraken_service.get_cached_account_balance(
                force_fresh=force_fresh, 
                target_currency=target_currency
            )
            if not balance_result:
                logger.warning("Could not retrieve Kraken balances for BalanceGuard")
                return None
            
            # CRITICAL FIX: Properly extract currency balances from all possible response formats
            currency_balances = None
            
            # Format 1: Standard format {'success': bool, 'balances': {currency: {total: X, available: Y}}}
            if isinstance(balance_result, dict) and 'success' in balance_result and 'balances' in balance_result:
                if not balance_result['success']:
                    logger.warning("Kraken balance retrieval failed - service returned success=False")
                    return None
                currency_balances = balance_result['balances']
                
            # Format 2: Tuple format (success, balances_dict)
            elif isinstance(balance_result, tuple) and len(balance_result) == 2:
                success, balances_data = balance_result
                if not success or not balances_data:
                    logger.warning("Kraken balance retrieval failed or returned empty balances (tuple format)")
                    return None
                currency_balances = balances_data
                
            # Format 3: Direct currency dictionary (fallback for legacy formats)
            elif isinstance(balance_result, dict):
                # Check if this looks like a direct currency dictionary (no 'success'/'balances' keys)
                if not any(key in balance_result for key in ['success', 'balances', 'error']):
                    currency_balances = balance_result
                else:
                    logger.warning("Unrecognized Kraken response format - contains metadata but not standard format")
                    return None
            else:
                logger.warning(f"Unrecognized Kraken response type: {type(balance_result)}")
                return None
            
            # Validate we have actual currency balance data
            if not currency_balances:
                logger.warning("No currency balances found in Kraken response")
                return None
            
            # PERFORMANCE OPTIMIZATION: Currency filtering for targeted operations
            currencies_to_process = [target_currency] if target_currency else self.monitored_currencies
            
            # Calculate combined USD-equivalent balance for monitored currencies
            total_usd = Decimal("0")
            currency_details = []
            aggregated_balances = {}
            
            # First, aggregate balances by normalized currency
            # Note: kraken_service returns {'currency': {'total': amount, 'available': amount, 'locked': 0.0}}
            for currency_symbol, balance_data in currency_balances.items():
                # Extract actual balance amount from the nested structure
                if isinstance(balance_data, dict) and 'total' in balance_data:
                    balance_amount = balance_data['total']
                else:
                    # Fallback for flat structure (in case format changes)
                    balance_amount = balance_data
                
                # PERFORMANCE OPTIMIZATION: Process only target currency or all monitored currencies
                if currency_symbol in currencies_to_process:
                    balance = Decimal(str(balance_amount))
                    if balance > 0:
                        if currency_symbol not in aggregated_balances:
                            aggregated_balances[currency_symbol] = Decimal("0")
                        aggregated_balances[currency_symbol] += balance
            
            # Convert aggregated balances to USD and calculate total
            for currency, total_balance in aggregated_balances.items():
                usd_value = await self._convert_to_usd(currency, total_balance)
                if usd_value:
                    total_usd += usd_value
                    currency_details.append(f"{total_balance:.8f} {currency} (${usd_value:.2f})")
                else:
                    logger.warning(f"Failed to convert {currency} balance {total_balance} to USD")
            
            # Display combined total balance - ALWAYS show this before any potential exceptions
            logger.info(f"💰 TOTAL COMBINED BALANCE: ${total_usd:.2f} USD ({len(aggregated_balances)} currencies)")
            
            # Get policy thresholds for Kraken
            thresholds = self.policy.get_kraken_thresholds()
            
            # CRITICAL SAFEGUARD: Near-threshold detection for fresh fetch requirements
            # If balance is close to operational threshold and using cached data, force fresh fetch
            near_threshold_margin = Decimal("10.0")  # $10 USD margin for safety
            is_near_operational = total_usd <= (thresholds['operational'] + near_threshold_margin)
            
            if is_near_operational and not force_fresh and not force_fresh_for_critical:
                logger.warning(f"🚨 NEAR_THRESHOLD_DETECTED: Balance ${total_usd:.2f} within ${near_threshold_margin} of operational threshold ${thresholds['operational']:.2f} - forcing fresh fetch")
                # Force fresh fetch for accurate near-threshold balance decisions
                balance_result = await self.kraken_service.get_cached_account_balance(
                    force_fresh=True, 
                    target_currency=target_currency
                )
                
                # Re-process with fresh data
                if balance_result and balance_result.get('success'):
                    # Extract fresh currency balances and recalculate
                    fresh_currency_balances = balance_result.get('balances', {})
                    if fresh_currency_balances:
                        # Recalculate with fresh data
                        fresh_total_usd = Decimal("0")
                        fresh_currency_details = []
                        fresh_aggregated_balances = {}
                        
                        # Recalculate balances with fresh data
                        for currency_symbol, balance_data in fresh_currency_balances.items():
                            if isinstance(balance_data, dict) and 'total' in balance_data:
                                balance_amount = balance_data['total']
                            else:
                                balance_amount = balance_data
                            
                            if currency_symbol in currencies_to_process:
                                balance = Decimal(str(balance_amount))
                                if balance > 0:
                                    if currency_symbol not in fresh_aggregated_balances:
                                        fresh_aggregated_balances[currency_symbol] = Decimal("0")
                                    fresh_aggregated_balances[currency_symbol] += balance
                        
                        # Convert fresh balances to USD
                        for currency, total_balance in fresh_aggregated_balances.items():
                            usd_value = await self._convert_to_usd(currency, total_balance)
                            if usd_value:
                                fresh_total_usd += usd_value
                                fresh_currency_details.append(f"{total_balance:.8f} {currency} (${usd_value:.2f})")
                        
                        # Use fresh totals
                        total_usd = fresh_total_usd
                        currency_details = fresh_currency_details
                        logger.info(f"✅ FRESH_NEAR_THRESHOLD: Updated balance ${total_usd:.2f} with fresh data")
                        
                        # CRITICAL FIX: Display combined total for fresh path too!
                        logger.info(f"💰 TOTAL COMBINED BALANCE (FRESH): ${total_usd:.2f} USD ({len(fresh_aggregated_balances)} currencies)")
            
            # Determine alert level based on total USD value
            alert_level = None
            if total_usd <= thresholds['operational']:
                alert_level = AlertLevel.OPERATIONAL_DANGER
            elif total_usd <= thresholds['emergency']:
                alert_level = AlertLevel.EMERGENCY
            elif total_usd <= thresholds['critical']:
                alert_level = AlertLevel.CRITICAL
            elif total_usd <= thresholds['warning']:
                alert_level = AlertLevel.WARNING
            
            # Create detailed formatted balance string
            formatted_balance = f"${total_usd:.2f} USD-equiv"
            if currency_details:
                formatted_balance += f" ({', '.join(currency_details[:3])}{'...' if len(currency_details) > 3 else ''})"
            
            snapshot = BalanceSnapshot(
                provider="kraken",
                currency="USD",  # Aggregated as USD equivalent
                balance=total_usd,
                formatted_balance=formatted_balance,
                timestamp=datetime.now(timezone.utc),
                threshold_base=thresholds['base'],
                threshold_warning=thresholds['warning'],
                threshold_critical=thresholds['critical'],
                threshold_emergency=thresholds['emergency'],
                threshold_operational=thresholds['operational'],
                alert_level=alert_level
            )
            
            # CRITICAL FIX: Caching now handled by shared kraken_service.get_cached_account_balance()
            # No need for instance-level caching here anymore
            
            return snapshot
            
        except Exception as e:
            logger.error(f"Error getting Kraken balance snapshot: {e}")
            return None
    
    async def _create_currency_specific_snapshot(
        self, 
        cached_balance_result: Dict[str, Any], 
        target_currency: str, 
        timestamp: datetime
    ) -> Optional[BalanceSnapshot]:
        """Create currency-specific snapshot from cached raw balance data (NO API CALLS)
        
        CRITICAL FIX: This method should filter cached raw balance data, not make fresh API calls.
        This provides true currency-specific optimization by processing only the target currency.
        """
        try:
            if not cached_balance_result or not cached_balance_result.get('success'):
                logger.warning(f"Invalid cached balance result for {target_currency} filtering")
                return None
            
            # Extract currency balances from cached result
            currency_balances = cached_balance_result.get('balances', {})
            if not currency_balances:
                logger.warning(f"No currency balances in cached data for {target_currency} filtering")
                return None
            
            # PERFORMANCE OPTIMIZATION: Process only the target currency instead of all monitored currencies
            target_currencies = [target_currency]  # Only process the specific currency
            
            # Calculate USD equivalent for just the target currency
            total_usd = Decimal("0")
            currency_details = []
            
            for currency_symbol, balance_data in currency_balances.items():
                # Extract balance amount
                if not isinstance(balance_data, dict) or 'total' not in balance_data:
                    continue
                    
                balance_amount = Decimal(str(balance_data['total']))
                if balance_amount <= 0:
                    continue
                
                # Check if this currency matches our target (handle both Kraken and standard symbols)
                if currency_symbol == target_currency or currency_symbol in target_currencies:
                    # Convert to USD
                    usd_equivalent = await self._convert_to_usd(target_currency, balance_amount)
                    if usd_equivalent is not None:
                        total_usd += usd_equivalent
                        currency_details.append({
                            'currency': target_currency,
                            'balance': balance_amount,
                            'usd_value': usd_equivalent
                        })
                    break  # Found our target currency, no need to continue
            
            # Create snapshot for the target currency
            thresholds = self.policy.get_kraken_thresholds()
            
            # Determine alert level based on USD equivalent
            alert_level = None
            if total_usd <= thresholds['operational']:
                alert_level = AlertLevel.OPERATIONAL_DANGER
            elif total_usd <= thresholds['emergency']:
                alert_level = AlertLevel.EMERGENCY
            elif total_usd <= thresholds['critical']:
                alert_level = AlertLevel.CRITICAL
            elif total_usd <= thresholds['warning']:
                alert_level = AlertLevel.WARNING
            
            formatted_balance = f"${total_usd:.2f} USD ({target_currency} specific)"
            
            return BalanceSnapshot(
                provider="kraken",
                currency=f"USD ({target_currency})",  # Indicate this is currency-specific
                balance=total_usd,
                formatted_balance=formatted_balance,
                timestamp=timestamp,
                threshold_base=thresholds['base'],
                threshold_warning=thresholds['warning'],
                threshold_critical=thresholds['critical'],
                threshold_emergency=thresholds['emergency'],
                threshold_operational=thresholds['operational'],
                alert_level=alert_level
            )
            
        except Exception as e:
            logger.error(f"Error creating currency-specific snapshot for {target_currency}: {e}")
            return None


class BalanceGuardPolicy:
    """Centralized balance policy configuration using unified config.py as single source of truth"""
    
    def __init__(self):
        # All configuration now comes from config.py - no hardcoded values
        # Base thresholds, percentages, and cooldowns all centralized
        pass
    
    def get_fincra_thresholds(self) -> Dict[str, Decimal]:
        """Get all Fincra NGN thresholds from unified config.py"""
        return Config.get_fincra_balance_thresholds()
    
    def get_kraken_thresholds(self) -> Dict[str, Decimal]:
        """Get all Kraken USD thresholds from unified config.py"""
        return Config.get_kraken_balance_thresholds()
    
    def get_cooldown_hours(self, alert_level: AlertLevel) -> int:
        """Get cooldown hours for specific alert level from unified config.py"""
        cooldowns = Config.get_balance_alert_cooldowns()
        
        # Map AlertLevel enum to config key names
        level_mapping = {
            AlertLevel.WARNING: 'warning',
            AlertLevel.CRITICAL: 'critical', 
            AlertLevel.EMERGENCY: 'emergency',
            AlertLevel.OPERATIONAL_DANGER: 'operational'
        }
        
        config_key = level_mapping.get(alert_level)
        if config_key:
            return cooldowns.get(config_key, 24)  # 24h fallback
        
        return 24  # Default fallback for unknown alert levels


class BalanceGuardNotifier:
    """Consolidated notification system with cooldown logic and deduplication"""
    
    def __init__(self, policy: BalanceGuardPolicy):
        self.policy = policy
        self.email_service = EmailService()
    
    async def should_send_alert(
        self, 
        provider: str, 
        currency: str, 
        alert_level: AlertLevel
    ) -> bool:
        """Check if alert should be sent based on cooldown rules"""
        try:
            cooldown_hours = self.policy.get_cooldown_hours(alert_level)
            cooldown_key = f"{provider}_{currency}_{alert_level.name}"
            
            with sync_managed_session() as session:
                # Check last alert time for this specific provider+currency+level
                result = session.execute(
                    text("""
                    SELECT last_alert_time 
                    FROM balance_alert_state 
                    WHERE alert_key = :alert_key
                    """),
                    {"alert_key": cooldown_key}
                )
                
                row = result.fetchone()
                if not row:
                    return True  # No previous alert, safe to send
                
                last_alert = row[0]
                if last_alert is None:
                    return True
                
                # Check if enough time has passed
                cooldown_expires = last_alert + timedelta(hours=cooldown_hours)
                should_send = datetime.now(timezone.utc) >= cooldown_expires
                
                if not should_send:
                    logger.debug(
                        f"Alert cooldown active for {cooldown_key}: "
                        f"Last={last_alert}, Cooldown={cooldown_hours}h, "
                        f"Next={cooldown_expires}"
                    )
                
                return should_send
                
        except Exception as e:
            logger.error(f"Error checking alert cooldown: {e}")
            return True  # Default to sending alert if check fails
    
    async def record_alert_sent(
        self, 
        provider: str, 
        currency: str, 
        alert_level: AlertLevel
    ):
        """Record that an alert was sent for cooldown tracking"""
        try:
            cooldown_key = f"{provider}_{currency}_{alert_level.name}"
            
            with sync_managed_session() as session:
                # Upsert alert timestamp
                session.execute(
                    text("""
                    INSERT INTO balance_alert_state (
                        alert_key, provider, currency, alert_level, 
                        last_alert_time, alert_count, created_at, updated_at
                    ) VALUES (
                        :alert_key, :provider, :currency, :alert_level,
                        :now, 1, :now, :now
                    )
                    ON CONFLICT (alert_key) DO UPDATE SET
                        last_alert_time = :now,
                        alert_count = balance_alert_state.alert_count + 1,
                        updated_at = :now
                    """),
                    {
                        "alert_key": cooldown_key,
                        "provider": provider,
                        "currency": currency,
                        "alert_level": alert_level.name,
                        "now": datetime.now(timezone.utc)
                    }
                )
                session.commit()
                
        except Exception as e:
            logger.error(f"Error recording alert sent: {e}")
    
    async def send_balance_alert(
        self, 
        snapshot: BalanceSnapshot, 
        force: bool = False
    ) -> bool:
        """Send balance alert with cooldown logic"""
        try:
            if not snapshot.alert_level:
                return False
            
            # Check cooldown unless forced
            if not force:
                should_send = await self.should_send_alert(
                    snapshot.provider, 
                    snapshot.currency, 
                    snapshot.alert_level
                )
                if not should_send:
                    return False
            
            # Generate alert content
            subject, content = self._generate_alert_content(snapshot)
            
            # Send email alert
            success = False
            if Config.ADMIN_EMAIL and self.email_service.enabled:
                success = self.email_service.send_email(
                    to_email=Config.ADMIN_EMAIL,
                    subject=subject,
                    text_content=content,
                    html_content=self._generate_html_alert_content(snapshot)
                )
            
            if success or force:
                # Record alert sent for cooldown tracking
                await self.record_alert_sent(
                    snapshot.provider, 
                    snapshot.currency, 
                    snapshot.alert_level
                )
                
                logger.warning(
                    f"🚨 BALANCE_ALERT_SENT: {snapshot.provider} {snapshot.currency} "
                    f"{snapshot.alert_level.name} - {snapshot.formatted_balance}"
                )
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error sending balance alert: {e}")
            return False
    
    def _generate_alert_content(self, snapshot: BalanceSnapshot) -> Tuple[str, str]:
        """Generate alert email subject and text content"""
        level_emoji = {
            AlertLevel.WARNING: "⚠️",
            AlertLevel.CRITICAL: "🔥", 
            AlertLevel.EMERGENCY: "🚨",
            AlertLevel.OPERATIONAL_DANGER: "🚫"
        }
        
        emoji = level_emoji.get(snapshot.alert_level, "⚠️")
        level_name = snapshot.alert_level.name.replace('_', ' ').title()
        
        subject = f"{emoji} {level_name}: {snapshot.provider} Balance Low - {Config.PLATFORM_NAME}"
        
        # Determine currency symbol based on provider
        currency_symbol = '₦' if snapshot.currency == 'NGN' else '$'
        
        content = f"""
{emoji} {level_name.upper()} BALANCE ALERT

Provider: {snapshot.provider}
Currency: {snapshot.currency}
Current Balance: {snapshot.formatted_balance}
Alert Level: {level_name}

THRESHOLDS:
• Warning (75%): {currency_symbol}{snapshot.threshold_warning:,.2f}
• Critical (50%): {currency_symbol}{snapshot.threshold_critical:,.2f}
• Emergency (25%): {currency_symbol}{snapshot.threshold_emergency:,.2f}
• Operational Min: {currency_symbol}{snapshot.threshold_operational:,.2f}

{self._get_operation_status_message(snapshot.alert_level)}

Time: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

Please review and fund the account as needed.

BalanceGuard Monitoring System - {Config.PLATFORM_NAME}
        """.strip()
        
        return subject, content
    
    def _get_operation_status_message(self, alert_level: AlertLevel) -> str:
        """
        Generate truthful operation status message based on current system behavior.
        
        Since the system now checks admin overrides first, we can provide accurate status.
        By default, operations proceed unless specifically blocked by admin override.
        """
        if alert_level == AlertLevel.OPERATIONAL_DANGER:
            return (
                "⚠️ BALANCE CRITICAL: Consider funding account soon. "
                "Operations currently proceed unless paused by admin override."
            )
        else:
            return "✅ Operations proceeding normally at this balance level."
    
    def _generate_html_operation_status(self, alert_level: AlertLevel) -> str:
        """
        Generate HTML operation status for email templates.
        Provides truthful status based on current system behavior.
        """
        if alert_level == AlertLevel.OPERATIONAL_DANGER:
            return """
                <div style='background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 10px; border-radius: 4px; margin-top: 15px;'>
                    <strong>⚠️ BALANCE CRITICAL:</strong> Consider funding account soon. Operations currently proceed unless paused by admin override.
                </div>
                """
        else:
            return """
                <div style='background-color: #d1edff; border: 1px solid #b3d4fc; padding: 10px; border-radius: 4px; margin-top: 15px;'>
                    <strong>✅ STATUS:</strong> Operations proceeding normally at this balance level.
                </div>
                """
    
    async def _send_operation_proceeding_with_low_balance_alert(
        self,
        operation_type: str,
        currency: str,
        amount: Decimal,
        blocking_providers: List[str],
        snapshots: List[BalanceSnapshot]
    ) -> None:
        """
        Send admin notification when operation proceeds despite low balance.
        This provides truthful status information to administrators.
        Cooldown: 12 hours per provider to limit to twice-daily max.
        """
        try:
            # --- 12-hour cooldown check (DB-backed, survives restarts) ---
            cooldown_key = f"op_low_balance_{operation_type}_{currency}"
            should_send = await self.should_send_alert(
                provider=operation_type,
                currency=currency,
                alert_level=AlertLevel.WARNING  # reuse WARNING cooldown (12h)
            )
            if not should_send:
                logger.debug(f"Skipping low-balance operation alert - cooldown active for {cooldown_key}")
                return

            from services.admin_funding_notifications import admin_funding_notifications
            import asyncio
            
            # Determine which service provider has low balance
            service_provider = "Unknown"
            service_balance = "$0.00"
            
            # Extract provider info from blocking providers
            for provider_info in blocking_providers:
                if "kraken" in provider_info.lower():
                    service_provider = "Kraken"
                    service_balance = provider_info.split("(")[1].split(")")[0] if "(" in provider_info else "$0.00"
                elif "fincra" in provider_info.lower():
                    service_provider = "Fincra" 
                    service_balance = provider_info.split("(")[1].split(")")[0] if "(" in provider_info else "₦0.00"
            
            # Generate appropriate cashout ID without misleading "BLOCKED" prefix
            operation_id = f"{operation_type.upper()}_{int(datetime.utcnow().timestamp())}"
            
            # Send async notification with truthful status
            asyncio.create_task(admin_funding_notifications.send_funding_required_alert(
                cashout_id=operation_id,
                service=service_provider,
                amount=float(amount),
                currency=currency,
                user_data={'operation_blocked': False, 'low_balance_warning': True},  # Operation proceeding
                service_currency=currency,
                service_amount=float(amount),
                retry_info={
                    'error_code': 'LOW_PROVIDER_BALANCE_WARNING',
                    'is_auto_retryable': True,  # Operation proceeding, funding recommended
                    'blocking_reason': f"Low {service_provider} balance warning: {service_balance} (operation proceeding)"
                }
            ))
            
            # Record cooldown
            await self.record_alert_sent(
                provider=operation_type,
                currency=currency,
                alert_level=AlertLevel.WARNING
            )
            
            logger.warning(
                f"⚠️ ADMIN_ALERT_SENT: {operation_type} proceeding with low balance - {service_provider} "
                f"balance {service_balance} for {amount} {currency}"
            )
            
        except Exception as e:
            logger.error(f"Failed to send low balance warning alert: {e}")
    
    async def _send_operation_blocked_admin_alert(
        self,
        operation_type: str,
        currency: str,
        amount: Decimal,
        blocking_reason: str,
        snapshots: List[BalanceSnapshot]
    ) -> None:
        """
        Send admin notification when operation is actually blocked by admin override.
        Cooldown: 12 hours per provider to limit to twice-daily max.
        """
        try:
            # --- 12-hour cooldown check (DB-backed, survives restarts) ---
            should_send = await self.should_send_alert(
                provider=f"blocked_{operation_type}",
                currency=currency,
                alert_level=AlertLevel.WARNING  # reuse WARNING cooldown (12h)
            )
            if not should_send:
                logger.debug(f"Skipping operation-blocked alert - cooldown active for blocked_{operation_type}_{currency}")
                return

            from services.admin_funding_notifications import admin_funding_notifications
            import asyncio
            
            # Generate appropriate cashout ID for blocked operation
            operation_id = f"BLOCKED_{operation_type.upper()}_{int(datetime.utcnow().timestamp())}"
            
            # Determine primary service provider based on currency
            service_provider = "System"
            if currency == "NGN" or "ngn" in operation_type.lower():
                service_provider = "Fincra"
            elif currency in ["BTC", "ETH", "LTC", "DOGE", "TRX", "USDT"] or "crypto" in operation_type.lower():
                service_provider = "Kraken"
            
            # Send async notification for actually blocked operation
            asyncio.create_task(admin_funding_notifications.send_funding_required_alert(
                cashout_id=operation_id,
                service=service_provider,
                amount=float(amount),
                currency=currency,
                user_data={'operation_blocked': True, 'admin_override': True},  # Actually blocked
                service_currency=currency,
                service_amount=float(amount),
                retry_info={
                    'error_code': 'ADMIN_OVERRIDE_BLOCK',
                    'is_auto_retryable': False,  # Requires admin intervention
                    'blocking_reason': blocking_reason
                }
            ))
            
            # Record cooldown
            await self.record_alert_sent(
                provider=f"blocked_{operation_type}",
                currency=currency,
                alert_level=AlertLevel.WARNING
            )
            
            logger.error(
                f"🚫 ADMIN_ALERT_SENT: {operation_type} BLOCKED by admin override - "
                f"{amount} {currency} - Reason: {blocking_reason}"
            )
            
        except Exception as e:
            logger.error(f"Failed to send operation blocked alert: {e}")
    
    async def _send_insufficient_balance_admin_alert(
        self,
        operation_type: str,
        currency: str,
        amount: Decimal,
        blocking_providers: List[str],
        snapshots: List[BalanceSnapshot]
    ) -> None:
        """
        LEGACY METHOD: Send admin notification when operation is blocked due to insufficient balance.
        
        ⚠️ DEPRECATED: This method has been replaced by more specific methods:
        - _send_operation_proceeding_with_low_balance_alert() for operations that proceed
        - _send_operation_blocked_admin_alert() for operations that are actually blocked
        
        Kept for backward compatibility.
        """
        logger.warning("DEPRECATED: _send_insufficient_balance_admin_alert called - use specific methods instead")
        
        # Redirect to appropriate new method based on operation logic
        await self._send_operation_proceeding_with_low_balance_alert(
            operation_type, currency, amount, blocking_providers, snapshots
        )
    
    def _generate_html_alert_content(self, snapshot: BalanceSnapshot) -> str:
        """Generate HTML email content for balance alerts"""
        level_colors = {
            AlertLevel.WARNING: "#ffc107",
            AlertLevel.CRITICAL: "#fd7e14",
            AlertLevel.EMERGENCY: "#dc3545",
            AlertLevel.OPERATIONAL_DANGER: "#6f42c1"
        }
        
        color = level_colors.get(snapshot.alert_level, "#6c757d")
        level_name = snapshot.alert_level.name.replace('_', ' ').title()
        
        # Determine currency symbol based on provider
        currency_symbol = '₦' if snapshot.currency == 'NGN' else '$'
        
        return f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <div style="background-color: {color}; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h2 style="margin: 0;">{level_name} Balance Alert</h2>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">{snapshot.provider} - {snapshot.currency}</p>
            </div>
            
            <div style="border: 1px solid {color}; border-top: none; padding: 20px; border-radius: 0 0 8px 8px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 15px;">
                    <strong>Current Balance:</strong>
                    <span style="color: {color}; font-weight: bold;">{snapshot.formatted_balance}</span>
                </div>
                
                <h4 style="margin: 20px 0 10px 0;">Balance Thresholds:</h4>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr><td>Warning (75%):</td><td style="text-align: right;">{currency_symbol}{snapshot.threshold_warning:,.2f}</td></tr>
                    <tr><td>Critical (50%):</td><td style="text-align: right;">{currency_symbol}{snapshot.threshold_critical:,.2f}</td></tr>
                    <tr><td>Emergency (25%):</td><td style="text-align: right;">{currency_symbol}{snapshot.threshold_emergency:,.2f}</td></tr>
                    <tr><td><strong>Operational Min:</strong></td><td style="text-align: right;"><strong>{currency_symbol}{snapshot.threshold_operational:,.2f}</strong></td></tr>
                </table>
                
{self._generate_html_operation_status(snapshot.alert_level)}
                
                <div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #eee; color: #666; font-size: 12px;">
                    Alert Time: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}<br>
                    BalanceGuard Monitoring System - {Config.PLATFORM_NAME}
                </div>
            </div>
        </div>
        """


class BalanceGuard:
    """
    Unified Balance Monitoring System
    
    Single point of control for all balance monitoring across all financial providers.
    Replaces 7 overlapping balance monitoring services with consistent policy and alerts.
    """
    
    def __init__(self):
        self.policy = BalanceGuardPolicy()
        self.notifier = BalanceGuardNotifier(self.policy)
        
        # Initialize providers
        self.providers: List[BalanceProvider] = [
            FincraBalanceProvider(self.policy),
            KrakenBalanceProvider(self.policy)
        ]
        
        # Get thresholds for logging
        fincra_thresholds = self.policy.get_fincra_thresholds()
        kraken_thresholds = self.policy.get_kraken_thresholds()
        
        logger.info(
            f"🛡️ BalanceGuard initialized: {len(self.providers)} providers, "
            f"Fincra threshold=₦{fincra_thresholds['base']:,.2f}, "
            f"Kraken threshold=${kraken_thresholds['base']:,.2f}"
        )
    
    async def _check_admin_override(
        self, 
        provider: str, 
        operation_type: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Check admin override controls for specific provider and operation.
        
        Args:
            provider: Provider name ('kraken', 'fincra', 'all')
            operation_type: Type of operation
            
        Returns:
            Tuple of (operations_allowed, override_reason)
        """
        try:
            with sync_managed_session() as session:
                # Check specific provider+operation override first
                result = session.execute(
                    text("""
                    SELECT override_type, reason, expires_at
                    FROM admin_operation_overrides 
                    WHERE provider = :provider 
                    AND (operation_type = :operation_type OR operation_type IS NULL)
                    AND is_active = true
                    AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY operation_type NULLS LAST, created_at DESC
                    LIMIT 1
                    """),
                    {"provider": provider, "operation_type": operation_type}
                )
                
                row = result.fetchone()
                if row:
                    override_type, reason, expires_at = row
                    
                    # Check if override has expired
                    if expires_at and expires_at <= datetime.now(timezone.utc):
                        # Deactivate expired override
                        session.execute(
                            text("""
                            UPDATE admin_operation_overrides 
                            SET is_active = false, updated_at = NOW()
                            WHERE provider = :provider 
                            AND (operation_type = :operation_type OR operation_type IS NULL)
                            AND is_active = true
                            AND expires_at <= NOW()
                            """),
                            {"provider": provider, "operation_type": operation_type}
                        )
                        session.commit()
                        return True, "No active admin override - operations allowed by default"
                    
                    # Apply override decision
                    if override_type == "pause_operations" or override_type == "emergency_pause":
                        return False, f"Admin override: {reason or 'Operations paused by administrator'}"
                    elif override_type == "allow_operations":
                        return True, f"Admin override: {reason or 'Operations explicitly allowed by administrator'}"
                
                # Check for 'all' provider override if no specific override found
                if provider != 'all':
                    result = session.execute(
                        text("""
                        SELECT override_type, reason, expires_at
                        FROM admin_operation_overrides 
                        WHERE provider = 'all'
                        AND (operation_type = :operation_type OR operation_type IS NULL)
                        AND is_active = true
                        AND (expires_at IS NULL OR expires_at > NOW())
                        ORDER BY operation_type NULLS LAST, created_at DESC
                        LIMIT 1
                        """),
                        {"operation_type": operation_type}
                    )
                    
                    row = result.fetchone()
                    if row:
                        override_type, reason, expires_at = row
                        
                        if override_type == "pause_operations" or override_type == "emergency_pause":
                            return False, f"Admin override (all providers): {reason or 'Operations paused by administrator'}"
                        elif override_type == "allow_operations":
                            return True, f"Admin override (all providers): {reason or 'Operations explicitly allowed by administrator'}"
                
                # Default: allow operations unless specifically overridden
                return True, "No admin override - operations allowed by default"
                
        except Exception as e:
            logger.error(f"Error checking admin override for {provider} {operation_type}: {e}")
            # Default to allowing operations if override check fails
            return True, f"Override check failed - defaulting to allow operations: {e}"
    
    def _get_relevant_providers(self, currency: str, operation_type: str) -> List[str]:
        """Determine which providers are relevant for this operation to optimize performance"""
        relevant_providers = []
        
        # Determine providers based on currency and operation type
        if currency == "NGN" or "ngn" in operation_type.lower():
            relevant_providers.append("fincra")
        
        if currency in ["BTC", "ETH", "LTC", "DOGE", "TRX", "USDT", "USD"] or "crypto" in operation_type.lower():
            relevant_providers.append("kraken")
        
        # Fallback: if no specific providers identified, check both for safety
        if not relevant_providers:
            relevant_providers = ["fincra", "kraken"]
        
        return relevant_providers
    
    async def check_operation_protection(
        self, 
        operation_type: str, 
        currency: str, 
        amount: Decimal
    ) -> ProtectionStatus:
        """
        Check if a financial operation should be allowed based on admin overrides and current balances.
        
        PERFORMANCE OPTIMIZATION LOGIC:
        1. LAZY LOADING: Check admin overrides FIRST - if admin allows/blocks, skip expensive balance checks
        2. CURRENCY-SPECIFIC: Only check target currency balance instead of all monitored currencies
        3. CACHED RESULTS: Reuse balance data within operation context to prevent redundant API calls
        
        PROTECTION LOGIC:
        1. Check admin overrides first - if admin paused operations, block immediately
        2. If no admin override or admin allows: check balance status but allow operations
        3. Send appropriate notifications based on actual system behavior
        
        Args:
            operation_type: Type of operation (cashout, withdrawal, transfer)
            currency: Currency for the operation (NGN, BTC, ETH, etc.)
            amount: Amount being processed
            
        Returns:
            ProtectionStatus with operation allowance and detailed reasoning
        """
        logger.info(
            f"🛡️ BALANCE_GUARD_PROTECTION_CHECK: {operation_type} "
            f"{amount} {currency}"
        )
        
        # PERFORMANCE OPTIMIZATION: Lazy Balance Loading - Check admin overrides FIRST
        # This prevents expensive balance API calls when admin has already made a decision
        
        # Determine relevant providers for this operation BEFORE expensive balance checks
        relevant_providers = self._get_relevant_providers(currency, operation_type)
        
        # Check admin overrides for relevant providers to potentially skip balance checks
        admin_allows_operation = False
        admin_blocks_operation = False
        admin_override_reasons = []
        
        for provider in relevant_providers:
            try:
                override_allowed, override_reason = await self._check_admin_override(provider, operation_type)
                if not override_allowed:
                    admin_blocks_operation = True
                    admin_override_reasons.append(f"{provider}: {override_reason}")
                elif "allow" in override_reason.lower():
                    admin_allows_operation = True
            except Exception as e:
                logger.error(f"Error checking admin override for {provider}: {e}")
        
        # PERFORMANCE WIN: Skip expensive balance checks if admin has blocked operations
        if admin_blocks_operation:
            return ProtectionStatus(
                operation_allowed=False,
                alert_level=None,
                balance_check_passed=False,
                insufficient_services=relevant_providers,
                blocking_providers=relevant_providers,
                warning_providers=[],
                balance_snapshots=[],
                warning_message=None,
                blocking_reason=f"Admin override blocks operation: {', '.join(admin_override_reasons)}",
                protection_reason=f"Admin override blocks operation: {', '.join(admin_override_reasons)}",
                recommendation="Contact administrator to modify operation override settings"
            )
        
        # CRITICAL FIX: Skip expensive balance checks if admin has explicitly allowed operations
        if admin_allows_operation:
            return ProtectionStatus(
                operation_allowed=True,
                alert_level=None,
                balance_check_passed=True,  # Admin allows, so balance check passes by override
                insufficient_services=[],
                blocking_providers=[],
                warning_providers=[],
                balance_snapshots=[],  # No balance data fetched due to admin override
                warning_message=None,
                blocking_reason=None,
                protection_reason=f"Admin override allows operation: {', '.join(admin_override_reasons)}",
                recommendation="Operation allowed by administrator - balance checks bypassed"
            )
        
        # PERFORMANCE OPTIMIZATION: Get currency-specific balance snapshots with caching
        # Only check balances for relevant providers and target currency
        
        # CRITICAL SAFEGUARD: Determine if we need fresh balance data for critical operations
        # Force fresh fetch for large operations or near-threshold conditions
        force_fresh_for_critical = (
            amount >= Decimal("100.0") or  # Large operations need fresh data
            operation_type in ["crypto_withdrawal", "large_cashout", "admin_funding"]  # Critical operations
        )
        
        snapshots = []
        for provider in self.providers:
            # Only fetch balance from relevant providers to avoid unnecessary API calls
            provider_name = provider.get_provider_name().lower()
            if any(rp in provider_name for rp in relevant_providers):
                # PERFORMANCE OPTIMIZATION: Pass target currency for currency-specific balance checks
                if hasattr(provider, 'get_balance_snapshot'):
                    # Check if provider supports currency-specific fetching
                    import inspect
                    sig = inspect.signature(provider.get_balance_snapshot)
                    if 'target_currency' in sig.parameters:
                        # CRITICAL SAFEGUARD: Pass force_fresh_for_critical to providers that support it
                        if 'force_fresh_for_critical' in sig.parameters:
                            snapshot = await provider.get_balance_snapshot(
                                target_currency=currency, 
                                force_fresh_for_critical=force_fresh_for_critical
                            )
                        else:
                            snapshot = await provider.get_balance_snapshot(target_currency=currency)
                    else:
                        # CRITICAL SAFEGUARD: Pass force_fresh_for_critical even for full fetches
                        if 'force_fresh_for_critical' in sig.parameters:
                            snapshot = await provider.get_balance_snapshot(force_fresh_for_critical=force_fresh_for_critical)
                        else:
                            snapshot = await provider.get_balance_snapshot()
                        
                    if snapshot:
                        snapshots.append(snapshot)
                    else:
                        logger.warning(f"Balance retrieval failed for {provider_name}")
        
        if not snapshots:
            logger.error("No balance snapshots available for protection check")
            return ProtectionStatus(
                operation_allowed=False,
                alert_level=None,
                balance_check_passed=False,
                insufficient_services=["all"],
                blocking_providers=["all"],
                warning_providers=[],
                balance_snapshots=[],
                warning_message=None,
                blocking_reason="Unable to verify balances - all providers failed",
                protection_reason="Unable to verify balances - all providers failed",
                recommendation="Check service connectivity and try again"
            )
        
        # Analyze protection requirements
        blocking_providers = []
        warning_providers = []
        highest_alert = None
        
        # Check relevant providers based on operation (optimized since we already filtered)
        for snapshot in snapshots:
            if snapshot.alert_level:
                # Update highest alert level
                if highest_alert is None or snapshot.alert_level.value > highest_alert.value:
                    highest_alert = snapshot.alert_level
                
                # Check if this provider blocks operations
                if snapshot.alert_level == AlertLevel.OPERATIONAL_DANGER:
                    # Provider is already filtered to be relevant, so add to blocking
                    blocking_providers.append(f"{snapshot.provider} ({snapshot.formatted_balance})")
                    logger.warning(f"🚫 BALANCE_BLOCKS: {snapshot.provider} balance too low for safe operations")
                elif snapshot.alert_level in [AlertLevel.EMERGENCY, AlertLevel.CRITICAL]:
                    warning_providers.append(f"{snapshot.provider} ({snapshot.formatted_balance})")
                    logger.warning(f"⚠️ BALANCE_WARNING: {snapshot.provider} balance approaching limits")
        
        # Admin overrides already checked above for performance optimization
        admin_override_blocks = admin_override_reasons if admin_blocks_operation else []
        
        # PROTECTION DECISION LOGIC:
        # 1. Block if admin has explicitly paused operations
        # 2. Block if balance insufficient AND no explicit admin override allowing low balance operations
        # 3. Allow operations only when balance sufficient OR admin override allows despite low balance
        
        # Admin allows operations check was already done during early optimization
        # No need to re-check admin overrides - we already have admin_allows_operation from above
        
        # FINAL DECISION: Optimized decision logic accounting for early admin override checks
        operation_allowed = (
            not admin_blocks_operation and  # No admin pause in effect (checked early)
            (len(blocking_providers) == 0 or admin_allows_operation)  # Sufficient balance OR admin allows
        )
        
        if not operation_allowed:
            if admin_override_blocks:
                logger.error(f"🚫 OPERATION_BLOCKED: Admin pause active: {', '.join(admin_override_blocks)}")
            elif blocking_providers and not admin_allows_operation:
                logger.error(
                    f"🚫 OPERATION_BLOCKED: Insufficient balance and no admin override: "
                    f"{', '.join(blocking_providers)}"
                )
        elif admin_allows_operation and blocking_providers:
            logger.warning(
                f"⚠️ OPERATION_ALLOWED_BY_ADMIN: Low balance detected but admin override active: "
                f"{', '.join(blocking_providers)}"
            )
        
        # Generate reasoning and recommendations
        
        # Send admin notification based on actual operation status
        if blocking_providers and operation_allowed:
            try:
                # Operations proceeding despite low balance - send informational alert
                await self.notifier._send_operation_proceeding_with_low_balance_alert(
                    operation_type=operation_type,
                    currency=currency, 
                    amount=amount,
                    blocking_providers=blocking_providers,
                    snapshots=snapshots
                )
            except Exception as notification_error:
                logger.error(f"Failed to send low balance notification: {notification_error}")
        elif not operation_allowed:
            try:
                # Operation actually blocked by admin override - send blocking alert
                await self.notifier._send_operation_blocked_admin_alert(
                    operation_type=operation_type,
                    currency=currency,
                    amount=amount,
                    blocking_reason=admin_override_reasons[0] if admin_override_reasons else "Admin override",
                    snapshots=snapshots
                )
            except Exception as notification_error:
                logger.error(f"Failed to send operation blocked notification: {notification_error}")
        # Generate protection reasoning based on actual decision
        protection_reason = None
        recommendation = None
        
        if not operation_allowed:
            if admin_override_blocks:
                protection_reason = f"Operation blocked by admin override: {', '.join(admin_override_blocks)}"
                recommendation = "Contact administrator to review operation permissions or check admin email for override options"
            elif blocking_providers:
                protection_reason = f"Operation blocked due to insufficient provider balance: {', '.join(blocking_providers)}"
                recommendation = "Fund provider accounts immediately or contact admin to override if urgent"
        elif blocking_providers and admin_allows_operation:
            protection_reason = f"Operation allowed by admin override despite low balance: {', '.join(blocking_providers)}"
            recommendation = "Operation proceeding due to admin override - consider funding accounts soon"
        elif warning_providers:
            protection_reason = f"Operation allowed with balance warnings: {', '.join(warning_providers)}"
            recommendation = "Consider funding accounts soon to maintain operational flexibility"
        
        # Generate warning message for financial_operation_protection compatibility
        warning_message = None
        if warning_providers:
            warning_message = f"Balance warnings: {', '.join(warning_providers)}"
        
        # Generate blocking reason based on actual protection decision
        blocking_reason = None
        if not operation_allowed:
            if admin_override_blocks:
                blocking_reason = f"Admin has paused operations: {', '.join(admin_override_blocks)}"
            elif blocking_providers:
                blocking_reason = f"Insufficient provider balance - operation blocked for safety: {', '.join(blocking_providers)}"
        elif blocking_providers and admin_allows_operation:
            blocking_reason = f"Low balance detected but admin override allows operation: {', '.join(blocking_providers)}"
        elif blocking_providers:
            blocking_reason = f"Low balance alert (operation would normally be blocked): {', '.join(blocking_providers)}"
        
        result = ProtectionStatus(
            operation_allowed=operation_allowed,
            alert_level=highest_alert,
            balance_check_passed=operation_allowed,  # Truthfully reflect if operation can proceed
            insufficient_services=blocking_providers if not operation_allowed else [],  # Only report as insufficient if actually blocking
            blocking_providers=blocking_providers + admin_override_blocks,  # Include both balance and admin blocks
            warning_providers=warning_providers,
            balance_snapshots=snapshots,
            warning_message=warning_message,
            blocking_reason=blocking_reason,
            protection_reason=protection_reason,
            recommendation=recommendation
        )
        
        # Log protection decision
        status_emoji = "✅" if operation_allowed else "🚫"
        logger.info(
            f"{status_emoji} BALANCE_PROTECTION_DECISION: "
            f"Allow={operation_allowed}, Alert={highest_alert.name if highest_alert else 'None'}, "
            f"Blocking={len(blocking_providers)}, Warning={len(warning_providers)}"
        )
        
        return result
    
    def _is_provider_relevant(self, provider: str, currency: str, operation_type: str) -> bool:
        """Check if a provider is relevant for the given operation"""
        # Fincra is relevant for NGN operations
        if provider == "fincra" and (currency == "NGN" or operation_type in ["ngn_cashout", "ngn_transfer"]):
            return True
        
        # Kraken is relevant for crypto operations
        if provider == "kraken" and (currency in ["BTC", "ETH", "LTC", "DOGE", "TRX", "USDT"] or operation_type in ["crypto_withdrawal", "crypto_cashout"]):
            return True
        
        return False
    
    async def monitor_all_balances(self) -> Dict[str, Any]:
        """
        Run comprehensive balance monitoring across all providers.
        This is the primary entry point for background balance monitoring jobs.
        
        Returns:
            Comprehensive monitoring results with alert status
        """
        logger.info("🛡️ BALANCE_GUARD_MONITORING: Starting comprehensive balance check")
        
        monitoring_start = datetime.utcnow()
        snapshots = []
        alerts_sent = []
        
        # Get balance snapshots from all providers with FRESH data for accurate alerts
        for provider in self.providers:
            try:
                # CRITICAL FIX: Always force fresh balance fetch for monitoring alerts
                # This ensures admin sees current balance, not cached/stale data
                snapshot = await provider.get_balance_snapshot(force_fresh_for_critical=True)
                if snapshot:
                    snapshots.append(snapshot)
                    
                    # Check if alert should be sent
                    if snapshot.alert_level:
                        alert_sent = await self.notifier.send_balance_alert(snapshot)
                        if alert_sent:
                            alerts_sent.append(f"{snapshot.provider}_{snapshot.currency}_{snapshot.alert_level.name}")
                        
                        logger.warning(
                            f"⚠️ BALANCE_ALERT: {snapshot.provider} {snapshot.currency} "
                            f"{snapshot.alert_level.name} - {snapshot.formatted_balance}"
                        )
                
            except Exception as e:
                logger.error(f"Error monitoring {provider.get_provider_name()}: {e}")
        
        # Calculate monitoring summary
        operational_providers = []
        warning_providers = []
        critical_providers = []
        emergency_providers = []
        blocked_providers = []
        
        for snapshot in snapshots:
            provider_name = f"{snapshot.provider}_{snapshot.currency}"
            
            if not snapshot.alert_level:
                operational_providers.append(provider_name)
            elif snapshot.alert_level == AlertLevel.WARNING:
                warning_providers.append(provider_name)
            elif snapshot.alert_level == AlertLevel.CRITICAL:
                critical_providers.append(provider_name)
            elif snapshot.alert_level == AlertLevel.EMERGENCY:
                emergency_providers.append(provider_name)
            elif snapshot.alert_level == AlertLevel.OPERATIONAL_DANGER:
                blocked_providers.append(provider_name)
        
        # Determine overall operational status
        if blocked_providers:
            overall_status = "operations_blocked"
        elif emergency_providers:
            overall_status = "emergency"
        elif critical_providers:
            overall_status = "critical"
        elif warning_providers:
            overall_status = "warning"
        else:
            overall_status = "healthy"
        
        monitoring_duration = (datetime.utcnow() - monitoring_start).total_seconds()
        
        result = {
            'status': 'completed',
            'overall_status': overall_status,
            'monitoring_duration_seconds': monitoring_duration,
            'timestamp': monitoring_start.isoformat(),
            'alerts_sent': alerts_sent,
            'balance_snapshots': [snapshot.to_dict() for snapshot in snapshots],
            'summary': {
                'operational_providers': operational_providers,
                'warning_providers': warning_providers,
                'critical_providers': critical_providers,
                'emergency_providers': emergency_providers,
                'blocked_providers': blocked_providers,
                'total_providers': len(snapshots),
                'total_alerts_sent': len(alerts_sent)
            }
        }
        
        logger.info(
            f"🛡️ BALANCE_GUARD_MONITORING_COMPLETE: "
            f"Status={overall_status}, Providers={len(snapshots)}, "
            f"Alerts={len(alerts_sent)}, Duration={monitoring_duration:.2f}s"
        )
        
        # Log summary by status
        if operational_providers:
            logger.info(f"✅ OPERATIONAL: {', '.join(operational_providers)}")
        if warning_providers:
            logger.warning(f"⚠️ WARNING: {', '.join(warning_providers)}")
        if critical_providers:
            logger.error(f"🔥 CRITICAL: {', '.join(critical_providers)}")
        if emergency_providers:
            logger.critical(f"🚨 EMERGENCY: {', '.join(emergency_providers)}")
        if blocked_providers:
            logger.critical(f"🚫 OPERATIONS_BLOCKED: {', '.join(blocked_providers)}")
        
        return result


# Global singleton instance
balance_guard = BalanceGuard()


# Convenience functions for backward compatibility and easy integration
async def check_operation_protection(operation_type: str, currency: str, amount: Decimal) -> ProtectionStatus:
    """Convenience function for operation protection checks"""
    return await balance_guard.check_operation_protection(operation_type, currency, amount)


async def monitor_all_balances() -> Dict[str, Any]:
    """Convenience function for comprehensive balance monitoring"""
    return await balance_guard.monitor_all_balances()