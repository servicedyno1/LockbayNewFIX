"""
Comprehensive test suite for audit logging fixes
Tests webhook simulation with various data types and email validation improvements
"""

import sys
import os
import json
import asyncio
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging for test visibility
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_email_validation_improvements():
    """Test improved email validation to distinguish between emails and 6-digit codes"""
    print("🧪 Testing email validation improvements...")
    
    try:
        from utils.helpers import validate_email
        
        # Test cases for email validation
        test_cases = [
            # Valid emails should pass
            ("user@example.com", True, "Valid email should pass"),
            ("test.email+tag@domain.co.uk", True, "Complex email should pass"),
            
            # 6-digit codes should fail (not emails)
            ("123456", False, "6-digit code should fail email validation"),
            ("000000", False, "All zeros code should fail email validation"),
            ("999999", False, "All nines code should fail email validation"),
            
            # Invalid emails should fail
            ("invalid.email", False, "Email without @ should fail"),
            ("user@", False, "Email without domain should fail"),
            ("@domain.com", False, "Email without user should fail"),
            ("", False, "Empty string should fail"),
            
            # Edge cases
            ("123@456", False, "Numeric only should fail without TLD"),
            ("test@123.45", False, "Numeric domain without proper TLD should fail"),
            ("123456@domain.com", True, "Numbers in email user part should be valid"),
        ]
        
        all_passed = True
        for test_input, expected, description in test_cases:
            try:
                result = validate_email(test_input)
                if result == expected:
                    print(f"  ✅ {description}: '{test_input}' -> {result}")
                else:
                    print(f"  ❌ {description}: '{test_input}' -> {result} (expected {expected})")
                    all_passed = False
            except Exception as e:
                print(f"  ❌ {description}: '{test_input}' -> ERROR: {e}")
                all_passed = False
        
        if all_passed:
            print("✅ Email validation improvements test PASSED")
            return True
        else:
            print("❌ Email validation improvements test FAILED")
            return False
            
    except ImportError as e:
        print(f"❌ Import error in email validation test: {e}")
        return False
    except Exception as e:
        print(f"❌ Error in email validation test: {e}")
        return False

def test_audit_logger_metadata_handling():
    """Test audit logger handles different metadata types including floats"""
    print("🧪 Testing audit logger metadata type handling...")
    
    try:
        from utils.comprehensive_audit_logger import (
            ComprehensiveAuditLogger,
            AuditEventType,
            AuditLevel,
            PayloadMetadata,
            TraceContext
        )
        
        logger_instance = ComprehensiveAuditLogger("test_metadata")
        trace_id = TraceContext.generate_trace_id()
        TraceContext.set_trace_id(trace_id)
        
        # Test various payload metadata types that previously caused errors
        test_payloads = [
            # String metadata
            {"message": "test string", "type": "string"},
            
            # Float metadata (this was causing the original error)
            {"processing_time": 123.456, "latency": 45.67, "type": "float"},
            
            # Integer metadata
            {"count": 42, "user_id": 12345, "type": "integer"},
            
            # Boolean metadata
            {"success": True, "has_attachments": False, "type": "boolean"},
            
            # Mixed type metadata
            {
                "message_length": 150,
                "processing_time": 45.78,
                "success": True,
                "command": "/start",
                "has_email": False,
                "type": "mixed"
            },
            
            # Edge cases
            {"empty_string": "", "zero": 0, "false": False, "none": None, "type": "edge_cases"},
            
            # Complex nested structure
            {
                "user_data": {"id": 123, "active": True},
                "metrics": {"latency": 12.34, "memory": 56.78},
                "type": "nested"
            }
        ]
        
        all_passed = True
        for i, payload in enumerate(test_payloads):
            try:
                print(f"  🔧 Testing payload {i+1}: {payload['type']}")
                
                # Test direct audit logging
                logger_instance.audit(
                    event_type=AuditEventType.SYSTEM,
                    action=f"test_metadata_type_{payload['type']}",
                    result="success",
                    payload=payload,
                    level=AuditLevel.INFO
                )
                
                print(f"    ✅ Direct audit logging succeeded for {payload['type']}")
                
                # Test PayloadMetadata creation with various data types
                if payload['type'] == "mixed":
                    metadata = PayloadMetadata()
                    metadata.message_length = payload.get('message_length')
                    metadata.has_email = payload.get('has_email')
                    metadata.command = payload.get('command')
                    
                    # Test metadata serialization
                    metadata_dict = metadata.to_dict()
                    json.dumps(metadata_dict)  # Ensure JSON serializable
                    print(f"    ✅ PayloadMetadata serialization succeeded for {payload['type']}")
                
            except Exception as e:
                print(f"    ❌ Failed for {payload['type']}: {e}")
                all_passed = False
        
        if all_passed:
            print("✅ Audit logger metadata handling test PASSED")
            return True
        else:
            print("❌ Audit logger metadata handling test FAILED")
            return False
            
    except ImportError as e:
        print(f"❌ Import error in metadata handling test: {e}")
        return False
    except Exception as e:
        print(f"❌ Error in metadata handling test: {e}")
        return False

