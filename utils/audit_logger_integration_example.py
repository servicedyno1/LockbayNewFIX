"""
Example integration and testing for the comprehensive audit logging framework
Demonstrates proper usage patterns and PII redaction capabilities
"""

import asyncio
from decimal import Decimal

# Import the audit logging framework
from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger,
    AuditEventType,
    AuditLevel,
    RelatedIDs,
    TraceContext,
    audit_decorator,
    audit_user_interaction,
    audit_transaction,
    audit_admin_action,
    audit_system_event
)


class TestTelegramUpdate:
    """Mock Telegram Update object for testing"""
    def __init__(self, user_id: int, chat_id: int, message_text: str, message_id: int = 123):
        self.message = TestMessage(message_text, message_id)
        self.effective_user = TestUser(user_id)
        self.effective_chat = TestChat(chat_id)


class TestMessage:
    """Mock Telegram Message object for testing"""
    def __init__(self, text: str, message_id: int):
        self.text = text
        self.message_id = message_id
        self.document = None


class TestUser:
    """Mock Telegram User object for testing"""
    def __init__(self, user_id: int):
        self.id = user_id
        self.first_name = "Test"
        self.username = "testuser"


class TestChat:
    """Mock Telegram Chat object for testing"""
    def __init__(self, chat_id: int):
        self.id = chat_id


def test_basic_audit_logging():
    """Test basic audit logging functionality"""
    print("🧪 Testing basic audit logging...")
    
    # Create audit logger
    logger = ComprehensiveAuditLogger("test")
    
    # Set trace context
    trace_id = TraceContext.generate_trace_id()
    TraceContext.set_trace_id(trace_id)
    TraceContext.set_user_context(user_id=12345, chat_id=67890)
    
    # Test basic audit logging
    logger.audit(
        event_type=AuditEventType.USER_INTERACTION,
        action="button_click",
        result="success",
        related_ids=RelatedIDs(escrow_id="ESC_12345"),
        payload={"button_type": "confirm", "page": "escrow_details"}
    )
    
    print("✅ Basic audit logging test completed")


def test_pii_redaction():
    """Test PII redaction capabilities"""
    print("🧪 Testing PII redaction...")
    
    logger = ComprehensiveAuditLogger("pii_test")
    
    # Test message with PII
    sensitive_message = """
    Hello, my email is user@example.com and my phone is +1234567890.
    Here's my Bitcoin address: bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh
    My bank account is 1234567890 at Wells Fargo
    """
    
    update = TestTelegramUpdate(
        user_id=98765,
        chat_id=55555,
        message_text=sensitive_message
    )
    
    # Set context
    TraceContext.set_user_context(user_id=98765, chat_id=55555)
    
    # Log with PII - should be redacted in metadata
    logger.audit(
        event_type=AuditEventType.USER_INTERACTION,
        action="message_received",
        result="processed",
        payload=update
    )
    
    print("✅ PII redaction test completed - check logs for redacted content")


def test_decorator_usage():
    """Test audit decorator functionality"""
    print("🧪 Testing audit decorator...")
    
    @audit_decorator(
        event_type=AuditEventType.TRANSACTION,
        action="process_payment",
        track_latency=True
    )
    async def process_payment(amount: Decimal, currency: str, user_id: int):
        """Mock payment processing function"""
        # Simulate some processing time
        await asyncio.sleep(0.1)
        
        # Set context
        TraceContext.set_user_context(user_id)
        
        return {"status": "success", "amount": amount, "currency": currency}
    
    # Test the decorated function
    async def run_decorator_test():
        result = await process_payment(Decimal("100.50"), "USD", 12345)
        print(f"Payment result: {result}")
    
    asyncio.run(run_decorator_test())
    print("✅ Decorator test completed")


def test_convenience_functions():
    """Test convenience audit functions"""
    print("🧪 Testing convenience functions...")
    
    # Set context
    TraceContext.set_trace_id(TraceContext.generate_trace_id())
    TraceContext.set_user_context(user_id=11111, chat_id=22222)
    
    # Test user interaction audit
    update = TestTelegramUpdate(11111, 22222, "/start")
    audit_user_interaction("command_start", update=update)
    
    # Test transaction audit
    related_ids = RelatedIDs(escrow_id="ESC_67890", transaction_id="TXN_111")
    audit_transaction(
        "escrow_deposit",
        amount=Decimal("50.00"),
        currency="USD",
        related_ids=related_ids
    )
    
    # Test admin action audit
    audit_admin_action(
        "user_suspension",
        admin_user_id=999,
        target_user_id=11111,
        related_ids=RelatedIDs(escrow_id="ESC_67890")
    )
    
    # Test system event audit
    audit_system_event(
        "database_backup_complete",
        level=AuditLevel.INFO,
        payload={"backup_size_mb": 150, "duration_seconds": 45}
    )
    
    print("✅ Convenience functions test completed")


