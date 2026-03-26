"""
Enhanced Refund Monitoring System
Comprehensive tracking and alerting for all refund operations
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
from sqlalchemy import func

from models import Refund
from database import SessionLocal

logger = logging.getLogger(__name__)


class RefundMetricType(Enum):
    """Types of refund metrics to track"""
    PROCESSING_TIME = "processing_time"
    SUCCESS_RATE = "success_rate"
    VOLUME = "volume"
    AMOUNT = "amount"
    ERROR_RATE = "error_rate"


@dataclass
class RefundMetric:
    """Individual refund metric data point"""
    metric_type: RefundMetricType
    value: float
    timestamp: datetime
    refund_type: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None


class RefundMonitor:
    """Comprehensive refund monitoring and alerting system"""
    
    def __init__(self):
        self.start_time = time.time()
        self.metrics: list[RefundMetric] = []
        
    def track_refund_start(self, refund_id: str, refund_type: str, amount: float, 
                          user_id: int, source_module: str) -> str:
        """Track the start of a refund operation"""
        operation_id = f"refund_{refund_id}_{int(time.time())}"
        
        logger.info(
            f"🔄 REFUND_START: ID={refund_id}, Type={refund_type}, "
            f"Amount=${amount:.2f}, User={user_id}, Source={source_module}, "
            f"OpID={operation_id}"
        )
        
        # Track volume metric
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.VOLUME,
            value=1,
            timestamp=datetime.utcnow(),
            refund_type=refund_type,
            additional_data={
                "refund_id": refund_id,
                "operation_id": operation_id,
                "source_module": source_module
            }
        ))
        
        # Track amount metric
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.AMOUNT,
            value=amount,
            timestamp=datetime.utcnow(),
            refund_type=refund_type,
            additional_data={
                "refund_id": refund_id,
                "operation_id": operation_id
            }
        ))
        
        return operation_id
    
    def track_refund_success(self, operation_id: str, refund_id: str, 
                           processing_time: float, final_amount: float):
        """Track successful completion of refund operation"""
        logger.info(
            f"✅ REFUND_SUCCESS: OpID={operation_id}, ID={refund_id}, "
            f"Time={processing_time:.3f}s, Amount=${final_amount:.2f}"
        )
        
        # Track processing time
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.PROCESSING_TIME,
            value=processing_time,
            timestamp=datetime.utcnow(),
            additional_data={
                "refund_id": refund_id,
                "operation_id": operation_id,
                "outcome": "success"
            }
        ))
        
        # Track success rate (1 = success)
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.SUCCESS_RATE,
            value=1.0,
            timestamp=datetime.utcnow(),
            additional_data={
                "refund_id": refund_id,
                "operation_id": operation_id
            }
        ))
        
    def track_refund_failure(self, operation_id: str, refund_id: str, 
                           error_message: str, processing_time: float):
        """Track failed refund operation"""
        logger.error(
            f"❌ REFUND_FAILURE: OpID={operation_id}, ID={refund_id}, "
            f"Time={processing_time:.3f}s, Error={error_message}"
        )
        
        # Track processing time for failures
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.PROCESSING_TIME,
            value=processing_time,
            timestamp=datetime.utcnow(),
            additional_data={
                "refund_id": refund_id,
                "operation_id": operation_id,
                "outcome": "failure",
                "error": error_message
            }
        ))
        
        # Track failure rate (0 = failure)
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.SUCCESS_RATE,
            value=0.0,
            timestamp=datetime.utcnow(),
            additional_data={
                "refund_id": refund_id,
                "operation_id": operation_id,
                "error": error_message
            }
        ))
        
        # Track error rate
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.ERROR_RATE,
            value=1.0,
            timestamp=datetime.utcnow(),
            additional_data={
                "refund_id": refund_id,
                "operation_id": operation_id,
                "error": error_message
            }
        ))
        
    def track_duplicate_refund_attempt(self, refund_id: str, existing_refund_id: str, 
                                     idempotency_key: str):
        """Track when duplicate refund attempt is prevented"""
        logger.warning(
            f"🚫 REFUND_DUPLICATE_PREVENTED: ID={refund_id}, "
            f"ExistingID={existing_refund_id}, Key={idempotency_key[:16]}..."
        )
        
        # This is actually a success - we prevented a duplicate
        self.metrics.append(RefundMetric(
            metric_type=RefundMetricType.SUCCESS_RATE,
            value=1.0,
            timestamp=datetime.utcnow(),
            additional_data={
                "refund_id": refund_id,
                "existing_refund_id": existing_refund_id,
                "prevention_type": "idempotency",
                "outcome": "duplicate_prevented"
            }
        ))
        
    def get_hourly_metrics(self, hours: int = 24) -> Dict[str, Any]:
        """Get refund metrics for the last N hours"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        try:
            with SessionLocal() as session:
                # Get refund counts by type
                refund_counts = (
                    session.query(Refund.refund_type, func.count(Refund.id))
                    .filter(Refund.created_at >= cutoff_time)
                    .group_by(Refund.refund_type)
                    .all()
                )
                
                # Get refund amounts by type
                refund_amounts = (
                    session.query(Refund.refund_type, func.sum(Refund.amount))
                    .filter(Refund.created_at >= cutoff_time)
                    .group_by(Refund.refund_type)
                    .all()
                )
                
                # Get success rates
                success_counts = (
                    session.query(Refund.refund_type, Refund.status, func.count(Refund.id))
                    .filter(Refund.created_at >= cutoff_time)
                    .group_by(Refund.refund_type, Refund.status)
                    .all()
                )
                
                return {
                    "time_period": f"Last {hours} hours",
                    "refund_counts": dict(refund_counts),
                    "refund_amounts": {k: float(v) for k, v in refund_amounts},
                    "success_rates": self._calculate_success_rates(success_counts),
                    "generated_at": datetime.utcnow().isoformat()
                }
                
        except Exception as e:
            logger.error(f"Error generating hourly metrics: {e}")
            return {"error": str(e)}
    
    def _calculate_success_rates(self, success_counts) -> Dict[str, Dict[str, float]]:
        """Calculate success rates by refund type"""
        rates = {}
        type_totals = {}
        
        # First pass: calculate totals
        for refund_type, status, count in success_counts:
            if refund_type not in type_totals:
                type_totals[refund_type] = 0
            type_totals[refund_type] += count
            
        # Second pass: calculate rates
        for refund_type, status, count in success_counts:
            if refund_type not in rates:
                rates[refund_type] = {}
            
            total = type_totals[refund_type]
            rates[refund_type][status] = (count / total * 100) if total > 0 else 0
            
        return rates
    
    def check_alert_conditions(self) -> list[Dict[str, Any]]:
        """Check for conditions that should trigger alerts"""
        alerts = []
        
        try:
            metrics = self.get_hourly_metrics(1)  # Last hour
            
            # Alert if error rate > 5%
            for refund_type, rates in metrics.get("success_rates", {}).items():
                failed_rate = rates.get("failed", 0)
                if failed_rate > 5:
                    alerts.append({
                        "type": "high_failure_rate",
                        "severity": "warning" if failed_rate < 10 else "critical",
                        "message": f"High failure rate for {refund_type}: {failed_rate:.1f}%",
                        "data": {"refund_type": refund_type, "failure_rate": failed_rate}
                    })
            
            # Alert if total refund volume is unusually high
            total_refunds = sum(metrics.get("refund_counts", {}).values())
            if total_refunds > 50:  # More than 50 refunds in an hour
                alerts.append({
                    "type": "high_volume",
                    "severity": "warning",
                    "message": f"High refund volume: {total_refunds} refunds in last hour",
                    "data": {"total_refunds": total_refunds}
                })
                
        except Exception as e:
            logger.error(f"Error checking alert conditions: {e}")
            
        return alerts


# Global monitor instance
refund_monitor = RefundMonitor()