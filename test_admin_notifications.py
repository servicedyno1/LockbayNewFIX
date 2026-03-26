#!/usr/bin/env python3
"""
Comprehensive test script for all 16 admin notifications.
This script triggers actual email delivery to verify the notification system is working.
"""

import asyncio
import sys
from decimal import Decimal
from datetime import datetime, timezone

from services.admin_trade_notifications import AdminTradeNotificationService


async def test_all_admin_notifications():
    """Test all 16 admin notification events with realistic data."""
    
    print("=" * 80)
    print("🧪 TESTING ALL 16 ADMIN NOTIFICATIONS")
    print("=" * 80)
    print()
    
    # Initialize notification service (takes no parameters)
    notification_service = AdminTradeNotificationService()
    
    results = []
    
    # ========================================================================
    # ESCROW LIFECYCLE NOTIFICATIONS (6 events)
    # ========================================================================
    
    print("📦 TESTING ESCROW LIFECYCLE NOTIFICATIONS (6 events)")
    print("-" * 80)
    
    # 1. Escrow Created
    print("\n1️⃣  Testing: Escrow Created")
    result = await notification_service.notify_escrow_created({
        'escrow_id': "TEST-ESC-001",
        'amount': Decimal("850.00"),
        'currency': 'USD',
        'buyer_info': 'Test Buyer (@test_buyer)',
        'seller_info': '@test_seller',
        'created_at': datetime.now(timezone.utc)
    })
    results.append(("Escrow Created", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 2. Escrow Completed
    print("\n2️⃣  Testing: Escrow Completed")
    result = await notification_service.notify_escrow_completed({
        'escrow_id': "TEST-ESC-002",
        'amount': Decimal("1500.00"),
        'currency': 'USD',
        'buyer_info': 'Test Buyer (@test_buyer)',
        'seller_info': '@test_seller',
        'completed_at': datetime.now(timezone.utc)
    })
    results.append(("Escrow Completed", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 3. Escrow Cancelled
    print("\n3️⃣  Testing: Escrow Cancelled")
    result = await notification_service.notify_escrow_cancelled({
        'escrow_id': "TEST-ESC-003",
        'amount': Decimal("750.00"),
        'currency': 'USD',
        'buyer_info': 'Test Buyer (@test_buyer)',
        'seller_info': '@test_seller',
        'cancelled_at': datetime.now(timezone.utc),
        'reason': 'User requested cancellation'
    })
    results.append(("Escrow Cancelled", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 4. Dispute Resolved
    print("\n4️⃣  Testing: Dispute Resolved")
    result = await notification_service.notify_dispute_resolved({
        'escrow_id': "TEST-ESC-004",
        'admin_name': 'Admin Test',
        'resolution': 'Refund to buyer - item not as described',
        'resolved_at': datetime.now(timezone.utc)
    })
    results.append(("Dispute Resolved", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 5. Escrow Expired
    print("\n5️⃣  Testing: Escrow Expired")
    result = await notification_service.notify_escrow_expired({
        'escrow_id': "TEST-ESC-005",
        'amount': Decimal("250000.00"),
        'currency': 'NGN',
        'buyer_info': 'Test Buyer (@test_buyer)',
        'seller_info': '@test_seller',
        'expired_at': datetime.now(timezone.utc),
        'expiry_reason': 'Timeout',
        'refund_status': 'Pending'
    })
    results.append(("Escrow Expired", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 6. Exchange Created
    print("\n6️⃣  Testing: Exchange Created")
    result = await notification_service.notify_exchange_created({
        'exchange_id': "TEST-EXC-001",
        'user_id': 12345678,
        'username': 'test_exchanger',
        'first_name': 'Test',
        'last_name': 'User',
        'from_currency': 'USD',
        'to_currency': 'BTC',
        'from_amount': Decimal("1000.00"),
        'to_amount': Decimal("0.0234"),
        'created_at': datetime.now(timezone.utc)
    })
    results.append(("Exchange Created", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # EXCHANGE NOTIFICATIONS (2 events)
    # ========================================================================
    
    print("\n\n💱 TESTING EXCHANGE NOTIFICATIONS (2 events)")
    print("-" * 80)
    
    # 7. Exchange Completed
    print("\n7️⃣  Testing: Exchange Completed")
    result = await notification_service.notify_exchange_completed({
        'exchange_id': "TEST-EXC-002",
        'user_id': 12345678,
        'username': 'test_exchanger',
        'first_name': 'Test',
        'last_name': 'User',
        'from_currency': 'USD',
        'to_currency': 'ETH',
        'from_amount': Decimal("500.00"),
        'to_amount': Decimal("0.15"),
        'completed_at': datetime.now(timezone.utc)
    })
    results.append(("Exchange Completed", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 8. Exchange Cancelled
    print("\n8️⃣  Testing: Exchange Cancelled")
    result = await notification_service.notify_exchange_cancelled({
        'exchange_id': "TEST-EXC-003",
        'user_id': 12345678,
        'username': 'test_exchanger',
        'first_name': 'Test',
        'last_name': 'User',
        'from_currency': 'BTC',
        'to_currency': 'USD',
        'from_amount': Decimal("0.01"),
        'to_amount': Decimal("450.00"),
        'cancelled_at': datetime.now(timezone.utc),
        'reason': 'User cancelled'
    })
    results.append(("Exchange Cancelled", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # USER ONBOARDING NOTIFICATIONS (2 events)
    # ========================================================================
    
    print("\n\n👤 TESTING USER ONBOARDING NOTIFICATIONS (2 events)")
    print("-" * 80)
    
    # 9. Onboarding Started
    print("\n9️⃣  Testing: Onboarding Started")
    result = await notification_service.notify_user_onboarding_started({
        'user_id': 1,
        'telegram_id': 11111111,
        'username': 'test_new_user',
        'first_name': 'New',
        'last_name': 'User',
        'started_at': datetime.now(timezone.utc)
    })
    results.append(("Onboarding Started", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 10. Onboarding Completed
    print("\n1️⃣0️⃣  Testing: Onboarding Completed")
    result = await notification_service.notify_user_onboarding_completed({
        'user_id': 2,
        'telegram_id': 22222222,
        'username': 'test_completed_user',
        'first_name': 'John',
        'last_name': 'Doe',
        'email': 'john.doe@example.com',
        'phone': '+234-801-234-5678',
        'completed_at': datetime.now(timezone.utc)
    })
    results.append(("Onboarding Completed", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # WALLET ACTIVITY NOTIFICATIONS (4 events)
    # ========================================================================
    
    print("\n\n💰 TESTING WALLET ACTIVITY NOTIFICATIONS (4 events)")
    print("-" * 80)
    
    # 11. Trade Creation Initiated
    print("\n1️⃣1️⃣  Testing: Trade Creation Initiated")
    result = await notification_service.notify_trade_creation_initiated({
        'user_id': 3,
        'telegram_id': 33333333,
        'username': 'test_trader',
        'first_name': 'Trader',
        'last_name': 'User',
        'initiated_at': datetime.now(timezone.utc)
    })
    results.append(("Trade Creation Initiated", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 12. Add Funds Clicked
    print("\n1️⃣2️⃣  Testing: Add Funds Clicked")
    result = await notification_service.notify_add_funds_clicked({
        'user_id': 4,
        'username': 'test_fund_adder',
        'first_name': 'Fund',
        'last_name': 'Adder',
        'clicked_at': datetime.now(timezone.utc),
        'current_balance': Decimal("50.00")
    })
    results.append(("Add Funds Clicked", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 13. Wallet Address Generated
    print("\n1️⃣3️⃣  Testing: Wallet Address Generated")
    result = await notification_service.notify_wallet_address_generated({
        'user_id': 5,
        'telegram_id': 55555555,
        'username': 'test_wallet_user',
        'first_name': 'Wallet',
        'last_name': 'User',
        'currency': 'ETH',
        'network': 'ERC20',
        'address': '0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb',
        'generated_at': datetime.now(timezone.utc)
    })
    results.append(("Wallet Address Generated", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 14. Wallet Funded
    print("\n1️⃣4️⃣  Testing: Wallet Funded")
    result = await notification_service.notify_wallet_funded({
        'user_id': 6,
        'telegram_id': 66666666,
        'username': 'test_funded_user',
        'first_name': 'Funded',
        'last_name': 'User',
        'amount': Decimal("0.05678912"),
        'currency': 'BTC',
        'funded_at': datetime.now(timezone.utc)
    })
    results.append(("Wallet Funded", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # CASHOUT OPERATION NOTIFICATIONS (2 events)
    # ========================================================================
    
    print("\n\n💸 TESTING CASHOUT OPERATION NOTIFICATIONS (2 events)")
    print("-" * 80)
    
    # 15. Cashout Started
    print("\n1️⃣5️⃣  Testing: Cashout Started (Crypto)")
    result = await notification_service.notify_cashout_started({
        'cashout_id': 'CO-TEST-001',
        'user_id': 7,
        'username': 'test_cashout_user',
        'first_name': 'Cashout',
        'last_name': 'User',
        'amount': 100.50,
        'currency': 'BTC',
        'cashout_type': 'crypto',
        'destination': '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa',
        'started_at': datetime.now(timezone.utc)
    })
    results.append(("Cashout Started (Crypto)", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 16. Cashout Completed
    print("\n1️⃣6️⃣  Testing: Cashout Completed")
    result = await notification_service.notify_cashout_completed({
        'cashout_id': 'CO-TEST-002',
        'user_id': 8,
        'username': 'test_completed_cashout',
        'first_name': 'Completed',
        'last_name': 'Cashout',
        'amount': 75.25,
        'currency': 'USD',
        'cashout_type': 'ngn',
        'destination': 'Bank Account ***1234',
        'completed_at': datetime.now(timezone.utc)
    })
    results.append(("Cashout Completed", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # RESULTS SUMMARY
    # ========================================================================
    
    print("\n\n" + "=" * 80)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 80)
    
    success_count = sum(1 for _, result in results if result)
    total_count = len(results)
    
    print(f"\n✅ Successful: {success_count}/{total_count}")
    print(f"❌ Failed: {total_count - success_count}/{total_count}")
    
    print("\n📋 Detailed Results:")
    print("-" * 80)
    for notification_name, result in results:
        status = "✅ SUCCESS" if result else "❌ FAILED"
        print(f"  {status} - {notification_name}")
    
    print("\n" + "=" * 80)
    
    if success_count == total_count:
        print("🎉 ALL NOTIFICATIONS WORKING PERFECTLY!")
        print("✅ Email delivery confirmed for all 16 admin notifications")
        print("=" * 80)
        return 0
    else:
        print("⚠️  SOME NOTIFICATIONS FAILED!")
        print(f"Please check the logs for details on the {total_count - success_count} failed notification(s)")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    print("\n🚀 Starting Admin Notification Test Suite...")
    print("📧 This will send real emails to the admin email address")
    print()
    
    exit_code = asyncio.run(test_all_admin_notifications())
    sys.exit(exit_code)
