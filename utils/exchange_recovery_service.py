"""
Exchange Recovery Service
Production-ready system to detect and recover stuck exchange orders
"""

import logging
from datetime import datetime, timedelta
from database import SessionLocal
from models import ExchangeOrder
from typing import List, Dict

logger = logging.getLogger(__name__)


class ExchangeRecoveryService:
    """Service to detect and recover stuck exchange orders"""
    
    @staticmethod
    async def detect_stuck_orders() -> List[Dict]:
        """Detect orders stuck in payment_received for too long"""
        session = SessionLocal()
        stuck_orders = []
        
        try:
            # Find orders stuck in payment_received for more than 10 minutes
            cutoff_time = datetime.utcnow() - timedelta(minutes=10)
            
            stuck_orders_query = (
                session.query(ExchangeOrder)
                .filter(
                    ExchangeOrder.status == "payment_received",
                    ExchangeOrder.created_at < cutoff_time,
                    ExchangeOrder.completed_at.is_(None)
                )
                .all()
            )
            
            for order in stuck_orders_query:
                order_info = {
                    'id': order.id,
                    'utid': getattr(order, 'utid', ''),
                    'user_id': getattr(order, 'user_id', 0),
                    'order_type': getattr(order, 'order_type', ''),
                    'source_amount': float(getattr(order, 'source_amount', 0)),
                    'final_amount': float(getattr(order, 'final_amount', 0)),
                    'created_at': getattr(order, 'created_at', datetime.utcnow()),
                    'stuck_duration_minutes': (datetime.utcnow() - order.created_at).total_seconds() / 60
                }
                stuck_orders.append(order_info)
                
            logger.info(f"Detected {len(stuck_orders)} stuck exchange orders")
            return stuck_orders
            
        except Exception as e:
            logger.error(f"Error detecting stuck orders: {e}")
            return []
        finally:
            session.close()
    
    @staticmethod
    async def force_process_stuck_order(order_id: int) -> Dict:
        """Force processing of a stuck order with enhanced error handling"""
        session = SessionLocal()
        
        try:
            # Get the stuck order
            order = session.query(ExchangeOrder).filter(ExchangeOrder.id == order_id).first()
            if not order:
                return {'success': False, 'error': 'Order not found'}
            
            if getattr(order, 'status', '') != 'payment_received':
                return {'success': False, 'error': f'Order status is {getattr(order, "status", "unknown")}, not payment_received'}
            
            # Force set to processing to trigger scheduler
            setattr(order, 'status', 'processing')
            session.commit()
            
            # Try immediate processing
            if getattr(order, 'order_type', '') == 'crypto_to_ngn':
                from jobs.exchange_monitor import process_ngn_payout
                result = await process_ngn_payout(session, order)
                
                if result:
                    logger.info(f"Successfully recovered stuck order {order_id}")
                    return {'success': True, 'message': f'Order {order_id} successfully processed'}
                else:
                    logger.error(f"Failed to process stuck order {order_id} - reverting status")
                    setattr(order, 'status', 'payment_received')
                    session.commit()
                    return {'success': False, 'error': 'NGN payout failed during recovery'}
            else:
                return {'success': False, 'error': 'Unsupported order type for recovery'}
                
        except Exception as e:
            logger.error(f"Error forcing recovery of order {order_id}: {e}")
            session.rollback()
            return {'success': False, 'error': f'Recovery failed: {str(e)}'}
        finally:
            session.close()
    
    @staticmethod
    async def get_recovery_statistics() -> Dict:
        """Get statistics about stuck orders for monitoring"""
        session = SessionLocal()
        
        try:
            from sqlalchemy import func
            
            # Count orders by status
            status_counts = (
                session.query(ExchangeOrder.status, func.count(ExchangeOrder.id))
                .group_by(ExchangeOrder.status)
                .all()
            )
            
            # Count stuck orders
            cutoff_time = datetime.utcnow() - timedelta(minutes=10)
            stuck_count = (
                session.query(func.count(ExchangeOrder.id))
                .filter(
                    ExchangeOrder.status == "payment_received",
                    ExchangeOrder.created_at < cutoff_time,
                    ExchangeOrder.completed_at.is_(None)
                )
                .scalar() or 0
            )
            
            # Get oldest stuck order
            oldest_stuck = (
                session.query(ExchangeOrder.created_at)
                .filter(
                    ExchangeOrder.status == "payment_received",
                    ExchangeOrder.created_at < cutoff_time,
                    ExchangeOrder.completed_at.is_(None)
                )
                .order_by(ExchangeOrder.created_at.asc())
                .first()
            )
            
            oldest_stuck_hours = None
            if oldest_stuck and oldest_stuck[0]:
                oldest_stuck_hours = (datetime.utcnow() - oldest_stuck[0]).total_seconds() / 3600
            
            return {
                'status_distribution': dict(status_counts),
                'stuck_orders_count': stuck_count,
                'oldest_stuck_hours': oldest_stuck_hours,
                'healthy': stuck_count == 0,
                'requires_attention': stuck_count > 0 or (oldest_stuck_hours and oldest_stuck_hours > 1)
            }
            
        except Exception as e:
            logger.error(f"Error getting recovery statistics: {e}")
            return {'error': str(e)}
        finally:
            session.close()