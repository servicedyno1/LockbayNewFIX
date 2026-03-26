"""
Cleanup job for expired pending cashout tokens
Runs every 5 minutes to clean up expired tokens for security
"""

import logging
from utils.cashout_token_security import CashoutTokenSecurity

logger = logging.getLogger(__name__)


class PendingCashoutCleanup:
    """Cleanup expired pending cashout tokens"""
    
    @staticmethod
    def run_cleanup():
        """Clean up expired pending cashout tokens"""
        try:
            logger.info("🧹 Starting pending cashout token cleanup")
            
            # Clean up expired tokens
            deleted_count = CashoutTokenSecurity.cleanup_expired_tokens()
            
            if deleted_count > 0:
                logger.info(f"🧹 Cleaned up {deleted_count} expired cashout tokens")
            else:
                logger.debug("🧹 No expired cashout tokens to clean up")
                
            return deleted_count
            
        except Exception as e:
            logger.error(f"❌ Error in pending cashout cleanup: {e}")
            return 0


def cleanup_expired_cashout_tokens():
    """Entry point for scheduled cleanup job"""
    return PendingCashoutCleanup.run_cleanup()