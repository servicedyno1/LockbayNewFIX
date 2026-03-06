"""
Lockbay Cryptocurrency Escrow Platform - Optimized Database Schema
================================================================

Clean, focused schema designed specifically for Lockbay's core use cases:
- Telegram-based cryptocurrency escrow transactions
- Multi-currency wallet management (8 DynoPay cryptocurrencies)
- NGN bank transfers via Fincra
- Automated Kraken cashouts
- Admin dispute resolution and controls

This schema eliminates complexity while maintaining all essential functionality.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from sqlalchemy import (
    Column, Integer, BigInteger, String, Numeric, DateTime, Date, Boolean, Text, Float,
    ForeignKey, UniqueConstraint, Index, CheckConstraint, func, JSON, DECIMAL
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models"""
    pass


# ============================================================================
# ENUMS - Business Logic Constants
# ============================================================================

class UserStatus(Enum):
    """User account status"""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BANNED = "banned"
    PENDING_VERIFICATION = "pending_verification"


class EscrowStatus(Enum):
    """Escrow transaction lifecycle states"""
    CREATED = "created"
    PAYMENT_PENDING = "payment_pending"
    PENDING_DEPOSIT = "pending_deposit"
    PAYMENT_CONFIRMED = "payment_confirmed"
    PARTIAL_PAYMENT = "partial_payment"
    PAYMENT_FAILED = "payment_failed"
    AWAITING_SELLER = "awaiting_seller"
    PENDING_SELLER = "pending_seller"
    ACTIVE = "active"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class RefundType(Enum):
    CASHOUT_FAILED = "cashout_failed"
    ESCROW_REFUND = "escrow_refund" 
    DISPUTE_REFUND = "dispute_refund"
    ADMIN_REFUND = "admin_refund"
    ERROR_REFUND = "error_refund"


class RefundStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class SellerContactType(Enum):
    """Types of seller contact methods for escrow invitations"""
    USERNAME = "username"  # Telegram username (e.g., "@onarrival2")
    EMAIL = "email"       # Email address (e.g., "seller@example.com")
    PHONE = "phone"       # Phone number (e.g., "+234123456789")


class OnboardingStep(Enum):
    """4-step onboarding state machine"""
    CAPTURE_EMAIL = "capture_email"     # User needs to provide email address
    VERIFY_OTP = "verify_otp"          # User needs to verify OTP sent to email
    ACCEPT_TOS = "accept_tos"          # User needs to accept terms of service
    DONE = "done"                      # Onboarding completed successfully


class AchievementType(Enum):
    """Types of achievements users can unlock"""
    FIRST_TRADE = "first_trade"
    TRADE_VOLUME = "trade_volume"  # 5, 10, 25, 50 trades
    DOLLAR_VOLUME = "dollar_volume"  # $100, $500, $1000+
    REPUTATION_MILESTONE = "reputation_milestone"  # 4.0, 4.5, 5.0 rating
    STREAK_TRADES = "streak_trades"  # 3, 5, 10 consecutive successful
    TIME_ACTIVE = "time_active"  # 1 week, 1 month, 3 months
    TRUSTED_TRADER = "trusted_trader"  # Special trusted status
    PERFECT_MONTH = "perfect_month"  # Month with no disputes
    QUICK_TRADER = "quick_trader"  # Fast completion times
    VOLUME_KING = "volume_king"  # High volume achievements


class CryptoDepositStatus(Enum):
    """State machine for cryptocurrency deposit processing"""
    PENDING_UNCONFIRMED = "pending_unconfirmed"  # Transaction received but not confirmed
    READY_TO_CREDIT = "ready_to_credit"         # Transaction confirmed, ready for wallet crediting
    CREDITED = "credited"                       # Successfully credited to wallet
    FAILED = "failed"                          # Failed to process after retries


class TransactionType(Enum):
    """Types of financial transactions"""
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    ESCROW_PAYMENT = "escrow_payment"
    ESCROW_RELEASE = "escrow_release"
    ESCROW_REFUND = "escrow_refund"
    WALLET_TRANSFER = "wallet_transfer"
    WALLET_DEPOSIT = "wallet_deposit"  # For wallet creation transactions
    WALLET_PAYMENT = "wallet_payment"  # For wallet payment transactions
    CASHOUT = "cashout"
    CASHOUT_DEBIT = "cashout_debit"    # For cashout debit transactions
    CASHOUT_HOLD = "cashout_hold"      # For cashout hold transactions
    CASHOUT_HOLD_RELEASE = "cashout_hold_release"  # For releasing cashout holds
    FROZEN_BALANCE_CONSUME = "frozen_balance_consume"  # For consuming frozen balance
    EXCHANGE_HOLD = "exchange_hold"    # For exchange hold transactions
    EXCHANGE_DEBIT = "exchange_debit"  # For exchange debit transactions
    EXCHANGE_HOLD_RELEASE = "exchange_hold_release"  # For releasing exchange holds
    REFUND = "refund"                  # For refund transactions
    FEE = "fee"
    ADMIN_ADJUSTMENT = "admin_adjustment"