async def test_webhook_simulation():
    """Simulate webhook requests with various data types to test audit logging"""
    print("🧪 Testing webhook simulation with audit logging...")
    
    try:
        from utils.webhook_audit_logger import WebhookAuditLogger
        from utils.comprehensive_audit_logger import RelatedIDs, TraceContext
        from fastapi import Request
        from unittest.mock import Mock
        
        webhook_logger = WebhookAuditLogger()
        
        # Create mock request objects with different data types
        mock_requests = [
            {
                "name": "payment_webhook_with_floats",
                "webhook_type": "payment",
                "client_ip": "192.168.1.1",
                "user_agent": "PaymentGateway/1.0",
                "content_type": "application/json",
                "content_length": "150",
                "processing_time": 45.67  # Float value that caused original issues
            },
            {
                "name": "telegram_webhook_with_mixed_types", 
                "webhook_type": "telegram",
                "client_ip": "127.0.0.1",
                "user_agent": "TelegramBot/1.0",
                "content_type": "application/json", 
                "content_length": "200",
                "processing_time": 123.456  # Another float
            },
            {
                "name": "twilio_webhook_with_integers",
                "webhook_type": "twilio",
                "client_ip": "10.0.0.1", 
                "user_agent": "Twilio/1.0",
                "content_type": "application/json",
                "content_length": "100",
                "processing_time": 78  # Integer
            }
        ]
        
        all_passed = True
        
        for mock_data in mock_requests:
            try:
                print(f"  🔧 Testing webhook: {mock_data['name']}")
                
                # Create mock request
                mock_request = Mock(spec=Request)
                mock_request.method = "POST"
                mock_request.url.path = f"/webhook/{mock_data['webhook_type']}"
                mock_request.query_params = {}
                mock_request.headers = {
                    "content-type": mock_data["content_type"],
                    "content-length": mock_data["content_length"], 
                    "user-agent": mock_data["user_agent"]
                }
                mock_request.client = Mock()
                mock_request.client.host = mock_data["client_ip"]
                
                # Create related IDs with different types
                related_ids = RelatedIDs()
                related_ids.transaction_id = f"tx_{mock_data['name']}"
                if mock_data['webhook_type'] == 'payment':
                    related_ids.escrow_id = "ESC123456789"
                
                # Test webhook start logging
                trace_id = await webhook_logger.log_webhook_start(
                    webhook_type=mock_data["webhook_type"],
                    request=mock_request,
                    related_ids=related_ids
                )
                
                print(f"    ✅ Webhook start logged with trace_id: {trace_id[:8]}...")
                
                # Test webhook end logging with float processing time
                await webhook_logger.log_webhook_end(
                    webhook_type=mock_data["webhook_type"],
                    trace_id=trace_id,
                    success=True,
                    response_code=200,
                    processing_time_ms=mock_data["processing_time"],  # This includes floats
                    result_data={"processed": True, "user_id": 12345},
                    related_ids=related_ids
                )
                
                print("    ✅ Webhook end logged successfully")
                
                # Test error case with float processing time
                await webhook_logger.log_webhook_end(
                    webhook_type=mock_data["webhook_type"],
                    trace_id=TraceContext.generate_trace_id(),
                    success=False,
                    response_code=500, 
                    processing_time_ms=67.89,  # Float in error case
                    error_details="Test error with float processing time",
                    related_ids=related_ids
                )
                
                print("    ✅ Error case logged successfully")
                
            except Exception as e:
                print(f"    ❌ Failed for {mock_data['name']}: {e}")
                all_passed = False
        
        if all_passed:
            print("✅ Webhook simulation test PASSED")
            return True
        else:
            print("❌ Webhook simulation test FAILED") 
            return False
            
    except ImportError as e:
        print(f"❌ Import error in webhook simulation test: {e}")
        return False
    except Exception as e:
        print(f"❌ Error in webhook simulation test: {e}")
        return False

