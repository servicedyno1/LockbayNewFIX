"""
Trace System Initializer
Comprehensive initialization and setup for the complete trace correlation system
"""

from typing import Dict, Any, Optional, List
from datetime import datetime

# Import all trace correlation modules
from utils.trace_correlation import trace_manager
from utils.trace_logging_integration import setup_trace_logging, get_trace_logger
from utils.telegram_trace_integration import setup_telegram_trace_integration
from utils.financial_trace_integration import setup_financial_trace_integration
from utils.background_job_trace_integration import setup_background_job_trace_integration
from utils.database_trace_integration import setup_database_trace_integration
from utils.service_trace_integration import setup_service_trace_integration
from utils.monitoring_trace_integration import (
    initialize_monitoring_integration,
    log_system_trace_summary,
    get_trace_health_summary
)

logger = get_trace_logger(__name__)

class TraceSystemInitializer:
    """Central initializer for the complete trace correlation system"""
    
    def __init__(self):
        self.initialization_status = {}
        self.startup_time = datetime.utcnow()
        self.initialized = False
        
    async def initialize_complete_system(self) -> Dict[str, Any]:
        """Initialize the complete trace correlation system"""
        
        logger.info("🚀 Starting comprehensive trace correlation system initialization...")
        
        initialization_results = {
            'started_at': self.startup_time.isoformat(),
            'components': {},
            'status': 'initializing'
        }
        
        try:
            # 1. Initialize core trace correlation infrastructure
            await self._initialize_core_infrastructure()
            initialization_results['components']['core_infrastructure'] = 'initialized'
            
            # 2. Setup trace logging integration
            await self._initialize_logging_integration()
            initialization_results['components']['logging_integration'] = 'initialized'
            
            # 3. Initialize Telegram trace integration
            await self._initialize_telegram_integration()
            initialization_results['components']['telegram_integration'] = 'initialized'
            
            # 4. Initialize financial operations tracing
            await self._initialize_financial_integration()
            initialization_results['components']['financial_integration'] = 'initialized'
            
            # 5. Initialize background job tracing
            await self._initialize_background_job_integration()
            initialization_results['components']['background_job_integration'] = 'initialized'
            
            # 6. Initialize database operations tracing
            await self._initialize_database_integration()
            initialization_results['components']['database_integration'] = 'initialized'
            
            # 7. Initialize service integration tracing
            await self._initialize_service_integration()
            initialization_results['components']['service_integration'] = 'initialized'
            
            # 8. Initialize monitoring and debug dashboard
            await self._initialize_monitoring_integration()
            initialization_results['components']['monitoring_integration'] = 'initialized'
            
            # Mark system as initialized
            self.initialized = True
            initialization_results['status'] = 'completed'
            initialization_results['completed_at'] = datetime.utcnow().isoformat()
            
            # Log comprehensive system summary
            system_summary = log_system_trace_summary()
            initialization_results['system_summary'] = system_summary
            
            logger.info(
                "✅ TRACE CORRELATION SYSTEM FULLY INITIALIZED",
                initialization_results=initialization_results
            )
            
            return initialization_results
            
        except Exception as e:
            logger.error(f"❌ Trace system initialization failed: {e}")
            initialization_results['status'] = 'failed'
            initialization_results['error'] = str(e)
            return initialization_results
    
    async def _initialize_core_infrastructure(self):
        """Initialize core trace correlation infrastructure"""
        logger.info("🔗 Initializing core trace correlation infrastructure...")
        
        # Core infrastructure is initialized when imported
        # Verify it's working
        if trace_manager:
            self.initialization_status['core_infrastructure'] = 'active'
            logger.info("✅ Core trace infrastructure verified")
        else:
            raise RuntimeError("Core trace infrastructure failed to initialize")
    
    async def _initialize_logging_integration(self):
        """Initialize trace logging integration"""
        logger.info("📝 Initializing trace logging integration...")
        
        try:
            setup_trace_logging()
            self.initialization_status['logging_integration'] = 'active'
            logger.info("✅ Trace logging integration initialized")
        except Exception as e:
            logger.error(f"Failed to initialize logging integration: {e}")
            raise
    
    async def _initialize_telegram_integration(self):
        """Initialize Telegram trace integration"""
        logger.info("🤖 Initializing Telegram trace integration...")
        
        try:
            setup_telegram_trace_integration()
            self.initialization_status['telegram_integration'] = 'active'
            logger.info("✅ Telegram trace integration initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram integration: {e}")
            raise
    
    async def _initialize_financial_integration(self):
        """Initialize financial operations trace integration"""
        logger.info("💰 Initializing financial operations trace integration...")
        
        try:
            setup_financial_trace_integration()
            self.initialization_status['financial_integration'] = 'active'
            logger.info("✅ Financial operations trace integration initialized")
        except Exception as e:
            logger.error(f"Failed to initialize financial integration: {e}")
            raise
    
    async def _initialize_background_job_integration(self):
        """Initialize background job trace integration"""
        logger.info("⚙️ Initializing background job trace integration...")
        
        try:
            setup_background_job_trace_integration()
            self.initialization_status['background_job_integration'] = 'active'
            logger.info("✅ Background job trace integration initialized")
        except Exception as e:
            logger.error(f"Failed to initialize background job integration: {e}")
            raise
    
    async def _initialize_database_integration(self):
        """Initialize database operations trace integration"""
        logger.info("💾 Initializing database operations trace integration...")
        
        try:
            setup_database_trace_integration()
            self.initialization_status['database_integration'] = 'active'
            logger.info("✅ Database operations trace integration initialized")
        except Exception as e:
            logger.error(f"Failed to initialize database integration: {e}")
            raise
    
    async def _initialize_service_integration(self):
        """Initialize service integration trace correlation"""
        logger.info("🔌 Initializing service integration trace correlation...")
        
        try:
            setup_service_trace_integration()
            self.initialization_status['service_integration'] = 'active'
            logger.info("✅ Service integration trace correlation initialized")
        except Exception as e:
            logger.error(f"Failed to initialize service integration: {e}")
            raise
    
    async def _initialize_monitoring_integration(self):
        """Initialize monitoring and debug dashboard integration"""
        logger.info("📊 Initializing monitoring and debug dashboard integration...")
        
        try:
            initialize_monitoring_integration()
            self.initialization_status['monitoring_integration'] = 'active'
            logger.info("✅ Monitoring and debug dashboard integration initialized")
        except Exception as e:
            logger.error(f"Failed to initialize monitoring integration: {e}")
            raise
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status"""
        return {
            'initialized': self.initialized,
            'startup_time': self.startup_time.isoformat(),
            'components_status': self.initialization_status,
            'health_summary': get_trace_health_summary() if self.initialized else None,
            'last_checked': datetime.utcnow().isoformat()
        }

# Global initializer instance
trace_system_initializer = TraceSystemInitializer()

async def initialize_trace_correlation_system() -> Dict[str, Any]:
    """Initialize the complete trace correlation system"""
    return await trace_system_initializer.initialize_complete_system()

def get_trace_system_status() -> Dict[str, Any]:
    """Get the current trace system status"""
    return trace_system_initializer.get_system_status()

def is_trace_system_initialized() -> bool:
    """Check if the trace system is fully initialized"""
    return trace_system_initializer.initialized

# Quick access functions for common operations
def quick_trace_search(user_id: Optional[int] = None, operation_type: Optional[str] = None, hours: int = 1) -> List[Dict[str, Any]]:
    """Quick search for traces with common parameters"""
    if not is_trace_system_initialized():
        return []
        
    from utils.monitoring_trace_integration import search_traces
    
    search_params = {
        'time_range_hours': hours
    }
    
    if user_id:
        search_params['user_id'] = user_id
    if operation_type:
        search_params['operation_type'] = operation_type
        
    return search_traces(search_params)

def quick_debug_summary() -> Dict[str, Any]:
    """Get a quick debug summary of the trace system"""
    if not is_trace_system_initialized():
        return {'status': 'not_initialized'}
        
    from utils.monitoring_trace_integration import get_debug_dashboard
    return get_debug_dashboard()

# Example usage and integration guide
TRACE_SYSTEM_USAGE_EXAMPLES = {
    'telegram_handler_example': '''
@telegram_traced(operation_name="handle_start_command")
async def start_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handler automatically gets trace correlation
    return await process_start_command(update, context)
''',
    
    'financial_operation_example': '''
@financial_traced(
    financial_operation_type=FinancialOperationType.CASHOUT_PROCESS,
    external_service="fincra",
    capture_amounts=True
)
async def process_cashout(user_id: int, amount: Decimal, currency: str):
    # Financial operation automatically gets trace correlation
    return await execute_cashout(user_id, amount, currency)
''',
    
    'background_job_example': '''
@background_job_traced(
    job_type=BackgroundJobType.BALANCE_MONITOR,
    critical_job=True,
    expected_duration_seconds=30
)
async def balance_monitoring_job():
    # Background job automatically gets trace correlation
    return await check_all_balances()
''',
    
    'database_operation_example': '''
@database_traced(
    operation_type=DatabaseOperationType.QUERY,
    capture_query=True,
    expected_duration_ms=200
)
async def get_user_balances(user_id: int):
    # Database operation automatically gets trace correlation
    return await fetch_user_balances(user_id)
''',
    
    'service_integration_example': '''
@service_traced(
    service_type=ServiceType.EMAIL_SERVICE,
    service_name="brevo",
    expected_duration_ms=3000
)
async def send_notification_email(recipient: str, subject: str, content: str):
    # Service integration automatically gets trace correlation
    return await email_service.send_email(recipient, subject, content)
'''
}

logger.info("🎯 Trace System Initializer ready - comprehensive trace correlation system available")