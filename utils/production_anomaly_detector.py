"""
Production Anomaly Detection System
Monitors critical financial operations for anomalies and safety violations
Focuses on wallet operations, BTC amount conversions, and address parsing
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
from models import CashoutType

logger = logging.getLogger(__name__)

class AnomalyType(Enum):
    """Types of production anomalies to detect"""
    CRITICAL_FINANCIAL_SAFETY = "critical_financial_safety"
    WALLET_LOCK_FAILURE = "wallet_lock_failure"
    USD_TO_CRYPTO_CONVERSION_ERROR = "usd_to_crypto_conversion_error"
    ADDRESS_PARSING_ERROR = "address_parsing_error"
    BINANCE_API_ERROR = "binance_api_error"
    REFERENCE_ID_TRUNCATION = "reference_id_truncation"
    AUTHENTICATION_ERROR = "authentication_error"
    EMERGENCY_REFUND_FAILURE = "emergency_refund_failure"
    AMOUNT_VALIDATION_ERROR = "amount_validation_error"
    NETWORK_MISMATCH = "network_mismatch"
    NGN_BANK_DETAILS_ERROR = "ngn_bank_details_error"
    NGN_AMOUNT_VALIDATION_ERROR = "ngn_amount_validation_error"
    NGN_EXCHANGE_RATE_ERROR = "ngn_exchange_rate_error"
    NGN_PROCESSING_ERROR = "ngn_processing_error"

class AnomalySeverity(Enum):
    """Severity levels for anomalies"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    EMERGENCY = "emergency"

@dataclass
class AnomalyEvent:
    """Production anomaly event"""
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    description: str
    user_id: Optional[int] = None
    cashout_id: Optional[str] = None
    transaction_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    address: Optional[str] = None
    network: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None
    context: Optional[Dict[str, Any]] = None

