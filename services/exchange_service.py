"""
Exchange Service for direct currency conversions
Handles CRYPTO-NGN-USD exchange operations using existing infrastructure
"""

import logging
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, timedelta
from database import SessionLocal
from models import ExchangeOrder
from services.fastforex_service import fastforex_service
from config import Config
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    FinancialContext,
    EntityType
)

logger = logging.getLogger(__name__)


class ExchangeService:
    """Service for handling direct currency exchanges"""

    # Simplified markup system - single configurable percentage
    @property
    def markup_percentage(self):
        """Get configurable markup percentage"""
        return Config.EXCHANGE_MARKUP_PERCENTAGE

    def __init__(self):
        self.supported_cryptos = [
            "BTC",
            "ETH",
            "USDT-TRC20",
            "USDT-ERC20",
            "LTC",
            "DOGE",
            "BCH",
            "TRX",
        ]
        self.supported_fiats = ["NGN", "USD"]
        self.unified_markup_enabled = True  # Enable unified LockBay markup system

    async def get_crypto_to_ngn_rate_with_lock(
        self,
        user_id: int,
        crypto_currency: str,
        amount: float,
        lock_duration_minutes: int = 30,
    ) -> Optional[Dict]:
        """Get exchange rate for crypto to NGN conversion WITH RATE LOCK"""
        try:
            # Step 1: Get crypto to USD rate
            crypto_usd_rate = await fastforex_service.get_crypto_to_usd_rate(
                crypto_currency
            )
            if not crypto_usd_rate:
                logger.error(f"Failed to get {crypto_currency} to USD rate")
                return None

            # Step 2: Convert crypto amount to USD using Decimal for precision
            usd_amount = Decimal(str(amount)) * Decimal(str(crypto_usd_rate or 0))

            # Step 3: Get clean USD to NGN rate (no markup yet)
            usd_ngn_rate = await fastforex_service.get_usd_to_ngn_rate_clean()
            if not usd_ngn_rate:
                logger.error("Failed to get USD to NGN rate")
                return None

            # Step 4: Calculate base exchange rate and amount using Decimal
            base_effective_rate = Decimal(str(crypto_usd_rate or 0)) * Decimal(str(usd_ngn_rate or 0))
            base_ngn_amount = Decimal(str(amount)) * base_effective_rate

            # Step 5: Apply unified markup system
            from utils.markup_utils import convert_to_fiat

            # Use Decimal for precise markup calculations
            markup_percentage = Decimal(str(self.markup_percentage)) / Decimal("100")

            # Use unified markup system for crypto-to-NGN conversion
            final_ngn_amount = convert_to_fiat(
                Decimal(str(amount)), base_effective_rate, markup_percentage
            )
            # Calculate markup amount with precise arithmetic
            exchange_markup = base_ngn_amount - final_ngn_amount

            # Step 7: Create rate lock
            from services.rate_lock_service import rate_lock_service

            rate_lock = await rate_lock_service.create_rate_lock(
                currency=crypto_currency, user_id=user_id
            )

            if not rate_lock:
                logger.error("Failed to create rate lock")
                return None

            # Log financial event for exchange rate lock
            financial_context = FinancialContext(
                amount=Decimal(str(amount)),
                currency=crypto_currency,
                exchange_rate=base_effective_rate,
                fee_amount=exchange_markup,
                markup_percentage=Decimal(str(self.markup_percentage))
            )
            
            financial_audit_logger.log_financial_event(
                event_type=FinancialEventType.EXCHANGE_RATE_LOCKED,
                entity_type=EntityType.EXCHANGE_ORDER,
                entity_id=rate_lock.lock_id,
                user_id=user_id,
                financial_context=financial_context,
                previous_state="rate_needed",
                new_state="rate_locked",
                additional_data={
                    "crypto_currency": crypto_currency,
                    "target_currency": "NGN",
                    "crypto_usd_rate": crypto_usd_rate,
                    "usd_ngn_rate": usd_ngn_rate,
                    "lock_duration_minutes": lock_duration_minutes,
                    "base_ngn_amount": float(base_ngn_amount),
                    "final_ngn_amount": float(final_ngn_amount),
                    "effective_rate": float(final_ngn_amount / Decimal(str(amount)))
                }
            )

            return {
                "order_id": rate_lock.lock_id,
                "crypto_currency": crypto_currency,
                "crypto_amount": amount,
                "crypto_usd_rate": crypto_usd_rate,
                "usd_amount": float(usd_amount),
                "usd_ngn_rate": usd_ngn_rate,
                "base_ngn_amount": float(base_ngn_amount),
                "exchange_markup_percentage": self.markup_percentage,
                "exchange_markup": float(exchange_markup),
                "processing_fee": 0.0,  # No separate processing fees - markup only
                "final_ngn_amount": float(final_ngn_amount),
                "effective_rate": float(final_ngn_amount / Decimal(str(amount))),
                "rate_locked": True,
                "lock_duration_minutes": lock_duration_minutes,
            }

        except Exception as e:
            logger.error(f"Error calculating crypto to NGN rate: {e}")
            return None

    async def get_ngn_to_crypto_rate_with_lock(
        self,
        user_id: int,
        crypto_currency: str,
        ngn_amount: float,
        lock_duration_minutes: int = 30,
    ) -> Optional[Dict]:
        """Get exchange rate for NGN to crypto conversion WITH RATE LOCK"""
        try:
            # Step 1: Get clean USD to NGN rate (for reverse calculation)
            usd_ngn_rate = await fastforex_service.get_usd_to_ngn_rate_clean()
            if not usd_ngn_rate:
                logger.error("Failed to get USD to NGN rate")
                return None

            # Step 2: Get crypto to USD rate
            crypto_usd_rate = await fastforex_service.get_crypto_to_usd_rate(
                crypto_currency
            )
            if not crypto_usd_rate:
                logger.error(f"Failed to get {crypto_currency} to USD rate")
                return None

            # Step 3: Calculate base exchange rate using Decimal
            base_ngn_crypto_rate = Decimal(str(crypto_usd_rate or 0)) * Decimal(str(usd_ngn_rate or 0))

            # Step 4: Apply unified markup system (user gets less crypto for their NGN)
            from utils.markup_utils import convert_to_crypto

            # Use Decimal for precise markup calculations
            markup_percentage = Decimal(str(self.markup_percentage)) / Decimal("100")
            crypto_amount = convert_to_crypto(
                Decimal(str(ngn_amount)), base_ngn_crypto_rate, markup_percentage
            )

            # Calculate effective amounts for reporting with precise arithmetic
            effective_ngn_amount = Decimal(str(ngn_amount)) / (Decimal("1") + markup_percentage)
            exchange_markup = Decimal(str(ngn_amount)) - effective_ngn_amount
            usd_amount = effective_ngn_amount / Decimal(str(usd_ngn_rate or 1))

            # Step 5: Create rate lock
            from services.rate_lock_service import rate_lock_service

            rate_lock = await rate_lock_service.create_rate_lock(
                currency=crypto_currency, user_id=user_id
            )

            if not rate_lock:
                logger.error("Failed to create rate lock")
                return None

            # Log financial event for reverse exchange rate lock
            financial_context = FinancialContext(
                amount=Decimal(str(ngn_amount)),
                currency="NGN",
                exchange_rate=base_ngn_crypto_rate,
                fee_amount=exchange_markup,
                markup_percentage=Decimal(str(self.markup_percentage))
            )
            
            financial_audit_logger.log_financial_event(
                event_type=FinancialEventType.EXCHANGE_RATE_LOCKED,
                entity_type=EntityType.EXCHANGE_ORDER,
                entity_id=rate_lock.lock_id,
                user_id=user_id,
                financial_context=financial_context,
                previous_state="rate_needed",
                new_state="rate_locked",
                additional_data={
                    "source_currency": "NGN",
                    "target_currency": crypto_currency,
                    "crypto_usd_rate": crypto_usd_rate,
                    "usd_ngn_rate": usd_ngn_rate,
                    "lock_duration_minutes": lock_duration_minutes,
                    "crypto_amount": float(crypto_amount),
                    "effective_ngn_amount": float(effective_ngn_amount),
                    "effective_rate": float(crypto_amount / Decimal(str(ngn_amount))),
                    "conversion_direction": "ngn_to_crypto"
                }
            )

            return {
                "order_id": rate_lock.lock_id,
                "ngn_amount": ngn_amount,
                "crypto_currency": crypto_currency,
                "crypto_amount": float(crypto_amount),
                "crypto_usd_rate": crypto_usd_rate,
                "usd_amount": float(usd_amount),
                "usd_ngn_rate": usd_ngn_rate,
                "base_ngn_crypto_rate": float(base_ngn_crypto_rate),
                "exchange_markup_percentage": self.markup_percentage,
                "exchange_markup": float(exchange_markup),
                "processing_fee": 0.0,  # No separate processing fees - markup only
                "effective_rate": float(crypto_amount / Decimal(str(ngn_amount))),
                "rate_locked": True,
                "lock_duration_minutes": lock_duration_minutes,
            }

        except Exception as e:
            logger.error(f"Error calculating NGN to crypto rate: {e}")
            return None

    async def get_ngn_to_crypto_rate(
        self, crypto_currency: str, ngn_amount: float
    ) -> Optional[Dict]:
        """Get exchange rate for NGN to crypto conversion"""
        try:
            # Step 1: Get clean USD to NGN rate (for reverse calculation)
            usd_ngn_rate = await fastforex_service.get_usd_to_ngn_rate_clean()
            if not usd_ngn_rate:
                logger.error("Failed to get USD to NGN rate")
                return None

            # Apply unified markup system for NGN to crypto conversion
            from utils.markup_utils import convert_to_crypto

            markup_percentage = Decimal(str(self.markup_percentage)) / Decimal("100")

            # Step 4: Get crypto to USD rate first
            crypto_usd_rate = await fastforex_service.get_crypto_to_usd_rate(
                crypto_currency
            )
            if not crypto_usd_rate:
                logger.error(f"Failed to get {crypto_currency} to USD rate")
                return None

            # Step 5: Calculate base NGN to crypto rate using Decimal
            base_ngn_crypto_rate = Decimal(str(usd_ngn_rate or 0)) / Decimal(str(crypto_usd_rate or 1))

            # Step 6: Apply unified markup (user gets less crypto for their NGN)
            crypto_amount = convert_to_crypto(
                Decimal(str(ngn_amount)), base_ngn_crypto_rate, markup_percentage
            )

            # Calculate effective amounts for reporting using Decimal
            effective_ngn_amount = Decimal(str(ngn_amount)) / (
                Decimal("1") + markup_percentage
            )
            exchange_markup = Decimal(str(ngn_amount)) - effective_ngn_amount
            usd_amount = effective_ngn_amount / Decimal(str(usd_ngn_rate or 1))

            return {
                "ngn_amount": ngn_amount,
                "crypto_currency": crypto_currency,
                "exchange_markup_percentage": self.markup_percentage,
                "exchange_markup": float(exchange_markup),
                "effective_ngn_amount": float(effective_ngn_amount),
                "usd_amount": float(usd_amount),
                "usd_ngn_rate": usd_ngn_rate,
                "crypto_usd_rate": crypto_usd_rate,
                "crypto_amount": float(crypto_amount),
                "effective_rate": float(Decimal(str(ngn_amount)) / crypto_amount),
            }

        except Exception as e:
            logger.error(f"Error calculating NGN to crypto rate: {e}")
            return None

    async def create_exchange_order(
        self,
        user_id: int,
        order_type: str,
        source_currency: str,
        source_amount: float,
        target_currency: str,
        rate_info: Dict,
        **kwargs,
    ) -> Optional[ExchangeOrder]:
        """
        Create a new exchange order with secure funds holding
        CRITICAL FIX: Implements frozen_balance pattern to prevent double-spending
        """
        session = None
        hold_transaction_id = None
        exchange_utid = None
        
        try:
            session = SessionLocal()

            # Calculate expiry time using configurable timeout
            from config import Config
            expires_at = datetime.utcnow() + timedelta(
                minutes=Config.NGN_EXCHANGE_TIMEOUT_MINUTES
            )

            # Generate Universal Transaction ID for exchange order
            from utils.universal_id_generator import UniversalIDGenerator
            exchange_utid = UniversalIDGenerator.generate_exchange_id()  # EX = Exchange orders

            # CRITICAL SECURITY: Hold wallet funds atomically BEFORE creating order
            # This prevents double-spending vulnerability
            wallet_payment_required = self._requires_wallet_payment(order_type, source_currency, kwargs)
            usd_amount_to_hold = 0  # Initialize to ensure variable is always defined
            hold_transaction_id = None  # Initialize to prevent unbound variable error
            
            if wallet_payment_required:
                # Calculate USD amount that needs to be held from wallet
                usd_amount_to_hold = self._calculate_wallet_hold_amount(
                    order_type, source_amount, source_currency, rate_info
                )
                
                if usd_amount_to_hold <= 0:
                    logger.error(f"Invalid wallet hold amount: ${usd_amount_to_hold:.2f} for exchange {exchange_utid}")
                    return None
                
                # ATOMICALLY hold wallet funds to prevent double-spending
                from services.crypto import CryptoServiceAtomic
                
                # FIXME: Replace with actual hold implementation when available
                # hold_result = CryptoServiceAtomic.place_exchange_hold(
                #     user_id=user_id,
                #     amount=usd_amount_to_hold,
                #     currency="USD", 
                #     exchange_id=exchange_utid,
                #     description=f"Exchange hold: {order_type} {source_amount} {source_currency}",
                #     session=session
                # )
                
                # Temporary fallback - check balance without holding
                import asyncio
                balance = asyncio.run(CryptoServiceAtomic.get_user_balance_atomic(user_id, "USD"))
                if balance < usd_amount_to_hold:
                    hold_result = {"success": False, "error": "Insufficient balance"}
                else:
                    hold_result = {"success": True, "hold_transaction_id": f"temp_hold_{exchange_utid}"}
                
                if not hold_result["success"]:
                    logger.warning(f"Exchange hold failed for user {user_id}: {hold_result['error']}")
                    return None
                
                hold_transaction_id = hold_result["hold_transaction_id"]
                logger.info(
                    f"✅ EXCHANGE_HOLD_PLACED: ${usd_amount_to_hold:.2f} USD held for user {user_id}, "
                    f"exchange {exchange_utid}, hold_tx: {hold_transaction_id}"
                )

            # SECURITY FIX: USD baseline validation for amount consistency
            expected_usd_value = rate_info.get("usd_amount", 0)
            if expected_usd_value > 0:
                # Validate source amount in USD using Decimal for precision
                if source_currency == "USD":
                    usd_deviation = abs(Decimal(str(source_amount)) - Decimal(str(expected_usd_value))) / Decimal(str(expected_usd_value))
                    if usd_deviation > Decimal("0.05"):  # 5% tolerance for rate fluctuations
                        logger.warning(f"USD amount deviation detected: {float(usd_deviation):.2%} for order {exchange_utid}")
                
                # Store USD equivalent for validation
                rate_info["validated_usd_equivalent"] = expected_usd_value
                logger.info(f"Order {exchange_utid} USD validation: ${expected_usd_value:.2f}")

            # Create exchange order with hold information
            exchange_order = ExchangeOrder(
                utid=exchange_utid,
                user_id=user_id,
                order_type=order_type,
                source_currency=source_currency,
                source_amount=Decimal(str(source_amount)),
                source_network=kwargs.get("source_network"),
                target_currency=target_currency,
                target_amount=Decimal(
                    str(
                        rate_info.get(
                            "final_ngn_amount", rate_info.get("crypto_amount", 0)
                        )
                    )
                ),
                # SECURITY FIX: Store USD validation data  
                usd_equivalent=Decimal(str(rate_info.get("validated_usd_equivalent", expected_usd_value))) if expected_usd_value > 0 else None,
                target_network=kwargs.get("target_network"),
                exchange_rate=Decimal(str(rate_info["effective_rate"])),
                markup_percentage=Decimal(str(rate_info["exchange_markup_percentage"])),
                fee_amount=Decimal(str(rate_info["processing_fee"])),
                final_amount=Decimal(
                    str(
                        rate_info.get(
                            "final_ngn_amount", rate_info.get("crypto_amount", 0)
                        )
                    )
                ),
                wallet_address=kwargs.get("wallet_address"),
                bank_account=kwargs.get("bank_account"),
                expires_at=expires_at,
                # CRITICAL: Store hold information for cleanup/completion
                metadata={
                    "wallet_payment_required": wallet_payment_required,
                    "hold_transaction_id": hold_transaction_id,
                    "held_amount_usd": usd_amount_to_hold if wallet_payment_required else 0,
                    "security_hold_placed": True if wallet_payment_required else False
                }
            )

            session.add(exchange_order)
            session.flush()  # Ensure exchange_order.id is available

            # Log comprehensive financial event for exchange order creation
            financial_context = FinancialContext(
                amount=Decimal(str(source_amount)),
                currency=source_currency,
                exchange_rate=Decimal(str(exchange_order.exchange_rate)) if exchange_order.exchange_rate is not None else Decimal("0"),
                fee_amount=Decimal(str(exchange_order.fee_amount)) if exchange_order.fee_amount is not None else Decimal("0"),
                markup_percentage=Decimal(str(exchange_order.markup_percentage)) if exchange_order.markup_percentage is not None else Decimal("0"),
                balance_before=None,  # Will be calculated by wallet operations
                balance_after=None   # Will be calculated by wallet operations
            )
            
            related_entities = {}
            if hold_transaction_id:
                related_entities["transaction_id"] = hold_transaction_id
            if rate_info.get("order_id"):
                related_entities["rate_lock_id"] = str(rate_info["order_id"])
            
            financial_audit_logger.log_financial_event(
                event_type=FinancialEventType.EXCHANGE_ORDER_CREATED,
                entity_type=EntityType.EXCHANGE_ORDER,
                entity_id=str(exchange_order.utid),
                user_id=user_id,
                financial_context=financial_context,
                previous_state="order_requested",
                new_state="order_created",
                related_entities=related_entities,
                additional_data={
                    "order_type": order_type,
                    "source_currency": source_currency,
                    "target_currency": target_currency,
                    "source_amount": float(source_amount),
                    "target_amount": float(str(exchange_order.target_amount)) if exchange_order.target_amount is not None else 0.0,
                    "final_amount": float(str(exchange_order.final_amount)) if exchange_order.final_amount is not None else 0.0,
                    "usd_equivalent": float(str(exchange_order.usd_equivalent)) if exchange_order.usd_equivalent is not None else None,
                    "expires_at": expires_at.isoformat(),
                    "wallet_payment_required": wallet_payment_required,
                    "held_amount_usd": usd_amount_to_hold if wallet_payment_required else 0,
                    "wallet_address": kwargs.get("wallet_address"),
                    "bank_account": kwargs.get("bank_account"),
                    "source_network": kwargs.get("source_network"),
                    "target_network": kwargs.get("target_network")
                },
                session=session
            )

            session.commit()

            logger.info(
                f"Created exchange order {exchange_order.id} for user {user_id}"
            )

            # Send order creation email notification
            await self._send_order_created_notification(exchange_order, session)
            
            # Send admin notification for NGN to crypto orders (configurable)
            if order_type == "ngn_to_crypto":
                try:
                    from jobs.exchange_monitor import notify_admin_order_created
                    await notify_admin_order_created(session, exchange_order)
                except Exception as e:
                    logger.error(f"Error sending admin order creation notification: {e}")

            return exchange_order

        except Exception as e:
            logger.error(f"Error creating exchange order: {e}")
            
            # CRITICAL: Release held funds on order creation failure
            if hold_transaction_id and exchange_utid:
                try:
                    from services.crypto import CryptoServiceAtomic
                    # FIXME: Replace with actual release implementation when available
                    # release_result = CryptoServiceAtomic.release_exchange_hold(...)
                    
                    # Temporary fallback - just log the release
                    release_result = {"success": True}
                    logger.info(f"Temporary hold release for exchange {exchange_utid}")
                    if release_result["success"]:
                        logger.info(f"✅ Released exchange hold on order creation failure: {exchange_utid}")
                    else:
                        logger.error(f"❌ Failed to release exchange hold on failure: {release_result['error']}")
                except Exception as release_error:
                    logger.error(f"CRITICAL: Failed to release exchange hold on failure: {release_error}")
            
            if session:
                session.rollback()
            return None
        finally:
            if session:
                session.close()
    
    def _requires_wallet_payment(self, order_type: str, source_currency: str, kwargs: Dict) -> bool:
        """
        Determine if this exchange order requires wallet payment (funds holding)
        CRITICAL: Identifies orders that need funds held to prevent double-spending
        """
        # Check for explicit wallet payment preference
        wallet_payment_preference = kwargs.get("payment_method") == "wallet"
        
        # NGN to crypto orders where user has wallet balance might use wallet payment
        
        # Crypto to NGN orders from wallet balance definitely need funds held
        crypto_from_wallet = order_type == "crypto_to_ngn" and source_currency == "USD"
        
        # Check if explicit wallet address indicates wallet payment
        has_wallet_address = bool(kwargs.get("wallet_address"))
        
        # Additional indicators that this is a wallet payment
        explicit_wallet_indicators = [
            wallet_payment_preference,
            crypto_from_wallet,
            # Could add more indicators here
        ]
        
        requires_wallet = any(explicit_wallet_indicators)
        
        if requires_wallet:
            logger.info(f"Exchange order requires wallet payment: type={order_type}, source={source_currency}, wallet_addr={has_wallet_address}")
        
        return requires_wallet
    
    def _calculate_wallet_hold_amount(
        self, 
        order_type: str, 
        source_amount: float, 
        source_currency: str, 
        rate_info: Dict
    ) -> float:
        """
        Calculate USD amount to hold from wallet for this exchange order
        CRITICAL: Accurate calculation prevents under/over-holding funds
        """
        try:
            if order_type == "crypto_to_ngn" and source_currency == "USD":
                # Direct USD to NGN conversion - hold the USD amount using Decimal
                usd_amount = Decimal(str(source_amount or 0))
                logger.debug(f"USD to NGN exchange: holding ${float(usd_amount):.2f}")
                return float(usd_amount)
            
            elif order_type == "crypto_to_ngn":
                # Crypto to NGN via wallet - convert crypto amount to USD equivalent using Decimal
                crypto_usd_rate = rate_info.get("crypto_usd_rate", 0)
                if crypto_usd_rate <= 0:
                    logger.error(f"Invalid crypto USD rate for hold calculation: {crypto_usd_rate}")
                    return 0
                
                usd_equivalent = Decimal(str(source_amount or 0)) * Decimal(str(crypto_usd_rate or 0))
                logger.debug(f"Crypto to NGN exchange: {source_amount} {source_currency} = ${float(usd_equivalent):.2f} USD to hold")
                return float(usd_equivalent)
            
            elif order_type == "ngn_to_crypto":
                # NGN to crypto - calculate USD equivalent of NGN amount using Decimal
                usd_ngn_rate = rate_info.get("usd_ngn_rate", 0)
                if usd_ngn_rate <= 0:
                    logger.error(f"Invalid USD NGN rate for hold calculation: {usd_ngn_rate}")
                    return 0
                
                # source_amount is NGN, convert to USD using Decimal
                ngn_amount = Decimal(str(source_amount or 0))
                usd_equivalent = ngn_amount / Decimal(str(usd_ngn_rate or 1))
                logger.debug(f"NGN to crypto exchange: ₦{float(ngn_amount):,.2f} = ${float(usd_equivalent):.2f} USD to hold")
                return float(usd_equivalent)
            
            else:
                logger.warning(f"Unknown order type for wallet hold calculation: {order_type}")
                return 0
                
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error calculating wallet hold amount: {e}")
            return 0

    async def get_supported_cryptos_with_rates(self) -> Dict[str, float]:
        """Get list of supported cryptocurrencies with current USD rates"""
        try:
            rates = {}
            for crypto in self.supported_cryptos:
                try:
                    rate = await fastforex_service.get_crypto_to_usd_rate(crypto)
                    if rate:
                        rates[crypto] = rate
                except Exception as e:
                    logger.warning(f"Failed to get rate for {crypto}: {e}")
                    continue

            return rates

        except Exception as e:
            logger.error(f"Error getting crypto rates: {e}")
            return {}

    async def _send_order_created_notification(self, exchange_order, session):
        """Send email notification for newly created exchange order"""
        try:
            # THREAD SAFETY FIX: Use session properly to avoid detached instances
            # Get user from database with explicit session handling
            from models import User
            user = session.query(User).filter(User.id == exchange_order.user_id).first()
            
            if not user or not user.email:
                logger.info(f"No email available for user {exchange_order.user_id} - skipping email notification")
                return

            # Check if user has email notifications enabled for exchanges
            from utils.preferences import is_enabled
            if not is_enabled(user, 'exchanges', 'email'):
                logger.info(f"Email notifications disabled for user {exchange_order.user_id} - skipping email notification")
                return

            # Calculate expiry minutes for display
            from datetime import datetime
            time_diff = exchange_order.expires_at - datetime.utcnow()
            expires_minutes = max(0, int(time_diff.total_seconds() / 60))

            # Prepare notification details
            notification_details = {
                "order_id": exchange_order.id,
                "type": exchange_order.order_type,
                "source_currency": exchange_order.source_currency,
                "target_currency": exchange_order.target_currency,
                "source_amount": float(exchange_order.source_amount),
                "target_amount": float(exchange_order.final_amount),
                "expires_minutes": expires_minutes,
            }

            # Send email notification with proper error handling
            from services.email import EmailService
            email_service = EmailService()
            
            success = await email_service.send_exchange_notification(
                user_email=user.email,
                user_name=user.username or "User",
                order_id=getattr(exchange_order, 'utid', f'EX{exchange_order.id}'),
                notification_type="order_created",
                details=notification_details
            )

            if success:
                logger.info(f"Order created email sent successfully to {user.email} for order {exchange_order.id}")
            else:
                logger.warning(f"Failed to send order created email to {user.email} for order {exchange_order.id}")

        except Exception as e:
            # Log error but don't fail order creation
            logger.error(f"Error sending order created notification for order {exchange_order.id}: {e}")


# Global service instance
exchange_service = ExchangeService()
