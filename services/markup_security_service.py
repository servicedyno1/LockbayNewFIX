"""
Markup Security Service
Addresses Issue #5: Markup calculations done at payment initiation, not receipt
"""

import logging
from decimal import Decimal
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from database import SessionLocal
from models import ExpectedPayment

logger = logging.getLogger(__name__)

class MarkupSecurityService:
    """Service to handle secure markup calculations at payment initiation"""
    
    @classmethod
    def calculate_and_store_markup(
        cls,
        user_id: int,
        payment_reference: str,
        base_amount: Decimal,
        currency: str,
        payment_type: str,
        markup_percentage: Decimal = Decimal('2.0')  # Default 2% markup
    ) -> Dict[str, Any]:
        """
        Calculate markup at payment initiation and store for later validation
        Issue #5: Markup Calculation After Receipt
        """
        try:
            # Calculate markup amounts
            markup_amount = base_amount * (markup_percentage / Decimal('100'))
            final_amount = base_amount - markup_amount  # User receives this after markup
            
            markup_data = {
                'base_amount': base_amount,
                'markup_percentage': markup_percentage,
                'markup_amount': markup_amount,
                'final_amount': final_amount,
                'currency': currency,
                'calculated_at': datetime.utcnow().isoformat()
            }
            
            # Store expected payment with markup calculations
            with SessionLocal() as session:
                # Check if expected payment already exists
                existing = session.query(ExpectedPayment).filter(
                    ExpectedPayment.payment_reference == payment_reference
                ).first()
                
                if existing:
                    # Update existing expected payment
                    existing.expected_amount = base_amount
                    existing.details = markup_data
                    existing.status = 'pending'
                    session.commit()
                    logger.info(f"Updated expected payment with markup: {payment_reference}")
                else:
                    # Create new expected payment
                    expected_payment = ExpectedPayment(
                        user_id=user_id,
                        payment_reference=payment_reference,
                        expected_amount=base_amount,
                        currency=currency,
                        payment_type=payment_type,
                        details=markup_data,
                        created_at=datetime.utcnow(),
                        expires_at=datetime.utcnow() + timedelta(hours=24),  # 24-hour expiry
                        status='pending'
                    )
                    session.add(expected_payment)
                    session.commit()
                    logger.info(f"Stored expected payment with markup: {payment_reference}")
            
            return {
                'success': True,
                'markup_data': markup_data,
                'reference': payment_reference
            }
            
        except Exception as e:
            logger.error(f"Error calculating and storing markup: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @classmethod
    def validate_received_payment_markup(
        cls,
        payment_reference: str,
        received_amount: Decimal,
        currency: str
    ) -> Dict[str, Any]:
        """
        Validate received payment against pre-calculated markup
        Issue #5: Markup Calculation After Receipt
        """
        try:
            with SessionLocal() as session:
                expected = session.query(ExpectedPayment).filter(
                    ExpectedPayment.payment_reference == payment_reference,
                    ExpectedPayment.status == 'pending'
                ).first()
                
                if not expected:
                    return {
                        'success': False,
                        'error': 'No expected payment found',
                        'should_process': False
                    }
                
                # Check if payment has expired
                if expected.expires_at and datetime.utcnow() > expected.expires_at:
                    expected.status = 'expired'
                    session.commit()
                    return {
                        'success': False,
                        'error': 'Payment reference expired',
                        'should_process': False
                    }
                
                # Validate currency
                if expected.currency != currency:
                    return {
                        'success': False,
                        'error': f'Currency mismatch: expected {expected.currency}, got {currency}',
                        'should_process': False
                    }
                
                # Get stored markup data
                markup_data = expected.details or {}
                stored_base_amount = Decimal(str(markup_data.get('base_amount', 0)))
                stored_final_amount = Decimal(str(markup_data.get('final_amount', 0)))
                markup_percentage = Decimal(str(markup_data.get('markup_percentage', 2.0)))
                
                # Validate received amount against expected
                tolerance = stored_base_amount * Decimal('0.01')  # 1% tolerance
                min_acceptable = stored_base_amount - tolerance
                max_acceptable = stored_base_amount + tolerance
                
                if min_acceptable <= received_amount <= max_acceptable:
                    # Amount is valid, mark as validated
                    expected.status = 'validated'
                    expected.actual_amount = received_amount
                    expected.validated_at = datetime.utcnow()
                    session.commit()
                    
                    return {
                        'success': True,
                        'should_process': True,
                        'markup_data': markup_data,
                        'received_amount': received_amount,
                        'final_amount_after_markup': stored_final_amount,
                        'markup_percentage': markup_percentage,
                        'variance_percentage': float(abs(received_amount - stored_base_amount) / stored_base_amount * 100)
                    }
                else:
                    # Amount variance too large
                    variance_pct = float(abs(received_amount - stored_base_amount) / stored_base_amount * 100)
                    
                    expected.status = 'variance_detected'
                    expected.actual_amount = received_amount
                    expected.variance_percentage = variance_pct
                    session.commit()
                    
                    return {
                        'success': False,
                        'error': f'Amount variance too large: {variance_pct:.2f}%',
                        'should_process': variance_pct < 5.0,  # Allow processing if variance < 5%
                        'variance_percentage': variance_pct
                    }
                
        except Exception as e:
            logger.error(f"Error validating received payment markup: {e}")
            return {
                'success': False,
                'error': str(e),
                'should_process': False
            }
    
    @classmethod
    def get_crypto_markup_config(cls, currency: str) -> Decimal:
        """
        Get markup percentage for specific cryptocurrency
        Issue #5: Markup Calculation After Receipt
        """
        # Different markups for different cryptocurrencies based on volatility/processing cost
        crypto_markups = {
            'BTC': Decimal('2.5'),    # Higher markup for Bitcoin due to network fees
            'ETH': Decimal('2.0'),    # Standard markup for Ethereum
            'USDT': Decimal('1.5'),   # Lower markup for stablecoins
            'LTC': Decimal('2.0'),    # Standard markup for Litecoin
            'BCH': Decimal('2.0'),    # Standard markup for Bitcoin Cash
            'DOGE': Decimal('3.0'),   # Higher markup due to volatility
            'TRX': Decimal('1.5'),    # Lower markup for TRON
        }
        
        return crypto_markups.get(currency.upper(), Decimal('2.0'))  # Default 2% markup
    
    @classmethod
    def calculate_network_fee_markup(
        cls,
        currency: str,
        amount: Decimal,
        network_type: Optional[str] = None
    ) -> Dict[str, Decimal]:
        """
        Calculate network-specific fee markups
        """
        try:
            # Base network fees (these should be updated periodically)
            network_fees = {
                'BTC': Decimal('0.0001'),      # ~$4-5 at current prices
                'ETH': Decimal('0.002'),       # ~$6-10 depending on gas
                'USDT_ERC20': Decimal('0.002'), # Same as ETH
                'USDT_TRC20': Decimal('1.0'),   # 1 USDT fee on TRON
                'LTC': Decimal('0.001'),       # ~$0.10-0.50
                'BCH': Decimal('0.0001'),      # Very low fees
                'DOGE': Decimal('1.0'),        # 1 DOGE fee
                'TRX': Decimal('1.0'),         # 1 TRX fee
            }
            
            fee_key = f"{currency}_{network_type}" if network_type else currency
            estimated_fee = network_fees.get(fee_key.upper(), network_fees.get(currency.upper(), Decimal('0.001')))
            
            # Calculate markup to cover network fees plus buffer
            fee_markup_percentage = (estimated_fee / amount) * 100 + Decimal('0.5')  # Add 0.5% buffer
            
            return {
                'estimated_network_fee': estimated_fee,
                'fee_markup_percentage': fee_markup_percentage,
                'recommended_total_markup': cls.get_crypto_markup_config(currency) + fee_markup_percentage
            }
            
        except Exception as e:
            logger.error(f"Error calculating network fee markup: {e}")
            return {
                'estimated_network_fee': Decimal('0.001'),
                'fee_markup_percentage': Decimal('0.5'),
                'recommended_total_markup': Decimal('2.5')
            }
    
    @classmethod
    def audit_markup_calculation(
        cls,
        payment_reference: str,
        calculated_markup: Dict[str, Any],
        user_id: int
    ):
        """
        Audit trail for markup calculations
        """
        try:
            from services.enhanced_payment_security import EnhancedPaymentSecurity
            
            EnhancedPaymentSecurity.comprehensive_audit_log(
                event_type='markup_calculation',
                user_id=user_id,
                amount=calculated_markup.get('base_amount'),
                currency=calculated_markup.get('currency'),
                reference=payment_reference,
                details={
                    'markup_percentage': str(calculated_markup.get('markup_percentage', 0)),
                    'markup_amount': str(calculated_markup.get('markup_amount', 0)),
                    'final_amount': str(calculated_markup.get('final_amount', 0)),
                    'calculation_method': 'pre_calculated_secure',
                    'timestamp': calculated_markup.get('calculated_at')
                }
            )
            
        except Exception as e:
            logger.error(f"Error auditing markup calculation: {e}")