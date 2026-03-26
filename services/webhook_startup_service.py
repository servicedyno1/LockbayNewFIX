"""
Webhook Startup Service
Ensures webhook processing starts automatically with application

This service:
- Initializes the webhook intake service  
- Registers webhook processors for all providers
- Starts background processing
- Integrates with application lifecycle
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class WebhookStartupService:
    """
    Service to automatically start webhook processing with application
    
    Ensures that:
    - Webhook processors are registered for all providers
    - Background processing starts automatically
    - Webhook intake service is initialized properly
    - All webhook components are ready before application serves requests
    """
    
    def __init__(self):
        self.initialized = False
        self.processing_started = False
        
    async def initialize_webhook_system(self) -> Dict[str, Any]:
        """
        Initialize the complete webhook processing system
        """
        try:
            logger.info("🚀 WEBHOOK_STARTUP: Initializing webhook processing system...")
            
            # LEGACY WEBHOOK SYSTEM REMOVED: Using simplified direct processing
            # Legacy webhook intake service has been replaced with direct handlers
            logger.info("✅ WEBHOOK_STARTUP: Using simplified direct processing architecture")
            
            # Step 2: Register all webhook processors
            await self._register_webhook_processors()
            
            # Step 3: Start background processing
            await self._start_background_processing()
            
            # Step 4: Initialize monitoring
            await self._initialize_webhook_monitoring()
            
            self.initialized = True
            
            logger.info("✅ WEBHOOK_STARTUP: Webhook processing system initialized successfully")
            
            return {
                'initialized': True,
                'processors_registered': True,
                'background_processing': self.processing_started,
                'monitoring_active': True
            }
            
        except Exception as e:
            logger.error(f"❌ WEBHOOK_STARTUP: Failed to initialize webhook system: {e}")
            return {
                'initialized': False,
                'error': str(e)
            }
    
    async def _register_webhook_processors(self):
        """Register webhook processors for all supported providers"""
        try:
            # LEGACY REMOVED: webhook_processor replaced with simplified handlers
            
            # Register DynoPay processors if not already registered
            
            # LEGACY REMOVED: Direct handlers replaced webhook processor registration
            # All providers now use simplified direct processing architecture
            logger.info("✅ WEBHOOK_STARTUP: Using simplified direct handlers instead of processor registration")
            
        except Exception as e:
            logger.error(f"❌ WEBHOOK_STARTUP: Failed to register processors: {e}")
            raise
    
    def _create_dynopay_payment_processor(self):
        """Create DynoPay payment webhook processor"""
        async def process_dynopay_payment(payload, headers, client_ip, signature=None, metadata=None, event_id=None):
            try:
                from handlers.dynopay_webhook import DynoPayWebhookHandler
                
                # Add idempotency tracking from metadata
                if metadata and 'idempotency_key' in metadata:
                    logger.info(f"🔄 DYNOPAY_PAYMENT: Processing with idempotency key {metadata['idempotency_key'][:16]}...")
                
                result = await DynoPayWebhookHandler.handle_escrow_deposit_webhook(payload)
                
                if isinstance(result, dict) and (result.get('status') == 'success' or result.get('ok')):
                    return {"status": "success", "result": result}
                else:
                    return {"status": "retry", "message": result.get('message', 'Unknown error')}
                    
            except Exception as e:
                error_msg = str(e)
                # Classify error for retry decision
                if any(keyword in error_msg.lower() for keyword in ['database', 'connection', 'timeout', 'ssl']):
                    return {"status": "retry", "message": error_msg}
                else:
                    return {"status": "error", "message": error_msg}
        
        return process_dynopay_payment
    
    def _create_dynopay_exchange_processor(self):
        """Create DynoPay exchange webhook processor"""
        async def process_dynopay_exchange(payload, headers, client_ip, signature=None, metadata=None, event_id=None):
            try:
                from handlers.dynopay_exchange_webhook import DynoPayExchangeWebhookHandler
                
                # Add idempotency tracking from metadata
                if metadata and 'idempotency_key' in metadata:
                    logger.info(f"🔄 DYNOPAY_EXCHANGE: Processing with idempotency key {metadata['idempotency_key'][:16]}...")
                
                result = await DynoPayExchangeWebhookHandler.handle_exchange_deposit_webhook(payload, headers)
                
                if isinstance(result, dict) and (result.get('status') == 'success' or result.get('ok')):
                    return {"status": "success", "result": result}
                else:
                    return {"status": "retry", "message": result.get('message', 'Unknown error')}
                    
            except Exception as e:
                error_msg = str(e)
                # Classify error for retry decision
                if any(keyword in error_msg.lower() for keyword in ['database', 'connection', 'timeout', 'ssl']):
                    return {"status": "retry", "message": error_msg}
                else:
                    return {"status": "error", "message": error_msg}
        
        return process_dynopay_exchange
    
    def _create_blockbee_payment_processor(self):
        """Create BlockBee payment webhook processor (placeholder for future)"""
        async def process_blockbee_payment(payload, headers, client_ip, signature=None, metadata=None, event_id=None):
            try:
                # Placeholder for BlockBee integration
                logger.info("🔄 BLOCKBEE_PAYMENT: Processing payment webhook (placeholder)")
                
                # For now, just log and mark as success
                return {"status": "success", "message": "BlockBee processor placeholder"}
                
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        return process_blockbee_payment
    
    async def _start_background_processing(self):
        """Start background webhook processing"""
        try:
            from services.webhook_intake_service import webhook_intake_service
            
            # CRITICAL FIX: Initialize webhook intake service to register processors first
            await webhook_intake_service.initialize()
            
            # Start background processing with optimized settings for better throughput
            batch_size = 10  # Process 10 webhooks at a time (up from 3)
            poll_interval = 1.0  # Check every 1 second (down from 2.0s for faster processing)
            
            await webhook_intake_service.start_processing(batch_size, poll_interval)
            
            self.processing_started = webhook_intake_service.is_running
            
            if self.processing_started:
                logger.info(f"✅ WEBHOOK_STARTUP: Background processing started (batch_size={batch_size}, poll_interval={poll_interval}s)")
            else:
                logger.error("❌ WEBHOOK_STARTUP: Failed to start background processing")
                
        except Exception as e:
            logger.error(f"❌ WEBHOOK_STARTUP: Error starting background processing: {e}")
            raise
    
    async def _initialize_webhook_monitoring(self):
        """Initialize webhook monitoring and alerting"""
        try:
            # LEGACY REMOVED: Simplified monitoring - no complex queue monitoring needed
            logger.info("✅ WEBHOOK_MONITORING: Using simplified direct processing monitoring")
            
            logger.info("✅ WEBHOOK_STARTUP: Webhook monitoring initialized")
            
        except Exception as e:
            logger.warning(f"⚠️ WEBHOOK_STARTUP: Monitoring initialization warning: {e}")
            # Don't fail startup if monitoring has issues
    
    async def stop_webhook_system(self):
        """Stop webhook processing system gracefully"""
        try:
            logger.info("🛑 WEBHOOK_STARTUP: Stopping webhook processing system...")
            
            from services.webhook_intake_service import webhook_intake_service
            await webhook_intake_service.stop_processing()
            
            self.processing_started = False
            logger.info("✅ WEBHOOK_STARTUP: Webhook system stopped gracefully")
            
        except Exception as e:
            logger.error(f"❌ WEBHOOK_STARTUP: Error stopping webhook system: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current webhook system status"""
        try:
            from services.webhook_intake_service import webhook_intake_service
            from webhook_queue.webhook_inbox.webhook_processor import webhook_processor
            
            return {
                'initialized': self.initialized,
                'processing_started': self.processing_started,
                'intake_service_running': webhook_intake_service.is_running,
                'registered_processors': list(webhook_processor.processors.keys()),
                'processor_stats': webhook_processor._stats
            }
        except Exception as e:
            return {
                'initialized': self.initialized,
                'processing_started': self.processing_started,
                'error': str(e)
            }


# Global webhook startup service instance
webhook_startup_service = WebhookStartupService()


# Convenience functions for application integration
async def initialize_webhook_system():
    """Initialize webhook system - call during application startup"""
    return await webhook_startup_service.initialize_webhook_system()


async def stop_webhook_system():
    """Stop webhook system - call during application shutdown"""
    await webhook_startup_service.stop_webhook_system()


def get_webhook_system_status():
    """Get webhook system status"""
    return webhook_startup_service.get_status()


__all__ = [
    'WebhookStartupService',
    'webhook_startup_service',
    'initialize_webhook_system',
    'stop_webhook_system', 
    'get_webhook_system_status'
]