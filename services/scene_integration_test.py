"""
Scene Engine Integration Test

Tests the complete Scene Engine integration with UTE and provider adapters.
Validates that scene flows work end-to-end with existing infrastructure.
"""

import logging
import asyncio
from typing import Dict, Any

from services.scene_engine import get_scene_engine
from services.scene_ute_integration import get_scene_ute_adapter
from database import SessionLocal
from models import User

logger = logging.getLogger(__name__)

class SceneIntegrationTester:
    """Tests Scene Engine integration with existing systems"""
    
    def __init__(self):
        self.test_results = []
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all integration tests"""
        logger.info("🧪 Starting Scene Engine integration tests...")
        
        try:
            # Test Scene Engine initialization
            await self._test_scene_engine_init()
            
            # Test UTE integration
            await self._test_ute_integration()
            
            # Test scene definitions loading
            await self._test_scene_definitions()
            
            # Test component system
            await self._test_component_system()
            
            # Test complete NGN cashout flow (simulated)
            await self._test_ngn_cashout_flow()
            
            # Test complete crypto cashout flow (simulated)
            await self._test_crypto_cashout_flow()
            
            # Generate test report
            return self._generate_test_report()
        
        except Exception as e:
            logger.error(f"Integration test failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'completed_tests': len(self.test_results)
            }
    
    async def _test_scene_engine_init(self) -> None:
        """Test Scene Engine initialization"""
        try:
            scene_engine = await get_scene_engine()
            assert scene_engine is not None
            self._add_test_result("Scene Engine Init", True, "Scene Engine initialized successfully")
        except Exception as e:
            self._add_test_result("Scene Engine Init", False, f"Initialization failed: {e}")
    
    async def _test_ute_integration(self) -> None:
        """Test UTE integration adapter"""
        try:
            ute_adapter = await get_scene_ute_adapter()
            assert ute_adapter is not None
            self._add_test_result("UTE Integration", True, "UTE adapter initialized successfully")
        except Exception as e:
            self._add_test_result("UTE Integration", False, f"UTE adapter failed: {e}")
    
    async def _test_scene_definitions(self) -> None:
        """Test scene definitions loading"""
        try:
            scene_engine = await get_scene_engine()
            
            # Check if scene definitions are loaded
            router = scene_engine.message_router
            scene_count = len(router.scene_registry)
            
            expected_scenes = ['ngn_cashout', 'crypto_cashout', 'wallet_funding', 'escrow_creation']
            loaded_scenes = list(router.scene_registry.keys())
            
            success = all(scene in loaded_scenes for scene in expected_scenes)
            message = f"Loaded {scene_count} scenes: {loaded_scenes}"
            
            self._add_test_result("Scene Definitions", success, message)
        except Exception as e:
            self._add_test_result("Scene Definitions", False, f"Scene loading failed: {e}")
    
    async def _test_component_system(self) -> None:
        """Test component system functionality"""
        try:
            # Import component classes to test imports
            from components import (
                AmountInputComponent, AddressSelectorComponent, BankSelectorComponent,
                ConfirmationComponent, StatusDisplayComponent
            )
            
            # Test component instantiation
            components = [
                AmountInputComponent(),
                AddressSelectorComponent(),
                BankSelectorComponent(),
                ConfirmationComponent(),
                StatusDisplayComponent()
            ]
            
            success = all(comp is not None for comp in components)
            self._add_test_result("Component System", success, f"All {len(components)} components loaded")
        except Exception as e:
            self._add_test_result("Component System", False, f"Component loading failed: {e}")
    
    async def _test_ngn_cashout_flow(self) -> None:
        """Test NGN cashout scene flow (simulated)"""
        try:
            # Get test user
            test_user_id = await self._get_test_user_id()
            if not test_user_id:
                self._add_test_result("NGN Cashout Flow", False, "No test user available")
                return
            
            scene_engine = await get_scene_engine()
            
            # Start NGN cashout scene
            scene_started = await scene_engine.start_scene(
                'ngn_cashout', 
                test_user_id,
                {'test_mode': True}
            )
            
            if scene_started:
                # Get scene status
                status = await scene_engine.get_scene_status(test_user_id)
                success = status is not None and status['scene_id'] == 'ngn_cashout'
                message = f"NGN cashout scene status: {status['status'] if status else 'None'}"
                
                # Cancel the test scene
                await scene_engine.cancel_scene(test_user_id)
            else:
                success = False
                message = "Failed to start NGN cashout scene"
            
            self._add_test_result("NGN Cashout Flow", success, message)
        except Exception as e:
            self._add_test_result("NGN Cashout Flow", False, f"NGN cashout test failed: {e}")
    
    async def _test_crypto_cashout_flow(self) -> None:
        """Test crypto cashout scene flow (simulated)"""
        try:
            # Get test user
            test_user_id = await self._get_test_user_id()
            if not test_user_id:
                self._add_test_result("Crypto Cashout Flow", False, "No test user available")
                return
            
            scene_engine = await get_scene_engine()
            
            # Start crypto cashout scene
            scene_started = await scene_engine.start_scene(
                'crypto_cashout', 
                test_user_id,
                {'test_mode': True}
            )
            
            if scene_started:
                # Get scene status
                status = await scene_engine.get_scene_status(test_user_id)
                success = status is not None and status['scene_id'] == 'crypto_cashout'
                message = f"Crypto cashout scene status: {status['status'] if status else 'None'}"
                
                # Cancel the test scene
                await scene_engine.cancel_scene(test_user_id)
            else:
                success = False
                message = "Failed to start crypto cashout scene"
            
            self._add_test_result("Crypto Cashout Flow", success, message)
        except Exception as e:
            self._add_test_result("Crypto Cashout Flow", False, f"Crypto cashout test failed: {e}")
    
    async def _get_test_user_id(self) -> int:
        """Get a test user ID for testing"""
        try:
            session = SessionLocal()
            try:
                # Find first user in database for testing
                user = session.query(User).first()
                return user.id if user else 99999999  # Use fake ID for testing
            finally:
                session.close()
        except Exception:
            return 99999999  # Use fake ID if database fails
    
    def _add_test_result(self, test_name: str, success: bool, message: str) -> None:
        """Add a test result"""
        result = {
            'test_name': test_name,
            'success': success,
            'message': message,
            'timestamp': str(asyncio.get_event_loop().time())
        }
        self.test_results.append(result)
        
        status = "✅ PASS" if success else "❌ FAIL"
        logger.info(f"{status} {test_name}: {message}")
    
    def _generate_test_report(self) -> Dict[str, Any]:
        """Generate test report"""
        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results if result['success'])
        failed_tests = total_tests - passed_tests
        
        success_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0
        
        return {
            'success': failed_tests == 0,
            'total_tests': total_tests,
            'passed_tests': passed_tests,
            'failed_tests': failed_tests,
            'success_rate': f"{success_rate:.1f}%",
            'test_results': self.test_results,
            'summary': f"Scene Engine Integration: {passed_tests}/{total_tests} tests passed ({success_rate:.1f}%)"
        }

# Global test runner
async def run_scene_integration_tests() -> Dict[str, Any]:
    """Run Scene Engine integration tests"""
    tester = SceneIntegrationTester()
    return await tester.run_all_tests()

# Quick test function for manual verification
async def quick_scene_test() -> bool:
    """Quick test to verify Scene Engine is working"""
    try:
        scene_engine = await get_scene_engine()
        await get_scene_ute_adapter()
        
        # Check basic functionality
        test_user_id = 99999999
        scene_started = await scene_engine.start_scene('ngn_cashout', test_user_id, {'test': True})
        
        if scene_started:
            await scene_engine.cancel_scene(test_user_id)
            logger.info("✅ Scene Engine quick test PASSED")
            return True
        else:
            logger.error("❌ Scene Engine quick test FAILED - could not start scene")
            return False
    
    except Exception as e:
        logger.error(f"❌ Scene Engine quick test FAILED: {e}")
        return False