class TransactionStatus(Enum):
    """Transaction processing states"""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CashoutStatus(Enum):
    """Cashout request states"""
    PENDING = "pending"
    OTP_PENDING = "otp_pending"
    USER_CONFIRM_PENDING = "user_confirm_pending"
    ADMIN_PENDING = "admin_pending"
    APPROVED = "approved"
    ADMIN_APPROVED = "admin_approved"
    AWAITING_RESPONSE = "awaiting_response"
    PENDING_ADDRESS_CONFIG = "pending_address_config"
    PENDING_SERVICE_FUNDING = "pending_service_funding"
    EXECUTING = "executing"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class DisputeStatus(Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"


class UnifiedTransactionStatus(Enum):
    """
    Unified status model covering all transaction types (cashout, escrow, exchange, deposits)
    with 16 standardized statuses across 4 lifecycle phases
    """
    # === INITIATION PHASE (4 statuses) ===
    PENDING = "pending"                    # Initial state, system processing request
    AWAITING_PAYMENT = "awaiting_payment"  # Waiting for user payment/deposit (escrows, exchanges)
    PARTIAL_PAYMENT = "partial_payment"    # Partial payment received, awaiting full amount
    PAYMENT_CONFIRMED = "payment_confirmed"  # Payment received and confirmed
    
    # === AUTHORIZATION PHASE (5 statuses) ===
    FUNDS_HELD = "funds_held"             # Funds secured in frozen balance
    AWAITING_APPROVAL = "awaiting_approval"  # Waiting for user/admin approval
    OTP_PENDING = "otp_pending"           # OTP verification required (wallet cashouts only)
    ADMIN_PENDING = "admin_pending"       # Waiting for admin action/approval
    
    # === EXECUTION PHASE (6 statuses) ===
    PROCESSING = "processing"             # Transaction being processed
    EXTERNAL_PENDING = "external_pending"  # Waiting for external service
    AWAITING_RESPONSE = "awaiting_response"  # Waiting for external service response
    RELEASE_PENDING = "release_pending"   # Funds pending release
    FUNDS_RELEASED = "funds_released"     # Funds have been released
    COMPLETED = "completed"               # Transaction completed (processing finished)
    
    # === FINAL PHASE (7 statuses) ===
    SUCCESS = "success"                   # Transaction completed successfully
    DELIVERED = "delivered"               # Item/service delivered
    FAILED = "failed"                     # Transaction failed
    CANCELLED = "cancelled"               # Transaction cancelled by user/system
    DISPUTED = "disputed"                 # Transaction in dispute
    EXPIRED = "expired"                   # Transaction expired
    REFUNDED = "refunded"                 # Transaction refunded


class PartnerApplicationStatus(Enum):
    """Partner program application review states"""
    NEW = "new"                    # Just submitted, not yet reviewed
    CONTACTED = "contacted"        # Admin has reached out to applicant
    QUALIFIED = "qualified"        # Meets partnership criteria
    APPROVED = "approved"          # Partnership approved
    REJECTED = "rejected"          # Application declined


class CommunityType(Enum):
    """Types of communities applying for partnership"""
    CRYPTO_TRADING = "crypto_trading"
    NFT_COMMUNITY = "nft_community"
    GAMING = "gaming"
    MARKETPLACE = "marketplace"
    OTHER = "other"


class CommissionTier(Enum):
    """Partnership commission tiers"""
    BRONZE = "bronze"  # 30% commission
    SILVER = "silver"  # 40% commission
    GOLD = "gold"      # 50% commission


class UnifiedTransactionType(Enum):
    """
    Simplified transaction types for unified transaction model
    Each type has specific OTP and approval requirements
    """
    WALLET_CASHOUT = "wallet_cashout"         # OTP required, user funds withdrawal
    EXCHANGE_SELL_CRYPTO = "exchange_sell_crypto"  # No OTP, crypto to fiat exchange
    EXCHANGE_BUY_CRYPTO = "exchange_buy_crypto"   # No OTP, fiat to crypto exchange  
    ESCROW = "escrow"                        # No OTP for releases, escrow transactions


class UnifiedTransactionPriority(Enum):
    """Priority levels for transaction processing"""
    LOW = "low"          # Background processing, no rush
    NORMAL = "normal"    # Standard processing timeline
    HIGH = "high"        # Expedited processing preferred
    URGENT = "urgent"    # Immediate processing required


class FundMovementType(Enum):
    """Types of fund movements in unified transactions"""
    HOLD = "hold"              # Move from available to frozen balance
    RELEASE = "release"        # Move from frozen to available balance
    DEBIT = "debit"           # Remove from available balance (outgoing)
    CREDIT = "credit"         # Add to available balance (incoming)
    TRANSFER = "transfer"     # Direct transfer between balances
    CONSUME = "consume"       # Remove from frozen balance (final processing)


class ExchangeStatus(Enum):
    CREATED = "created"
    AWAITING_DEPOSIT = "awaiting_deposit"
    RATE_LOCKED = "rate_locked"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_CONFIRMED = "payment_confirmed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ADDRESS_GENERATION_FAILED = "address_generation_failed"
    PENDING_APPROVAL = "pending_approval"


class OutboxEventStatus(Enum):
    """Status for outbox events ensuring reliable event processing"""
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    RETRYING = "retrying"


class AdminActionType(Enum):
    """Types of admin actions for failed transactions"""
    RETRY = "retry"
    REFUND = "refund"
    DECLINE = "decline"
    INVESTIGATE = "investigate"


class SagaStepStatus(Enum):
    """Status for individual saga steps"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    SKIPPED = "skipped"


class WalletHoldStatus(Enum):
    """Lifecycle status for funds in transit - SECURITY: Frozen funds never auto-release to available"""
    HELD = "held"                    # Funds in frozen_balance, awaiting processing
    CONSUMED_SENT = "consumed_sent"  # Funds sent to provider, now external
    SETTLED = "settled"              # Transaction completed successfully
    REFUNDED = "refunded"            # Funds returned to available balance
    EXPIRED = "expired"              # Hold expired, funds released
    FAILED_HELD = "failed_held"      # Transaction failed, funds held for review
    CANCELLED_HELD = "cancelled_held" # Transaction cancelled, funds held for review
    DISPUTED_HELD = "disputed_held"  # Transaction disputed, funds held for review
    REFUND_APPROVED = "refund_approved" # Admin approved refund, ready to release
    RELEASED = "released"            # Funds released back to available balance
    ACTIVE = "active"                # Hold is currently active


class OperationFailureType(Enum):
    """Classification of operation failure types for retry logic (supports cashout, escrow, deposit)"""
    TECHNICAL = "technical"  # System bugs, API issues, network failures - should retry
    USER = "user"           # User errors, insufficient funds, invalid addresses - should refund


class CashoutErrorCode(Enum):
    """Specific error codes for cashout failures"""
    # Technical errors (retryable)
    KRAKEN_ADDR_NOT_FOUND = "kraken_addr_not_found"
    KRAKEN_API_TIMEOUT = "kraken_api_timeout"
    KRAKEN_API_ERROR = "kraken_api_error"
    KRAKEN_INSUFFICIENT_FUNDS = "kraken_insufficient_funds"
    KRAKEN_INVALID_AMOUNT = "kraken_invalid_amount"
    KRAKEN_RATE_LIMITED = "kraken_rate_limited"
    KRAKEN_SERVER_ERROR = "kraken_server_error"
    
    # Fincra errors (retryable)
    FINCRA_API_TIMEOUT = "fincra_api_timeout"
    FINCRA_API_ERROR = "fincra_api_error"
    FINCRA_INSUFFICIENT_FUNDS = "fincra_insufficient_funds"
    
    # Network and system errors (retryable)
    API_TIMEOUT = "api_timeout"
    API_AUTHENTICATION_FAILED = "api_authentication_failed"
    API_INSUFFICIENT_FUNDS = "api_insufficient_funds"
    API_INVALID_REQUEST = "api_invalid_request"
    NETWORK_ERROR = "network_error"
    SSL_ERROR = "ssl_error"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    METADATA_PARSE_ERROR = "metadata_parse_error"
    DATABASE_ERROR = "database_error"
    
    # Exchange errors (retryable)
    EXCHANGE_RATE_ERROR = "exchange_rate_error"
    EXCHANGE_PROCESSING_ERROR = "exchange_processing_error"
    
    # User errors (non-retryable)
    INVALID_ADDRESS = "invalid_address"
    INVALID_AMOUNT = "invalid_amount"
    AMOUNT_TOO_SMALL = "amount_too_small"
    AMOUNT_TOO_LARGE = "amount_too_large"
    MIN_AMOUNT_NOT_MET = "min_amount_not_met"
    MAX_AMOUNT_EXCEEDED = "max_amount_exceeded"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    ACCOUNT_FROZEN = "account_frozen"
    SANCTIONS_BLOCKED = "sanctions_blocked"
    CURRENCY_NOT_SUPPORTED = "currency_not_supported"
    USER_CANCELLED = "user_cancelled"
    
    # Escrow-specific errors
    ESCROW_ADDRESS_GENERATION_FAILED = "escrow_address_generation_failed"
    ESCROW_DEPOSIT_CONFIRMATION_TIMEOUT = "escrow_deposit_confirmation_timeout"
    ESCROW_RELEASE_REFUND_FAILED = "escrow_release_refund_failed"
    
    # Deposit/Webhook errors
    DEPOSIT_WEBHOOK_PROCESSING_FAILED = "deposit_webhook_processing_failed"
    DEPOSIT_CONFIRMATION_POLLING_FAILED = "deposit_confirmation_polling_failed"
    
    # Wallet operation errors
    WALLET_CREDIT_DEADLOCK = "wallet_credit_deadlock"
    WALLET_DEADLOCK_ERROR = "wallet_deadlock_error"
    WALLET_CONNECTION_TIMEOUT = "wallet_connection_timeout"
    WALLET_TRANSACTION_CONFLICT = "wallet_transaction_conflict"
    WALLET_INSUFFICIENT_BALANCE = "wallet_insufficient_balance"
    WALLET_INVALID_AMOUNT = "wallet_invalid_amount"
    WALLET_LOCKED = "wallet_locked"
    
    # Notification errors
    NOTIFICATION_EMAIL_TIMEOUT = "notification_email_timeout"
    NOTIFICATION_SMS_TIMEOUT = "notification_sms_timeout"
    NOTIFICATION_TELEGRAM_TIMEOUT = "notification_telegram_timeout"
    NOTIFICATION_RATE_LIMITED = "notification_rate_limited"
    NOTIFICATION_SERVICE_UNAVAILABLE = "notification_service_unavailable"
    NOTIFICATION_INVALID_RECIPIENT = "notification_invalid_recipient"
    
    # Admin/Provider errors
    ADMIN_PROVIDER_TIMEOUT = "admin_provider_timeout"
    ADMIN_EMAIL_DELIVERY_FAILED = "admin_email_delivery_failed"
    ADMIN_AUTHENTICATION_FAILED = "admin_authentication_failed"
    ADMIN_SERVICE_LOCKED = "admin_service_locked"
    ADMIN_PERMISSION_DENIED = "admin_permission_denied"
    ADMIN_INVALID_INPUT = "admin_invalid_input"
    
    # System errors
    SYSTEM_ERROR = "system_error"
    UNKNOWN_ERROR = "unknown_error"


# Backward compatibility alias
CashoutFailureType = OperationFailureType


class CashoutProcessingMode(Enum):
    """Processing mode for cashout requests to prevent dual processing"""
    IMMEDIATE = "immediate"  # User-initiated immediate cashouts (via wallet interface)
    MANUAL = "manual"       # Admin-processed cashouts (via admin interface or batch jobs)


class CashoutType(Enum):
    """Types of cashout destinations"""
    CRYPTO = "crypto"
    NGN_BANK = "ngn_bank"


class PaymentProvider(Enum):
    """Supported payment providers"""
    DYNOPAY = "dynopay"
    BLOCKBEE = "blockbee"
    FINCRA = "fincra"
    KRAKEN = "kraken"


# ============================================================================
# CORE ENTITIES
# ============================================================================

class User(Base):
    """Core user entity - Telegram-based authentication"""
    __tablename__ = 'users'
    
    # Primary identification
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    profile_slug: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, unique=True, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # Contact info
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # User preferences
    language_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    last_activity: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    
    # Status flags
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_seller: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_cashout_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cashout_preference: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # Preferred cashout method (CRYPTO, NGN_BANK)
    auto_cashout_crypto_address_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    auto_cashout_bank_account_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Onboarding tracking
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # User status
    conversation_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Conversation flow state
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # When terms accepted
    onboarded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # When onboarding completed
    
    # Onboarding
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Universal welcome bonus tracking
    universal_welcome_bonus_given: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    universal_welcome_bonus_given_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Referral system
    referral_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    referred_by_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    total_referrals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    referral_earnings: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    
    # Trading metrics
    reputation_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=0, nullable=False)
    total_ratings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Profile
    profile_image_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Relationships
    wallets: Mapped[list["Wallet"]] = relationship("Wallet", back_populates="user", cascade="all, delete-orphan")
    escrows_as_buyer: Mapped[list["Escrow"]] = relationship("Escrow", foreign_keys="Escrow.buyer_id", back_populates="buyer")
    escrows_as_seller: Mapped[list["Escrow"]] = relationship("Escrow", foreign_keys="Escrow.seller_id", back_populates="seller")
    transactions: Mapped[list["Transaction"]] = relationship("Transaction", back_populates="user")
    cashouts: Mapped[list["Cashout"]] = relationship("Cashout", back_populates="user")
    saved_bank_accounts: Mapped[list["SavedBankAccount"]] = relationship("SavedBankAccount", back_populates="user")
    saved_addresses: Mapped[list["SavedAddress"]] = relationship("SavedAddress", back_populates="user")
    pending_cashouts: Mapped[list["PendingCashout"]] = relationship("PendingCashout", back_populates="user")
    
    # Constraints and indexes
    __table_args__ = (
        Index('ix_users_telegram_id', 'telegram_id', unique=True),
    )


class Wallet(Base):
    """Multi-currency wallet balances"""
    __tablename__ = 'wallets'
    
    # Primary key (PRESERVED - never change type)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    utid: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True, index=True)  # Universal Transaction ID
    
    # Owner and currency
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)  # BTC, ETH, USDT, NGN, etc.
    
    # Balances with high precision for all cryptocurrencies (supports up to 18 decimals)
    available_balance: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    frozen_balance: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=0, nullable=False)  # Funds on hold
    trading_credit: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=0, nullable=False)  # Non-withdrawable bonus funds (can only be used for trades/fees)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="wallets")
    
    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint('user_id', 'currency', name='uq_user_currency'),
        CheckConstraint('available_balance >= 0', name='ck_wallet_available_positive'),
        CheckConstraint('frozen_balance >= 0', name='ck_wallet_frozen_positive'),
        CheckConstraint('trading_credit >= 0', name='ck_wallet_trading_credit_positive'),
        Index('ix_wallets_user_currency', 'user_id', 'currency'),
    )


class Escrow(Base):
    """Cryptocurrency escrow transactions"""
    __tablename__ = 'escrows'
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    escrow_id = Column(String(16), unique=True, nullable=False, index=True)  # Public facing ID
    utid = Column(String(32), unique=True, nullable=True, index=True)  # Universal Transaction ID
    
    # Participants
    buyer_id = Column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    seller_id = Column(BigInteger, ForeignKey('users.id'), nullable=True, index=True)
    seller_email = Column(String(255), nullable=True)  # For invitations (deprecated - use typed contact fields)
    
    # Typed seller contact information for proper invitation routing
    seller_contact_type = Column(String(20), nullable=True)  # username|email|phone
    seller_contact_value = Column(String(255), nullable=True)  # Normalized contact value
    seller_contact_display = Column(String(255), nullable=True)  # UI-safe display format
    
    # Financial details with high precision
    amount = Column(Numeric(38, 18), nullable=False)
    currency = Column(String(10), nullable=False)
    fee_amount = Column(Numeric(38, 18), default=0, nullable=False)
    total_amount = Column(Numeric(38, 18), nullable=False)  # amount + fees
    
    # Fee split configuration and amounts
    fee_split_option = Column(String(20), default='buyer_pays', nullable=False)  # buyer_pays|seller_pays|split
    buyer_fee_amount = Column(Numeric(38, 18), default=0, nullable=False)  # Fee paid by buyer
    seller_fee_amount = Column(Numeric(38, 18), default=0, nullable=False)  # Fee paid by seller
    
    # Immutable pricing snapshot for auditability and dispute resolution
    pricing_snapshot = Column(JSONB, nullable=True)  # Locked rates, fees, markup at transaction time
    
    # Transaction details
    description = Column(Text, nullable=True)
    payment_method = Column(String(20), nullable=True)  # crypto, bank_transfer
    deposit_address = Column(String(100), nullable=True)  # Generated crypto address
    deposit_tx_hash = Column(String(100), nullable=True)  # Blockchain transaction
    
    # Status and lifecycle
    status = Column(String(20), default=EscrowStatus.CREATED.value, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    
    # Important timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=True)
    payment_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    seller_accepted_at = Column(DateTime(timezone=True), nullable=True)  # When seller accepted the trade
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Delivery and auto-release timing for escrow lifecycle
    delivery_deadline = Column(DateTime(timezone=True), nullable=True)  # When delivery should be completed
    delivered_at = Column(DateTime(timezone=True), nullable=True)  # When seller marked item as delivered
    auto_release_at = Column(DateTime(timezone=True), nullable=True)  # When funds auto-release to seller
    
    # Delivery warning tracking (prevents duplicate notifications)
    warning_24h_sent = Column(Boolean, default=False, nullable=False)  # 24-hour warning sent
    warning_8h_sent = Column(Boolean, default=False, nullable=False)  # 8-hour warning sent
    warning_2h_sent = Column(Boolean, default=False, nullable=False)  # 2-hour warning sent
    warning_30m_sent = Column(Boolean, default=False, nullable=False)  # 30-minute warning sent
    
    # Refund and expiry tracking
    refund_processed = Column(Boolean, default=False, nullable=False)  # Whether refund has been processed
    expiry_notified = Column(Boolean, default=False, nullable=False)  # Whether expiry notification was sent
    
    # Admin and dispute
    admin_notes = Column(Text, nullable=True)
    dispute_reason = Column(Text, nullable=True)
    
    # Relationships
    buyer = relationship("User", foreign_keys=[buyer_id], back_populates="escrows_as_buyer")
    seller = relationship("User", foreign_keys=[seller_id], back_populates="escrows_as_seller")
    transactions = relationship("Transaction", back_populates="escrow")
    
    # Computed property for USD amount (for legacy compatibility with daily financial report)
    @property
    def amount_usd(self) -> Optional[float]:
        """
        Calculate/extract USD amount for reporting and analytics.
        
        For stablecoins (USDT, USDC), returns amount directly.
        For other currencies, attempts to extract from pricing_snapshot.
        Falls back to None if USD value cannot be determined.
        """
        try:
            # Stablecoins are already in USD
            if self.currency in ('USDT', 'USDC', 'USDT-TRC20', 'USDT-ERC20', 'BUSD', 'DAI'):
                return float(self.amount)
            
            # Try to extract from pricing_snapshot
            if self.pricing_snapshot and isinstance(self.pricing_snapshot, dict):
                # Check various possible keys where USD amount might be stored
                for key in ['usd_amount', 'amount_usd', 'usd_value', 'total_usd']:
                    if key in self.pricing_snapshot:
                        return float(self.pricing_snapshot[key])
                
                # Try to calculate from locked_rate if available
                if 'locked_rate' in self.pricing_snapshot or 'usd_rate' in self.pricing_snapshot:
                    rate = self.pricing_snapshot.get('locked_rate') or self.pricing_snapshot.get('usd_rate')
                    if rate:
                        return float(self.amount) * float(rate)
            
            # Cannot determine USD value - return None
            return None
        except (ValueError, TypeError, KeyError):
            return None
    
    # Constraints and indexes
    __table_args__ = (
        CheckConstraint(f"status IN ('{EscrowStatus.CREATED.value}', '{EscrowStatus.PAYMENT_PENDING.value}', '{EscrowStatus.PAYMENT_CONFIRMED.value}', '{EscrowStatus.PARTIAL_PAYMENT.value}', '{EscrowStatus.PAYMENT_FAILED.value}', '{EscrowStatus.ACTIVE.value}', '{EscrowStatus.COMPLETED.value}', '{EscrowStatus.DISPUTED.value}', '{EscrowStatus.REFUNDED.value}', '{EscrowStatus.CANCELLED.value}', '{EscrowStatus.EXPIRED.value}')", name='ck_escrow_status_valid'),
        CheckConstraint('amount > 0', name='ck_escrow_amount_positive'),
        CheckConstraint('fee_amount >= 0', name='ck_escrow_fee_positive'),
        CheckConstraint('total_amount > 0', name='ck_escrow_total_positive'),
        CheckConstraint('total_amount = amount + fee_amount', name='ck_escrow_total_equals_sum'),  # Monetary invariant
        # Fee split validation
        CheckConstraint("fee_split_option IN ('buyer_pays', 'seller_pays', 'split')", name='ck_escrow_fee_split_valid'),
        CheckConstraint('buyer_fee_amount >= 0', name='ck_escrow_buyer_fee_positive'),
        CheckConstraint('seller_fee_amount >= 0', name='ck_escrow_seller_fee_positive'),
        CheckConstraint('fee_amount = buyer_fee_amount + seller_fee_amount', name='ck_escrow_fee_split_sum'),  # Fee split invariant
        # Seller assignment constraint: Either seller_id OR typed contact info must be present
        CheckConstraint('seller_id IS NOT NULL OR (seller_contact_type IS NOT NULL AND seller_contact_value IS NOT NULL)', name='ck_escrow_seller_assigned'),
        # Seller contact type validation
        CheckConstraint("seller_contact_type IS NULL OR seller_contact_type IN ('username', 'email', 'phone')", name='ck_escrow_contact_type_valid'),
        Index('ix_escrows_buyer_status', 'buyer_id', 'status'),
        Index('ix_escrows_seller_status', 'seller_id', 'status'),
        Index('ix_escrows_status_created', 'status', 'created_at'),
        Index('ix_escrows_expires_at', 'expires_at'),
        Index('ix_escrows_delivery_deadline', 'delivery_deadline'),
        Index('ix_escrows_auto_release', 'auto_release_at'),
        # Partial indexes for seller contact lookups by type
        Index('ix_escrows_contact_username', 'seller_contact_value', postgresql_where="seller_contact_type = 'username'"),
        Index('ix_escrows_contact_email', 'seller_contact_value', postgresql_where="seller_contact_type = 'email'"),
        Index('ix_escrows_contact_phone', 'seller_contact_value', postgresql_where="seller_contact_type = 'phone'"),
    )


class Transaction(Base):
    """Financial transaction ledger"""
    __tablename__ = 'transactions'
    
    # Primary key (PRESERVED - never change type)
    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String(36), unique=True, nullable=False, index=True)  # Legacy public ID
    utid = Column(String(32), unique=True, nullable=True, index=True)  # Universal Transaction ID
    
    # Core transaction data
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    transaction_type = Column(String(20), nullable=False)
    amount = Column(Numeric(38, 18), nullable=False)
    currency = Column(String(10), nullable=False)
    fee = Column(Numeric(38, 18), nullable=True)  # Transaction fee
    
    # Status and processing
    status = Column(String(20), default=TransactionStatus.PENDING.value, nullable=False)
    provider = Column(String(20), nullable=True)  # Payment provider used
    
    # External references
    external_id = Column(String(100), nullable=True)  # Legacy external ID field
    external_tx_id = Column(String(100), nullable=True)  # Provider transaction ID
    blockchain_tx_hash = Column(String(100), nullable=True)  # On-chain transaction
    escrow_id = Column(Integer, ForeignKey('escrows.id'), nullable=True, index=True)
    cashout_id = Column(Integer, ForeignKey('cashouts.id'), nullable=True, index=True)
    
    # Metadata
    description = Column(Text, nullable=True)
    extra_data = Column(JSONB, nullable=True)  # Provider-specific data
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)  # Legacy updated timestamp
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="transactions")
    escrow = relationship("Escrow", back_populates="transactions")
    cashout = relationship("Cashout", back_populates="transactions")
    
    # Constraints and indexes
    __table_args__ = (
        CheckConstraint(f"transaction_type IN ('{TransactionType.DEPOSIT.value}', '{TransactionType.WITHDRAWAL.value}', '{TransactionType.ESCROW_PAYMENT.value}', '{TransactionType.ESCROW_RELEASE.value}', '{TransactionType.ESCROW_REFUND.value}', '{TransactionType.WALLET_TRANSFER.value}', '{TransactionType.WALLET_DEPOSIT.value}', '{TransactionType.WALLET_PAYMENT.value}', '{TransactionType.CASHOUT.value}', '{TransactionType.CASHOUT_HOLD.value}', '{TransactionType.CASHOUT_HOLD_RELEASE.value}', '{TransactionType.FEE.value}', '{TransactionType.ADMIN_ADJUSTMENT.value}')", name='ck_transaction_type_valid'),
        CheckConstraint(f"status IN ('{TransactionStatus.PENDING.value}', '{TransactionStatus.CONFIRMED.value}', '{TransactionStatus.COMPLETED.value}', '{TransactionStatus.FAILED.value}', '{TransactionStatus.CANCELLED.value}')", name='ck_transaction_status_valid'),
        CheckConstraint('amount > 0', name='ck_transaction_amount_positive'),
        # Relational integrity: Escrow-related transactions must have escrow_id
        CheckConstraint(f"(transaction_type IN ('{TransactionType.ESCROW_PAYMENT.value}', '{TransactionType.ESCROW_RELEASE.value}', '{TransactionType.ESCROW_REFUND.value}') AND escrow_id IS NOT NULL) OR (transaction_type IN ('{TransactionType.CASHOUT.value}', '{TransactionType.CASHOUT_HOLD.value}', '{TransactionType.CASHOUT_HOLD_RELEASE.value}') AND cashout_id IS NOT NULL) OR (transaction_type IN ('{TransactionType.DEPOSIT.value}', '{TransactionType.WITHDRAWAL.value}', '{TransactionType.WALLET_TRANSFER.value}', '{TransactionType.WALLET_DEPOSIT.value}', '{TransactionType.WALLET_PAYMENT.value}', '{TransactionType.FEE.value}', '{TransactionType.ADMIN_ADJUSTMENT.value}'))", name='ck_transaction_entity_link_required'),
        Index('ix_transactions_user_type', 'user_id', 'transaction_type'),
        Index('ix_transactions_status_created', 'status', 'created_at'),
        Index('ix_transactions_external_tx', 'external_tx_id'),
        Index('ix_transactions_blockchain_hash', 'blockchain_tx_hash'),
        # IDEMPOTENCY: Prevent duplicate overpayment credits for same escrow
        # Partial unique index only for escrow_overpayment transactions with completed status
        Index(
            'ix_unique_escrow_overpayment',
            'user_id', 'escrow_id', 'transaction_type', 'amount', 'status',
            unique=True,
            postgresql_where="transaction_type = 'escrow_overpayment' AND status = 'completed'"
        ),
    )


class Cashout(Base):
    """Cashout requests for crypto and NGN"""
    __tablename__ = 'cashouts'
    
    # Primary key (PRESERVED - never change type)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cashout_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)  # Legacy public ID
    utid: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True, index=True)  # Universal Transaction ID
    
    # User and financial details
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    cashout_type: Mapped[str] = mapped_column(String(20), nullable=False)  # crypto or ngn_bank
    
    # Destination details
    destination_type: Mapped[str] = mapped_column(String(20), nullable=False)  # Type of destination (crypto/bank)
    destination_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Crypto address
    destination: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Unified destination field (crypto address or bank reference)
    bank_account_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Reference to SavedBankAccount
    bank_details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # NGN bank account info
    cashout_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Additional metadata for processing
    
    # Fees and net amount with high precision
    network_fee: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=0, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    
    # Immutable pricing snapshot for reconciliation and dispute resolution
    pricing_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Exchange rates, fee policy, calculations
    
    # Status and processing
    status: Mapped[str] = mapped_column(String(20), default=CashoutStatus.PENDING.value, nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # Processing provider
    processing_mode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # Processing mode (IMMEDIATE, HOLD_FIRST, etc.)
    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # External transaction ID
    external_tx_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Alternative external transaction ID field
    external_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Provider reference
    fincra_request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Fincra-specific request ID
    
    # Admin approval
    admin_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Error handling and retry system
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # technical or user failure
    last_error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # Last error code for diagnostics
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # When cashout failed
    technical_failure_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # Track technical failure duration
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="cashouts")
    transactions: Mapped[list["Transaction"]] = relationship("Transaction", back_populates="cashout")
    
    # Constraints and indexes
    __table_args__ = (
        CheckConstraint(f"status IN ('{CashoutStatus.PENDING.value}', '{CashoutStatus.OTP_PENDING.value}', '{CashoutStatus.USER_CONFIRM_PENDING.value}', '{CashoutStatus.ADMIN_PENDING.value}', '{CashoutStatus.APPROVED.value}', '{CashoutStatus.ADMIN_APPROVED.value}', '{CashoutStatus.AWAITING_RESPONSE.value}', '{CashoutStatus.PENDING_ADDRESS_CONFIG.value}', '{CashoutStatus.PENDING_SERVICE_FUNDING.value}', '{CashoutStatus.EXECUTING.value}', '{CashoutStatus.PROCESSING.value}', '{CashoutStatus.COMPLETED.value}', '{CashoutStatus.SUCCESS.value}', '{CashoutStatus.FAILED.value}', '{CashoutStatus.EXPIRED.value}', '{CashoutStatus.CANCELLED.value}')", name='ck_cashout_status_valid'),
        CheckConstraint(f"cashout_type IN ('{CashoutType.CRYPTO.value}', '{CashoutType.NGN_BANK.value}')", name='ck_cashout_type_valid'),
        CheckConstraint('amount > 0', name='ck_cashout_amount_positive'),
        CheckConstraint('network_fee >= 0', name='ck_cashout_network_fee_positive'),
        CheckConstraint('platform_fee >= 0', name='ck_cashout_platform_fee_positive'),
        CheckConstraint('net_amount > 0', name='ck_cashout_net_amount_positive'),
        CheckConstraint('net_amount = amount - network_fee - platform_fee', name='ck_cashout_net_equals_calculation'),  # Monetary invariant
        Index('ix_cashouts_user_status', 'user_id', 'status'),
        Index('ix_cashouts_status_created', 'status', 'created_at'),
        Index('ix_cashouts_admin_approval', 'admin_approved', 'status'),
    )


class Refund(Base):
    """Audit table for all refund operations to prevent double refunds"""
    __tablename__ = "refunds"

    # Primary key (PRESERVED - never change type)
    id = Column(Integer, primary_key=True)
    refund_id = Column(String(20), unique=True, nullable=False, index=True)  # Legacy public ID
    utid = Column(String(32), unique=True, nullable=True, index=True)  # Universal Transaction ID
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    
    # Refund details
    refund_type = Column(String(20), nullable=False)  # RefundType enum value
    amount = Column(Numeric(38, 18), nullable=False)
    currency = Column(String(10), nullable=False, default="USD")
    reason = Column(Text, nullable=False)
    
    # Related entity tracking
    cashout_id = Column(String(20), ForeignKey("cashouts.cashout_id"), nullable=True)
    escrow_id = Column(Integer, ForeignKey("escrows.id"), nullable=True) 
    transaction_id = Column(String(20), nullable=True)  # Original transaction reference
    
    # Status and processing
    status = Column(String(20), default=RefundStatus.PENDING.value, nullable=False, index=True)
    
    # Idempotency tracking
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    processed_by = Column(String(50), nullable=False)  # Service/module that processed
    
    # Balance tracking for verification
    balance_before = Column(Numeric(38, 18), nullable=False)
    balance_after = Column(Numeric(38, 18), nullable=False)
    
    # Admin approval for large refunds
    admin_approved = Column(Boolean, default=False, nullable=False)
    admin_approved_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    admin_approved_at = Column(DateTime(timezone=True), nullable=True)
    
    # Error handling
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    
    # CRITICAL FIX: Archival fields for audit trail preservation
    archived_at = Column(DateTime(timezone=True), nullable=True)  # When record was archived instead of deleted
    archive_reason = Column(String(100), nullable=True)  # Reason for archival
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    cashout = relationship("Cashout", foreign_keys=[cashout_id])
    admin_approver = relationship("User", foreign_keys=[admin_approved_by])
    
    # Constraints
    __table_args__ = (
        Index("idx_refund_user_type", "user_id", "refund_type"),
        Index("idx_refund_status_created", "status", "created_at"),
        Index("idx_refund_cashout", "cashout_id"),
        Index("idx_refund_idempotency", "idempotency_key"),
        CheckConstraint('amount > 0', name='positive_refund_amount'),
    )

    def __repr__(self):
        return f"<Refund(id={self.refund_id}, user_id={self.user_id}, amount={self.amount}, type={self.refund_type})>"


class EscrowRefundOperation(Base):
    """
    Ledger table for escrow refund deduplication - ARCHITECT'S SOLUTION
    Prevents double refunds with unique constraint on (escrow_id, buyer_id, refund_cycle_id)
    """
    __tablename__ = "escrow_refund_operations"
    
    id = Column(Integer, primary_key=True)
    
    # CRITICAL: Unique constraint fields for deduplication
    escrow_id = Column(Integer, ForeignKey("escrows.id"), nullable=False, index=True)
    buyer_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    refund_cycle_id = Column(String(64), nullable=False)  # Generated from escrow_id + refund_reason (deterministic)
    
    # Refund operation details
    refund_reason = Column(String(100), nullable=False)  # 'expired_timeout', 'buyer_cancelled', etc.
    amount_refunded = Column(Numeric(38, 18), nullable=False)
    currency = Column(String(10), nullable=False)
    
    # Transaction references for audit trail
    transaction_id = Column(String(50), nullable=True)  # Credit transaction ID
    idempotency_key = Column(String(128), nullable=False, index=True)
    
    # Processing metadata
    processed_by_service = Column(String(100), nullable=False)  # 'RefundService', 'CleanupExpiryEngine', etc.
    processing_context = Column(JSONB, nullable=True)  # Additional context for debugging
    
    # Status tracking
    status = Column(String(20), default="completed", nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    escrow = relationship("Escrow", foreign_keys=[escrow_id])
    buyer = relationship("User", foreign_keys=[buyer_id])
    
    # CRITICAL: Multiple unique constraints prevent double refunds
    __table_args__ = (
        # Primary deduplication constraint (original)
        UniqueConstraint('escrow_id', 'buyer_id', 'refund_cycle_id', name='uq_escrow_refund_dedup'),
        # ARCHITECT'S STRONGEST PROTECTION: Prevent ANY refund for same escrow+reason
        UniqueConstraint('escrow_id', 'refund_reason', name='uq_escrow_refund_reason'),
        Index('idx_escrow_refund_ops_escrow', 'escrow_id'),
        Index('idx_escrow_refund_ops_buyer', 'buyer_id'),
        Index('idx_escrow_refund_ops_reason', 'refund_reason'),
        Index('idx_escrow_refund_ops_status_created', 'status', 'created_at'),
        CheckConstraint('amount_refunded > 0', name='ck_escrow_refund_amount_positive'),
    )
    
    def __repr__(self):
        return f"<EscrowRefundOperation(escrow_id={self.escrow_id}, buyer_id={self.buyer_id}, amount={self.amount_refunded})>"


# ============================================================================
# OPERATIONAL ENTITIES (Essential for system safety)
# ============================================================================

class DistributedLock(Base):
    """Database-backed distributed lock with atomic guarantees"""
    __tablename__ = 'distributed_locks'
    
    id = Column(Integer, primary_key=True, index=True)
    lock_name = Column(String(255), nullable=False, index=True)  # FIXED: Removed unique constraint that doesn't exist in DB
    locked_by = Column(String(255), nullable=True)  # FIXED: Match database schema (was owner_token)
    locked_at = Column(DateTime(timezone=False), nullable=True, server_default=func.now())  # FIXED: Match database schema (was acquired_at)
    expires_at = Column(DateTime(timezone=False), nullable=False)  # FIXED: Match database timezone
    lock_metadata = Column('metadata', JSONB, nullable=True)  # FIXED: Use different field name but map to 'metadata' column
    
    __table_args__ = (
        Index('ix_distributed_locks_expires_at', 'expires_at'),
    )


class IdempotencyToken(Base):
    """Idempotency protection for financial operations"""
    __tablename__ = 'idempotency_tokens'
    
    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    operation_type = Column(String(100), nullable=False)
    resource_id = Column(String(255), nullable=False)
    status = Column(String(50), default='processing', nullable=False)
    result_data = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    metadata_json = Column(Text, nullable=True)
    
    __table_args__ = (
        Index('ix_idempotency_tokens_operation_resource', 'operation_type', 'resource_id'),
        Index('ix_idempotency_tokens_status', 'status'),
        Index('ix_idempotency_tokens_expires_at', 'expires_at'),
    )


class WebhookEventLedger(Base):
    """Webhook Event Ledger for comprehensive webhook idempotency protection"""
    __tablename__ = 'webhook_event_ledger'
    
    id = Column(Integer, primary_key=True, index=True)
    event_provider = Column(String(50), nullable=False, index=True)
    event_id = Column(String(255), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)  # Required field for webhook categorization
    payload = Column(JSONB, nullable=False)  # CRITICAL FIX: Missing NOT NULL JSONB field that exists in database
    txid = Column(String(255), nullable=True, index=True)
    reference_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), default='processing', nullable=False, index=True)
    amount = Column(Numeric(38, 18), nullable=True)
    currency = Column(String(10), nullable=True)
    processed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    webhook_payload = Column(Text, nullable=True)
    processing_result = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    user_id = Column(BigInteger, nullable=True, index=True)
    event_metadata = Column(JSON, nullable=True)
    processing_duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    __table_args__ = (
        UniqueConstraint('event_provider', 'event_id', name='uq_webhook_event_provider_id'),
        Index('ix_webhook_event_ledger_provider_status', 'event_provider', 'status'),
        Index('ix_webhook_event_ledger_txid_reference', 'txid', 'reference_id'),
        Index('ix_webhook_event_ledger_processed_at', 'processed_at'),
        Index('ix_webhook_event_ledger_user_id_status', 'user_id', 'status'),
        Index('ix_webhook_event_ledger_created_status', 'created_at', 'status'),
    )


# ============================================================================
# SUPPORTING ENTITIES
# ============================================================================

class PaymentAddress(Base):
    """Generated payment addresses for deposits"""
    __tablename__ = 'payment_addresses'
    
    # Primary key (PRESERVED - never change type)
    id = Column(Integer, primary_key=True, autoincrement=True)
    utid = Column(String(32), unique=True, nullable=True, index=True)  # Universal Transaction ID
    
    # Address details
    address = Column(String(255), unique=True, nullable=False, index=True)
    currency = Column(String(10), nullable=False)
    provider = Column(String(20), nullable=False)  # dynopay, blockbee
    
    # Usage tracking
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=True, index=True)
    escrow_id = Column(Integer, ForeignKey('escrows.id'), nullable=True, index=True)
    is_used = Column(Boolean, default=False, nullable=False)
    
    # Provider metadata
    provider_data = Column(JSONB, nullable=True)  # Provider-specific details
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Indexes
    __table_args__ = (
        Index('ix_payment_addresses_currency_provider', 'currency', 'provider'),
        Index('ix_payment_addresses_user_used', 'user_id', 'is_used'),
    )


class NotificationQueue(Base):
    """Outbound notifications (email, SMS, Telegram)"""
    __tablename__ = 'notification_queue'
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Recipient and content
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    channel = Column(String(20), nullable=False)  # email, sms, telegram
    recipient = Column(String(255), nullable=False)  # email/phone/telegram_id
    
    # Message details
    subject = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    template_name = Column(String(100), nullable=True)
    template_data = Column(JSONB, nullable=True)
    
    # Status and delivery
    status = Column(String(20), default='pending', nullable=False)
    priority = Column(Integer, default=1, nullable=False)  # 1=high, 5=low
    scheduled_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    
    # Error handling
    retry_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    
    # Idempotency protection (prevents duplicate notifications from being enqueued)
    idempotency_key = Column(String(255), nullable=True, index=True)  # e.g., "escrow_{escrow_id}_payment_confirmed"
    
    # Indexes
    __table_args__ = (
        Index('ix_notifications_status_priority', 'status', 'priority'),
        Index('ix_notifications_scheduled', 'scheduled_at'),
        Index('ix_notifications_user_channel', 'user_id', 'channel'),
    )


class AuditLog(Base):
    """System audit trail for compliance and debugging"""
    __tablename__ = 'audit_logs'
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Event details
    event_type = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(50), nullable=False)  # user, escrow, transaction, etc.
    entity_id = Column(String(50), nullable=False)
    
    # User context
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=True, index=True)
    admin_id = Column(BigInteger, nullable=True, index=True)  # For admin actions
    
    # Change tracking
    previous_state = Column(JSONB, nullable=True)
    new_state = Column(JSONB, nullable=True)
    changes = Column(JSONB, nullable=True)  # Diff of changes
    
    # Context and metadata
    description = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)  # IPv6 compatible
    user_agent = Column(Text, nullable=True)
    extra_data = Column(JSONB, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Indexes
    __table_args__ = (
        Index('ix_audit_event_entity', 'event_type', 'entity_type'),
        Index('ix_audit_user_created', 'user_id', 'created_at'),
        Index('ix_audit_entity_id', 'entity_type', 'entity_id'),
        Index('ix_audit_created', 'created_at'),
    )


class SystemConfig(Base):
    """System configuration settings"""
    __tablename__ = 'system_config'
    
    # Primary key
    key = Column(String(100), primary_key=True)
    
    # Value and metadata
    value = Column(Text, nullable=False)
    value_type = Column(String(20), default='string', nullable=False)  # string, int, float, bool, json
    description = Column(Text, nullable=True)
    
    # Access control
    is_public = Column(Boolean, default=False, nullable=False)  # Can non-admins read?
    is_encrypted = Column(Boolean, default=False, nullable=False)  # Is value encrypted?
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Indexes
    __table_args__ = (
        Index('ix_system_config_public', 'is_public'),
    )


# ============================================================================
# SESSION AND TEMPORARY DATA
# ============================================================================

class UserSession(Base):
    """User session data for Telegram bot conversations"""
    __tablename__ = 'user_sessions'
    
    # Primary key
    session_id = Column(String(100), primary_key=True)
    
    # Session details
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    session_type = Column(String(50), nullable=False)  # onboarding, escrow_create, etc.
    status = Column(String(20), default='active', nullable=False)
    
    # Session data
    data = Column(JSONB, nullable=True)  # Conversation state
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_accessed = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Indexes
    __table_args__ = (
        Index('ix_user_sessions_user_type', 'user_id', 'session_type'),
        Index('ix_user_sessions_expires', 'expires_at'),
    )


class SavedAddress(Base):
    """Saved cryptocurrency cashout addresses"""
    __tablename__ = "saved_addresses"

    # Primary key (PRESERVED - never change type)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    network: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    # Verification
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Security
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], back_populates="saved_addresses")

    # Constraints
    __table_args__ = (
        UniqueConstraint("user_id", "address", name="uq_user_address"),
        Index("idx_saved_address_user_currency", "user_id", "currency"),
        Index("idx_saved_address_active", "is_active"),
    )

    def __repr__(self):
        return f"<SavedAddress(user_id={self.user_id}, currency={self.currency}, label={self.label})>"


class SavedBankAccount(Base):
    """Saved bank accounts for NGN cashouts - Enhanced to match SavedAddress functionality"""
    __tablename__ = "saved_bank_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(10), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    # Enhanced fields for full feature parity with SavedAddress
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Verification (matches SavedAddress pattern)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps (matches SavedAddress pattern)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], back_populates="saved_bank_accounts")

    # Constraints (enhanced with new indexes for performance)
    __table_args__ = (
        UniqueConstraint("user_id", "account_number", name="uq_user_account"),
        Index("idx_saved_bank_user", "user_id"),
        Index("idx_saved_bank_active", "is_active"),
        Index("idx_saved_bank_default", "user_id", "is_default"),
    )

    def __repr__(self):
        return f"<SavedBankAccount(user_id={self.user_id}, bank={self.bank_name}, account={self.account_number})>"


class EmailVerification(Base):
    """Email verification codes"""
    __tablename__ = 'email_verifications'
    
    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Verification details
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    verification_code: Mapped[str] = mapped_column(String(10), nullable=False)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)  # registration, cashout, etc.
    
    # Status and attempts
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Indexes
    __table_args__ = (
        Index('ix_email_verifications_user_purpose', 'user_id', 'purpose'),
        Index('ix_email_verifications_expires', 'expires_at'),
        Index('ix_email_verifications_code', 'verification_code'),
    )


class OTPVerification(Base):
    """OTP verification codes for cashouts and secure operations"""
    __tablename__ = 'otp_verifications'
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Verification details
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    otp_code = Column(String(10), nullable=False)
    verification_type = Column(String(50), nullable=False)  # cashout, ngn_cashout, etc.
    context_data = Column(Text, nullable=True)  # JSON string with additional context
    
    # Status
    is_verified = Column(Boolean, default=False, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    
    # Indexes and constraints
    __table_args__ = (
        UniqueConstraint('user_id', 'verification_type', name='uq_otp_user_type'),
        Index('ix_otp_verifications_user_type', 'user_id', 'verification_type'),
        Index('ix_otp_verifications_expires', 'expires_at'),
    )


class PendingCashout(Base):
    """
    Temporary storage for crypto cashout confirmation data.
    Provides persistence across bot restarts to fix "Invalid confirmation request" errors.
    Records expire after 10 minutes for security.
    """
    __tablename__ = "pending_cashouts"
    
    # Primary key
    id = Column(Integer, primary_key=True)
    
    # Token and signature fields
    token = Column(Text, nullable=False)
    signature = Column(String(16), nullable=False)  # Exactly 16 characters for HMAC signature
    
    # User and cashout details
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(38, 18), nullable=False)
    currency = Column(String(10), nullable=False, default='USDT')
    withdrawal_address = Column(String(255), nullable=False)
    network = Column(String(20), nullable=False, default='ETH')
    
    # Fee calculations
    fee_amount = Column(Numeric(38, 18), nullable=True)
    net_amount = Column(Numeric(38, 18), nullable=True)
    fee_breakdown = Column(Text, nullable=True)  # Human readable fee description
    
    # Metadata storage for extended cashout data (using cashout_metadata to avoid SQLAlchemy reserved name)
    cashout_metadata = Column("metadata", JSONB, nullable=True)  # Store additional context like is_saved_address, address_label, etc.
    
    # Timestamps for security and cleanup
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)  # 10 minute expiry for security
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="pending_cashouts")
    
    # Constraints and indexes
    __table_args__ = (
        Index("idx_pending_cashout_user", "user_id"),
        Index("idx_pending_cashout_expires", "expires_at"),
        Index("idx_pending_cashout_created", "created_at"),
    )
    
    def __repr__(self):
        return f"<PendingCashout(token={self.token[:8]}..., user_id={self.user_id}, amount={self.amount} {self.currency})>"


class ExchangeOrder(Base):
    """Direct exchange orders for crypto-to-fiat conversions"""
    __tablename__ = "exchange_orders"

    id = Column(Integer, primary_key=True)
    utid = Column(String(50), nullable=True)  # Unique transaction ID
    exchange_id = Column(String(20), nullable=True, unique=True)  # Unique exchange ID for callbacks (EX000001)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)  # bigint in DB
    order_type = Column(String(50), nullable=False)
    source_currency = Column(String(10), nullable=False)
    source_amount = Column(Numeric(38, 18), nullable=False)
    source_network = Column(String(50), nullable=True)
    target_currency = Column(String(10), nullable=False)
    target_amount = Column(Numeric(38, 18), nullable=False)
    target_network = Column(String(50), nullable=True)
    exchange_rate = Column(Numeric(38, 18), nullable=False)
    markup_percentage = Column(Numeric(5, 2), nullable=False)
    fee_amount = Column(Numeric(38, 18), nullable=False)
    final_amount = Column(Numeric(38, 18), nullable=False)
    # SECURITY FIX: USD validation field for amount consistency
    usd_equivalent = Column(Numeric(38, 18), nullable=True)
    rate_locked_at = Column(DateTime(timezone=True), nullable=True)
    rate_lock_expires_at = Column(DateTime(timezone=True), nullable=True)
    rate_lock_duration_minutes = Column(Integer, nullable=True)
    crypto_address = Column(String(200), nullable=True)
    bank_account = Column(Text, nullable=True)
    wallet_address = Column(String(200), nullable=True)
    deposit_tx_hash = Column(String(200), nullable=True)
    payout_tx_hash = Column(String(200), nullable=True)
    bank_reference = Column(String(100), nullable=True)
    
    # Status and provider
    status = Column(String(255), default="pending", nullable=True)
    provider = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])

    # Indexes
    __table_args__ = (
        Index('ix_exchange_orders_user', 'user_id'),
        Index('ix_exchange_orders_utid', 'utid'),
        Index('ix_exchange_orders_exchange_id', 'exchange_id'),
    )

    def __repr__(self):
        return f"<ExchangeOrder(id={self.id}, user_id={self.user_id}, source={self.source_amount} {self.source_currency})>"


class IdempotencyKey(Base):
    """Prevent duplicate financial operations across bot restarts"""
    __tablename__ = "idempotency_keys"

    id = Column(Integer, primary_key=True)
    operation_key = Column(String(255), unique=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    operation_type = Column(
        String(50), nullable=False
    )  # 'cashout', 'escrow_create', 'deposit'
    entity_id = Column(String(50), nullable=True)  # ID of affected entity

    # Operation result for replay protection
    result_data = Column(JSONB, nullable=True)
    success = Column(Boolean, nullable=True)
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # Indexes
    __table_args__ = (
        Index('ix_idempotency_keys_operation_key', 'operation_key'),
        Index('ix_idempotency_keys_user', 'user_id'),
        Index('ix_idempotency_keys_operation_type', 'operation_type'),
        Index('ix_idempotency_keys_expires', 'expires_at'),
    )

    def __repr__(self):
        return f"<IdempotencyKey(operation_key={self.operation_key}, operation_type={self.operation_type})>"


class EscrowMessage(Base):
    """Messages exchanged between buyers and sellers in escrow transactions"""
    __tablename__ = "escrow_messages"

    id = Column(Integer, primary_key=True)
    escrow_id = Column(Integer, ForeignKey("escrows.id"), nullable=True)
    sender_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    content = Column(Text, nullable=False)
    message_type = Column(String(255), default="text", nullable=True)  # text, image, file
    attachments = Column(JSONB, nullable=True)  # For file attachments
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=True)
    
    # Relationships
    escrow = relationship("Escrow", foreign_keys=[escrow_id])
    sender = relationship("User", foreign_keys=[sender_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_escrow_messages_escrow', 'escrow_id'),
        Index('ix_escrow_messages_sender', 'sender_id'),
        Index('ix_escrow_messages_created', 'created_at'),
    )

    def __repr__(self):
        return f"<EscrowMessage(escrow_id={self.escrow_id}, sender_id={self.sender_id}, type={self.message_type})>"


class Dispute(Base):
    """Dispute resolution system for escrow transactions"""
    __tablename__ = "disputes"

    id = Column(Integer, primary_key=True)
    escrow_id = Column(Integer, ForeignKey("escrows.id"), nullable=True)
    initiator_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    respondent_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    dispute_type = Column(String(255), nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(String(255), default="open", nullable=True)
    admin_assigned_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    resolution = Column(Text, nullable=True)
    
    # Timestamps
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=True)
    
    # Relationships
    escrow = relationship("Escrow", foreign_keys=[escrow_id])
    initiator = relationship("User", foreign_keys=[initiator_id])
    respondent = relationship("User", foreign_keys=[respondent_id])
    admin_assigned = relationship("User", foreign_keys=[admin_assigned_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_disputes_escrow', 'escrow_id'),
        Index('ix_disputes_initiator', 'initiator_id'),
        Index('ix_disputes_status', 'status'),
        Index('ix_disputes_created', 'created_at'),
    )

    def __repr__(self):
        return f"<Dispute(escrow_id={self.escrow_id}, status={self.status}, initiator_id={self.initiator_id})>"


class DisputeMessage(Base):
    """Messages in dispute resolution system"""
    __tablename__ = "dispute_messages"

    id = Column(Integer, primary_key=True)
    dispute_id = Column(Integer, ForeignKey("disputes.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    dispute = relationship("Dispute", foreign_keys=[dispute_id])
    sender = relationship("User", foreign_keys=[sender_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_dispute_messages_dispute', 'dispute_id'),
        Index('ix_dispute_messages_sender', 'sender_id'),
        Index('ix_dispute_messages_created', 'created_at'),
    )

    def __repr__(self):
        return f"<DisputeMessage(dispute_id={self.dispute_id}, sender_id={self.sender_id})>"


class UnifiedTransaction(Base):
    """
    Unified transaction model supporting all transaction types with standardized lifecycle
    Matches the actual database schema for unified_transactions table
    """
    __tablename__ = "unified_transactions"
    
    # Primary identification
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    
    # Transaction classification
    transaction_type = Column(String(25), nullable=False, index=True)  # UnifiedTransactionType enum
    status = Column(String(20), nullable=False, index=True)  # UnifiedTransactionStatus enum
    
    # Core transaction details
    amount = Column(Numeric(38, 18), nullable=False)  # Primary transaction amount
    currency = Column(String(10), nullable=False)  # USD, NGN, BTC, etc.
    fee = Column(Numeric(38, 18), nullable=True)  # Platform fees charged
    description = Column(Text, nullable=True)  # Human-readable transaction description
    
    # Processing metadata
    phase = Column(String(20), nullable=True)  # Transaction phase
    external_id = Column(String(255), nullable=True)  # External API reference
    reference_id = Column(String(255), nullable=True, index=True)  # Reference identifier
    
    # Flexible data storage  
    transaction_metadata = Column("metadata", JSONB, nullable=True)  # Transaction metadata
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    status_history = relationship("UnifiedTransactionStatusHistory", back_populates="transaction", 
                                 cascade="all, delete-orphan")
    retry_logs = relationship("UnifiedTransactionRetryLog", back_populates="transaction",
                             cascade="all, delete-orphan")
    
    # Indexes for performance
    __table_args__ = (
        Index('ix_unified_transactions_user_status', 'user_id', 'status'),
        Index('ix_unified_transactions_type_status', 'transaction_type', 'status'),
        Index('ix_unified_transactions_external_id', 'external_id'),
        Index('ix_unified_transactions_created_at', 'created_at'),
    )

    def __repr__(self):
        return f"<UnifiedTransaction(id={self.id}, type={self.transaction_type}, status={self.status}, amount={self.amount} {self.currency})>"


class Rating(Base):
    """User rating system for escrow transactions"""
    __tablename__ = "ratings"

    id = Column(Integer, primary_key=True)
    escrow_id = Column(Integer, ForeignKey("escrows.id"), nullable=False)
    rater_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)  # Who gave the rating
    rated_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)  # Who received the rating (NULL for trade ratings)

    # Rating details
    rating = Column(Integer, nullable=False)  # 1-5 stars
    comment = Column(Text, nullable=True)
    category = Column(String(20), nullable=False)  # 'buyer', 'seller', 'trade'

    # Dispute rating context (for analytics and abuse mitigation)
    is_dispute_rating = Column(Boolean, nullable=False, server_default='false')
    dispute_outcome = Column(String(20), nullable=True)  # 'winner', 'loser', 'neutral'
    dispute_resolution_type = Column(String(20), nullable=True)  # 'refund', 'release', 'split'

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    rater = relationship("User", foreign_keys=[rater_id])
    rated = relationship("User", foreign_keys=[rated_id])
    escrow = relationship("Escrow", foreign_keys=[escrow_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_ratings_escrow', 'escrow_id'),
        Index('ix_ratings_rater', 'rater_id'),
        Index('ix_ratings_rated', 'rated_id'),
        Index('ix_ratings_created', 'created_at'),
        Index('ix_ratings_dispute', 'is_dispute_rating'),
    )

    def __repr__(self):
        return f"<Rating(escrow_id={self.escrow_id}, rating={self.rating}, rater_id={self.rater_id})>"


class UnifiedTransactionStatusHistory(Base):
    """
    Audit trail for all status changes in unified transactions
    Provides complete lifecycle tracking and debugging capabilities
    """
    __tablename__ = "unified_transaction_status_history"
    
    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("unified_transactions.id"), 
                           nullable=False, index=True)
    
    # Status change details
    from_status = Column(String(20), nullable=True)  # Previous status (null for initial)
    to_status = Column(String(20), nullable=False)   # New status
    change_reason = Column(String(100), nullable=False)  # Reason for status change
    
    # Context and tracking
    changed_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)  # User who triggered change
    system_action = Column(Boolean, default=False, nullable=False)  # True for automatic changes
    
    # Timestamps
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    transaction = relationship("UnifiedTransaction", foreign_keys=[transaction_id])
    user = relationship("User", foreign_keys=[changed_by])
    
    # Indexes
    __table_args__ = (
        Index('ix_transaction_status_history_transaction', 'transaction_id'),
        Index('ix_transaction_status_history_changed_at', 'changed_at'),
        Index('ix_transaction_status_history_to_status', 'to_status'),
    )

    def __repr__(self):
        return f"<UnifiedTransactionStatusHistory(transaction_id={self.transaction_id}, {self.from_status} -> {self.to_status})>"


class UnifiedTransactionRetryLog(Base):
    """
    Detailed logging for retry attempts in unified transactions
    Supports debugging and retry strategy optimization
    """
    __tablename__ = "unified_transaction_retry_logs"
    
    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("unified_transactions.id"),
                           nullable=False, index=True)
    
    # Retry attempt details
    retry_attempt = Column(Integer, nullable=False)  # 1, 2, 3, etc.
    retry_reason = Column(String(100), nullable=False)  # Why retry was triggered
    
    # Error information
    error_code = Column(String(50), nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    error_details = Column(JSONB, nullable=True)  # Structured error data
    
    # Retry strategy
    retry_strategy = Column(String(50), nullable=False)  # exponential, linear, immediate
    delay_seconds = Column(Integer, nullable=False)  # Delay before this retry
    next_retry_at = Column(DateTime(timezone=True), nullable=True)  # When next retry scheduled
    
    # External context
    external_provider = Column(String(50), nullable=True)  # Which provider failed
    external_response_code = Column(String(20), nullable=True)  # HTTP status, etc.
    external_response_body = Column(Text, nullable=True)  # Provider error response
    
    # Outcome
    retry_successful = Column(Boolean, nullable=True)  # True if this retry succeeded
    final_retry = Column(Boolean, default=False, nullable=False)  # True if max retries reached
    
    # Timing
    attempted_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)  # How long this retry took
    
    # Relationships
    transaction = relationship("UnifiedTransaction", back_populates="retry_logs")
    
    # Indexes
    __table_args__ = (
        Index("idx_unified_retry_transaction", "transaction_id", "retry_attempt"),
        Index("idx_unified_retry_error_code", "error_code", "attempted_at"),
        Index("idx_unified_retry_provider", "external_provider", "attempted_at"),
        Index("idx_unified_retry_next", "next_retry_at"),
        Index("idx_unified_retry_final", "final_retry", "attempted_at"),
    )


class EscrowHolding(Base):
    """Tracks actual funds held in escrow - separate from escrow metadata"""
    __tablename__ = "escrow_holdings"
    
    id = Column(Integer, primary_key=True)
    escrow_id = Column(String(50), nullable=False, index=True)  # Links to escrows table
    amount_held = Column(DECIMAL(20, 8), nullable=False)  # Actual amount held for this escrow
    currency = Column(String(10), nullable=False, default="USD")
    
    # CRITICAL FIX: Overpayment audit trail tracking
    overpayment_amount = Column(DECIMAL(20, 8), nullable=True)  # Amount overpaid if any
    overpayment_currency = Column(String(10), nullable=True)  # Currency of overpayment
    overpayment_usd_value = Column(DECIMAL(20, 8), nullable=True)  # USD value of overpayment
    overpayment_transaction_id = Column(String(100), nullable=True)  # Reference to wallet credit transaction
    
    # CRITICAL FIX: Partial release audit trail tracking
    original_amount = Column(DECIMAL(20, 8), nullable=True)  # Original amount before any releases
    total_released = Column(DECIMAL(20, 8), nullable=False, default=0)  # Total amount released so far
    remaining_amount = Column(DECIMAL(20, 8), nullable=True)  # Calculated remaining amount
    partial_releases_count = Column(Integer, nullable=False, default=0)  # Number of partial releases
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    released_at = Column(DateTime(timezone=True), nullable=True)  # Final release timestamp
    first_release_at = Column(DateTime(timezone=True), nullable=True)  # First partial release timestamp
    released_to_user_id = Column(BigInteger, nullable=True)  # FIX: BigInteger for Telegram user IDs
    status = Column(String(20), default="held")  # held, partially_released, released, refunded


class PlatformRevenue(Base):
    """Tracks platform fees and revenue from escrow transactions"""
    __tablename__ = "platform_revenue"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    escrow_id = Column(String(50), nullable=False)
    fee_amount = Column(Numeric(38, 18), nullable=False)
    fee_currency = Column(String(10), nullable=False, default="USD")
    fee_type = Column(String(50), nullable=False)
    source_transaction_id = Column(String(100), nullable=True, index=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    __table_args__ = (
        Index('ix_platform_revenue_escrow', 'escrow_id'),
        Index('ix_platform_revenue_fee_type', 'fee_type'),
        Index('ix_platform_revenue_created', 'created_at'),
    )
    
    def __repr__(self):
        return f"<PlatformRevenue(escrow_id={self.escrow_id}, fee_amount={self.fee_amount}, fee_type={self.fee_type})>"


class SecurityAudit(Base):
    """Security audit logging for PII protection and system monitoring"""
    __tablename__ = "security_audits"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    action_type = Column(String(50), nullable=False, index=True)  # pii_access, login_attempt, etc.
    resource_type = Column(String(50), nullable=True)  # user_data, financial_record, etc.
    resource_id = Column(String(100), nullable=True)  # ID of accessed resource
    
    # Security details
    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6 address
    user_agent = Column(Text, nullable=True)  # Browser/client info
    success = Column(Boolean, nullable=False)  # Whether action succeeded
    risk_level = Column(String(20), default="low", nullable=False)  # low, medium, high, critical
    
    # Context and metadata
    description = Column(Text, nullable=True)  # Human-readable description
    context_data = Column(JSONB, nullable=True)  # Additional context data
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_security_audits_user', 'user_id'),
        Index('ix_security_audits_created_at', 'created_at'),
        Index('ix_security_audits_risk_level', 'risk_level'),
    )

    def __repr__(self):
        return f"<SecurityAudit(action_type={self.action_type}, user_id={self.user_id}, risk_level={self.risk_level})>"


class SupportTicket(Base):
    """Support ticket system"""
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(String(20), unique=True, nullable=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    subject = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default="open", nullable=False)  # open, in_progress, resolved, closed
    priority = Column(String(10), default="normal", nullable=False)  # low, normal, high, urgent
    category = Column(String(50), nullable=True)  # general, technical, billing, etc.
    
    # Admin handling
    assigned_to = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    admin_notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    assigned_admin = relationship("User", foreign_keys=[assigned_to])
    
    # Indexes
    __table_args__ = (
        Index('ix_support_tickets_user', 'user_id'),
        Index('ix_support_tickets_status', 'status'),
        Index('ix_support_tickets_created', 'created_at'),
        Index('ix_support_tickets_assigned', 'assigned_to'),
    )

    def __repr__(self):
        return f"<SupportTicket(id={self.id}, user_id={self.user_id}, status={self.status})>"


class SupportMessage(Base):
    """Messages in support tickets"""
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id"), nullable=False)
    sender_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    is_admin_reply = Column(Boolean, default=False, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    ticket = relationship("SupportTicket", foreign_keys=[ticket_id])
    sender = relationship("User", foreign_keys=[sender_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_support_messages_ticket', 'ticket_id'),
        Index('ix_support_messages_sender', 'sender_id'),
        Index('ix_support_messages_created', 'created_at'),
    )

    def __repr__(self):
        return f"<SupportMessage(ticket_id={self.ticket_id}, sender_id={self.sender_id})>"


class OutboxEvent(Base):
    """Outbox pattern for reliable event processing"""
    __tablename__ = "outbox_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(50), nullable=False)
    aggregate_id = Column(String(100), nullable=False)
    event_data = Column(JSONB, nullable=False)
    processed = Column(Boolean, default=False, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Error handling
    retry_count = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    
    # Indexes
    __table_args__ = (
        Index('ix_outbox_events_processed', 'processed'),
        Index('ix_outbox_events_event_type', 'event_type'),
        Index('ix_outbox_events_aggregate_id', 'aggregate_id'),
        Index('ix_outbox_events_created_at', 'created_at'),
    )

    def __repr__(self):
        return f"<OutboxEvent(event_type={self.event_type}, aggregate_id={self.aggregate_id}, processed={self.processed})>"


class AdminActionToken(Base):
    """Secure tokens for admin email actions on failed transactions"""
    __tablename__ = "admin_action_tokens"
    
    id = Column(Integer, primary_key=True)
    
    # Token and action details
    token = Column(String(64), unique=True, nullable=False)  # Cryptographically secure token
    action = Column(String(20), nullable=False)  # RETRY, REFUND, DECLINE
    
    # Transaction reference
    cashout_id = Column(String(20), nullable=False, index=True)
    
    # Admin identification
    admin_email = Column(String(255), nullable=False, index=True)
    admin_user_id = Column(BigInteger, nullable=True, index=True)  # Telegram admin ID if available
    
    # Security and lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Audit trail
    used_by_ip = Column(String(45), nullable=True)  # IPv4/IPv6 address
    used_by_user_agent = Column(Text, nullable=True)
    
    # Action result tracking
    action_result = Column(String(20), nullable=True)  # PENDING, SUCCESS, FAILED
    error_message = Column(Text, nullable=True)  # Error details if action failed
    completed_at = Column(DateTime(timezone=True), nullable=True)  # When action completed
    
    # Indexes
    __table_args__ = (
        Index('ix_admin_action_tokens_token', 'token'),
        Index('ix_admin_action_tokens_expires', 'expires_at'),
        Index('ix_admin_action_tokens_created', 'created_at'),
    )

    def __repr__(self):
        return f"<AdminActionToken(token={self.token[:8]}..., action={self.action}, cashout_id={self.cashout_id})>"


class InboxWebhookStatus(Enum):
    """Status for inbox webhook processing ensuring idempotency"""
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class InboxWebhook(Base):
    """
    Webhook Inbox Pattern for idempotent webhook processing
    
    Ensures that webhooks are processed exactly once even if received multiple times
    from external providers (Fincra, Kraken, BlockBee, DynoPay).
    """
    __tablename__ = "inbox_webhooks"
    
    # Primary identifiers
    id = Column(Integer, primary_key=True)
    webhook_id = Column(String(100), unique=True, nullable=False, index=True)  # Provider's webhook ID
    
    # Webhook source
    provider = Column(String(20), nullable=False, index=True)  # 'fincra', 'kraken', 'blockbee'
    event_type = Column(String(50), nullable=False, index=True)  # 'payment_confirmed', 'payout_completed'
    status = Column(String(20), default="received", nullable=False)  # InboxWebhookStatus enum
    
    # Webhook data
    raw_payload = Column(JSONB, nullable=False)  # Original webhook payload
    processed_data = Column(JSONB, nullable=True)  # Extracted/transformed data
    
    # Processing metadata
    transaction_id = Column(String(50), nullable=True)  # Linked transaction
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    
    # Idempotency and timing
    first_received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    
    # Error handling
    last_error = Column(Text, nullable=True)
    error_details = Column(JSONB, nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_inbox_webhooks_provider_event', 'provider', 'event_type'),
        Index('ix_inbox_webhooks_status', 'status'),
        Index('ix_inbox_webhooks_received_at', 'first_received_at'),
        Index('ix_inbox_webhooks_transaction', 'transaction_id'),
    )

    def __repr__(self):
        return f"<InboxWebhook(webhook_id={self.webhook_id}, provider={self.provider}, status={self.status})>"


class SagaStep(Base):
    """Saga pattern step for complex transaction orchestration"""
    __tablename__ = "saga_steps"

    id = Column(Integer, primary_key=True)
    saga_id = Column(String(50), nullable=False)
    step_name = Column(String(100), nullable=False)
    status = Column(String(20), default="pending", nullable=False)  # pending, completed, failed, compensated
    step_data = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Indexes
    __table_args__ = (
        Index('ix_saga_steps_saga_id', 'saga_id'),
        Index('ix_saga_steps_status', 'status'),
        Index('ix_saga_steps_created', 'created_at'),
    )

    def __repr__(self):
        return f"<SagaStep(saga_id={self.saga_id}, step_name={self.step_name}, status={self.status})>"


class WalletHolds(Base):
    """Wallet holds for pending transactions"""
    __tablename__ = "wallet_holds"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    currency = Column(String(10), nullable=False)
    amount = Column(Numeric(38, 18), nullable=False)
    hold_type = Column(String(50), nullable=False)  # cashout, escrow, exchange
    reference_id = Column(String(100), nullable=False, index=True)  # Transaction ID
    status = Column(String(20), default="active", nullable=False)  # active, released, expired
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_wallet_holds_user', 'user_id'),
        Index('ix_wallet_holds_reference', 'reference_id'),
        Index('ix_wallet_holds_status', 'status'),
        Index('ix_wallet_holds_created', 'created_at'),
    )

    def __repr__(self):
        return f"<WalletHolds(user_id={self.user_id}, amount={self.amount} {self.currency}, status={self.status})>"


class TransactionEngineEvent(Base):
    """
    UTE Internal Events for state machine transitions and audit trails
    
    Captures all state transitions and business events within the
    Unified Transaction Engine for debugging and compliance.
    """
    __tablename__ = "transaction_engine_events"
    
    # Primary identifiers
    id = Column(Integer, primary_key=True)
    event_id = Column(String(50), unique=True, nullable=False, index=True)
    
    # Transaction context
    transaction_id = Column(Integer, ForeignKey("unified_transactions.id"), nullable=False, index=True)
    saga_id = Column(String(50), nullable=True, index=True)
    
    # Event classification
    event_type = Column(String(50), nullable=False, index=True)  # 'status_changed', 'step_completed'
    event_category = Column(String(20), nullable=False)  # 'system', 'business', 'integration'
    
    # Event data
    event_data = Column(JSONB, nullable=False)  # Event payload
    previous_state = Column(JSONB, nullable=True)  # Previous transaction state
    new_state = Column(JSONB, nullable=True)  # New transaction state
    
    # Context
    triggered_by = Column(String(50), nullable=False)  # 'user', 'system', 'webhook', 'admin'
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    transaction = relationship("UnifiedTransaction", foreign_keys=[transaction_id])
    user = relationship("User", foreign_keys=[user_id])
    
    # Indexes
    __table_args__ = (
        Index('ix_transaction_engine_events_transaction', 'transaction_id'),
        Index('ix_transaction_engine_events_type', 'event_type'),
        Index('ix_transaction_engine_events_category', 'event_category'),
        Index('ix_transaction_engine_events_created', 'created_at'),
    )

    def __repr__(self):
        return f"<TransactionEngineEvent(event_id={self.event_id}, transaction_id={self.transaction_id}, event_type={self.event_type})>"


class AuditEvent(Base):
    """Financial audit events outbox table for atomic transaction logging"""
    __tablename__ = "audit_events"
    
    id = Column(Integer, primary_key=True)
    event_id = Column(String(36), unique=True, nullable=False, index=True)  # UUID
    
    # Event classification
    event_type = Column(String(50), nullable=False)  # escrow_created, wallet_credit, exchange_order_completed, etc.
    entity_type = Column(String(50), nullable=False)  # user, escrow, exchange_order, wallet, transaction
    entity_id = Column(String(50), nullable=False)   # ID of the entity
    
    # Event data
    event_data = Column(JSONB, nullable=False)  # Complete audit payload
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    
    # Processing status
    processed = Column(Boolean, default=False, nullable=False, index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    
    # Indexes
    __table_args__ = (
        Index("idx_audit_event_processed", "processed"),
        Index("idx_audit_event_timestamp", "created_at"),
        Index("idx_audit_event_type", "event_type"),
        Index("idx_audit_event_entity", "entity_type", "entity_id"),
    )

    def __repr__(self):
        return f"<AuditEvent(event_id={self.event_id}, event_type={self.event_type}, entity_type={self.entity_type})>"


class InternalWallet(Base):
    """
    Internal wallet tracking for service provider balances
    
    Tracks balances for external payment providers like Fincra, Kraken, BlockBee
    to ensure accurate reconciliation and prevent discrepancies.
    """
    __tablename__ = 'internal_wallets'
    
    # Primary key and identification
    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Provider and currency details
    provider_name = Column(String(50), nullable=False, index=True)  # fincra, kraken, blockbee
    currency = Column(String(10), nullable=False, index=True)
    provider_account_id = Column(String(100), nullable=True)  # External account ID
    
    # Balance tracking with high precision
    available_balance = Column(DECIMAL(38, 18), default=0, nullable=False)
    locked_balance = Column(DECIMAL(38, 18), default=0, nullable=False)  # Funds in pending operations
    reserved_balance = Column(DECIMAL(38, 18), default=0, nullable=False)  # Reserved for operational needs
    total_balance = Column(DECIMAL(38, 18), default=0, nullable=False)  # Total provider balance
    
    # Operational limits and controls
    minimum_balance = Column(DECIMAL(38, 18), default=0, nullable=False)  # Minimum required balance
    withdrawal_limit = Column(DECIMAL(38, 18), nullable=True)  # Maximum withdrawal per transaction
    daily_limit = Column(DECIMAL(38, 18), nullable=True)  # Daily withdrawal limit
    
    # Status and control flags
    is_active = Column(Boolean, default=True, nullable=False)
    auto_reconcile = Column(Boolean, default=True, nullable=False)
    emergency_freeze = Column(Boolean, default=False, nullable=False)
    
    # Timestamps and version control
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_reconciled_at = Column(DateTime(timezone=True), nullable=True)
    last_balance_check_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, default=1, nullable=False)  # Optimistic locking
    
    # Metadata and configuration
    configuration = Column(Text, nullable=True)  # JSON configuration for provider-specific settings
    notes = Column(Text, nullable=True)  # Admin notes
    
    # Performance indexes and constraints
    __table_args__ = (
        UniqueConstraint('provider_name', 'currency', name='uq_provider_currency'),
        Index('idx_internal_wallet_provider', 'provider_name', 'is_active'),
        Index('idx_internal_wallet_currency', 'currency', 'is_active'),
        Index('idx_internal_wallet_balance', 'available_balance', 'currency'),
    )


class BalanceAuditLog(Base):
    """
    Comprehensive audit trail for all balance changes
    
    Logs every balance change with before/after states for complete traceability
    and regulatory compliance. Supports both user and internal wallets.
    """
    __tablename__ = 'balance_audit_logs'
    
    # Primary key and identification
    id = Column(Integer, primary_key=True, index=True)
    audit_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Target identification (user or internal wallet)
    wallet_type = Column(String(20), nullable=False, index=True)  # 'user', 'internal'
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    wallet_id = Column(Integer, nullable=True, index=True)  # References wallets.id or internal_wallets.id
    internal_wallet_id = Column(String(100), ForeignKey("internal_wallets.wallet_id"), nullable=True, index=True)
    currency = Column(String(10), nullable=False, index=True)
    
    # Balance change details
    balance_type = Column(String(20), nullable=False)  # 'available', 'frozen', 'locked', 'reserved'
    amount_before = Column(DECIMAL(20, 8), nullable=False)
    amount_after = Column(DECIMAL(20, 8), nullable=False)
    change_amount = Column(DECIMAL(20, 8), nullable=False)
    change_type = Column(String(10), nullable=False)  # 'credit', 'debit'
    
    # Transaction context
    transaction_id = Column(String(100), nullable=True, index=True)
    transaction_type = Column(String(50), nullable=False, index=True)
    operation_type = Column(String(50), nullable=False, index=True)  # 'deposit', 'withdrawal', 'escrow_lock', etc.
    
    # Audit trail information
    initiated_by = Column(String(50), nullable=False)  # 'user', 'system', 'admin', 'webhook'
    initiated_by_id = Column(String(100), nullable=True)  # User ID, admin ID, or system process
    reason = Column(Text, nullable=False)  # Human-readable reason for the change
    
    # Related entity references  
    escrow_pk = Column(Integer, ForeignKey("escrows.id"), nullable=True, index=True)  # Proper FK to escrows.id
    escrow_id = Column(String(20), nullable=True, index=True)  # Business ID for correlation (no FK)
    cashout_id = Column(String(100), nullable=True, index=True)
    exchange_id = Column(String(100), nullable=True, index=True)
    
    # Validation and safety
    balance_validation_passed = Column(Boolean, default=True, nullable=False)
    pre_validation_checksum = Column(String(64), nullable=True)  # SHA256 of pre-change state
    post_validation_checksum = Column(String(64), nullable=True)  # SHA256 of post-change state
    idempotency_key = Column(String(255), nullable=True, index=True)  # For duplicate prevention
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Metadata and context
    audit_metadata = Column(Text, nullable=True)  # JSON metadata for additional context
    ip_address = Column(String(45), nullable=True)  # Client IP if applicable (changed from INET to String)
    user_agent = Column(String(500), nullable=True)  # User agent if applicable
    api_version = Column(String(20), nullable=True)  # API version used
    
    # System context
    hostname = Column(String(100), nullable=True)  # Server hostname
    process_id = Column(String(50), nullable=True)  # Process ID
    thread_id = Column(String(50), nullable=True)  # Thread ID
    
    # Performance indexes and constraints
    __table_args__ = (
        Index('idx_balance_audit_wallet_time', 'wallet_type', 'wallet_id', 'created_at'),
        Index('idx_balance_audit_user_currency_time', 'user_id', 'currency', 'created_at'),
        Index('idx_balance_audit_transaction', 'transaction_id', 'transaction_type'),
        Index('idx_balance_audit_operation_type', 'operation_type', 'created_at'),
        Index('idx_balance_audit_change_type', 'change_type', 'currency', 'created_at'),
        Index('idx_balance_audit_validation', 'balance_validation_passed', 'created_at'),
        CheckConstraint('change_amount != 0', name='chk_non_zero_change'),
        CheckConstraint("change_type IN ('credit', 'debit')", name='chk_valid_change_type'),
        CheckConstraint("wallet_type IN ('user', 'internal')", name='chk_valid_wallet_type'),
        CheckConstraint("balance_type IN ('available', 'frozen', 'locked', 'reserved')", name='chk_valid_balance_type'),
    )


class WalletBalanceSnapshot(Base):
    """
    Periodic snapshots of wallet balances for verification and reconciliation
    
    Captures point-in-time balance states to detect discrepancies and support
    balance reconstruction in case of data corruption.
    """
    __tablename__ = 'wallet_balance_snapshots'
    
    # Primary key and identification
    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Target wallet identification
    wallet_type = Column(String(20), nullable=False, index=True)  # 'user', 'internal'
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    wallet_id = Column(Integer, nullable=True, index=True)
    internal_wallet_id = Column(String(100), ForeignKey("internal_wallets.wallet_id"), nullable=True, index=True)
    currency = Column(String(10), nullable=False, index=True)
    
    # Balance snapshot data
    available_balance = Column(DECIMAL(38, 18), nullable=False)
    frozen_balance = Column(DECIMAL(38, 18), default=0, nullable=False)
    locked_balance = Column(DECIMAL(38, 18), default=0, nullable=False)
    reserved_balance = Column(DECIMAL(38, 18), default=0, nullable=False)  # For internal wallets
    total_balance = Column(DECIMAL(38, 18), nullable=False)
    
    # Snapshot metadata
    snapshot_type = Column(String(50), nullable=False, index=True)  # 'scheduled', 'manual', 'pre_operation', 'post_operation'
    trigger_event = Column(String(100), nullable=True)  # What triggered this snapshot
    previous_snapshot_id = Column(String(100), nullable=True, index=True)  # Link to previous snapshot
    
    # Validation and integrity
    balance_checksum = Column(String(64), nullable=False)  # SHA256 checksum of all balances
    transaction_count = Column(Integer, default=0, nullable=False)  # Number of transactions at snapshot time
    last_transaction_id = Column(String(100), nullable=True)  # Last transaction ID at snapshot time
    validation_passed = Column(Boolean, default=True, nullable=False)
    validation_errors = Column(Text, nullable=True)  # JSON array of validation errors
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    valid_from = Column(DateTime(timezone=True), nullable=False, index=True)
    valid_until = Column(DateTime(timezone=True), nullable=True, index=True)
    
    # System context
    created_by = Column(String(50), nullable=False)  # 'system', 'admin', 'scheduler'
    hostname = Column(String(100), nullable=True)
    process_id = Column(String(50), nullable=True)
    
    # Metadata
    snapshot_metadata = Column(Text, nullable=True)  # JSON metadata
    notes = Column(Text, nullable=True)  # Admin notes
    
    # Performance indexes and constraints
    __table_args__ = (
        Index('idx_snapshot_wallet_time', 'wallet_type', 'wallet_id', 'created_at'),
        Index('idx_snapshot_user_currency_time', 'user_id', 'currency', 'created_at'),
        Index('idx_snapshot_type_time', 'snapshot_type', 'created_at'),
        Index('idx_snapshot_validation', 'validation_passed', 'created_at'),
        CheckConstraint('total_balance >= 0', name='chk_positive_total_balance'),
        CheckConstraint("wallet_type IN ('user', 'internal')", name='chk_snapshot_valid_wallet_type'),
    )


class BalanceReconciliationLog(Base):
    """
    Log of balance reconciliation activities and discrepancy resolution
    
    Tracks all reconciliation attempts, discovered discrepancies, and resolution actions
    for audit trail and operational monitoring.
    """
    __tablename__ = 'balance_reconciliation_logs'
    
    # Primary key and identification
    id = Column(Integer, primary_key=True, index=True)
    reconciliation_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Reconciliation scope
    reconciliation_type = Column(String(50), nullable=False, index=True)  # 'full', 'user', 'internal', 'currency'
    target_type = Column(String(20), nullable=False)  # 'user', 'internal', 'all'
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    internal_wallet_id = Column(String(100), ForeignKey("internal_wallets.wallet_id"), nullable=True, index=True)
    currency = Column(String(10), nullable=True, index=True)
    
    # Reconciliation results
    status = Column(String(50), nullable=False, index=True)  # 'started', 'completed', 'failed', 'partial'
    discrepancies_found = Column(Integer, default=0, nullable=False)
    discrepancies_resolved = Column(Integer, default=0, nullable=False)
    total_amount_discrepancy = Column(DECIMAL(20, 8), default=0, nullable=False)
    
    # Processing statistics
    wallets_checked = Column(Integer, default=0, nullable=False)
    transactions_verified = Column(Integer, default=0, nullable=False)
    snapshots_created = Column(Integer, default=0, nullable=False)
    audit_logs_created = Column(Integer, default=0, nullable=False)
    
    # Timing information
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    duration_seconds = Column(Integer, nullable=True)
    
    # Trigger and context
    triggered_by = Column(String(50), nullable=False)  # 'schedule', 'manual', 'alert', 'api'
    triggered_by_id = Column(String(100), nullable=True)  # Admin ID or system process
    trigger_reason = Column(String(200), nullable=True)  # Why reconciliation was triggered
    
    # Results and actions
    findings_summary = Column(Text, nullable=True)  # JSON summary of findings
    actions_taken = Column(Text, nullable=True)  # JSON array of corrective actions
    recommendations = Column(Text, nullable=True)  # JSON array of recommendations
    
    # Error handling
    error_count = Column(Integer, default=0, nullable=False)
    last_error_message = Column(Text, nullable=True)
    warning_count = Column(Integer, default=0, nullable=False)
    
    # System context
    hostname = Column(String(100), nullable=True)
    process_id = Column(String(50), nullable=True)
    version = Column(String(20), nullable=True)  # Reconciliation engine version
    
    # Metadata
    configuration = Column(Text, nullable=True)  # JSON reconciliation configuration
    reconciliation_metadata = Column(Text, nullable=True)  # Additional metadata
    notes = Column(Text, nullable=True)  # Admin notes
    
    # Performance indexes and constraints
    __table_args__ = (
        Index('idx_reconciliation_type_status', 'reconciliation_type', 'status'),
        Index('idx_reconciliation_user_time', 'user_id', 'started_at'),
        Index('idx_reconciliation_currency_time', 'currency', 'started_at'),
        Index('idx_reconciliation_status_time', 'status', 'started_at'),
        Index('idx_reconciliation_discrepancies', 'discrepancies_found', 'started_at'),
        CheckConstraint('discrepancies_resolved <= discrepancies_found', name='chk_valid_resolution_count'),
        CheckConstraint("status IN ('started', 'completed', 'failed', 'partial')", name='chk_valid_reconciliation_status'),
        CheckConstraint("target_type IN ('user', 'internal', 'all')", name='chk_valid_target_type'),
    )


class OnboardingSession(Base):
    """4-step onboarding state machine for managing user registration flow"""
    
    __tablename__ = "onboarding_sessions"
    
    # Primary identification
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    
    # State management
    current_step: Mapped[str] = mapped_column(String(20), default=OnboardingStep.CAPTURE_EMAIL.value, nullable=False, index=True)
    
    # Flow context and data
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)  # Email being verified
    invite_token: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)  # If started from invitation
    context_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Additional flow data
    
    # Session tracking data
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)  # User's IP address
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # User's browser/client info
    referral_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # How user found the platform
    
    # State tracking
    email_captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    otp_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    
    # Constraints and indexes
    __table_args__ = (
        Index("idx_onboarding_user", "user_id"),
        Index("idx_onboarding_step", "current_step"),
        Index("idx_onboarding_email", "email"),
        Index("idx_onboarding_created", "created_at"),
        Index("idx_onboarding_expires", "expires_at"),
    )

    def __repr__(self):
        return f"<OnboardingSession(user_id={self.user_id}, step={self.current_step}, email={self.email})>"


class UserContact(Base):
    """Additional contact methods for users to enable multi-channel notifications"""

    __tablename__ = "user_contacts"

    id = Column(Integer, primary_key=True)
    contact_id = Column(String(50), unique=True, nullable=False, index=True)

    # User relationship
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

    # Contact details
    contact_type = Column(
        String(20), nullable=False, index=True
    )  # email, phone, telegram
    contact_value = Column(
        String(255), nullable=False, index=True
    )  # actual email/phone/username

    # Verification status
    is_verified = Column(Boolean, default=False, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verification_code = Column(String(10), nullable=True)
    verification_expires = Column(DateTime(timezone=True), nullable=True)
    verification_attempts = Column(Integer, default=0, nullable=False)

    # Activity and priority tracking
    is_primary = Column(
        Boolean, default=False, nullable=False
    )  # Primary contact for this type
    last_used = Column(DateTime(timezone=True), nullable=True)

    # Status and preferences
    is_active = Column(Boolean, default=True, nullable=False)
    notifications_enabled = Column(Boolean, default=True, nullable=False)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class NotificationActivity(Base):
    """Track notification delivery and response times for intelligent channel prioritization"""

    __tablename__ = "notification_activities"

    id = Column(Integer, primary_key=True)
    activity_id = Column(String(50), unique=True, nullable=False, index=True)

    # User and notification details
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    notification_type = Column(
        String(50), nullable=False, index=True
    )  # escrow_invite, payment_received, etc.

    # Channel details
    channel_type = Column(
        String(20), nullable=False, index=True
    )  # telegram, email, sms
    channel_value = Column(
        String(255), nullable=False
    )  # email address, phone number, telegram_id

    # Activity tracking
    sent_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)  # Email opened, message read
    clicked_at = Column(DateTime(timezone=True), nullable=True)  # Link clicked, action taken
    response_time = Column(Integer, nullable=True)  # Seconds to response

    # Success metrics
    delivery_status = Column(
        String(20), default="sent", nullable=False
    )  # sent, delivered, failed, bounced
    engagement_level = Column(
        String(20), default="none", nullable=False
    )  # none, opened, clicked, responded

    # Idempotency protection (prevents duplicate notifications)
    idempotency_key = Column(String(255), nullable=True, index=True)  # e.g., "escrow_{escrow_id}_payment_confirmed"

    # Context for intelligent routing
    priority_score = Column(Float, default=1.0, nullable=False)
    device_type = Column(String(20), nullable=True)  # mobile, desktop, unknown
    location_context = Column(String(100), nullable=True)  # timezone, country

    # Metadata
    created_at = Column(DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

    # Notification channels
    telegram_enabled = Column(Boolean, default=True, nullable=False)
    email_enabled = Column(Boolean, default=True, nullable=False)

    # Notification types
    escrow_updates = Column(Boolean, default=True, nullable=False)
    payment_notifications = Column(Boolean, default=True, nullable=False)
    dispute_notifications = Column(Boolean, default=True, nullable=False)
    marketing_emails = Column(Boolean, default=False, nullable=False)
    security_alerts = Column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False
    )

    # Constraints
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_notification_pref"),)


class CryptoDeposit(Base):
    """
    Cryptocurrency deposit state machine for robust confirmation handling
    
    This model implements the architect's recommended design for handling
    unconfirmed -> confirmed transaction transitions without idempotency conflicts.
    """
    __tablename__ = "crypto_deposits"
    
    # Primary key
    id = Column(Integer, primary_key=True)
    
    # Unique transaction identification
    provider = Column(String(20), nullable=False, index=True)  # blockbee, dynopay, etc.
    txid = Column(String(100), nullable=False, index=True)     # Blockchain transaction ID
    order_id = Column(String(50), nullable=True, index=True)   # Provider order ID (WALLET-xxx)
    
    # Financial details
    address_in = Column(String(200), nullable=False)          # Receiving address
    address_out = Column(String(200), nullable=True)          # Forwarding address (if applicable)
    coin = Column(String(10), nullable=False)                 # ltc, btc, eth, etc.
    amount = Column(DECIMAL(38, 18), nullable=False)           # Cryptocurrency amount
    amount_fiat = Column(DECIMAL(38, 18), nullable=True)       # USD equivalent
    
    # Confirmation tracking
    confirmations = Column(Integer, default=0, nullable=False)
    required_confirmations = Column(Integer, default=1, nullable=False)
    
    # State machine
    status = Column(String(25), default=CryptoDepositStatus.PENDING_UNCONFIRMED.value, nullable=False, index=True)
    
    # User tracking
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)  # May be null for unattributed deposits
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    credited_at = Column(DateTime(timezone=True), nullable=True)
    
    # Indexing for efficient queries
    __table_args__ = (
        Index('idx_crypto_deposit_txid_provider', 'txid', 'provider'),
        Index('idx_crypto_deposit_status_created', 'status', 'created_at'),
        Index('idx_crypto_deposit_user_status', 'user_id', 'status'),
    )


class UserSMSUsage(Base):
    """
    Track SMS usage for trade invitations with daily limits
    """
    __tablename__ = "user_sms_usage"
    
    # Primary key
    id = Column(Integer, primary_key=True)
    
    # User reference
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    
    # Usage tracking
    date = Column(Date, nullable=False, index=True)
    sms_count = Column(Integer, default=1, nullable=False)
    last_sms_sent_at = Column(DateTime(timezone=True), nullable=True)
    phone_numbers_contacted = Column(Text, nullable=True)  # JSON string of contacted numbers
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)
    
    # Constraints and indexing
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_user_sms_usage_daily"),
        Index('idx_user_sms_usage_date', 'date'),
        Index('idx_user_sms_usage_user_date', 'user_id', 'date'),
    )


class UserAchievement(Base):
    """
    Track user achievements and milestones for gamification
    Phase 3B: Milestone Tracking & Receipt Generation
    """
    __tablename__ = "user_achievements"
    
    # Primary key
    id = Column(Integer, primary_key=True)
    
    # User reference
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    
    # Achievement details
    achievement_type = Column(String(50), nullable=False, index=True)
    achievement_name = Column(String(200), nullable=False)
    achievement_description = Column(Text, nullable=True)
    achievement_tier = Column(Integer, default=1, nullable=False)
    
    # Progress tracking
    target_value = Column(DECIMAL(20, 2), nullable=False)
    current_value = Column(DECIMAL(20, 2), default=0, nullable=False)
    achieved = Column(Boolean, default=False, nullable=False, index=True)
    
    # Rewards and display
    reward_message = Column(Text, nullable=True)
    badge_emoji = Column(String(50), nullable=True)
    points_awarded = Column(Integer, default=0, nullable=False)
    
    # Timestamps
    first_eligible_at = Column(DateTime(timezone=True), nullable=True)
    achieved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    
    # Trigger context
    trigger_transaction_id = Column(Integer, nullable=True)
    trigger_escrow_id = Column(String(20), nullable=True)
    additional_context = Column(JSON, nullable=True)
    
    # Indexing for efficient queries
    __table_args__ = (
        Index('idx_user_achievements_user_type', 'user_id', 'achievement_type'),
        Index('idx_user_achievements_achieved', 'achieved', 'achieved_at'),
    )


class UserStreakTracking(Base):
    """
    Track user activity and trade streaks for achievement system
    Phase 3B: Milestone Tracking & Receipt Generation
    """
    __tablename__ = "user_streak_tracking"
    
    # Primary key
    id = Column(Integer, primary_key=True)
    
    # User reference
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    
    # Activity streak tracking
    daily_activity_streak = Column(Integer, default=0, nullable=False)
    best_daily_activity_streak = Column(Integer, default=0, nullable=False)
    activity_reset_count = Column(Integer, default=0, nullable=False)
    last_activity_date = Column(DateTime(timezone=True), nullable=True)
    
    # Successful trades streak tracking
    successful_trades_streak = Column(Integer, default=0, nullable=False)
    best_successful_trades_streak = Column(Integer, default=0, nullable=False)
    successful_trades_reset_count = Column(Integer, default=0, nullable=False)
    last_successful_trade_date = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)
    
    # Indexing for efficient queries
    __table_args__ = (
        Index('idx_user_streak_user', 'user_id'),
        Index('idx_user_streak_activity', 'daily_activity_streak', 'last_activity_date'),
    )


class AdminOperationOverride(Base):
    """
    Admin controls for payment processor and service operations.
    Allows admins to temporarily enable/disable operations on specific providers.
    """
    __tablename__ = "admin_operation_overrides"
    
    # Primary key
    id = Column(Integer, primary_key=True)
    
    # Override configuration
    provider = Column(String(50), nullable=False, index=True)  # 'kraken', 'fincra', 'all'
    operation_type = Column(String(50), nullable=True, index=True)  # 'crypto_cashout', 'crypto_withdrawal', NULL for all
    override_type = Column(String(20), nullable=False)  # 'allow', 'block'
    
    # Admin context
    reason = Column(Text, nullable=True)  # Why this override was created
    created_by = Column(String(100), nullable=True)  # Admin email/username
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Expiry
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True)
    
    # Indexing for efficient queries
    __table_args__ = (
        Index('idx_admin_override_active', 'provider', 'operation_type', 'is_active'),
        Index('idx_admin_override_expires', 'expires_at'),
    )


class BalanceProtectionLog(Base):
    """
    Audit log for balance protection checks on financial operations.
    Tracks whether operations were allowed and why, with balance snapshots.
    """
    __tablename__ = "balance_protection_logs"
    
    # Primary key
    id = Column(Integer, primary_key=True)
    
    # Operation details
    operation_type = Column(String(50), nullable=False, index=True)  # 'crypto_cashout', 'crypto_withdrawal', 'ngn_payout'
    currency = Column(String(10), nullable=False, index=True)  # 'ETH', 'BTC', 'NGN'
    amount = Column(Numeric(38, 18), nullable=False)
    user_id = Column(BigInteger, nullable=True, index=True)  # NULL for admin operations
    
    # Decision outcome
    operation_allowed = Column(Boolean, nullable=False, index=True)
    alert_level = Column(String(20), nullable=True)  # 'critical', 'warning', 'info', NULL
    
    # Balance check results
    balance_check_passed = Column(Boolean, nullable=False)
    insufficient_services = Column(Text, nullable=True)  # Comma-separated list of services with low balance
    
    # Context and reasoning
    warning_message = Column(Text, nullable=True)
    blocking_reason = Column(Text, nullable=True)
    
    # Balance snapshots (for audit trail)
    fincra_balance = Column(Float, nullable=True)  # NGN balance snapshot
    kraken_balances = Column(JSONB, nullable=True)  # {"BTC": 0.5, "ETH": 2.3, ...}
    
    # Timestamp
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    
    # Indexing for efficient queries
    __table_args__ = (
        Index('idx_balance_log_operation', 'operation_type', 'currency'),
        Index('idx_balance_log_allowed', 'operation_allowed', 'created_at'),
        Index('idx_balance_log_user', 'user_id', 'created_at'),
    )


class AdminNotification(Base):
    """
    Database-backed queue for admin email/Telegram notifications.
    Ensures critical admin alerts are never lost due to rapid state changes.
    """
    __tablename__ = "admin_notifications"
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Notification type and category
    notification_type = Column(String(50), nullable=False, index=True)  # 'escrow_created', 'escrow_funded', 'dispute_opened', etc.
    category = Column(String(30), nullable=False)  # 'trade', 'cashout', 'dispute', 'system'
    priority = Column(Integer, default=2, nullable=False)  # 1=critical, 2=high, 3=normal, 4=low
    
    # Notification channels
    send_email = Column(Boolean, default=True, nullable=False)
    send_telegram = Column(Boolean, default=True, nullable=False)
    
    # Content
    subject = Column(String(255), nullable=False)
    html_content = Column(Text, nullable=True)  # For email
    telegram_message = Column(Text, nullable=True)  # For Telegram group
    
    # Related entity tracking
    entity_type = Column(String(50), nullable=True)  # 'escrow', 'cashout', 'dispute'
    entity_id = Column(String(100), nullable=True, index=True)  # escrow_id, cashout_id, etc.
    
    # Notification data (JSON)
    notification_data = Column(JSONB, nullable=True)  # Full context for template rendering
    
    # Status tracking
    status = Column(String(20), default='pending', nullable=False, index=True)  # pending, sent, failed, cancelled
    email_sent = Column(Boolean, default=False, nullable=False)
    telegram_sent = Column(Boolean, default=False, nullable=False)
    
    # Delivery timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)  # Delayed sending
    sent_at = Column(DateTime(timezone=True), nullable=True)
    
    # Error handling
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    
    # Idempotency (prevent duplicate notifications for same event)
    idempotency_key = Column(String(255), nullable=True, unique=True, index=True)  # e.g., "escrow_created_ES110125TQD2"
    
    # Indexes for efficient processing
    __table_args__ = (
        Index('idx_admin_notif_status_priority', 'status', 'priority', 'created_at'),
        Index('idx_admin_notif_type', 'notification_type', 'created_at'),
        Index('idx_admin_notif_retry', 'status', 'next_retry_at'),
        Index('idx_admin_notif_entity', 'entity_type', 'entity_id'),
    )


class PartnerApplication(Base):
    """Partner program applications from potential whitelabel partners"""
    __tablename__ = "partner_applications"
    
    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Contact information
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_handle: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Community details
    community_type: Mapped[str] = mapped_column(String(50), nullable=False)  # Values from CommunityType enum
    audience_size: Mapped[str] = mapped_column(String(50), nullable=False)
    primary_region: Mapped[str] = mapped_column(String(50), nullable=False)
    
    # Business expectations
    monthly_volume: Mapped[str] = mapped_column(String(50), nullable=False)
    commission_tier: Mapped[str] = mapped_column(String(20), nullable=False)  # Values from CommissionTier enum
    
    # Application details
    goals: Mapped[str] = mapped_column(Text, nullable=False)  # What they want to achieve
    
    # Review tracking
    status: Mapped[str] = mapped_column(String(20), default='new', nullable=False, index=True)  # Values from PartnerApplicationStatus enum
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now())
    
    # Indexes for efficient queries
    __table_args__ = (
        Index('idx_partner_app_status', 'status', 'created_at'),
        Index('idx_partner_app_email', 'email'),
    )


class BotGroup(Base):
    """Tracks Telegram groups the bot has been added to for event broadcasting"""
    __tablename__ = 'bot_groups'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    chat_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    chat_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    events_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('ix_bot_groups_active', 'is_active'),
    )


class PromoMessageLog(Base):
    """Tracks promotional messages sent to users to prevent duplicates"""
    __tablename__ = 'promo_message_logs'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    promo_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    __table_args__ = (
        Index('ix_promo_user_key', 'user_id', 'promo_key', unique=True),
    )


class PromoOptOut(Base):
    """Users who have opted out of promotional messages"""
    __tablename__ = 'promo_opt_outs'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    opted_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    __table_args__ = (
        Index('ix_promo_optout_user', 'user_id', unique=True),
    )
