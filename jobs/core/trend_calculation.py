"""
Core Trend Calculation Job - Completion Time Monitoring

Periodically calculates and updates completion time trends for system performance monitoring.
Integrates with the completion time trends monitor to provide data-driven insights.
"""

import logging
from datetime import datetime, timezone

from utils.completion_time_trends_monitor import completion_time_monitor
from utils.safe_timing import safe_datetime_duration

logger = logging.getLogger(__name__)


async def run_trend_calculation():
    """
    Core trend calculation job - analyzes completion time patterns and generates insights
    
    This job:
    1. Triggers trend analysis update for all monitored operations
    2. Calculates performance scores and detects regressions
    3. Generates alerts for significant changes
    4. Logs comprehensive trend summaries for admin monitoring
    """
    
    try:
        logger.info("📈 CORE_TREND_CALCULATION: Starting trend analysis cycle")
        
        # Start trend calculation timer using UTC timezone for consistency
        start_time = datetime.now(timezone.utc)
        
        # Initialize stats tracking
        trend_stats = {
            'operations_analyzed': 0,
            'regressions_detected': 0,
            'improvements_detected': 0,
            'alerts_generated': 0,
            'total_records_processed': 0
        }
        
        # Force update of all trends (bypass the 5-minute background loop)
        await completion_time_monitor._update_all_trends()
        
        # Get comprehensive trends summary
        trends_summary = completion_time_monitor.get_trends_summary()
        
        # Update stats from summary
        trend_stats['operations_analyzed'] = trends_summary['summary']['total_operations_monitored']
        trend_stats['regressions_detected'] = trends_summary['summary']['regressions_detected']
        trend_stats['improvements_detected'] = trends_summary['summary']['improvements_detected']
        
        # Count total records processed
        for key, records in completion_time_monitor.completion_records.items():
            trend_stats['total_records_processed'] += len(records)
        
        # Log detailed trend analysis by operation type
        operation_types_analyzed = set()
        performance_scores = []
        
        for key, metrics in trends_summary['metrics'].items():
            operation_type = metrics['operation_type']
            operation_types_analyzed.add(operation_type)
            performance_scores.append(metrics['performance_score'])
            
            # Log significant findings
            if metrics['regression_detected']:
                logger.warning(
                    f"🔴 REGRESSION: {metrics['operation_name']} - "
                    f"Performance degraded by {metrics['avg_change_percent']:.1f}% "
                    f"(Current: {metrics['current_avg_ms']:.0f}ms, Score: {metrics['performance_score']:.1f}/100)"
                )
                trend_stats['alerts_generated'] += 1
                
            elif metrics['improvement_detected']:
                logger.info(
                    f"🟢 IMPROVEMENT: {metrics['operation_name']} - "
                    f"Performance improved by {abs(metrics['avg_change_percent']):.1f}% "
                    f"(Current: {metrics['current_avg_ms']:.0f}ms, Score: {metrics['performance_score']:.1f}/100)"
                )
                trend_stats['alerts_generated'] += 1
            
            # Log concerning performance scores
            elif metrics['performance_score'] < 50:
                logger.warning(
                    f"⚠️ LOW_PERFORMANCE: {metrics['operation_name']} - "
                    f"Score: {metrics['performance_score']:.1f}/100 "
                    f"(Current: {metrics['current_avg_ms']:.0f}ms, Trend: {metrics['trend_direction']})"
                )
        
        # Calculate execution time safely to prevent negative durations
        execution_time = safe_datetime_duration(
            start_time,
            datetime.now(timezone.utc),
            scale_factor=1.0,  # Keep in seconds
            min_duration=0.0
        )
        
        # Log comprehensive summary
        avg_performance_score = sum(performance_scores) / len(performance_scores) if performance_scores else 0
        
        logger.info(
            f"📊 TREND_ANALYSIS_COMPLETE: {trend_stats['operations_analyzed']} operations analyzed in {execution_time:.2f}s"
        )
        logger.info(
            f"📈 PERFORMANCE_SUMMARY: Avg Score: {avg_performance_score:.1f}/100, "
            f"Records: {trend_stats['total_records_processed']}, "
            f"Operation Types: {len(operation_types_analyzed)}"
        )
        logger.info(
            f"🚨 ALERTS_SUMMARY: {trend_stats['regressions_detected']} regressions, "
            f"{trend_stats['improvements_detected']} improvements, "
            f"{trend_stats['alerts_generated']} alerts generated"
        )
        
        # Log operation type breakdown
        if operation_types_analyzed:
            logger.info(f"🔍 OPERATION_TYPES_MONITORED: {', '.join(sorted(operation_types_analyzed))}")
        
        # Log recent significant operations (top 5 by volume)
        recent_operations = []
        for key, records in completion_time_monitor.completion_records.items():
            if len(records) >= 5:  # Only include operations with significant data
                recent_operations.append({
                    'operation': key,
                    'record_count': len(records),
                    'latest_record': max(records, key=lambda r: r.timestamp) if records else None
                })
        
        # Sort by record count and log top operations
        recent_operations.sort(key=lambda x: x['record_count'], reverse=True)
        if recent_operations:
            top_operations = recent_operations[:5]
            logger.info("🏆 TOP_ACTIVE_OPERATIONS:")
            for op in top_operations:
                latest_time = op['latest_record'].timestamp.strftime("%H:%M:%S") if op['latest_record'] else "N/A"
                logger.info(f"   • {op['operation']}: {op['record_count']} records (latest: {latest_time})")
        
        # Return execution summary for monitoring
        return {
            'success': True,
            'execution_time_seconds': execution_time,
            'operations_analyzed': trend_stats['operations_analyzed'],
            'regressions_detected': trend_stats['regressions_detected'],
            'improvements_detected': trend_stats['improvements_detected'],
            'alerts_generated': trend_stats['alerts_generated'],
            'total_records_processed': trend_stats['total_records_processed'],
            'avg_performance_score': avg_performance_score,
            'operation_types_count': len(operation_types_analyzed)
        }
        
    except Exception as e:
        logger.error(f"Error in trend calculation job: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'execution_time_seconds': (datetime.now() - start_time).total_seconds() if 'start_time' in locals() else 0
        }


