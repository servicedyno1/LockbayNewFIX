#!/usr/bin/env python3
"""
Comprehensive test script for user email notifications.
This script triggers actual email delivery to verify user notification systems.
"""

import asyncio
import sys
from decimal import Decimal
from datetime import datetime, timezone

from services.post_completion_notification_service import PostCompletionNotificationService
from services.user_cancellation_notifications import UserCancellationNotificationService
from services.notification_service import NotificationService
from services.rating_reminder_service import RatingReminderService


async def test_all_user_notifications():
    """Test all user notification methods with realistic data."""
    
    print("=" * 80)
    print("🧪 TESTING USER EMAIL NOTIFICATIONS")
    print("=" * 80)
    print()
    
    results = []
    
    # ========================================================================
    # POST-COMPLETION NOTIFICATIONS (3 completion types)
    # ========================================================================
    
    print("✅ TESTING POST-COMPLETION NOTIFICATIONS (3 types)")
    print("-" * 80)
    
    post_completion_service = PostCompletionNotificationService()
    
    # 1. Released (buyer released funds to seller)
    print("\n1️⃣  Testing: Escrow Completion - Released")
    result = await post_completion_service.notify_escrow_completion(
        escrow_id="TEST-ESC-RELEASED-001",
        completion_type='released',
        amount=1234.56,
        buyer_id=1,
        seller_id=2,
        buyer_email='test.buyer@example.com',
        seller_email='test.seller@example.com'
    )
    success = any(result.values())
    results.append(("Completion - Released", success))
    print(f"   Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
    print(f"   Details: {result}")
    
    # 2. Refunded (escrow refunded to buyer)
    print("\n2️⃣  Testing: Escrow Completion - Refunded")
    result = await post_completion_service.notify_escrow_completion(
        escrow_id="TEST-ESC-REFUNDED-001",
        completion_type='refunded',
        amount=850.00,
        buyer_id=1,
        seller_id=2,
        buyer_email='test.buyer@example.com',
        seller_email='test.seller@example.com'
    )
    success = any(result.values())
    results.append(("Completion - Refunded", success))
    print(f"   Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
    print(f"   Details: {result}")
    
    # 3. Dispute Resolved (buyer wins)
    print("\n3️⃣  Testing: Escrow Completion - Dispute Resolved (Buyer Wins)")
    result = await post_completion_service.notify_escrow_completion(
        escrow_id="TEST-ESC-DISPUTE-001",
        completion_type='dispute_resolved',
        amount=2500.00,
        buyer_id=1,
        seller_id=2,
        buyer_email='test.buyer@example.com',
        seller_email='test.seller@example.com',
        dispute_winner_id=1,  # Buyer wins
        dispute_loser_id=2,   # Seller loses
        resolution_type='buyer_wins_full_refund'
    )
    success = any(result.values())
    results.append(("Completion - Dispute (Buyer Wins)", success))
    print(f"   Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
    print(f"   Details: {result}")
    
    # 4. Dispute Resolved (custom split)
    print("\n4️⃣  Testing: Escrow Completion - Dispute Resolved (Custom Split 60/40)")
    result = await post_completion_service.notify_escrow_completion(
        escrow_id="TEST-ESC-DISPUTE-SPLIT-001",
        completion_type='dispute_resolved',
        amount=1000.00,
        buyer_id=1,
        seller_id=2,
        buyer_email='test.buyer@example.com',
        seller_email='test.seller@example.com',
        dispute_winner_id=None,
        dispute_loser_id=None,
        resolution_type='custom_split_60_40'  # Buyer 60%, Seller 40%
    )
    success = any(result.values())
    results.append(("Completion - Dispute (Custom Split)", success))
    print(f"   Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
    print(f"   Details: {result}")
    
    # 5. NGN Currency Test
    print("\n5️⃣  Testing: Escrow Completion - NGN Currency")
    result = await post_completion_service.notify_escrow_completion(
        escrow_id="TEST-ESC-NGN-001",
        completion_type='released',
        amount=250000.00,  # ₦250,000.00
        buyer_id=1,
        seller_id=2,
        buyer_email='test.buyer.ngn@example.com',
        seller_email='test.seller.ngn@example.com'
    )
    success = any(result.values())
    results.append(("Completion - NGN Currency", success))
    print(f"   Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
    print(f"   Details: {result}")
    
    # ========================================================================
    # CANCELLATION NOTIFICATIONS
    # ========================================================================
    
    print("\n\n❌ TESTING CANCELLATION NOTIFICATIONS")
    print("-" * 80)
    
    cancellation_service = UserCancellationNotificationService()
    
    # 6. USD Cancellation
    print("\n6️⃣  Testing: Escrow Cancelled - USD")
    escrow_data = {
        'escrow_id': 'TEST-CANCEL-USD-001',
        'amount': 750.00,
        'currency': 'USD',
        'seller_info': '@test_seller',
        'cancellation_reason': 'Buyer requested cancellation',
        'cancelled_at': datetime.now(timezone.utc)
    }
    result = await cancellation_service.notify_user_escrow_cancelled(
        user_email='test.cancelled@example.com',
        escrow_data=escrow_data
    )
    results.append(("Cancellation - USD", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 7. NGN Cancellation
    print("\n7️⃣  Testing: Escrow Cancelled - NGN")
    escrow_data = {
        'escrow_id': 'TEST-CANCEL-NGN-001',
        'amount': 150000.00,  # ₦150,000.00
        'currency': 'NGN',
        'seller_info': 'Test Seller (@seller_user)',
        'cancellation_reason': 'Payment timeout',
        'cancelled_at': datetime.now(timezone.utc)
    }
    result = await cancellation_service.notify_user_escrow_cancelled(
        user_email='test.cancelled.ngn@example.com',
        escrow_data=escrow_data
    )
    results.append(("Cancellation - NGN", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 8. BTC Cancellation
    print("\n8️⃣  Testing: Escrow Cancelled - BTC")
    escrow_data = {
        'escrow_id': 'TEST-CANCEL-BTC-001',
        'amount': 0.05,  # 0.05 BTC
        'currency': 'BTC',
        'seller_info': 'crypto.seller@example.com',
        'cancellation_reason': 'Seller unavailable',
        'cancelled_at': datetime.now(timezone.utc)
    }
    result = await cancellation_service.notify_user_escrow_cancelled(
        user_email='test.cancelled.btc@example.com',
        escrow_data=escrow_data
    )
    results.append(("Cancellation - BTC", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # SELLER INVITATION NOTIFICATIONS
    # ========================================================================
    
    print("\n\n💌 TESTING SELLER INVITATION NOTIFICATIONS")
    print("-" * 80)
    
    notification_service = NotificationService()
    
    # 9. Email Invitation - USD
    print("\n9️⃣  Testing: Seller Invitation - Email (USD)")
    result = await notification_service.send_seller_invitation(
        escrow_id='TEST-INVITE-EMAIL-USD-001',
        seller_identifier='seller.invite@example.com',
        seller_type='email',
        amount=Decimal("500.00")
    )
    results.append(("Seller Invitation - Email USD", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 10. Email Invitation - Large Amount
    print("\n1️⃣0️⃣  Testing: Seller Invitation - Email (Large Amount)")
    result = await notification_service.send_seller_invitation(
        escrow_id='TEST-INVITE-EMAIL-LARGE-001',
        seller_identifier='seller.large@example.com',
        seller_type='email',
        amount=Decimal("15000.00")
    )
    results.append(("Seller Invitation - Large Amount", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 11. Email Invitation - Small Amount
    print("\n1️⃣1️⃣  Testing: Seller Invitation - Email (Small Amount)")
    result = await notification_service.send_seller_invitation(
        escrow_id='TEST-INVITE-EMAIL-SMALL-001',
        seller_identifier='seller.small@example.com',
        seller_type='email',
        amount=Decimal("25.99")
    )
    results.append(("Seller Invitation - Small Amount", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # CASHOUT DECLINE NOTIFICATIONS
    # ========================================================================
    
    print("\n\n💸 TESTING CASHOUT DECLINE NOTIFICATIONS")
    print("-" * 80)
    
    # 12. USD Cashout Declined
    print("\n1️⃣2️⃣  Testing: Cashout Decline - USD")
    result = await NotificationService.send_cashout_decline_notification(
        user_email='test.cashout.usd@example.com',
        user_name='John Doe',
        cashout_id='CO-USD-001',
        amount='250.00',
        currency='USD',
        reason='Invalid bank account details provided'
    )
    results.append(("Cashout Decline - USD", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 13. NGN Cashout Declined
    print("\n1️⃣3️⃣  Testing: Cashout Decline - NGN")
    result = await NotificationService.send_cashout_decline_notification(
        user_email='test.cashout.ngn@example.com',
        user_name='Ada Obi',
        cashout_id='CO-NGN-001',
        amount='100000.00',
        currency='NGN',
        reason='Account verification required'
    )
    results.append(("Cashout Decline - NGN", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 14. BTC Cashout Declined
    print("\n1️⃣4️⃣  Testing: Cashout Decline - BTC")
    result = await NotificationService.send_cashout_decline_notification(
        user_email='test.cashout.btc@example.com',
        user_name='Crypto Trader',
        cashout_id='CO-BTC-001',
        amount='0.12345678',
        currency='BTC',
        reason='Invalid wallet address format'
    )
    results.append(("Cashout Decline - BTC", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # 15. ETH Cashout Declined
    print("\n1️⃣5️⃣  Testing: Cashout Decline - ETH")
    result = await NotificationService.send_cashout_decline_notification(
        user_email='test.cashout.eth@example.com',
        user_name='Ethereum User',
        cashout_id='CO-ETH-001',
        amount='2.5',
        currency='ETH',
        reason='Minimum withdrawal amount not met'
    )
    results.append(("Cashout Decline - ETH", result))
    print(f"   Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
    
    # ========================================================================
    # RATING REMINDER NOTIFICATIONS (Database-dependent)
    # ========================================================================
    
    print("\n\n⭐ TESTING RATING REMINDER NOTIFICATIONS")
    print("-" * 80)
    
    # 16. Rating Reminders (queries database for completed escrows)
    print("\n1️⃣6️⃣  Testing: Rating Reminder Processing")
    print("   Note: This queries the database for completed escrows")
    try:
        result = await RatingReminderService.process_rating_reminders()
        success = 'errors' in result and len(result['errors']) == 0
        results.append(("Rating Reminders", success))
        print(f"   Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        print(f"   Details: {result}")
    except Exception as e:
        results.append(("Rating Reminders", False))
        print("   Result: ❌ FAILED")
        print(f"   Error: {e}")
    
    # ========================================================================
    # FINAL SUMMARY
    # ========================================================================
    
    print("\n")
    print("=" * 80)
    print("📊 TEST SUMMARY")
    print("=" * 80)
    print()
    
    total_tests = len(results)
    passed_tests = sum(1 for _, success in results if success)
    failed_tests = total_tests - passed_tests
    
    print(f"Total Tests: {total_tests}")
    print(f"✅ Passed: {passed_tests}")
    print(f"❌ Failed: {failed_tests}")
    print(f"Success Rate: {(passed_tests / total_tests * 100):.1f}%")
    print()
    
    print("Detailed Results:")
    print("-" * 80)
    for i, (test_name, success) in enumerate(results, 1):
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{i:2d}. {status} - {test_name}")
    
    print()
    print("=" * 80)
    
    if failed_tests > 0:
        print("⚠️  Some tests failed. Please check email service configuration.")
        return 1
    else:
        print("✅ All tests passed! User notification system is working correctly.")
        return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(test_all_user_notifications())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
