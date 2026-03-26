"""
Unified Error Classification Service
Determines whether cashout/exchange failures are technical (retryable) or user errors (refundable)
"""

import logging
import re
from typing import Tuple, Optional, Dict, Any
from datetime import datetime
from models import OperationFailureType, CashoutFailureType, CashoutErrorCode

logger = logging.getLogger(__name__)


class UnifiedErrorClassifier:
    """Classifies cashout and exchange errors for intelligent retry logic"""
    
    # Map exception types/messages to error codes
    TECHNICAL_ERROR_PATTERNS = {
        # Kraken errors (Cashouts + Exchanges)
        r"address.*not.*found|no.*address.*found": CashoutErrorCode.KRAKEN_ADDR_NOT_FOUND,
        r"address.*not.*configured|not.*configured.*address": CashoutErrorCode.KRAKEN_ADDR_NOT_FOUND,
        r"invalid.*key|unknown.*withdraw.*key": CashoutErrorCode.KRAKEN_ADDR_NOT_FOUND,
        r"address.*not.*verified|not.*verified.*address": CashoutErrorCode.KRAKEN_ADDR_NOT_FOUND,
        r"kraken.*timeout|kraken.*timed.*out": CashoutErrorCode.KRAKEN_API_TIMEOUT,
        r"kraken.*api.*error|kraken.*error": CashoutErrorCode.KRAKEN_API_ERROR,
        
        # Fincra errors (Cashouts + Exchanges)
        r"fincra.*timeout|fincra.*timed.*out": CashoutErrorCode.FINCRA_API_TIMEOUT,
        r"fincra.*api.*error|fincra.*error": CashoutErrorCode.FINCRA_API_ERROR,
        r"NO_ENOUGH_MONEY_IN_WALLET|insufficient.*funds.*wallet": CashoutErrorCode.FINCRA_INSUFFICIENT_FUNDS,
        
        # Exchange-specific rate/API errors
        r"exchange.*rate.*error|rate.*fetch.*failed": CashoutErrorCode.EXCHANGE_RATE_ERROR,
        r"fastforex.*timeout|fastforex.*error": CashoutErrorCode.EXCHANGE_RATE_ERROR,
        r"blockbee.*timeout|blockbee.*error": CashoutErrorCode.NETWORK_ERROR,
        
        # Network/SSL errors
        r"ssl.*error|ssl.*failed|certificate.*error": CashoutErrorCode.SSL_ERROR,
        r"network.*error|connection.*error|timeout": CashoutErrorCode.NETWORK_ERROR,
        r"service.*unavailable|502|503|504": CashoutErrorCode.SERVICE_UNAVAILABLE,
        r"rate.*limit|too.*many.*requests|429": CashoutErrorCode.RATE_LIMIT_EXCEEDED,
        
        # Database/parsing errors
        r"metadata.*object.*has.*no.*attribute|metadata.*parse": CashoutErrorCode.METADATA_PARSE_ERROR,
        r"database.*error|connection.*pool|psycopg": CashoutErrorCode.DATABASE_ERROR,
        
        # Circuit breaker
        r"circuit.*breaker.*open": CashoutErrorCode.CIRCUIT_BREAKER_OPEN,
        
        # Phase 2: Generic API Integration error patterns (standardized for all external providers)
        r"api.*timeout|request.*timeout|connection.*timeout": CashoutErrorCode.API_TIMEOUT,
        r"401|unauthorized|authentication.*failed|invalid.*credentials": CashoutErrorCode.API_AUTHENTICATION_FAILED,
        r"403|forbidden|access.*denied|permission.*denied": CashoutErrorCode.API_AUTHENTICATION_FAILED,
        r"insufficient.*balance|insufficient.*funds|balance.*too.*low": CashoutErrorCode.API_INSUFFICIENT_FUNDS,
        
        # Exchange confirmation/processing errors (retryable)
        r"exchange.*confirmation.*failed|confirmation.*retry": CashoutErrorCode.EXCHANGE_PROCESSING_ERROR,
        r"exchange.*processing.*failed|processing.*error": CashoutErrorCode.EXCHANGE_PROCESSING_ERROR,
        
        # Escrow-specific technical errors (retryable)
        r"address.*generation.*failed|failed.*generate.*address": CashoutErrorCode.ESCROW_ADDRESS_GENERATION_FAILED,
        r"blockbee.*address.*error|address.*service.*timeout": CashoutErrorCode.ESCROW_ADDRESS_GENERATION_FAILED,
        r"deposit.*confirmation.*timeout|confirmation.*delayed": CashoutErrorCode.ESCROW_DEPOSIT_CONFIRMATION_TIMEOUT,
        r"webhook.*delayed|webhook.*timeout|webhook.*failed": CashoutErrorCode.ESCROW_DEPOSIT_CONFIRMATION_TIMEOUT,
        r"escrow.*release.*failed|release.*error|refund.*failed": CashoutErrorCode.ESCROW_RELEASE_REFUND_FAILED,
        
        # Deposit-specific technical errors (retryable)
        r"webhook.*processing.*failed|webhook.*error": CashoutErrorCode.DEPOSIT_WEBHOOK_PROCESSING_FAILED,
        r"dynopay.*webhook.*failed|blockbee.*webhook.*error": CashoutErrorCode.DEPOSIT_WEBHOOK_PROCESSING_FAILED,
        r"confirmation.*polling.*failed|blockchain.*rpc.*lag": CashoutErrorCode.DEPOSIT_CONFIRMATION_POLLING_FAILED,
        r"wallet.*credit.*deadlock|database.*contention": CashoutErrorCode.WALLET_CREDIT_DEADLOCK,
        r"balance.*update.*failed|atomic.*operation.*failed": CashoutErrorCode.WALLET_CREDIT_DEADLOCK,
        
        # Phase 3: Notification technical errors (retryable)
        r"smtp.*timeout|email.*timeout|brevo.*timeout": CashoutErrorCode.NOTIFICATION_EMAIL_TIMEOUT,
        r"twilio.*timeout|sms.*timeout|phone.*timeout": CashoutErrorCode.NOTIFICATION_SMS_TIMEOUT,
        r"telegram.*timeout|bot.*api.*timeout|telegram.*api.*error": CashoutErrorCode.NOTIFICATION_TELEGRAM_TIMEOUT,
        r"notification.*rate.*limit|too.*many.*notifications|429.*notification": CashoutErrorCode.NOTIFICATION_RATE_LIMITED,
        r"notification.*service.*unavailable|email.*service.*down|sms.*service.*down": CashoutErrorCode.NOTIFICATION_SERVICE_UNAVAILABLE,
        
        # Phase 3: Wallet operation technical errors (retryable)
        r"wallet.*deadlock|balance.*deadlock|transaction.*deadlock": CashoutErrorCode.WALLET_DEADLOCK_ERROR,
        r"wallet.*connection.*timeout|database.*connection.*timeout|db.*timeout": CashoutErrorCode.WALLET_CONNECTION_TIMEOUT,
        r"wallet.*transaction.*conflict|concurrent.*transaction|balance.*conflict": CashoutErrorCode.WALLET_TRANSACTION_CONFLICT,
        
        # Phase 3: Admin operation technical errors (retryable)
        r"admin.*provider.*timeout|funding.*timeout|admin.*api.*timeout": CashoutErrorCode.ADMIN_PROVIDER_TIMEOUT,
        r"admin.*email.*delivery.*failed|admin.*notification.*failed": CashoutErrorCode.ADMIN_EMAIL_DELIVERY_FAILED,
        r"admin.*authentication.*failed|2fa.*failed|admin.*auth.*error": CashoutErrorCode.ADMIN_AUTHENTICATION_FAILED,
        r"service.*locked|maintenance.*mode|admin.*service.*unavailable": CashoutErrorCode.ADMIN_SERVICE_LOCKED,
    }
    
    USER_ERROR_PATTERNS = {
        # Funds/amounts
        r"insufficient.*funds|balance.*too.*low": CashoutErrorCode.INSUFFICIENT_FUNDS,
        r"invalid.*amount|amount.*too.*small|amount.*too.*large": CashoutErrorCode.INVALID_AMOUNT,
        r"minimum.*amount|min.*amount": CashoutErrorCode.MIN_AMOUNT_NOT_MET,
        r"maximum.*amount|max.*amount": CashoutErrorCode.MAX_AMOUNT_EXCEEDED,
        
        # Address validation
        r"invalid.*address|address.*format|address.*validation": CashoutErrorCode.INVALID_ADDRESS,
        
        # Phase 2: API User Error patterns (non-retryable user errors)
        r"400|bad.*request|invalid.*request|malformed.*request": CashoutErrorCode.API_INVALID_REQUEST,
        
        # Account issues
        r"account.*frozen|account.*suspended": CashoutErrorCode.ACCOUNT_FROZEN,
        r"sanctions|blocked.*country|prohibited": CashoutErrorCode.SANCTIONS_BLOCKED,
        r"currency.*not.*supported|unsupported.*currency": CashoutErrorCode.CURRENCY_NOT_SUPPORTED,
        
        # Phase 3: Notification user errors (non-retryable)
        r"invalid.*email|email.*format.*invalid|invalid.*phone|phone.*format.*invalid": CashoutErrorCode.NOTIFICATION_INVALID_RECIPIENT,
        
        # Phase 3: Wallet operation user errors (non-retryable)
        r"wallet.*insufficient.*balance|insufficient.*wallet.*funds": CashoutErrorCode.WALLET_INSUFFICIENT_BALANCE,
        r"wallet.*invalid.*amount|invalid.*wallet.*amount": CashoutErrorCode.WALLET_INVALID_AMOUNT,
        r"wallet.*locked|wallet.*frozen|account.*locked": CashoutErrorCode.WALLET_LOCKED,
        
        # Phase 3: Admin operation user errors (non-retryable)
        r"admin.*permission.*denied|access.*denied.*admin|admin.*access.*denied": CashoutErrorCode.ADMIN_PERMISSION_DENIED,
        r"admin.*invalid.*input|admin.*validation.*error|invalid.*admin.*request": CashoutErrorCode.ADMIN_INVALID_INPUT,
    }
    
    # Retry configuration based on error type
    RETRY_CONFIG = {
        # Technical errors - aggressive retry
        CashoutErrorCode.KRAKEN_ADDR_NOT_FOUND: {
            "max_retries": 5,
            "backoff_delays": [60, 300, 900, 1800, 3600],  # 1min, 5min, 15min, 30min, 1hour
            "retryable": True
        },
        CashoutErrorCode.KRAKEN_API_TIMEOUT: {
            "max_retries": 3,
            "backoff_delays": [300, 900, 1800],  # 5min, 15min, 30min
            "retryable": True
        },
        CashoutErrorCode.NETWORK_ERROR: {
            "max_retries": 4,
            "backoff_delays": [120, 600, 1200, 2400],  # 2min, 10min, 20min, 40min
            "retryable": True
        },
        CashoutErrorCode.SSL_ERROR: {
            "max_retries": 3,
            "backoff_delays": [180, 900, 1800],  # 3min, 15min, 30min
            "retryable": True
        },
        CashoutErrorCode.SERVICE_UNAVAILABLE: {
            "max_retries": 4,
            "backoff_delays": [300, 600, 1200, 2400],  # 5min, 10min, 20min, 40min
            "retryable": True
        },
        CashoutErrorCode.RATE_LIMIT_EXCEEDED: {
            "max_retries": 3,
            "backoff_delays": [600, 1800, 3600],  # 10min, 30min, 1hour
            "retryable": True
        },
        CashoutErrorCode.METADATA_PARSE_ERROR: {
            "max_retries": 2,
            "backoff_delays": [300, 900],  # 5min, 15min
            "retryable": True
        },
        CashoutErrorCode.CIRCUIT_BREAKER_OPEN: {
            "max_retries": 2,
            "backoff_delays": [1800, 3600],  # 30min, 1hour
            "retryable": True
        },
        
        # Exchange-specific error configurations
        CashoutErrorCode.FINCRA_INSUFFICIENT_FUNDS: {
            "max_retries": 5,
            "backoff_delays": [300, 900, 1800, 3600, 7200],  # 5min, 15min, 30min, 1hour, 2hours
            "retryable": True  # Admin can top up and retry
        },
        CashoutErrorCode.EXCHANGE_RATE_ERROR: {
            "max_retries": 4,
            "backoff_delays": [60, 300, 900, 1800],  # 1min, 5min, 15min, 30min
            "retryable": True  # Rate services often recover quickly
        },
        CashoutErrorCode.EXCHANGE_PROCESSING_ERROR: {
            "max_retries": 3,
            "backoff_delays": [120, 600, 1800],  # 2min, 10min, 30min
            "retryable": True  # Processing issues often temporary
        },
        
        # Phase 2: Generic API Integration error configurations
        CashoutErrorCode.API_TIMEOUT: {
            "max_retries": 4,
            "backoff_delays": [60, 300, 900, 1800],  # 1min, 5min, 15min, 30min
            "retryable": True  # Timeouts are typically transient
        },
        CashoutErrorCode.API_AUTHENTICATION_FAILED: {
            "max_retries": 2,
            "backoff_delays": [300, 1800],  # 5min, 30min
            "retryable": True  # May recover if API keys are rotated
        },
        CashoutErrorCode.API_INSUFFICIENT_FUNDS: {
            "max_retries": 6,
            "backoff_delays": [300, 900, 1800, 3600, 7200, 14400],  # 5min, 15min, 30min, 1hr, 2hr, 4hr
            "retryable": True  # Admin can top up funds and retry
        },
        
        # User errors - no retry
        CashoutErrorCode.INSUFFICIENT_FUNDS: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.INVALID_ADDRESS: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.INVALID_AMOUNT: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.SANCTIONS_BLOCKED: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.ACCOUNT_FROZEN: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.API_INVALID_REQUEST: {"max_retries": 0, "retryable": False},
        
        # Escrow-specific error configurations
        CashoutErrorCode.ESCROW_ADDRESS_GENERATION_FAILED: {
            "max_retries": 4,
            "backoff_delays": [180, 600, 1200, 2400],  # 3min, 10min, 20min, 40min
            "retryable": True  # Address generation often recovers after API issues
        },
        CashoutErrorCode.ESCROW_DEPOSIT_CONFIRMATION_TIMEOUT: {
            "max_retries": 6,
            "backoff_delays": [300, 600, 1200, 1800, 3600, 7200],  # 5min, 10min, 20min, 30min, 1hr, 2hr
            "retryable": True  # Blockchain confirmations can be delayed
        },
        CashoutErrorCode.ESCROW_RELEASE_REFUND_FAILED: {
            "max_retries": 5,
            "backoff_delays": [300, 900, 1800, 3600, 7200],  # 5min, 15min, 30min, 1hr, 2hr
            "retryable": True  # Critical to retry release/refund operations
        },
        
        # Deposit-specific error configurations
        CashoutErrorCode.DEPOSIT_WEBHOOK_PROCESSING_FAILED: {
            "max_retries": 4,
            "backoff_delays": [120, 600, 1200, 2400],  # 2min, 10min, 20min, 40min
            "retryable": True  # Webhook processing often recovers
        },
        CashoutErrorCode.DEPOSIT_CONFIRMATION_POLLING_FAILED: {
            "max_retries": 3,
            "backoff_delays": [300, 900, 1800],  # 5min, 15min, 30min
            "retryable": True  # Blockchain RPC lag is temporary
        },
        CashoutErrorCode.WALLET_CREDIT_DEADLOCK: {
            "max_retries": 5,
            "backoff_delays": [60, 120, 300, 600, 1200],  # 1min, 2min, 5min, 10min, 20min
            "retryable": True  # Database contention resolves quickly
        },
        
        # Phase 3: Notification error configurations
        CashoutErrorCode.NOTIFICATION_EMAIL_TIMEOUT: {
            "max_retries": 4,
            "backoff_delays": [60, 300, 900, 1800],  # 1min, 5min, 15min, 30min
            "retryable": True
        },
        CashoutErrorCode.NOTIFICATION_SMS_TIMEOUT: {
            "max_retries": 3,
            "backoff_delays": [120, 600, 1800],  # 2min, 10min, 30min
            "retryable": True
        },
        CashoutErrorCode.NOTIFICATION_TELEGRAM_TIMEOUT: {
            "max_retries": 5,
            "backoff_delays": [30, 120, 300, 900, 1800],  # 30sec, 2min, 5min, 15min, 30min
            "retryable": True  # Telegram is often fastest to recover
        },
        CashoutErrorCode.NOTIFICATION_RATE_LIMITED: {
            "max_retries": 3,
            "backoff_delays": [900, 1800, 3600],  # 15min, 30min, 1hour
            "retryable": True  # Rate limits have long recovery times
        },
        CashoutErrorCode.NOTIFICATION_SERVICE_UNAVAILABLE: {
            "max_retries": 4,
            "backoff_delays": [300, 900, 1800, 3600],  # 5min, 15min, 30min, 1hour
            "retryable": True
        },
        
        # Phase 3: Wallet operation error configurations
        CashoutErrorCode.WALLET_DEADLOCK_ERROR: {
            "max_retries": 5,
            "backoff_delays": [30, 60, 120, 300, 600],  # 30sec, 1min, 2min, 5min, 10min
            "retryable": True  # Database deadlocks resolve quickly
        },
        CashoutErrorCode.WALLET_CONNECTION_TIMEOUT: {
            "max_retries": 4,
            "backoff_delays": [60, 180, 600, 1200],  # 1min, 3min, 10min, 20min
            "retryable": True
        },
        CashoutErrorCode.WALLET_TRANSACTION_CONFLICT: {
            "max_retries": 6,
            "backoff_delays": [15, 30, 60, 120, 300, 600],  # 15sec, 30sec, 1min, 2min, 5min, 10min
            "retryable": True  # Conflicts resolve very quickly
        },
        
        # Phase 3: Admin operation error configurations
        CashoutErrorCode.ADMIN_PROVIDER_TIMEOUT: {
            "max_retries": 4,
            "backoff_delays": [300, 900, 1800, 3600],  # 5min, 15min, 30min, 1hour
            "retryable": True  # Admin operations are critical
        },
        CashoutErrorCode.ADMIN_EMAIL_DELIVERY_FAILED: {
            "max_retries": 3,
            "backoff_delays": [300, 900, 1800],  # 5min, 15min, 30min
            "retryable": True
        },
        CashoutErrorCode.ADMIN_AUTHENTICATION_FAILED: {
            "max_retries": 2,
            "backoff_delays": [900, 3600],  # 15min, 1hour
            "retryable": True  # May recover if auth tokens refresh
        },
        CashoutErrorCode.ADMIN_SERVICE_LOCKED: {
            "max_retries": 6,
            "backoff_delays": [600, 1200, 1800, 3600, 7200, 10800],  # 10min, 20min, 30min, 1hr, 2hr, 3hr
            "retryable": True  # Maintenance windows eventually end
        },
        
        # Phase 3: User errors - no retry
        CashoutErrorCode.NOTIFICATION_INVALID_RECIPIENT: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.WALLET_INSUFFICIENT_BALANCE: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.WALLET_INVALID_AMOUNT: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.WALLET_LOCKED: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.ADMIN_PERMISSION_DENIED: {"max_retries": 0, "retryable": False},
        CashoutErrorCode.ADMIN_INVALID_INPUT: {"max_retries": 0, "retryable": False},
        
        # Default for unknown errors
        CashoutErrorCode.UNKNOWN_ERROR: {
            "max_retries": 1,
            "backoff_delays": [600],  # 10min
            "retryable": True
        }
    }
    
    @classmethod
    def classify_error(cls, 
                      exception: Exception, 
                      context: Optional[Dict[str, Any]] = None) -> Tuple[CashoutFailureType, CashoutErrorCode, bool, int]:
        """
        Classify a cashout error and determine retry strategy
        
        Args:
            exception: The exception that occurred
            context: Additional context (cashout_id, service, etc.)
        
        Returns:
            Tuple of (failure_type, error_code, retryable, recommended_delay_seconds)
        """
        error_message = str(exception).lower()
        exception_type = type(exception).__name__
        
        # Enhanced classification logging with structured data
        classification_start = datetime.utcnow()
        classification_context = {
            "exception_type": exception_type,
            "error_message_length": len(error_message),
            "has_context": bool(context),
            "context_keys": list(context.keys()) if context else [],
            "classification_timestamp": classification_start.isoformat()
        }
        
        logger.info(f"🔍 CLASSIFICATION_START: {exception_type}: {error_message[:100]}...", extra=classification_context)
        if context:
            logger.debug(f"🔍 CLASSIFICATION_CONTEXT: {context}", extra=classification_context)
        
        # Check for specific error patterns
        error_code = cls._detect_error_code(error_message, exception_type)
        
        # Log pattern detection result
        detection_data = {
            "detected_error_code": error_code.value,
            "detection_method": "pattern_match" if error_code != CashoutErrorCode.UNKNOWN_ERROR else "fallback"
        }
        
        # Determine failure type and logging
        if error_code in cls.RETRY_CONFIG:
            config = cls.RETRY_CONFIG[error_code]
            
            # Enhanced classification result logging
            classification_result = {
                "failure_type": "technical" if config["retryable"] else "user",
                "retryable": config["retryable"],
                "max_retries": config.get("max_retries", 0),
                "backoff_delays": config.get("backoff_delays", []),
                "classification_duration_ms": int((datetime.utcnow() - classification_start).total_seconds() * 1000)
            }
            retryable = config["retryable"]
            failure_type = OperationFailureType.TECHNICAL if retryable else OperationFailureType.USER
            recommended_delay = config.get("backoff_delays", [600])[0]  # First delay
            
            logger.info(f"✅ CLASSIFICATION_RESULT: {error_code.value} -> {failure_type.value} (retryable={retryable})", extra={**classification_context, **detection_data, **classification_result})
        else:
            # Default: treat unknown errors as technical but limited retry
            failure_type = OperationFailureType.TECHNICAL
            error_code = CashoutErrorCode.UNKNOWN_ERROR
            retryable = True
            recommended_delay = 600  # 10 minutes
        
        logger.info(f"✅ Classification: type={failure_type.value}, code={error_code.value}, retryable={retryable}, delay={recommended_delay}s")
        
        return failure_type, error_code, retryable, recommended_delay
    
    @classmethod
    def classify_escrow_error(cls,
                             exception: Exception,
                             escrow_id: str,
                             operation: str,
                             context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
        """
        Classify an escrow operation error with intelligent retry logic
        
        Args:
            exception: The exception that occurred
            escrow_id: Escrow identifier for context
            operation: Escrow operation type ("payment_processing", "release", "refund", "cancellation")
            context: Additional context (service, user_id, etc.)
        
        Returns:
            Tuple of (failure_type, error_code, retryable, recommended_delay_seconds)
        """
        start_time = datetime.utcnow()
        logger.info(f"🔍 ESCROW_CLASSIFICATION: {escrow_id} operation '{operation}' - {type(exception).__name__}: {str(exception)[:100]}...")
        
        # Enhanced context for escrow operations
        enhanced_context = {
            "escrow_id": escrow_id,
            "operation": operation,
            "entity_type": "escrow",
            **(context or {})
        }
        
        # Use base classification with enhanced context
        failure_type, error_code, retryable, delay = cls.classify_error(exception, enhanced_context)
        
        # Escrow-specific adjustments
        if operation in ["release", "refund"] and error_code == CashoutErrorCode.UNKNOWN_ERROR:
            # Critical operations should have more aggressive retry
            error_code = CashoutErrorCode.ESCROW_RELEASE_REFUND_FAILED
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0]
            
        elif operation == "payment_processing" and "address" in str(exception).lower():
            # Address generation issues
            error_code = CashoutErrorCode.ESCROW_ADDRESS_GENERATION_FAILED
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0]
        
        processing_time = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"✅ ESCROW_CLASSIFIED: {escrow_id} - {failure_type.value}/{error_code.value} (retryable={retryable}, delay={delay}s) in {processing_time:.3f}s")
        
        return failure_type, error_code, retryable, delay
    
    @classmethod
    def classify_deposit_error(cls,
                              exception: Exception,
                              user_id: int,
                              operation: str,
                              context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
        """
        Classify a deposit/wallet operation error with intelligent retry logic
        
        Args:
            exception: The exception that occurred
            user_id: User identifier for context
            operation: Deposit operation type ("webhook_processing", "confirmation_polling", "wallet_credit")
            context: Additional context (transaction_id, amount, etc.)
        
        Returns:
            Tuple of (failure_type, error_code, retryable, recommended_delay_seconds)
        """
        start_time = datetime.utcnow()
        logger.info(f"🔍 DEPOSIT_CLASSIFICATION: User {user_id} operation '{operation}' - {type(exception).__name__}: {str(exception)[:100]}...")
        
        # Enhanced context for deposit operations
        enhanced_context = {
            "user_id": user_id,
            "operation": operation,
            "entity_type": "deposit",
            **(context or {})
        }
        
        # Use base classification with enhanced context
        failure_type, error_code, retryable, delay = cls.classify_error(exception, enhanced_context)
        
        # Deposit-specific adjustments
        if operation == "webhook_processing" and error_code == CashoutErrorCode.UNKNOWN_ERROR:
            # Webhook processing failures should use specific error code
            error_code = CashoutErrorCode.DEPOSIT_WEBHOOK_PROCESSING_FAILED
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0]
            
        elif operation == "wallet_credit" and ("deadlock" in str(exception).lower() or "lock" in str(exception).lower()):
            # Database contention issues
            error_code = CashoutErrorCode.WALLET_CREDIT_DEADLOCK
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0]
            
        elif operation == "confirmation_polling" and error_code == CashoutErrorCode.UNKNOWN_ERROR:
            # Blockchain polling issues
            error_code = CashoutErrorCode.DEPOSIT_CONFIRMATION_POLLING_FAILED
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0]
        
        processing_time = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"✅ DEPOSIT_CLASSIFIED: User {user_id} - {failure_type.value}/{error_code.value} (retryable={retryable}, delay={delay}s) in {processing_time:.3f}s")
        
        return failure_type, error_code, retryable, delay
    
    @classmethod
    def classify_notification_error(cls,
                                   exception: Exception,
                                   recipient: str,
                                   channel: str,
                                   context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
        """
        Classify a notification operation error with intelligent retry logic
        
        Args:
            exception: The exception that occurred
            recipient: Email, phone number, or username
            channel: Notification channel ("email", "sms", "telegram")
            context: Additional context (service, message_type, etc.)
        
        Returns:
            Tuple of (failure_type, error_code, retryable, recommended_delay_seconds)
        """
        start_time = datetime.utcnow()
        logger.info(f"🔍 NOTIFICATION_CLASSIFICATION: Channel '{channel}' to {recipient} - {type(exception).__name__}: {str(exception)[:100]}...")
        
        # Enhanced context for notification operations
        enhanced_context = {
            "recipient": recipient,
            "channel": channel,
            "entity_type": "notification",
            **(context or {})
        }
        
        # Use base classification with enhanced context
        failure_type, error_code, retryable, delay = cls.classify_error(exception, enhanced_context)
        
        # Notification-specific adjustments based on channel and error type
        error_msg = str(exception).lower()
        
        if channel == "email" and ("timeout" in error_msg or "smtp" in error_msg or "brevo" in error_msg):
            error_code = CashoutErrorCode.NOTIFICATION_EMAIL_TIMEOUT
        elif channel == "sms" and ("timeout" in error_msg or "twilio" in error_msg):
            error_code = CashoutErrorCode.NOTIFICATION_SMS_TIMEOUT
        elif channel == "telegram" and ("timeout" in error_msg or "bot" in error_msg or "telegram" in error_msg):
            error_code = CashoutErrorCode.NOTIFICATION_TELEGRAM_TIMEOUT
        elif "rate limit" in error_msg or "429" in error_msg:
            error_code = CashoutErrorCode.NOTIFICATION_RATE_LIMITED
        elif "invalid" in error_msg and ("email" in error_msg or "phone" in error_msg):
            error_code = CashoutErrorCode.NOTIFICATION_INVALID_RECIPIENT
            failure_type = OperationFailureType.USER
            retryable = False
        elif "service unavailable" in error_msg or "503" in error_msg or "502" in error_msg:
            error_code = CashoutErrorCode.NOTIFICATION_SERVICE_UNAVAILABLE
        
        # Get proper retry configuration for detected error code
        if error_code in cls.RETRY_CONFIG:
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0] if config.get("backoff_delays") else delay
            if not retryable:
                failure_type = OperationFailureType.USER
        
        processing_time = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"✅ NOTIFICATION_CLASSIFIED: {channel} to {recipient} - {failure_type.value}/{error_code.value} (retryable={retryable}, delay={delay}s) in {processing_time:.3f}s")
        
        return failure_type, error_code, retryable, delay
    
    @classmethod
    def classify_wallet_error(cls,
                             exception: Exception,
                             user_id: int,
                             operation: str,
                             context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
        """
        Classify a wallet operation error with intelligent retry logic
        
        Args:
            exception: The exception that occurred
            user_id: User identifier for context
            operation: Wallet operation type ("balance_update", "debit", "credit", "freeze", "unfreeze")
            context: Additional context (amount, currency, transaction_id, etc.)
        
        Returns:
            Tuple of (failure_type, error_code, retryable, recommended_delay_seconds)
        """
        start_time = datetime.utcnow()
        logger.info(f"🔍 WALLET_CLASSIFICATION: User {user_id} operation '{operation}' - {type(exception).__name__}: {str(exception)[:100]}...")
        
        # Enhanced context for wallet operations
        enhanced_context = {
            "user_id": user_id,
            "operation": operation,
            "entity_type": "wallet",
            **(context or {})
        }
        
        # Use base classification with enhanced context
        failure_type, error_code, retryable, delay = cls.classify_error(exception, enhanced_context)
        
        # Wallet-specific adjustments based on operation and error type
        error_msg = str(exception).lower()
        
        if "deadlock" in error_msg or "lock" in error_msg:
            error_code = CashoutErrorCode.WALLET_DEADLOCK_ERROR
        elif "timeout" in error_msg or "connection" in error_msg:
            error_code = CashoutErrorCode.WALLET_CONNECTION_TIMEOUT
        elif "conflict" in error_msg or "concurrent" in error_msg:
            error_code = CashoutErrorCode.WALLET_TRANSACTION_CONFLICT
        elif "insufficient" in error_msg and "balance" in error_msg:
            error_code = CashoutErrorCode.WALLET_INSUFFICIENT_BALANCE
            failure_type = OperationFailureType.USER
            retryable = False
        elif "invalid" in error_msg and "amount" in error_msg:
            error_code = CashoutErrorCode.WALLET_INVALID_AMOUNT
            failure_type = OperationFailureType.USER
            retryable = False
        elif "locked" in error_msg or "frozen" in error_msg:
            error_code = CashoutErrorCode.WALLET_LOCKED
            failure_type = OperationFailureType.USER
            retryable = False
        
        # Get proper retry configuration for detected error code
        if error_code in cls.RETRY_CONFIG:
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0] if config.get("backoff_delays") else delay
            if not retryable:
                failure_type = OperationFailureType.USER
        
        processing_time = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"✅ WALLET_CLASSIFIED: User {user_id} - {failure_type.value}/{error_code.value} (retryable={retryable}, delay={delay}s) in {processing_time:.3f}s")
        
        return failure_type, error_code, retryable, delay
    
    @classmethod
    def classify_admin_error(cls,
                            exception: Exception,
                            admin_user_id: int,
                            operation: str,
                            context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
        """
        Classify an admin operation error with intelligent retry logic
        
        Args:
            exception: The exception that occurred
            admin_user_id: Admin user identifier for context
            operation: Admin operation type ("funding", "approval", "notification", "authentication")
            context: Additional context (target_service, amount, etc.)
        
        Returns:
            Tuple of (failure_type, error_code, retryable, recommended_delay_seconds)
        """
        start_time = datetime.utcnow()
        logger.info(f"🔍 ADMIN_CLASSIFICATION: Admin {admin_user_id} operation '{operation}' - {type(exception).__name__}: {str(exception)[:100]}...")
        
        # Enhanced context for admin operations
        enhanced_context = {
            "admin_user_id": admin_user_id,
            "operation": operation,
            "entity_type": "admin",
            **(context or {})
        }
        
        # Use base classification with enhanced context
        failure_type, error_code, retryable, delay = cls.classify_error(exception, enhanced_context)
        
        # Admin-specific adjustments based on operation and error type
        error_msg = str(exception).lower()
        
        if operation == "funding" and "timeout" in error_msg:
            error_code = CashoutErrorCode.ADMIN_PROVIDER_TIMEOUT
        elif operation == "notification" and ("email" in error_msg or "delivery" in error_msg):
            error_code = CashoutErrorCode.ADMIN_EMAIL_DELIVERY_FAILED
        elif "authentication" in error_msg or "2fa" in error_msg or "auth" in error_msg:
            error_code = CashoutErrorCode.ADMIN_AUTHENTICATION_FAILED
        elif "permission" in error_msg or "access denied" in error_msg or "403" in error_msg:
            error_code = CashoutErrorCode.ADMIN_PERMISSION_DENIED
            failure_type = OperationFailureType.USER
            retryable = False
        elif "invalid" in error_msg and "input" in error_msg:
            error_code = CashoutErrorCode.ADMIN_INVALID_INPUT
            failure_type = OperationFailureType.USER
            retryable = False
        elif "locked" in error_msg or "maintenance" in error_msg:
            error_code = CashoutErrorCode.ADMIN_SERVICE_LOCKED
        
        # Get proper retry configuration for detected error code
        if error_code in cls.RETRY_CONFIG:
            config = cls.RETRY_CONFIG[error_code]
            retryable = config["retryable"]
            delay = config["backoff_delays"][0] if config.get("backoff_delays") else delay
            if not retryable:
                failure_type = OperationFailureType.USER
        
        processing_time = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"✅ ADMIN_CLASSIFIED: Admin {admin_user_id} - {failure_type.value}/{error_code.value} (retryable={retryable}, delay={delay}s) in {processing_time:.3f}s")
        
        return failure_type, error_code, retryable, delay
    
    @classmethod
    def _detect_error_code(cls, error_message: str, exception_type: str) -> CashoutErrorCode:
        """Detect specific error code from message/type"""
        
        # Check technical patterns first
        for pattern, code in cls.TECHNICAL_ERROR_PATTERNS.items():
            if re.search(pattern, error_message, re.IGNORECASE):
                return code
        
        # Check user error patterns
        for pattern, code in cls.USER_ERROR_PATTERNS.items():
            if re.search(pattern, error_message, re.IGNORECASE):
                return code
        
        # Check exception types
        exception_lower = exception_type.lower()
        if any(x in exception_lower for x in ['timeout', 'connectionerror', 'sslerror']):
            return CashoutErrorCode.NETWORK_ERROR
        elif any(x in exception_lower for x in ['keyerror', 'attributeerror', 'typeerror']):
            return CashoutErrorCode.METADATA_PARSE_ERROR
        elif any(x in exception_lower for x in ['valueerror', 'validationerror']):
            return CashoutErrorCode.INVALID_AMOUNT
        
        return CashoutErrorCode.UNKNOWN_ERROR
    
    @classmethod
    def get_retry_config(cls, error_code: CashoutErrorCode) -> Dict[str, Any]:
        """Get retry configuration for an error code"""
        return cls.RETRY_CONFIG.get(error_code, cls.RETRY_CONFIG[CashoutErrorCode.UNKNOWN_ERROR])
    
    @classmethod
    def should_retry(cls, error_code: CashoutErrorCode, current_retry_count: int) -> bool:
        """Check if we should retry based on error code and current retry count"""
        config = cls.get_retry_config(error_code)
        return config["retryable"] and current_retry_count < config["max_retries"]
    
    @classmethod
    def get_next_retry_delay(cls, error_code: CashoutErrorCode, retry_count: int) -> int:
        """Get delay in seconds for next retry attempt"""
        config = cls.get_retry_config(error_code)
        backoff_delays = config.get("backoff_delays", [600])
        
        # Use the retry_count as index, or last delay if we've exceeded the array
        delay_index = min(retry_count, len(backoff_delays) - 1)
        return backoff_delays[delay_index]
    
    @classmethod
    def classify_from_log_context(cls, cashout_id: str, log_entries: list) -> Tuple[CashoutFailureType, CashoutErrorCode]:
        """
        Classify error from log entries when original exception is not available
        Used by cleanup jobs to classify already-failed cashouts
        """
        # Look for error patterns in recent log entries
        for entry in log_entries[-10:]:  # Last 10 log entries
            if cashout_id in entry and any(keyword in entry.lower() for keyword in ['error', 'failed', 'exception']):
                # Extract error message and classify
                try:
                    # Create a mock exception from log entry
                    mock_exception = Exception(entry)
                    failure_type, error_code, _, _ = cls.classify_error(mock_exception)
                    return failure_type, error_code
                except Exception as e:
                    logger.warning(f"⚠️ Failed to classify from log entry: {e}")
        
        # Default to unknown technical error if we can't determine from logs
        return OperationFailureType.TECHNICAL, CashoutErrorCode.UNKNOWN_ERROR


