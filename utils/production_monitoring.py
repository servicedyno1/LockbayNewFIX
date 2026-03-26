"""
Production Monitoring Module
Integrates all production-level monitoring components for comprehensive system oversight
"""

import logging
import asyncio

logger = logging.getLogger(__name__)


def start_production_monitoring():
    """Initialize and start all production monitoring components"""
    try:
        logger.info("🔍 Starting production monitoring systems...")
        
        # Initialize production safeguards
        _initialize_safeguards()
        
        # Initialize anomaly detection
        _initialize_anomaly_detection()
        
        # Initialize production cache monitoring
        _initialize_cache_monitoring()
        
        # Initialize production validation
        _initialize_production_validation()
        
        logger.info("✅ Production monitoring systems initialized successfully")
        
    except Exception as e:
        logger.warning(f"Production monitoring initialization had issues: {e}")
        # Don't fail completely - this is non-critical monitoring


def _initialize_safeguards():
    """Initialize production safeguards"""
    try:
        from utils.production_safeguards import ProductionSafeguards
        ProductionSafeguards.ensure_database_constraints()
        logger.info("✅ Production safeguards initialized")
    except Exception as e:
        logger.warning(f"Production safeguards initialization failed: {e}")


def _initialize_anomaly_detection():
    """Initialize anomaly detection system"""
    try:
        from utils.production_anomaly_detector import ProductionAnomalyDetector
        # Create global instance for anomaly detection
        ProductionAnomalyDetector()
        logger.info("✅ Production anomaly detection initialized")
    except Exception as e:
        logger.warning(f"Anomaly detection initialization failed: {e}")


def _initialize_cache_monitoring():
    """Initialize production cache monitoring"""
    try:
        from utils.production_cache import setup_production_cache
        setup_production_cache()
        logger.info("✅ Production cache monitoring initialized")
    except Exception as e:
        logger.warning(f"Production cache monitoring initialization failed: {e}")


def _initialize_production_validation():
    """Initialize production validation systems"""
    try:
        from utils.production_validator import ProductionValidator
        ProductionValidator()
        logger.info("✅ Production validation systems initialized")
    except Exception as e:
        logger.warning(f"Production validation initialization failed: {e}")


async def start_production_monitoring_async():
    """Async version for use in background operations"""
    await asyncio.get_event_loop().run_in_executor(None, start_production_monitoring)