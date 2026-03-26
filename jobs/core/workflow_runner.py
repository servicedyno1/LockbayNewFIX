"""
Core Workflow Runner - UTE Execution and Outbox Processing

This core job consolidates:
- UTE (Unified Transaction Engine) step processing
- Outbox message processing
- Saga orchestration
- Transaction workflow execution
- State machine progression

Replaces multiple specialized workflow jobs with a single optimized runner.
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
from database import managed_session, get_db_session
from services.unified_transaction_engine import unified_transaction_engine
from utils.performance_monitor import performance_monitor

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Core workflow execution engine for UTE and outbox processing"""

    def __init__(self):
        self.batch_size = 50  # Process up to 50 items per run
        self.max_execution_time = 300  # 5 minutes max execution time
        
    async def run_core_workflow_processing(self) -> Dict[str, Any]:
        """
        Main workflow processing entry point
        Handles all UTE execution and outbox processing
        """
        start_time = datetime.utcnow()
        results = {
            "ute_processing": {"processed": 0, "successful": 0, "failed": 0},
            "outbox_processing": {"processed": 0, "successful": 0, "failed": 0},
            "saga_orchestration": {"processed": 0, "successful": 0, "failed": 0},
            "execution_time_ms": 0,
            "status": "success"
        }
        
        logger.info("🔄 CORE_WORKFLOW_RUNNER: Starting workflow processing cycle")
        
        try:
            # 1. Process UTE steps (highest priority)
            ute_results = await self._process_ute_steps()
            results["ute_processing"] = ute_results
            
            # 2. Process outbox messages  
            outbox_results = await self._process_outbox_messages()
            results["outbox_processing"] = outbox_results
            
            # 3. Handle saga orchestration
            saga_results = await self._orchestrate_sagas()
            results["saga_orchestration"] = saga_results
            
            # 4. Update performance metrics
            execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            results["execution_time_ms"] = execution_time
            
            total_processed = (
                ute_results.get("processed", 0) + 
                outbox_results.get("processed", 0) + 
                saga_results.get("processed", 0)
            )
            
            if total_processed > 0:
                logger.info(
                    f"✅ CORE_WORKFLOW_COMPLETE: Processed {total_processed} items "
                    f"in {execution_time:.0f}ms"
                )
            else:
                logger.debug("💤 CORE_WORKFLOW_IDLE: No workflow items to process")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ CORE_WORKFLOW_ERROR: Workflow processing failed: {e}")
            results["status"] = "error"
            results["error"] = str(e)
            return results

    async def _process_ute_steps(self) -> Dict[str, Any]:
        """
        Process pending UTE transaction steps
        
        Note: UTE step processing is currently handled internally by the UTE engine
        when transactions are created and processed. External polling of pending
        steps is not required as the UTE manages its own state machine progression.
        """
        results = {"processed": 0, "successful": 0, "failed": 0}
        
        # UTE processes transactions internally via its state machine
        # No external step polling needed - this is a placeholder for future expansion
        logger.debug("UTE processes transactions internally, no external step polling required")
        
        return results

    async def _process_outbox_messages(self) -> Dict[str, Any]:
        """Process outbox notification queue using ConsolidatedNotificationService"""
        results = {"processed": 0, "successful": 0, "failed": 0}
        
        try:
            # Import and use ConsolidatedNotificationService for outbox processing
            from services.consolidated_notification_service import ConsolidatedNotificationService
            
            # Initialize notification service if not already done
            notification_service = ConsolidatedNotificationService()
            if not notification_service.initialized:
                await notification_service.initialize()
            
            # Process pending outbox notifications
            outbox_result = await notification_service.process_outbox_notifications()
            
            if isinstance(outbox_result, dict):
                results["processed"] = outbox_result.get("processed", 0)
                results["successful"] = outbox_result.get("successful", 0)
                results["failed"] = outbox_result.get("failed", 0)
            else:
                results["processed"] = outbox_result or 0
                results["successful"] = outbox_result or 0
                
            if results["processed"] > 0:
                logger.info(
                    f"📤 OUTBOX: Processed {results['processed']} notifications "
                    f"({results['successful']} success, {results['failed']} failed)"
                )
            else:
                logger.debug("💤 OUTBOX_IDLE: No pending notifications to process")
                
        except Exception as e:
            logger.error(f"❌ OUTBOX_PROCESSING_ERROR: {e}")
            results["failed"] = 1
            
        return results

    async def _orchestrate_sagas(self) -> Dict[str, Any]:
        """Handle saga orchestration and compensation"""
        results = {"processed": 0, "successful": 0, "failed": 0}
        
        try:
            # Attempt to import saga orchestrator if available
            # Note: saga_orchestrator is an optional module for advanced multi-step transaction workflows
            try:
                from services.saga_orchestrator import saga_orchestrator  # type: ignore[import]
                
                saga_result = await saga_orchestrator.process_pending_sagas(
                    batch_size=self.batch_size
                )
                
                if isinstance(saga_result, dict):
                    results["processed"] = saga_result.get("processed", 0)  
                    results["successful"] = saga_result.get("successful", 0)
                    results["failed"] = saga_result.get("failed", 0)
                else:
                    results["processed"] = saga_result or 0
                    results["successful"] = saga_result or 0
                    
                if results["processed"] > 0:
                    logger.info(
                        f"🎭 SAGAS: Processed {results['processed']} sagas "
                        f"({results['successful']} success, {results['failed']} failed)"
                    )
                    
            except ImportError:
                # Saga orchestrator module not installed - this is expected if not using saga patterns
                logger.debug("Saga orchestrator module not found, skipping saga processing (sagas handled internally by UTE)")
                
        except Exception as e:
            logger.error(f"❌ SAGA_ORCHESTRATION_ERROR: {e}")
            results["failed"] = 1
            
        return results


# Global workflow runner instance
workflow_runner = WorkflowRunner()


# Exported functions for scheduler integration
async def run_workflow_processing():
    """Main entry point for scheduler - processes UTE steps and outbox messages with pool guard"""
    from database import is_pool_healthy
    if not is_pool_healthy(threshold=0.80):
        logger.warning("POOL_GUARD: Skipping workflow processing - pool utilization too high")
        return {"status": "skipped", "reason": "pool_exhaustion"}
    return await workflow_runner.run_core_workflow_processing()


async def run_ute_processing():
    """Process UTE steps only - for high-frequency processing"""
    return await workflow_runner._process_ute_steps()


async def run_outbox_processing():
    """Process outbox messages only - for message-specific processing"""  
    return await workflow_runner._process_outbox_messages()


async def run_saga_orchestration():
    """Handle saga orchestration only - for complex transaction flows"""
    return await workflow_runner._orchestrate_sagas()


# Export for scheduler
__all__ = [
    "WorkflowRunner",
    "workflow_runner", 
    "run_workflow_processing",
    "run_ute_processing",
    "run_outbox_processing", 
    "run_saga_orchestration"
]