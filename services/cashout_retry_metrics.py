"""
Cashout Retry Metrics Service
Comprehensive analytics and monitoring for the retry system
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from sqlalchemy import func, or_, case

from database import managed_session
from models import Cashout, CashoutStatus, CashoutFailureType

logger = logging.getLogger(__name__)


class RetryMetricsService:
    """Service for analyzing and monitoring retry system performance"""
    
    @classmethod
    async def get_queue_metrics(cls) -> Dict[str, Any]:
        """Get current retry queue metrics and status"""
        logger.info("RETRY_METRICS: Generating queue metrics")
        
        try:
            with managed_session() as db:
                now = datetime.utcnow()
                
                # Count ready for retry (due now)
                ready_for_retry = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at <= now,
                    Cashout.next_retry_at.isnot(None)
                ).count()
                
                # Count pending retries (scheduled for future)
                pending_retries = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at > now,
                    Cashout.next_retry_at.isnot(None)
                ).count()
                
                # Count technical failures (total)
                technical_failures = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value
                ).count()
                
                # Count max retries reached (no next_retry_at set)
                max_retries_reached = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at.is_(None),
                    Cashout.retry_count > 0
                ).count()
                
                # Get next retry time
                next_retry = db.query(Cashout.next_retry_at).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at.isnot(None)
                ).order_by(Cashout.next_retry_at.asc()).first()
                
                next_retry_time = "None"
                if next_retry and next_retry[0]:
                    delta = next_retry[0] - now
                    if delta.total_seconds() > 0:
                        minutes = int(delta.total_seconds() / 60)
                        next_retry_time = f"{minutes}m" if minutes > 0 else "< 1m"
                    else:
                        next_retry_time = "Now"
                
                # Get oldest pending duration
                oldest_pending = db.query(Cashout.technical_failure_since).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.technical_failure_since.isnot(None)
                ).order_by(Cashout.technical_failure_since.asc()).first()
                
                oldest_pending_duration = "None"
                if oldest_pending and oldest_pending[0]:
                    delta = now - oldest_pending[0]
                    hours = int(delta.total_seconds() / 3600)
                    if hours > 24:
                        oldest_pending_duration = f"{hours // 24}d {hours % 24}h"
                    else:
                        oldest_pending_duration = f"{hours}h"
                
                # Calculate average retry delay
                avg_delay_result = db.query(
                    func.avg(
                        func.extract('epoch', Cashout.next_retry_at - Cashout.last_retry_at) / 60
                    )
                ).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at.isnot(None),
                    Cashout.last_retry_at.isnot(None)
                ).scalar()
                
                average_retry_delay = int(avg_delay_result) if avg_delay_result else 0
                
                # Calculate success rate (last 24h)
                yesterday = now - timedelta(days=1)
                total_retries_24h = db.query(Cashout).filter(
                    Cashout.last_retry_at >= yesterday,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value
                ).count()
                
                successful_retries_24h = db.query(Cashout).filter(
                    Cashout.completed_at >= yesterday,
                    Cashout.retry_count > 0
                ).count()
                
                retry_success_rate = 0
                if total_retries_24h > 0:
                    retry_success_rate = int((successful_retries_24h / total_retries_24h) * 100)
                
                # Queue capacity analysis
                queue_depth = ready_for_retry + pending_retries
                
                last_processing_time = cls._get_last_processing_time()
                metrics = {
                    'ready_for_retry': ready_for_retry,
                    'pending_retries': pending_retries,
                    'technical_failures': technical_failures,
                    'max_retries_reached': max_retries_reached,
                    'next_retry_time': next_retry_time,
                    'oldest_pending_duration': oldest_pending_duration,
                    'average_retry_delay': average_retry_delay,
                    'retry_success_rate': retry_success_rate,
                    'queue_depth': queue_depth,
                    'last_processing_time': last_processing_time,
                    # Backward compatibility alias
                    'last_sweep_time': last_processing_time,
                    'timestamp': now.isoformat()
                }
                
                logger.info(f"RETRY_METRICS: Queue metrics generated: {metrics}")
                return metrics
                
        except Exception as e:
            logger.error(f"RETRY_METRICS: Error generating queue metrics: {e}")
            return cls._get_default_metrics()
    
    @classmethod
    async def get_queue_items(cls, limit: int = 10) -> List[Dict[str, Any]]:
        """Get detailed information about items in the retry queue"""
        try:
            with managed_session() as db:
                now = datetime.utcnow()
                
                # Get retry queue items ordered by priority (ready first, then by retry time)
                items = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at.isnot(None)
                ).order_by(
                    case(
                        (Cashout.next_retry_at <= now, 0),  # Ready items first
                        else_=1
                    ),
                    Cashout.next_retry_at.asc()
                ).limit(limit).all()
                
                result = []
                for item in items:
                    ready_now = item.next_retry_at <= now if item.next_retry_at else False
                    
                    next_retry_str = "Now" if ready_now else "Unknown"
                    if item.next_retry_at and not ready_now:
                        delta = item.next_retry_at - now
                        minutes = int(delta.total_seconds() / 60)
                        next_retry_str = f"{minutes}m" if minutes > 0 else "< 1m"
                    
                    result.append({
                        'cashout_id': item.cashout_id,
                        'amount': float(item.amount),
                        'currency': item.currency,
                        'failure_type': item.failure_type,
                        'error_code': item.last_error_code or 'unknown',
                        'retry_count': item.retry_count,
                        'next_retry': next_retry_str,
                        'user_id': item.user_id,
                        'ready_now': ready_now,
                        'created_at': item.created_at.isoformat() if item.created_at else None
                    })
                
                logger.info(f"RETRY_METRICS: Retrieved {len(result)} queue items")
                return result
                
        except Exception as e:
            logger.error(f"RETRY_METRICS: Error getting queue items: {e}")
            return []
    
    @classmethod
    async def get_comprehensive_stats(cls) -> Dict[str, Any]:
        """Get comprehensive retry system statistics"""
        logger.info("RETRY_METRICS: Generating comprehensive statistics")
        
        try:
            with managed_session() as db:
                now = datetime.utcnow()
                yesterday = now - timedelta(days=1)
                
                # Daily performance metrics
                total_processed_24h = db.query(Cashout).filter(
                    or_(
                        Cashout.completed_at >= yesterday,
                        Cashout.failed_at >= yesterday
                    ),
                    Cashout.retry_count > 0
                ).count()
                
                successful_24h = db.query(Cashout).filter(
                    Cashout.completed_at >= yesterday,
                    Cashout.retry_count > 0
                ).count()
                
                success_rate = 0
                if total_processed_24h > 0:
                    success_rate = int((successful_24h / total_processed_24h) * 100)
                
                # Average processing time for successful retries
                avg_processing_time = db.query(
                    func.avg(
                        func.extract('epoch', Cashout.completed_at - Cashout.created_at)
                    )
                ).filter(
                    Cashout.completed_at >= yesterday,
                    Cashout.retry_count > 0
                ).scalar() or 0
                
                # Recovery rate (technical failures that eventually succeeded)
                technical_failures_24h = db.query(Cashout).filter(
                    Cashout.created_at >= yesterday,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value
                ).count()
                
                recovered_24h = db.query(Cashout).filter(
                    Cashout.completed_at >= yesterday,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.retry_count > 0
                ).count()
                
                recovery_rate = 0
                if technical_failures_24h > 0:
                    recovery_rate = int((recovered_24h / technical_failures_24h) * 100)
                
                # Error classification statistics
                technical_count = db.query(Cashout).filter(
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.created_at >= yesterday
                ).count()
                
                user_count = db.query(Cashout).filter(
                    Cashout.failure_type == CashoutFailureType.USER.value,
                    Cashout.created_at >= yesterday
                ).count()
                
                unknown_count = db.query(Cashout).filter(
                    Cashout.failure_type.is_(None),
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.created_at >= yesterday
                ).count()
                
                total_errors = technical_count + user_count + unknown_count
                
                technical_percentage = int((technical_count / total_errors * 100)) if total_errors > 0 else 0
                user_percentage = int((user_count / total_errors * 100)) if total_errors > 0 else 0
                
                # Top error codes
                top_codes = db.query(
                    Cashout.last_error_code,
                    func.count(Cashout.last_error_code).label('count')
                ).filter(
                    Cashout.last_error_code.isnot(None),
                    Cashout.created_at >= yesterday
                ).group_by(
                    Cashout.last_error_code
                ).order_by(
                    func.count(Cashout.last_error_code).desc()
                ).limit(10).all()
                
                top_codes_list = [
                    {'code': code[0], 'count': code[1]} 
                    for code in top_codes
                ]
                
                # Timing analysis
                avg_retry_delay = db.query(
                    func.avg(
                        func.extract('epoch', Cashout.next_retry_at - Cashout.last_retry_at) / 60
                    )
                ).filter(
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.last_retry_at >= yesterday,
                    Cashout.next_retry_at.isnot(None),
                    Cashout.last_retry_at.isnot(None)
                ).scalar() or 0
                
                # Max recovery time
                max_recovery = db.query(
                    func.max(
                        func.extract('epoch', Cashout.completed_at - Cashout.technical_failure_since) / 3600
                    )
                ).filter(
                    Cashout.completed_at >= yesterday,
                    Cashout.technical_failure_since.isnot(None),
                    Cashout.retry_count > 0
                ).scalar() or 0
                
                max_recovery_str = f"{int(max_recovery)}h" if max_recovery > 0 else "0h"
                
                # Retry success by attempt number
                first_retry_success = db.query(Cashout).filter(
                    Cashout.completed_at >= yesterday,
                    Cashout.retry_count == 1
                ).count()
                
                second_retry_success = db.query(Cashout).filter(
                    Cashout.completed_at >= yesterday,
                    Cashout.retry_count == 2
                ).count()
                
                total_first_retries = db.query(Cashout).filter(
                    Cashout.retry_count >= 1,
                    Cashout.last_retry_at >= yesterday
                ).count()
                
                total_second_retries = db.query(Cashout).filter(
                    Cashout.retry_count >= 2,
                    Cashout.last_retry_at >= yesterday
                ).count()
                
                first_retry_success_rate = int((first_retry_success / total_first_retries * 100)) if total_first_retries > 0 else 0
                second_retry_success_rate = int((second_retry_success / total_second_retries * 100)) if total_second_retries > 0 else 0
                
                # System health indicators
                backlog_size = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at.isnot(None)
                ).count()
                
                # Processing rate (items processed per minute in last hour)
                last_hour = now - timedelta(hours=1)
                processed_last_hour = db.query(Cashout).filter(
                    or_(
                        Cashout.completed_at >= last_hour,
                        Cashout.failed_at >= last_hour
                    ),
                    Cashout.retry_count > 0
                ).count()
                
                processing_rate = processed_last_hour  # Already per hour, convert to per minute if needed
                
                # Error rate trend (compare last 24h to previous 24h)
                day_before_yesterday = yesterday - timedelta(days=1)
                
                errors_yesterday = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.created_at >= day_before_yesterday,
                    Cashout.created_at < yesterday
                ).count()
                
                errors_today = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.created_at >= yesterday
                ).count()
                
                if errors_yesterday > 0:
                    error_trend_pct = ((errors_today - errors_yesterday) / errors_yesterday) * 100
                    if error_trend_pct > 10:
                        error_trend = f"↗️ +{error_trend_pct:.1f}%"
                    elif error_trend_pct < -10:
                        error_trend = f"↘️ {error_trend_pct:.1f}%"
                    else:
                        error_trend = "➡️ Stable"
                else:
                    error_trend = "➡️ No data"
                
                # Overall health status
                if backlog_size > 50:
                    overall_status = "⚠️ High Load"
                elif success_rate < 70:
                    overall_status = "⚠️ Low Success Rate"
                elif processing_rate < 5:
                    overall_status = "⚠️ Slow Processing"
                else:
                    overall_status = "✅ Healthy"
                
                stats = {
                    'daily': {
                        'total_processed': total_processed_24h,
                        'success_rate': success_rate,
                        'avg_processing_time': int(avg_processing_time),
                        'recovery_rate': recovery_rate
                    },
                    'errors': {
                        'technical_count': technical_count,
                        'technical_percentage': technical_percentage,
                        'user_count': user_count,
                        'user_percentage': user_percentage,
                        'unknown_count': unknown_count,
                        'top_codes': top_codes_list
                    },
                    'timing': {
                        'avg_retry_delay': int(avg_retry_delay),
                        'max_recovery_time': max_recovery_str,
                        'first_retry_success': first_retry_success_rate,
                        'second_retry_success': second_retry_success_rate
                    },
                    'health': {
                        'backlog_size': backlog_size,
                        'processing_rate': processing_rate,
                        'error_trend': error_trend,
                        'overall_status': overall_status
                    },
                    'timestamp': now.isoformat()
                }
                
                logger.info(f"RETRY_METRICS: Comprehensive stats generated with overall status: {overall_status}")
                return stats
                
        except Exception as e:
            logger.error(f"RETRY_METRICS: Error generating comprehensive stats: {e}")
            return cls._get_default_comprehensive_stats()
    
    @classmethod
    async def get_max_retry_items(cls, limit: int = 10) -> List[Dict[str, Any]]:
        """Get cashouts that have reached maximum retries and need attention"""
        try:
            with managed_session() as db:
                # Find cashouts that have reached max retries but not refunded
                items = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                    Cashout.next_retry_at.is_(None),
                    Cashout.retry_count > 0
                ).order_by(
                    Cashout.technical_failure_since.asc()
                ).limit(limit).all()
                
                result = []
                for item in items:
                    result.append({
                        'cashout_id': item.cashout_id,
                        'amount': float(item.amount),
                        'currency': item.currency,
                        'error_code': item.last_error_code or 'unknown',
                        'retry_count': item.retry_count,
                        'user_id': item.user_id,
                        'technical_failure_since': item.technical_failure_since,
                        'created_at': item.created_at.isoformat() if item.created_at else None
                    })
                
                logger.info(f"RETRY_METRICS: Retrieved {len(result)} max retry items")
                return result
                
        except Exception as e:
            logger.error(f"RETRY_METRICS: Error getting max retry items: {e}")
            return []
    
    @classmethod
    def _get_last_processing_time(cls) -> str:
        """Get the last time the unified retry processor ran"""
        # This would ideally come from a job status table or cache
        # For now, return a placeholder
        return "< 2m ago"
    
    @classmethod
    def _get_default_metrics(cls) -> Dict[str, Any]:
        """Return default metrics when query fails"""
        return {
            'ready_for_retry': 0,
            'pending_retries': 0,
            'technical_failures': 0,
            'max_retries_reached': 0,
            'next_retry_time': "Unknown",
            'oldest_pending_duration': "Unknown",
            'average_retry_delay': 0,
            'retry_success_rate': 0,
            'queue_depth': 0,
            'last_processing_time': "Unknown",
            # Backward compatibility alias
            'last_sweep_time': "Unknown",
            'timestamp': datetime.utcnow().isoformat()
        }
    
    @classmethod
    def _get_default_comprehensive_stats(cls) -> Dict[str, Any]:
        """Return default comprehensive stats when query fails"""
        return {
            'daily': {
                'total_processed': 0,
                'success_rate': 0,
                'avg_processing_time': 0,
                'recovery_rate': 0
            },
            'errors': {
                'technical_count': 0,
                'technical_percentage': 0,
                'user_count': 0,
                'user_percentage': 0,
                'unknown_count': 0,
                'top_codes': []
            },
            'timing': {
                'avg_retry_delay': 0,
                'max_recovery_time': "0h",
                'first_retry_success': 0,
                'second_retry_success': 0
            },
            'health': {
                'backlog_size': 0,
                'processing_rate': 0,
                'error_trend': "Unknown",
                'overall_status': "⚠️ No Data"
            },
            'timestamp': datetime.utcnow().isoformat()
        }