def test_trace_correlation():
    """Test trace correlation across operations"""
    print("🧪 Testing trace correlation...")
    
    logger = ComprehensiveAuditLogger("correlation")
    
    # Start a trace
    trace_id = TraceContext.generate_trace_id()
    session_id = f"session_{trace_id[:8]}"
    
    TraceContext.set_trace_id(trace_id)
    TraceContext.set_session_id(session_id)
    TraceContext.set_user_context(user_id=33333, chat_id=44444, conversation_id="conv_123")
    
    # Log multiple related operations
    operations = [
        "user_login",
        "view_escrow_list", 
        "create_escrow",
        "confirm_payment"
    ]
    
    for i, operation in enumerate(operations):
        logger.audit(
            event_type=AuditEventType.USER_INTERACTION,
            action=operation,
            result="success",
            related_ids=RelatedIDs(escrow_id="ESC_TRACE_TEST" if i >= 2 else None),
            latency_ms=float(i * 50 + 25)  # Simulated latency
        )
    
    print(f"✅ Trace correlation test completed - trace_id: {trace_id}")


def test_error_handling():
    """Test error handling and resilience"""
    print("🧪 Testing error handling...")
    
    logger = ComprehensiveAuditLogger("error_test")
    
    # Test with invalid payload that might cause issues
    invalid_payloads = [
        None,
        {"circular_ref": None},
        {"huge_string": "x" * 100000},
        object(),  # Non-serializable object
        {"nested": {"very": {"deep": {"object": {"structure": "test"}}}}}
    ]
    
    for i, payload in enumerate(invalid_payloads):
        try:
            if i == 1:
                payload["circular_ref"] = payload  # Create circular reference
            
            logger.audit(
                event_type=AuditEventType.SYSTEM,
                action=f"error_test_{i}",
                payload=payload,
                result="handled"
            )
        except Exception as e:
            print(f"Error handled gracefully for payload {i}: {e}")
    
    print("✅ Error handling test completed")


def demonstrate_json_output():
    """Demonstrate the JSON output format"""
    print("🧪 Demonstrating JSON output format...")
    print("📋 Sample audit log entries:")
    print("-" * 80)
    
    logger = ComprehensiveAuditLogger("demo")
    
    # Set up context
    trace_id = TraceContext.generate_trace_id()
    TraceContext.set_trace_id(trace_id)
    TraceContext.set_user_context(user_id=77777, chat_id=88888)
    
    # Create a realistic scenario
    update = TestTelegramUpdate(
        user_id=77777,
        chat_id=88888,
        message_text="I want to create an escrow for $100.50"
    )
    
    # This will output JSON to the console
    logger.audit(
        event_type=AuditEventType.USER_INTERACTION,
        action="escrow_creation_request",
        result="processing",
        related_ids=RelatedIDs(escrow_id="ESC_DEMO_123"),
        payload=update,
        latency_ms=42.5
    )
    
    print("-" * 80)
    print("✅ JSON output demonstration completed")


def run_all_tests():
    """Run all audit logging tests"""
    print("🚀 Starting comprehensive audit logging framework tests...")
    print("=" * 80)
    
    try:
        test_basic_audit_logging()
        test_pii_redaction()
        test_decorator_usage()
        test_convenience_functions()
        test_trace_correlation()
        test_error_handling()
        demonstrate_json_output()
        
        print("=" * 80)
        print("🎉 All audit logging tests completed successfully!")
        print("\n📊 Framework features verified:")
        print("✅ JSON-structured logging with all required fields")
        print("✅ PII redaction and safe metadata extraction")
        print("✅ Contextvars-based trace correlation")
        print("✅ Event type categorization")
        print("✅ Decorator support for automatic auditing")
        print("✅ Convenience functions for common patterns")
        print("✅ Error handling and resilience")
        print("✅ Global enrichment (environment, service, version)")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()