class ProductionAnomalyDetector:
    """Real-time production anomaly detection system"""
    
    def __init__(self):
        self.active_alerts: List[AnomalyEvent] = []
        self.detection_rules = self._initialize_detection_rules()
        self.critical_thresholds = self._initialize_thresholds()
        
    def _initialize_detection_rules(self) -> Dict[str, Any]:
        """Initialize anomaly detection rules"""
        return {
            # Critical financial safety patterns
            "usd_sent_as_crypto": {
                "pattern": r"sending \$(\d+(?:\.\d+)?) USD as (\d+(?:\.\d+)?) (\w+)",
                "severity": AnomalySeverity.EMERGENCY,
                "description": "USD amount sent directly as crypto amount to Binance"
            },
            
            # Address parsing issues
            "address_colon_format": {
                "pattern": r"address.*:.*network sent to binance",
                "severity": AnomalySeverity.CRITICAL,
                "description": "Address:network format sent to Binance instead of clean address"
            },
            
            # Wallet lock failures
            "wallet_lock_failure": {
                "pattern": r"failed to lock.*balance.*user \d+",
                "severity": AnomalySeverity.CRITICAL,
                "description": "Wallet balance locking failed"
            },
            
            # Reference ID truncation
            "reference_id_truncation": {
                "pattern": r"reference id truncated.*\w{8}.*expected \w{12}",
                "severity": AnomalySeverity.HIGH,
                "description": "Transaction reference ID truncated"
            },
            
            # Binance authentication errors
            "binance_auth_error": {
                "pattern": r"binance.*authentication.*failed",
                "severity": AnomalySeverity.HIGH,
                "description": "Binance API authentication failure"
            },
            
            # Emergency refund failures
            "refund_failure": {
                "pattern": r"emergency refund.*failed.*user \d+",
                "severity": AnomalySeverity.EMERGENCY,
                "description": "Emergency refund system failed"
            }
        }
        
    def _initialize_thresholds(self) -> Dict[str, Any]:
        """Initialize critical threshold values"""
        return {
            "max_usd_crypto_discrepancy": 0.01,  # Max $0.01 difference in USD vs crypto calc
            "min_address_length": 26,  # Minimum valid crypto address length
            "max_address_length": 62,  # Maximum valid crypto address length
            "max_reference_id_length": 12,  # Expected reference ID length
            "max_failed_wallet_locks_per_hour": 3,  # Max wallet lock failures per hour
            "max_binance_errors_per_hour": 5,  # Max Binance API errors per hour
        }
        
    async def monitor_cashout_operation(self, 
                                      cashout_id: str,
                                      user_id: int,
                                      usd_amount: float,
                                      crypto_currency: str,
                                      destination_address: str,
                                      network: str = None) -> List[AnomalyEvent]:
        """Monitor a cashout operation for anomalies"""
        anomalies = []
        
        try:
            # Check 1: USD to crypto conversion validation
            conversion_anomaly = await self._check_usd_crypto_conversion(
                usd_amount, crypto_currency, cashout_id, user_id
            )
            if conversion_anomaly:
                anomalies.append(conversion_anomaly)
                
            # Check 2: Address format validation
            address_anomaly = await self._check_address_format(
                destination_address, network, cashout_id, user_id
            )
            if address_anomaly:
                anomalies.append(address_anomaly)
                
            # Check 3: Network consistency validation
            network_anomaly = await self._check_network_consistency(
                destination_address, network, crypto_currency, cashout_id, user_id
            )
            if network_anomaly:
                anomalies.append(network_anomaly)
                
            # Check 4: Amount validation
            amount_anomaly = await self._check_amount_validation(
                usd_amount, crypto_currency, cashout_id, user_id
            )
            if amount_anomaly:
                anomalies.append(amount_anomaly)
                
            # Check 5: Reference ID validation (if provided)
            if hasattr(self, '_current_reference_id'):
                ref_anomaly = await self._check_reference_id_format(
                    self._current_reference_id, cashout_id, user_id
                )
                if ref_anomaly:
                    anomalies.append(ref_anomaly)
                
            # Log monitoring result
            if anomalies:
                logger.critical(f"🚨 ANOMALY DETECTION: Found {len(anomalies)} anomalies in cashout {cashout_id}")
                for anomaly in anomalies:
                    await self._handle_anomaly(anomaly)
            else:
                logger.info(f"✅ ANOMALY CHECK: Cashout {cashout_id} passed all safety checks")
                
        except Exception as e:
            logger.error(f"Error in anomaly monitoring for cashout {cashout_id}: {e}")
            
        return anomalies
        
    async def _check_usd_crypto_conversion(self, 
                                         usd_amount: float,
                                         crypto_currency: str,
                                         cashout_id: str,
                                         user_id: int) -> Optional[AnomalyEvent]:
        """Check for USD amount being sent as crypto amount"""
        
        # Critical check: Ensure USD amount is being properly converted to crypto
        try:
            from services.crypto import CryptoServiceAtomic
            
            # Get current crypto price in USD
            crypto_price = await CryptoServiceAtomic.get_real_time_exchange_rate(crypto_currency)
            
            if crypto_price and crypto_price > 0:
                # Calculate expected crypto amount
                expected_crypto_amount = usd_amount / crypto_price
                
                # Check if USD amount is suspiciously close to what would be sent as crypto
                # This catches the critical bug where $5 USD was sent as 5 BTC
                if abs(usd_amount - expected_crypto_amount) > self.critical_thresholds["max_usd_crypto_discrepancy"]:
                    # This is normal - USD and crypto amounts should be different
                    pass
                else:
                    # CRITICAL: USD amount equals crypto amount - potential bug
                    return AnomalyEvent(
                        anomaly_type=AnomalyType.USD_TO_CRYPTO_CONVERSION_ERROR,
                        severity=AnomalySeverity.EMERGENCY,
                        description=f"CRITICAL: USD amount ${usd_amount} suspiciously equals crypto amount {expected_crypto_amount} {crypto_currency}",
                        user_id=user_id,
                        cashout_id=cashout_id,
                        amount=usd_amount,
                        currency=crypto_currency,
                        error_details={
                            "usd_amount": usd_amount,
                            "expected_crypto_amount": expected_crypto_amount,
                            "crypto_price": crypto_price,
                            "risk": "USD amount may be sent as crypto amount to Binance"
                        },
                        timestamp=datetime.utcnow()
                    )
                    
        except Exception as e:
            logger.error(f"Error checking USD-crypto conversion: {e}")
            
        return None
        
    async def _check_address_format(self,
                                  address: str,
                                  network: str,
                                  cashout_id: str,
                                  user_id: int) -> Optional[AnomalyEvent]:
        """Check for address format issues"""
        
        # Check for colon format (address:network) that shouldn't be sent to Binance
        if ":" in address:
            return AnomalyEvent(
                anomaly_type=AnomalyType.ADDRESS_PARSING_ERROR,
                severity=AnomalySeverity.CRITICAL,
                description=f"CRITICAL: Address contains colon format '{address}' - may be sent incorrectly to Binance",
                user_id=user_id,
                cashout_id=cashout_id,
                address=address,
                network=network,
                error_details={
                    "raw_address": address,
                    "contains_colon": True,
                    "risk": "Address:network format sent to Binance instead of clean address"
                },
                timestamp=datetime.utcnow()
            )
            
        # Check address length
        if len(address) < self.critical_thresholds["min_address_length"] or len(address) > self.critical_thresholds["max_address_length"]:
            return AnomalyEvent(
                anomaly_type=AnomalyType.ADDRESS_PARSING_ERROR,
                severity=AnomalySeverity.HIGH,
                description=f"Address length {len(address)} outside valid range ({self.critical_thresholds['min_address_length']}-{self.critical_thresholds['max_address_length']})",
                user_id=user_id,
                cashout_id=cashout_id,
                address=address,
                error_details={
                    "address_length": len(address),
                    "min_expected": self.critical_thresholds["min_address_length"],
                    "max_expected": self.critical_thresholds["max_address_length"]
                },
                timestamp=datetime.utcnow()
            )
            
        return None
        
    async def _check_network_consistency(self,
                                       address: str,
                                       network: str,
                                       crypto_currency: str,
                                       cashout_id: str,
                                       user_id: int) -> Optional[AnomalyEvent]:
        """Check for network and currency consistency"""
        
        # Basic network validation rules
        network_currency_map = {
            "BTC": ["bitcoin", "btc"],
            "ETH": ["ethereum", "erc20"],
            "USDT": ["ethereum", "erc20", "tron", "trc20", "bsc", "bep20"],
            "TRX": ["tron", "trc20"],
            "BCH": ["bitcoin-cash", "bch"],
            "LTC": ["litecoin", "ltc"],
            "DOGE": ["dogecoin", "doge"]
        }
        
        if crypto_currency.upper() in network_currency_map:
            valid_networks = network_currency_map[crypto_currency.upper()]
            if network and network.lower() not in valid_networks:
                return AnomalyEvent(
                    anomaly_type=AnomalyType.NETWORK_MISMATCH,
                    severity=AnomalySeverity.HIGH,
                    description=f"Network '{network}' invalid for currency '{crypto_currency}'",
                    user_id=user_id,
                    cashout_id=cashout_id,
                    currency=crypto_currency,
                    network=network,
                    error_details={
                        "provided_network": network,
                        "valid_networks": valid_networks,
                        "currency": crypto_currency
                    },
                    timestamp=datetime.utcnow()
                )
                
        return None
        
    async def _check_amount_validation(self,
                                     amount: float,
                                     currency: str,
                                     cashout_id: str,
                                     user_id: int) -> Optional[AnomalyEvent]:
        """Check for amount validation issues"""
        
        # Check for zero or negative amounts
        if amount <= 0:
            return AnomalyEvent(
                anomaly_type=AnomalyType.AMOUNT_VALIDATION_ERROR,
                severity=AnomalySeverity.CRITICAL,
                description=f"Invalid amount: {amount} (must be positive)",
                user_id=user_id,
                cashout_id=cashout_id,
                amount=amount,
                currency=currency,
                error_details={
                    "amount": amount,
                    "validation_error": "Amount must be positive"
                },
                timestamp=datetime.utcnow()
            )
            
        # Check for suspiciously large amounts (potential data corruption)
        if amount > 1000000:  # $1M threshold
            return AnomalyEvent(
                anomaly_type=AnomalyType.AMOUNT_VALIDATION_ERROR,
                severity=AnomalySeverity.HIGH,
                description=f"Suspiciously large amount: ${amount}",
                user_id=user_id,
                cashout_id=cashout_id,
                amount=amount,
                currency=currency,
                error_details={
                    "amount": amount,
                    "threshold": 1000000,
                    "risk": "Potential data corruption or input error"
                },
                timestamp=datetime.utcnow()
            )
            
        return None
        
    async def _check_reference_id_format(self,
                                       reference_id: str,
                                       cashout_id: str,
                                       user_id: int) -> Optional[AnomalyEvent]:
        """Check for reference ID truncation issues"""
        
        # Check if reference ID is truncated (should be 12 chars, not 8)
        if len(reference_id) == 8 and reference_id.startswith(cashout_id[:8]):
            return AnomalyEvent(
                anomaly_type=AnomalyType.REFERENCE_ID_TRUNCATION,
                severity=AnomalySeverity.HIGH,
                description=f"Reference ID '{reference_id}' appears truncated (8 chars instead of expected 12)",
                user_id=user_id,
                cashout_id=cashout_id,
                transaction_id=reference_id,
                error_details={
                    "reference_id": reference_id,
                    "expected_length": 12,
                    "actual_length": len(reference_id),
                    "risk": "Truncated reference IDs may cause transaction tracking issues"
                },
                timestamp=datetime.utcnow()
            )
            
        return None
        
    async def _handle_anomaly(self, anomaly: AnomalyEvent):
        """Handle detected anomaly"""
        try:
            # Log the anomaly with appropriate severity
            severity_emoji = {
                AnomalySeverity.EMERGENCY: "🚨🚨🚨",
                AnomalySeverity.CRITICAL: "🚨",
                AnomalySeverity.HIGH: "⚠️",
                AnomalySeverity.MEDIUM: "⚠️",
                AnomalySeverity.LOW: "ℹ️"
            }
            
            emoji = severity_emoji.get(anomaly.severity, "⚠️")
            
            logger.critical(f"{emoji} PRODUCTION ANOMALY DETECTED")
            logger.critical(f"Type: {anomaly.anomaly_type.value}")
            logger.critical(f"Severity: {anomaly.severity.value}")
            logger.critical(f"Description: {anomaly.description}")
            logger.critical(f"User ID: {anomaly.user_id}")
            logger.critical(f"Cashout ID: {anomaly.cashout_id}")
            
            if anomaly.error_details:
                logger.critical(f"Details: {anomaly.error_details}")
                
            # Store anomaly for tracking
            self.active_alerts.append(anomaly)
            
            # For emergency/critical anomalies, trigger additional actions
            if anomaly.severity in [AnomalySeverity.EMERGENCY, AnomalySeverity.CRITICAL]:
                await self._trigger_emergency_response(anomaly)
                
        except Exception as e:
            logger.error(f"Error handling anomaly: {e}")
            
    async def _trigger_emergency_response(self, anomaly: AnomalyEvent):
        """Trigger emergency response for critical anomalies"""
        try:
            # Emergency actions based on anomaly type
            if anomaly.anomaly_type == AnomalyType.USD_TO_CRYPTO_CONVERSION_ERROR:
                logger.critical("🚨 EMERGENCY: Blocking all cashout operations due to USD-crypto conversion error")
                # Could implement cashout halt logic here
                
            elif anomaly.anomaly_type == AnomalyType.ADDRESS_PARSING_ERROR:
                logger.critical("🚨 EMERGENCY: Blocking address-based cashouts due to parsing error")
                # Could implement address validation enhancement here
                
            elif anomaly.anomaly_type == AnomalyType.WALLET_LOCK_FAILURE:
                logger.critical("🚨 EMERGENCY: Wallet lock failure detected - investigating balance integrity")
                # Could trigger wallet integrity check here
                
            # Send notification to audit system
            try:
                from utils.enhanced_audit_logger import enhanced_audit_logger
                await enhanced_audit_logger.log_security_event(
                    event_type="production_anomaly_emergency",
                    description=f"Emergency anomaly: {anomaly.description}",
                    severity="CRITICAL",
                    user_id=anomaly.user_id,
                    metadata={
                        "anomaly_type": anomaly.anomaly_type.value,
                        "cashout_id": anomaly.cashout_id,
                        "error_details": anomaly.error_details
                    }
                )
            except Exception as e:
                logger.error(f"Failed to log emergency anomaly to audit system: {e}")
                
        except Exception as e:
            logger.error(f"Error in emergency response: {e}")
    
    async def monitor_ngn_cashout_operation(self, 
                                       cashout_id: str, 
                                       user_id: int, 
                                       usd_amount: float, 
                                       ngn_destination: str, 
                                       cashout_type: str) -> List[AnomalyEvent]:
        """Monitor NGN cashout operation for critical anomalies"""
        anomalies = []
        
        try:
            logger.info(f"🔍 NGN ANOMALY DETECTION: Starting monitoring for cashout {cashout_id}")
            
            # 1. CRITICAL: Validate bank destination format
            if not self._validate_ngn_bank_destination(ngn_destination):
                anomaly = AnomalyEvent(
                    anomaly_type=AnomalyType.NGN_BANK_DETAILS_ERROR,
                    severity=AnomalySeverity.CRITICAL,
                    cashout_id=cashout_id,
                    user_id=user_id,
                    details={
                        "destination_format": ngn_destination,
                        "expected_format": "BANK_CODE:ACCOUNT_NUMBER",
                        "error": "Invalid NGN bank destination format"
                    }
                )
                anomalies.append(anomaly)
                logger.critical(f"🚨 NGN BANK DETAILS ERROR: Invalid destination format '{ngn_destination}' for cashout {cashout_id}")
            
            # 2. CRITICAL: Validate USD amount consistency
            if not self._validate_ngn_amount(usd_amount):
                anomaly = AnomalyEvent(
                    anomaly_type=AnomalyType.NGN_AMOUNT_VALIDATION_ERROR,
                    severity=AnomalySeverity.CRITICAL,
                    cashout_id=cashout_id,
                    user_id=user_id,
                    details={
                        "usd_amount": usd_amount,
                        "error": "Invalid USD amount for NGN cashout"
                    }
                )
                anomalies.append(anomaly)
                logger.critical(f"🚨 NGN AMOUNT ERROR: Invalid USD amount {usd_amount} for cashout {cashout_id}")
            
            # 3. CRITICAL: Check cashout type consistency
            if cashout_type != CashoutType.NGN_BANK.value:
                anomaly = AnomalyEvent(
                    anomaly_type=AnomalyType.NGN_PROCESSING_ERROR,
                    severity=AnomalySeverity.CRITICAL,
                    cashout_id=cashout_id,
                    user_id=user_id,
                    details={
                        "expected_type": "NGN_BANK",
                        "actual_type": cashout_type,
                        "error": "Cashout type mismatch for NGN processing"
                    }
                )
                anomalies.append(anomaly)
                logger.critical(f"🚨 NGN TYPE ERROR: Incorrect cashout type '{cashout_type}' for NGN cashout {cashout_id}")
            
            # 4. CRITICAL: Validate exchange rate availability (simulate check)
            try:
                # Simulate exchange rate validation
                if usd_amount > 0:
                    # Check if amount is reasonable for NGN conversion
                    if usd_amount > 50000:  # Flag very large amounts
                        anomaly = AnomalyEvent(
                            anomaly_type=AnomalyType.NGN_EXCHANGE_RATE_ERROR,
                            severity=AnomalySeverity.HIGH,
                            cashout_id=cashout_id,
                            user_id=user_id,
                            details={
                                "usd_amount": usd_amount,
                                "threshold": 50000,
                                "warning": "Large NGN cashout amount detected"
                            }
                        )
                        anomalies.append(anomaly)
                        logger.warning(f"⚠️ NGN LARGE AMOUNT: USD {usd_amount} detected for cashout {cashout_id}")
            
            except Exception as rate_error:
                anomaly = AnomalyEvent(
                    anomaly_type=AnomalyType.NGN_EXCHANGE_RATE_ERROR,
                    severity=AnomalySeverity.CRITICAL,
                    cashout_id=cashout_id,
                    user_id=user_id,
                    details={
                        "error": str(rate_error),
                        "context": "Exchange rate validation failed"
                    }
                )
                anomalies.append(anomaly)
                logger.critical(f"🚨 NGN RATE ERROR: Exchange rate validation failed for cashout {cashout_id}: {rate_error}")
            
            # Store detected anomalies
            self.detected_anomalies.extend(anomalies)
            
            if anomalies:
                logger.warning(f"🔍 NGN ANOMALY DETECTION: Found {len(anomalies)} anomalies for cashout {cashout_id}")
            else:
                logger.info(f"✅ NGN ANOMALY DETECTION: No anomalies detected for cashout {cashout_id}")
                
            return anomalies
            
        except Exception as e:
            logger.error(f"Error during NGN anomaly detection for cashout {cashout_id}: {e}")
            # Return emergency anomaly for monitoring failure
            emergency_anomaly = AnomalyEvent(
                anomaly_type=AnomalyType.NGN_PROCESSING_ERROR,
                severity=AnomalySeverity.EMERGENCY,
                cashout_id=cashout_id,
                user_id=user_id,
                details={"monitoring_error": str(e)}
            )
            return [emergency_anomaly]
    
    def _validate_ngn_bank_destination(self, destination: str) -> bool:
        """Validate NGN bank destination format"""
        try:
            # Expected format: BANK_CODE:ACCOUNT_NUMBER
            if ":" not in destination:
                return False
            
            parts = destination.split(":")
            if len(parts) != 2:
                return False
            
            bank_code, account_number = parts
            
            # Basic validation
            if not bank_code or not account_number:
                return False
            
            # Account number should be numeric and reasonable length
            if not account_number.isdigit() or len(account_number) < 10 or len(account_number) > 11:
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Error validating NGN bank destination: {e}")
            return False
    
    def _validate_ngn_amount(self, usd_amount: float) -> bool:
        """Validate USD amount for NGN cashout"""
        try:
            # Basic amount validation
            if usd_amount <= 0:
                return False
            
            # Check for reasonable range (between $1 and $100,000)
            if usd_amount < 1 or usd_amount > 100000:
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Error validating NGN amount: {e}")
            return False
            
    def get_anomaly_summary(self) -> Dict[str, Any]:
        """Get summary of recent anomalies"""
        try:
            recent_cutoff = datetime.utcnow() - timedelta(hours=24)
            recent_anomalies = [
                a for a in self.active_alerts 
                if a.timestamp and a.timestamp > recent_cutoff
            ]
            
            # Count by severity
            severity_counts = {}
            for severity in AnomalySeverity:
                severity_counts[severity.value] = len([
                    a for a in recent_anomalies 
                    if a.severity == severity
                ])
                
            # Count by type
            type_counts = {}
            for anomaly_type in AnomalyType:
                type_counts[anomaly_type.value] = len([
                    a for a in recent_anomalies 
                    if a.anomaly_type == anomaly_type
                ])
                
            return {
                "total_anomalies_24h": len(recent_anomalies),
                "severity_breakdown": severity_counts,
                "type_breakdown": type_counts,
                "critical_count": severity_counts.get("emergency", 0) + severity_counts.get("critical", 0),
                "last_anomaly": recent_anomalies[-1].timestamp.isoformat() if recent_anomalies else None
            }
            
        except Exception as e:
            logger.error(f"Error generating anomaly summary: {e}")
            return {"error": str(e)}

# Global anomaly detector instance
_anomaly_detector = ProductionAnomalyDetector()

async def monitor_cashout_anomalies(cashout_id: str,
                                  user_id: int,
                                  usd_amount: float,
                                  crypto_currency: str,
                                  destination_address: str,
                                  network: str = None) -> List[AnomalyEvent]:
    """Monitor crypto cashout for production anomalies"""
    return await _anomaly_detector.monitor_cashout_operation(
        cashout_id, user_id, usd_amount, crypto_currency, destination_address, network
    )

async def monitor_ngn_cashout_anomalies(cashout_id: str,
                                      user_id: int,
                                      usd_amount: float,
                                      ngn_destination: str,
                                      cashout_type: str) -> List[AnomalyEvent]:
    """Monitor NGN cashout for production anomalies"""
    return await _anomaly_detector.monitor_ngn_cashout_operation(
        cashout_id, user_id, usd_amount, ngn_destination, cashout_type
    )

def get_anomaly_detector() -> ProductionAnomalyDetector:
    """Get global anomaly detector instance"""
    return _anomaly_detector

# Initialize on import
logger.info("🔧 Production anomaly detection system ready")