def test_json_serialization_edge_cases():
    """Test JSON serialization with various data types including Decimal"""
    print("🧪 Testing JSON serialization edge cases...")
    
    try:
        from utils.comprehensive_audit_logger import AuditRecord
        from decimal import Decimal
        import json
        
        # Test data with various types that might cause serialization issues
        test_record = AuditRecord(
            timestamp=datetime.now().isoformat(),
            environment="test",
            level="INFO",
            event_type="test",
            user_id=12345,
            is_admin=False,
            chat_id=67890,
            message_id=None,
            session_id="sess123",
            conversation_id=None,
            trace_id="trace123",
            related_ids={"escrow_id": "ESC123", "amount": Decimal("123.45")},
            action="test_serialization",
            result="success",
            latency_ms=45.67,  # Float
            payload_metadata={
                "message_length": 150,  # Int
                "processing_time": 67.89,  # Float  
                "has_email": True,  # Bool
                "command": "/start",  # String
                "amount": Decimal("99.99")  # Decimal (problematic type)
            }
        )
        
        # Test JSON conversion
        json_str = test_record.to_json()
        print("    ✅ AuditRecord JSON conversion succeeded")
        
        # Verify it's valid JSON
        json.loads(json_str)
        print("    ✅ Generated JSON is valid and parseable")
        
        # Test with edge case metadata types
        edge_case_metadata = {
            "float_value": 123.456,
            "decimal_value": Decimal("678.90"),
            "int_value": 42,
            "bool_value": True,
            "none_value": None,
            "empty_string": "",
            "zero": 0,
            "false_bool": False
        }
        
        test_record.payload_metadata = edge_case_metadata
        json_str = test_record.to_json()
        json.loads(json_str)
        print("    ✅ Edge case metadata serialization succeeded")
        
        print("✅ JSON serialization edge cases test PASSED")
        return True
        
    except Exception as e:
        print(f"❌ JSON serialization test failed: {e}")
        return False

async def run_comprehensive_tests():
    """Run all comprehensive audit logging tests"""
    print("🚀 Starting comprehensive audit logging fix tests...")
    print("=" * 60)
    
    # Track test results
    test_results = []
    
    # Test 1: Email validation improvements
    result1 = test_email_validation_improvements()
    test_results.append(("Email Validation", result1))
    
    print("-" * 40)
    
    # Test 2: Audit logger metadata handling
    result2 = test_audit_logger_metadata_handling()
    test_results.append(("Metadata Handling", result2))
    
    print("-" * 40)
    
    # Test 3: Webhook simulation
    result3 = await test_webhook_simulation()
    test_results.append(("Webhook Simulation", result3))
    
    print("-" * 40)
    
    # Test 4: JSON serialization edge cases
    result4 = test_json_serialization_edge_cases()
    test_results.append(("JSON Serialization", result4))
    
    print("=" * 60)
    print("📊 TEST RESULTS SUMMARY:")
    
    passed_count = 0
    for test_name, result in test_results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {test_name}: {status}")
        if result:
            passed_count += 1
    
    total_tests = len(test_results)
    print(f"\nOverall: {passed_count}/{total_tests} tests passed")
    
    if passed_count == total_tests:
        print("🎉 ALL TESTS PASSED - Audit logging fixes are working correctly!")
        return True
    else:
        print("⚠️  SOME TESTS FAILED - Review failures above")
        return False

if __name__ == "__main__":
    # Run the comprehensive test suite
    try:
        result = asyncio.run(run_comprehensive_tests())
        sys.exit(0 if result else 1)
    except Exception as e:
        print(f"💥 Test suite failed with error: {e}")
        sys.exit(1)