# Convenience functions for easy integration
def classify_cashout_error(exception: Exception, context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
    """Convenience function to classify cashout errors"""
    return UnifiedErrorClassifier.classify_error(exception, context)


def classify_escrow_error(exception: Exception, escrow_id: str, operation: str, context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
    """Convenience function to classify escrow operation errors"""
    return UnifiedErrorClassifier.classify_escrow_error(exception, escrow_id, operation, context)


def classify_deposit_error(exception: Exception, user_id: int, operation: str, context: Optional[Dict[str, Any]] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
    """Convenience function to classify deposit operation errors"""
    return UnifiedErrorClassifier.classify_deposit_error(exception, user_id, operation, context)


def should_retry_operation(error_code: CashoutErrorCode, retry_count: int) -> bool:
    """Convenience function to check if operation should be retried"""
    return UnifiedErrorClassifier.should_retry(error_code, retry_count)


def get_operation_retry_delay(error_code: CashoutErrorCode, retry_count: int) -> int:
    """Convenience function to get next retry delay"""
    return UnifiedErrorClassifier.get_next_retry_delay(error_code, retry_count)


# Backward compatibility aliases
def should_retry_cashout(error_code: CashoutErrorCode, retry_count: int) -> bool:
    """Convenience function to check if cashout should be retried"""
    return should_retry_operation(error_code, retry_count)


def get_cashout_retry_delay(error_code: CashoutErrorCode, retry_count: int) -> int:
    """Convenience function to get next retry delay"""
    return get_operation_retry_delay(error_code, retry_count)