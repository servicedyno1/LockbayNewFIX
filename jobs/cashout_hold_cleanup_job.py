"""
Background job to clean up unreleased holds for completed cashouts
Ensures no frozen balance issues persist
"""

import logging
import asyncio
from utils.cashout_completion_handler import cleanup_completed_cashout_holds

logger = logging.getLogger(__name__)


async def run_cashout_hold_cleanup():
    """
    Main entry point for cashout hold cleanup job
    Scans for completed cashouts with unreleased holds and releases them
    """
    try:
        logger.info("🔄 HOLD_CLEANUP: Starting cashout hold cleanup job")
        
        results = await cleanup_completed_cashout_holds()
        
        if results.get("error"):
            logger.error(f"❌ HOLD_CLEANUP_FAILED: {results['error']}")
            return {"success": False, "error": results["error"]}
        
        if results["released"] > 0:
            logger.info(
                f"✅ HOLD_CLEANUP_COMPLETE: Released {results['released']} holds "
                f"totaling ${results['total_amount_released']:.2f} from {results['processed']} cashouts"
            )
        else:
            logger.debug(f"✅ HOLD_CLEANUP_COMPLETE: No unreleased holds found ({results['processed']} cashouts checked)")
        
        return {
            "success": True,
            "processed": results["processed"],
            "released": results["released"],
            "total_amount_released": results["total_amount_released"],
            "errors": results["errors"]
        }
        
    except Exception as e:
        logger.error(f"❌ HOLD_CLEANUP_ERROR: Critical error in cashout hold cleanup: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Can be run standalone for testing
    async def test_cleanup():
        result = await run_cashout_hold_cleanup()
        print(f"Cleanup result: {result}")
    
    asyncio.run(test_cleanup())