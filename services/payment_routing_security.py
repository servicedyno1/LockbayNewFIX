"""
Payment Routing Security Service
Addresses Issue #6: Sequential Table Checking - prevents same payment matching multiple order types
"""

import logging
from typing import Dict, Optional, Tuple
from datetime import datetime
from decimal import Decimal
from database import SessionLocal
from models import ExchangeOrder, ExpectedPayment

logger = logging.getLogger(__name__)

# Try to import DirectExchange, but gracefully handle if it doesn't exist
try:
    from models import DirectExchange
except ImportError:
    DirectExchange = None

class PaymentRoutingSecurityService:
    """Service to securely route payments to correct order types without double-matching"""
    
    @classmethod
    def determine_payment_destination(
        cls,
        reference: str,
        amount: Decimal,
        currency: str,
        session
    ) -> Tuple[Optional[str], Optional[Dict], Optional[str]]:
        """
        Securely determine which order type this payment belongs to
        Issue #6: Sequential Table Checking
        
        Returns: (order_type, order_object, routing_decision_reason)
        """
        try:
            routing_log = {
                'reference': reference,
                'amount': str(amount),
                'currency': currency,
                'checks_performed': []
            }
            
            # CRITICAL FIX: Check expected payments table first for explicit routing
            expected_payment = session.query(ExpectedPayment).filter(
                ExpectedPayment.payment_reference == reference,
                ExpectedPayment.status == 'pending'
            ).first()
            
            if expected_payment:
                routing_log['checks_performed'].append({
                    'check': 'expected_payment_lookup',
                    'result': 'found',
                    'payment_type': expected_payment.payment_type
                })
                
                # Route based on stored payment type
                if expected_payment.payment_type == 'exchange_order':
                    order = session.query(ExchangeOrder).filter(
                        ExchangeOrder.id == expected_payment.order_id,
                        ExchangeOrder.user_id == expected_payment.user_id
                    ).first()
                    if order:
                        return 'exchange_order', order, 'expected_payment_routing'
                        
                elif expected_payment.payment_type == 'direct_exchange':
                    order = session.query(DirectExchange).filter(
                        DirectExchange.id == expected_payment.order_id,
                        DirectExchange.user_id == expected_payment.user_id
                    ).first()
                    if order:
                        return 'direct_exchange', order, 'expected_payment_routing'
            
            # CRITICAL FIX: Use reference pattern analysis for deterministic routing
            order_type, order_object, reason = cls._analyze_reference_pattern(reference, session)
            if order_type and order_object:
                routing_log['checks_performed'].append({
                    'check': 'reference_pattern_analysis',
                    'result': 'matched',
                    'order_type': order_type,
                    'reason': reason
                })
                return order_type, order_object, reason
            
            # FALLBACK: Try both tables but with conflict detection
            exchange_order_candidates = cls._find_exchange_order_candidates(reference, amount, currency, session)
            direct_exchange_candidates = cls._find_direct_exchange_candidates(reference, amount, currency, session)
            
            routing_log['checks_performed'].append({
                'check': 'candidate_search',
                'exchange_order_count': len(exchange_order_candidates),
                'direct_exchange_count': len(direct_exchange_candidates)
            })
            
            # CRITICAL: Detect and prevent conflicts
            if exchange_order_candidates and direct_exchange_candidates:
                logger.warning(
                    f"PAYMENT_ROUTING_CONFLICT: Payment {reference} matches both ExchangeOrder "
                    f"and DirectExchange candidates. This could indicate a security issue."
                )
                
                # Audit the conflict
                from services.enhanced_payment_security import EnhancedPaymentSecurity
                EnhancedPaymentSecurity.comprehensive_audit_log(
                    event_type='payment_routing_conflict',
                    user_id=None,
                    amount=amount,
                    currency=currency,
                    reference=reference,
                    details={
                        'exchange_order_matches': len(exchange_order_candidates),
                        'direct_exchange_matches': len(direct_exchange_candidates),
                        'routing_log': routing_log
                    }
                )
                
                # Use timestamp-based priority (most recent order wins)
                latest_exchange = max(exchange_order_candidates, key=lambda x: x.created_at) if exchange_order_candidates else None
                latest_direct = max(direct_exchange_candidates, key=lambda x: x.created_at) if direct_exchange_candidates else None
                
                if latest_exchange and latest_direct:
                    if latest_exchange.created_at > latest_direct.created_at:
                        return 'exchange_order', latest_exchange, 'conflict_resolution_timestamp'
                    else:
                        return 'direct_exchange', latest_direct, 'conflict_resolution_timestamp'
                elif latest_exchange:
                    return 'exchange_order', latest_exchange, 'only_exchange_order_match'
                elif latest_direct:
                    return 'direct_exchange', latest_direct, 'only_direct_exchange_match'
            
            # Single match scenarios
            if exchange_order_candidates:
                return 'exchange_order', exchange_order_candidates[0], 'single_exchange_order_match'
            elif direct_exchange_candidates:
                return 'direct_exchange', direct_exchange_candidates[0], 'single_direct_exchange_match'
            
            # No matches found
            logger.warning(f"No matching orders found for payment reference: {reference}")
            return None, None, 'no_matches_found'
            
        except Exception as e:
            logger.error(f"Error in payment routing security: {e}")
            return None, None, f'routing_error: {str(e)}'
    
    @classmethod
    def _analyze_reference_pattern(cls, reference: str, session) -> Tuple[Optional[str], Optional[Dict], str]:
        """
        Analyze reference pattern to determine order type deterministically
        """
        try:
            # Reference patterns:
            # ExchangeOrder: LKBY_VA_wallet_funding_{user_id}_{timestamp}
            # DirectExchange: Usually starts with "EX" or has different pattern
            
            if reference.startswith("EX"):
                # Likely DirectExchange
                try:
                    order_id = reference.split("_")[0].replace("EX", "")
                    order = session.query(DirectExchange).filter(DirectExchange.id == int(order_id)).first()
                    if order:
                        return 'direct_exchange', order, 'reference_pattern_EX_prefix'
                except (ValueError, IndexError):
                    pass
            
            elif "wallet_funding" in reference:
                # Likely ExchangeOrder (NGN to Crypto)
                try:
                    parts = reference.split("_")
                    if len(parts) >= 5:
                        user_id = int(parts[4])
                        # Find by exact reference match first
                        order = session.query(ExchangeOrder).filter(
                            ExchangeOrder.payment_reference == reference,
                            ExchangeOrder.user_id == user_id
                        ).first()
                        if order:
                            return 'exchange_order', order, 'reference_pattern_wallet_funding'
                except (ValueError, IndexError):
                    pass
            
            return None, None, 'pattern_analysis_inconclusive'
            
        except Exception as e:
            logger.error(f"Error analyzing reference pattern: {e}")
            return None, None, 'pattern_analysis_error'
    
    @classmethod
    def _find_exchange_order_candidates(cls, reference: str, amount: Decimal, currency: str, session) -> list:
        """Find potential ExchangeOrder matches"""
        candidates = []
        
        try:
            # Exact reference match
            exact_match = session.query(ExchangeOrder).filter(
                ExchangeOrder.payment_reference == reference
            ).first()
            if exact_match:
                candidates.append(exact_match)
            
            # Pattern-based matching for wallet funding
            if "wallet_funding" in reference and not exact_match:
                parts = reference.split("_")
                if len(parts) >= 5:
                    try:
                        user_id = int(parts[4])
                        recent_orders = session.query(ExchangeOrder).filter(
                            ExchangeOrder.user_id == user_id,
                            ExchangeOrder.status.in_(["awaiting_deposit", "cancelled"]),
                            ExchangeOrder.order_type == "ngn_to_crypto"
                        ).order_by(ExchangeOrder.created_at.desc()).limit(3).all()
                        
                        candidates.extend(recent_orders)
                    except (ValueError, IndexError):
                        pass
            
        except Exception as e:
            logger.error(f"Error finding ExchangeOrder candidates: {e}")
        
        return candidates
    
    @classmethod
    def _find_direct_exchange_candidates(cls, reference: str, amount: Decimal, currency: str, session) -> list:
        """Find potential DirectExchange matches"""
        candidates = []
        
        try:
            # Order ID pattern match
            if reference.startswith("EX"):
                try:
                    order_id = reference.split("_")[0].replace("EX", "")
                    order = session.query(DirectExchange).filter(DirectExchange.id == int(order_id)).first()
                    if order:
                        candidates.append(order)
                except (ValueError, IndexError):
                    pass
            
            # Amount-based matching (less reliable, used as fallback)
            if not candidates and currency == "NGN":
                amount_tolerance = amount * Decimal('0.02')  # 2% tolerance
                min_amount = amount - amount_tolerance
                max_amount = amount + amount_tolerance
                
                potential_orders = session.query(DirectExchange).filter(
                    DirectExchange.from_amount >= min_amount,
                    DirectExchange.from_amount <= max_amount,
                    DirectExchange.from_currency == currency,
                    DirectExchange.status.in_(["awaiting_deposit", "cancelled"])
                ).order_by(DirectExchange.created_at.desc()).limit(3).all()
                
                candidates.extend(potential_orders)
                
        except Exception as e:
            logger.error(f"Error finding DirectExchange candidates: {e}")
        
        return candidates
    
    @classmethod
    def create_payment_lock(cls, reference: str, order_type: str, order_id: int) -> bool:
        """
        Create payment processing lock to prevent double-processing
        """
        try:
            with SessionLocal() as session:
                # Store processing lock in expected payments table
                lock_entry = ExpectedPayment(
                    user_id=0,  # System entry
                    payment_reference=f"LOCK_{reference}",
                    expected_amount=Decimal('0'),
                    currency='LOCK',
                    payment_type=f"processing_lock_{order_type}",
                    order_id=order_id,
                    status='processing',
                    details={'locked_at': datetime.utcnow().isoformat(), 'lock_type': 'payment_processing'}
                )
                session.add(lock_entry)
                session.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error creating payment lock: {e}")
            return False
    
    @classmethod
    def release_payment_lock(cls, reference: str) -> bool:
        """Release payment processing lock"""
        try:
            with SessionLocal() as session:
                lock_entry = session.query(ExpectedPayment).filter(
                    ExpectedPayment.payment_reference == f"LOCK_{reference}",
                    ExpectedPayment.status == 'processing'
                ).first()
                
                if lock_entry:
                    lock_entry.status = 'completed'
                    session.commit()
                    return True
                    
        except Exception as e:
            logger.error(f"Error releasing payment lock: {e}")
            return False