async def calculate_operation_health_metrics():
    """
    Calculate health metrics for different operation types
    
    Returns insights about:
    - Most/least performant operations
    - Operations showing consistent degradation
    - Operations with high variance (instability)
    """
    
    try:
        health_metrics = {
            'stable_operations': [],
            'degrading_operations': [],
            'volatile_operations': [],
            'improving_operations': [],
            'insufficient_data': []
        }
        
        trends_summary = completion_time_monitor.get_trends_summary()
        
        for key, metrics in trends_summary['metrics'].items():
            operation_info = {
                'operation': metrics['operation_name'],
                'type': metrics['operation_type'],
                'performance_score': metrics['performance_score'],
                'avg_change_percent': metrics['avg_change_percent'],
                'sample_count': metrics['sample_count']
            }
            
            # Categorize based on trend direction
            trend_direction = metrics['trend_direction']
            if trend_direction == 'stable':
                health_metrics['stable_operations'].append(operation_info)
            elif trend_direction == 'degrading':
                health_metrics['degrading_operations'].append(operation_info)
            elif trend_direction == 'volatile':
                health_metrics['volatile_operations'].append(operation_info)
            elif trend_direction == 'improving':
                health_metrics['improving_operations'].append(operation_info)
            else:
                health_metrics['insufficient_data'].append(operation_info)
        
        # Sort by performance score
        for category in health_metrics:
            health_metrics[category].sort(key=lambda x: x['performance_score'], reverse=True)
        
        logger.info(f"🏥 OPERATION_HEALTH: "
                   f"Stable: {len(health_metrics['stable_operations'])}, "
                   f"Degrading: {len(health_metrics['degrading_operations'])}, "
                   f"Volatile: {len(health_metrics['volatile_operations'])}, "
                   f"Improving: {len(health_metrics['improving_operations'])}")
        
        return health_metrics
        
    except Exception as e:
        logger.error(f"Error calculating operation health metrics: {e}")
        return {}