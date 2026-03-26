"""
Webhook Intake Service Integration
Connects the durable webhook queue with existing webhook handlers
"""

import asyncio
import logging
from typing import Dict, Any
from webhook_queue.webhook_inbox.webhook_processor import webhook_processor
from handlers.dynopay_webhook import DynoPayWebhookHandler
from handlers.dynopay_exchange_webhook import DynoPayExchangeWebhookHandler

logger = logging.getLogger(__name__)


class WebhookIntakeService:
    """
    Service to integrate durable webhook intake with existing webhook processors.
    
    This service:
    - Registers webhook processors for different providers/endpoints
    - Starts background processing of queued webhook events
    - Provides monitoring and management interfaces
    """
    
    def __init__(self):
        self.is_running = False
        self._processing_task = None
        
    async def initialize(self):
        """Initialize webhook intake service with processor registration"""
        try:
            logger.info("🔧 WEBHOOK_INTAKE: Initializing service...")
            
            # Register DynoPay webhook processors
            self._register_dynopay_processors()
            
            # Register Fincra webhook processors
            self._register_fincra_processors()
            
            logger.info("✅ WEBHOOK_INTAKE: Service initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ WEBHOOK_INTAKE: Initialization failed: {e}")
            raise
    
    def _register_dynopay_processors(self):
        """Register DynoPay webhook processors"""
        
        # Register DynoPay payment webhook processor
        webhook_processor.register_processor(
            provider="dynopay",
            endpoint="payment", 
            processor_func=self._process_dynopay_payment_webhook
        )
        
        # Register DynoPay exchange webhook processor
        webhook_processor.register_processor(
            provider="dynopay",
            endpoint="exchange",
            processor_func=self._process_dynopay_exchange_webhook
        )
        
        # Register DynoPay escrow webhook processor
        webhook_processor.register_processor(
            provider="dynopay",
            endpoint="escrow",
            processor_func=self._process_dynopay_escrow_webhook
        )
        
        # Register DynoPay wallet webhook processor
        webhook_processor.register_processor(
            provider="dynopay",
            endpoint="wallet",
            processor_func=self._process_dynopay_wallet_webhook
        )
        
        logger.info("✅ WEBHOOK_INTAKE: Registered DynoPay webhook processors (payment, exchange, escrow, wallet)")
    
    def _register_fincra_processors(self):
        """Register Fincra webhook processors"""
        
        webhook_processor.register_processor(
            provider="fincra",
            endpoint="payment",
            processor_func=self._process_fincra_payment_webhook
        )
        
        logger.info("✅ WEBHOOK_INTAKE: Registered Fincra webhook processors")
    
    async def _process_dynopay_payment_webhook(
        self, 
        payload: Dict[str, Any], 
        headers: Dict[str, str],
        client_ip: str,
        signature: str = None,
        metadata: Dict[str, Any] = None,
        event_id: str = None
    ) -> Dict[str, Any]:
        """Process DynoPay payment webhook from queue"""
        try:
            logger.info(f"🔄 WEBHOOK_INTAKE: Processing DynoPay payment webhook {event_id[:8] if event_id else 'unknown'}")
            
            # Call existing DynoPay webhook handler
            result = await DynoPayWebhookHandler.handle_escrow_deposit_webhook(payload)
            
            if isinstance(result, dict):
                if result.get('status') == 'success' or result.get('ok'):
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay payment webhook processed successfully")
                    return {"status": "success", "result": result}
                elif result.get('status') == 'already_processing':
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay payment webhook already processing - passing through")
                    return {"status": "already_processing", "message": result.get('message')}
                else:
                    logger.warning(f"⚠️ WEBHOOK_INTAKE: DynoPay payment webhook returned error: {result}")
                    return {"status": "retry", "message": result.get('message', 'Unknown error')}
            else:
                logger.info("✅ WEBHOOK_INTAKE: DynoPay payment webhook processed (no specific result)")
                return {"status": "success"}
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ WEBHOOK_INTAKE: Error processing DynoPay payment webhook: {error_msg}")
            
            # Check if this is a retryable error
            if any(keyword in error_msg.lower() for keyword in ['database', 'connection', 'timeout', 'ssl']):
                return {"status": "retry", "message": error_msg}
            else:
                return {"status": "error", "message": error_msg}
    
    async def _process_dynopay_exchange_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str], 
        client_ip: str,
        signature: str = None,
        metadata: Dict[str, Any] = None,
        event_id: str = None
    ) -> Dict[str, Any]:
        """Process DynoPay exchange webhook from queue"""
        try:
            logger.info(f"🔄 WEBHOOK_INTAKE: Processing DynoPay exchange webhook {event_id[:8] if event_id else 'unknown'}")
            
            # Call existing DynoPay exchange webhook handler
            result = await DynoPayExchangeWebhookHandler.handle_exchange_deposit_webhook(payload, headers)
            
            if isinstance(result, dict):
                if result.get('status') == 'success' or result.get('ok'):
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay exchange webhook processed successfully")
                    return {"status": "success", "result": result}
                elif result.get('status') == 'already_processing':
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay exchange webhook already processing - passing through")
                    return {"status": "already_processing", "message": result.get('message')}
                else:
                    logger.warning(f"⚠️ WEBHOOK_INTAKE: DynoPay exchange webhook returned error: {result}")
                    return {"status": "retry", "message": result.get('message', 'Unknown error')}
            else:
                logger.info("✅ WEBHOOK_INTAKE: DynoPay exchange webhook processed (no specific result)")
                return {"status": "success"}
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ WEBHOOK_INTAKE: Error processing DynoPay exchange webhook: {error_msg}")
            
            # Check if this is a retryable error
            if any(keyword in error_msg.lower() for keyword in ['database', 'connection', 'timeout', 'ssl']):
                return {"status": "retry", "message": error_msg}
            else:
                return {"status": "error", "message": error_msg}
    
    async def _process_dynopay_escrow_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        client_ip: str,
        signature: str = None,
        metadata: Dict[str, Any] = None,
        event_id: str = None
    ) -> Dict[str, Any]:
        """Process DynoPay escrow payment webhook from queue"""
        try:
            logger.info(f"🔄 WEBHOOK_INTAKE: Processing DynoPay escrow webhook {event_id[:8] if event_id else 'unknown'}")
            
            # Call existing DynoPay webhook handler for escrow deposits
            result = await DynoPayWebhookHandler.handle_escrow_deposit_webhook(payload)
            
            if isinstance(result, dict):
                if result.get('status') == 'success' or result.get('ok'):
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay escrow webhook processed successfully")
                    return {"status": "success", "result": result}
                elif result.get('status') == 'already_processing':
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay escrow webhook already processing - passing through")
                    return {"status": "already_processing", "message": result.get('message')}
                else:
                    logger.warning(f"⚠️ WEBHOOK_INTAKE: DynoPay escrow webhook returned error: {result}")
                    return {"status": "retry", "message": result.get('message', 'Unknown error')}
            else:
                logger.info("✅ WEBHOOK_INTAKE: DynoPay escrow webhook processed (no specific result)")
                return {"status": "success"}
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ WEBHOOK_INTAKE: Error processing DynoPay escrow webhook: {error_msg}")
            
            # Check if this is a retryable error
            if any(keyword in error_msg.lower() for keyword in ['database', 'connection', 'timeout', 'ssl']):
                return {"status": "retry", "message": error_msg}
            else:
                return {"status": "error", "message": error_msg}
    
    async def _process_dynopay_wallet_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        client_ip: str,
        signature: str = None,
        metadata: Dict[str, Any] = None,
        event_id: str = None
    ) -> Dict[str, Any]:
        """Process DynoPay wallet deposit webhook from queue"""
        try:
            logger.info(f"🔄 WEBHOOK_INTAKE: Processing DynoPay wallet webhook {event_id[:8] if event_id else 'unknown'}")
            
            # FIXED: Call correct wallet deposit handler instead of escrow handler
            result = await DynoPayWebhookHandler.handle_wallet_deposit_webhook(payload)
            
            if isinstance(result, dict):
                if result.get('status') == 'success' or result.get('ok'):
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay wallet webhook processed successfully")
                    return {"status": "success", "result": result}
                elif result.get('status') == 'already_processing':
                    logger.info("✅ WEBHOOK_INTAKE: DynoPay wallet webhook already processing - passing through")
                    return {"status": "already_processing", "message": result.get('message')}
                else:
                    logger.warning(f"⚠️ WEBHOOK_INTAKE: DynoPay wallet webhook returned error: {result}")
                    return {"status": "retry", "message": result.get('message', 'Unknown error')}
            else:
                logger.info("✅ WEBHOOK_INTAKE: DynoPay wallet webhook processed (no specific result)")
                return {"status": "success"}
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ WEBHOOK_INTAKE: Error processing DynoPay wallet webhook: {error_msg}")
            
            # Check if this is a retryable error
            if any(keyword in error_msg.lower() for keyword in ['database', 'connection', 'timeout', 'ssl']):
                return {"status": "retry", "message": error_msg}
            else:
                return {"status": "error", "message": error_msg}
    
    async def _process_fincra_payment_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        client_ip: str,
        signature: str = None,
        metadata: Dict[str, Any] = None,
        event_id: str = None
    ) -> Dict[str, Any]:
        """Process Fincra payment webhook from queue with signature verification and idempotency"""
        try:
            logger.info(f"🔄 WEBHOOK_INTAKE: Processing Fincra payment webhook {event_id[:8] if event_id else 'unknown'}")
            
            # SECURITY FIX: Call production-safe Fincra webhook handler with signature verification and idempotency
            # CRITICAL: Pass metadata with raw_body for accurate signature verification
            from handlers.fincra_webhook import process_fincra_webhook_from_queue
            result = await process_fincra_webhook_from_queue(
                payload=payload,
                headers=headers,
                client_ip=client_ip,
                event_id=event_id,
                metadata=metadata
            )
            
            # Handle all status returns properly
            if isinstance(result, dict):
                status = result.get('status')
                
                if status == 'success':
                    logger.info("✅ WEBHOOK_INTAKE: Fincra payment webhook processed successfully")
                    return {"status": "success", "result": result.get('result')}
                    
                elif status == 'already_processing':
                    logger.info("✅ WEBHOOK_INTAKE: Fincra payment webhook already processing - passing through")
                    return {"status": "already_processing", "message": result.get('message')}
                    
                elif status == 'retry':
                    logger.warning(f"⚠️ WEBHOOK_INTAKE: Fincra payment webhook needs retry: {result.get('message')}")
                    return {"status": "retry", "message": result.get('message', 'Processing requires retry')}
                    
                else:  # error or unknown status
                    logger.error(f"❌ WEBHOOK_INTAKE: Fincra payment webhook returned error: {result}")
                    return {"status": "error", "message": result.get('message', 'Unknown error')}
            else:
                logger.info("✅ WEBHOOK_INTAKE: Fincra payment webhook processed (no specific result)")
                return {"status": "success"}
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ WEBHOOK_INTAKE: Error processing Fincra payment webhook: {error_msg}")
            
            # Check if this is a retryable error
            if any(keyword in error_msg.lower() for keyword in ['database', 'connection', 'timeout', 'ssl']):
                return {"status": "retry", "message": error_msg}
            else:
                return {"status": "error", "message": error_msg}
    
    async def start_processing(self, batch_size: int = 10, poll_interval: float = 1.0):
        """Start background webhook processing"""
        if self.is_running:
            logger.warning("⚠️ WEBHOOK_INTAKE: Already running")
            return
        
        logger.info(f"🚀 WEBHOOK_INTAKE: Starting background processing (batch_size={batch_size}, poll_interval={poll_interval}s)")
        
        self.is_running = True
        self._processing_task = asyncio.create_task(
            webhook_processor.start_processing(batch_size, poll_interval)
        )
        
        # Wait a moment to ensure processing starts
        await asyncio.sleep(0.1)
        
        if webhook_processor.is_running:
            logger.info("✅ WEBHOOK_INTAKE: Background processing started successfully")
        else:
            logger.error("❌ WEBHOOK_INTAKE: Failed to start background processing")
            self.is_running = False
    
    async def stop_processing(self):
        """Stop background webhook processing"""
        if not self.is_running:
            logger.warning("⚠️ WEBHOOK_INTAKE: Not running")
            return
        
        logger.info("🛑 WEBHOOK_INTAKE: Stopping background processing...")
        
        # Stop the processor
        await webhook_processor.stop_processing()
        
        # Cancel the processing task
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
            self._processing_task = None
        
        self.is_running = False
        logger.info("✅ WEBHOOK_INTAKE: Background processing stopped")
    
    def get_status(self) -> Dict[str, Any]:
        """Get webhook intake service status"""
        return {
            "service_running": self.is_running,
            "processor_running": webhook_processor.is_running,
            "registered_processors": list(webhook_processor.processors.keys()),
            "processor_stats": webhook_processor.get_stats()
        }


# Global instance
webhook_intake_service = WebhookIntakeService()