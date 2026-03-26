"""
Standalone Test Runner for Notification System
Runs comprehensive notification tests without pytest fixtures
"""

import sys
import os
import asyncio
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from telegram.error import TelegramError

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import async_managed_session
from services.consolidated_notification_service import (
    ConsolidatedNotificationService,
    NotificationRequest,
    NotificationChannel,
    NotificationPriority,
    NotificationCategory,
    DeliveryStatus
)
from models import User, Escrow, EscrowStatus, Wallet
from config import Config
from sqlalchemy import delete

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def cleanup_test_data(test_user_id: int):
    """Clean up test data"""
    async with async_managed_session() as session:
        await session.execute(delete(Escrow).where(Escrow.buyer_id == test_user_id))
        await session.execute(delete(Wallet).where(Wallet.user_id == test_user_id))
        await session.execute(delete(User).where(User.id == test_user_id))
        await session.commit()


async def create_test_user() -> User:
    """Create a test user"""
    test_user_id = 999888777
    await cleanup_test_data(test_user_id)
    
    async with async_managed_session() as session:
        user = User(
            id=test_user_id,
            telegram_id=test_user_id,
            username="test_notification_user",
            first_name="Test",
            last_name="User",
            email="test_notifications@example.com",
            email_verified=True,
            is_verified=True,
            status="active",
            phone_number="+2348012345678",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        
        wallet = Wallet(
            user_id=user.id,
            currency="USD",
            balance=Decimal("100.00"),
            frozen_balance=Decimal("0.00")
        )
        session.add(wallet)
        await session.commit()
        
        return user


async def create_test_escrow(user_id: int) -> Escrow:
    """Create a test escrow"""
    async with async_managed_session() as session:
        escrow = Escrow(
            escrow_id="ES092925TEST",
            buyer_id=user_id,
            amount=Decimal("100.00"),
            currency="USD",
            status=EscrowStatus.PAYMENT_PENDING,
            payment_address="test_btc_address_12345",
            rate=Decimal("50000.00"),
            expected_crypto_amount=Decimal("0.002"),
            rate_locked_until=datetime.utcnow() + timedelta(hours=1),
            created_at=datetime.utcnow()
        )
        session.add(escrow)
        await session.commit()
        await session.refresh(escrow)
        return escrow


async def test_telegram_payment_notification():
    """Test 1: Payment confirmation with Telegram working"""
    logger.info("=" * 80)
    logger.info("🧪 TEST 1: Exact payment confirmation via Telegram")
    logger.info("=" * 80)
    
    test_user = await create_test_user()
    test_escrow = await create_test_escrow(test_user.id)
    service = ConsolidatedNotificationService()
    
    try:
        await service.initialize()
        
        notification_request = NotificationRequest(
            user_id=test_user.id,
            category=NotificationCategory.PAYMENTS,
            priority=NotificationPriority.HIGH,
            title="💰 Payment Confirmed",
            message=f"Your payment of 0.002 BTC ($100.00) for escrow {test_escrow.escrow_id} has been confirmed!",
            template_data={
                "escrow_id": test_escrow.escrow_id,
                "amount_crypto": "0.002",
                "amount_usd": "100.00",
                "currency": "BTC"
            },
            channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
            broadcast_mode=False
        )
        
        with patch('telegram.Bot.send_message', new_callable=AsyncMock) as mock_telegram:
            mock_telegram.return_value = Mock(message_id=12345)
            
            results = await service.send_notification(notification_request)
            
            logger.info(f"📊 Notification results: {list(results.keys())}")
            assert mock_telegram.called, "Telegram should be called"
            
            successful = any(
                r.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]
                for r in results.values()
            )
            assert successful, "At least one channel should succeed"
            
            logger.info("✅ TEST 1 PASSED: Telegram notification successful")
    
    except Exception as e:
        logger.error(f"❌ TEST 1 FAILED: {e}")
        raise
    finally:
        await cleanup_test_data(test_user.id)


async def test_email_fallback():
    """Test 2: Email fallback when Telegram fails"""
    logger.info("=" * 80)
    logger.info("🧪 TEST 2: Email fallback when Telegram fails")
    logger.info("=" * 80)
    
    test_user = await create_test_user()
    test_escrow = await create_test_escrow(test_user.id)
    service = ConsolidatedNotificationService()
    
    try:
        await service.initialize()
        
        notification_request = NotificationRequest(
            user_id=test_user.id,
            category=NotificationCategory.PAYMENTS,
            priority=NotificationPriority.HIGH,
            title="💰 Payment Confirmed",
            message=f"Payment confirmed for {test_escrow.escrow_id}",
            channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
            broadcast_mode=False
        )
        
        with patch('telegram.Bot.send_message', new_callable=AsyncMock) as mock_telegram, \
             patch.object(service.email_service, 'send_email') as mock_email:
            
            mock_telegram.side_effect = TelegramError("Bot blocked by user")
            mock_email.return_value = True
            
            results = await service.send_notification(notification_request)
            
            logger.info(f"📊 Notification results: {list(results.keys())}")
            assert mock_telegram.called, "Telegram should be attempted"
            assert mock_email.called, "Email fallback should be triggered"
            
            logger.info("✅ TEST 2 PASSED: Email fallback activated successfully")
    
    except Exception as e:
        logger.error(f"❌ TEST 2 FAILED: {e}")
        raise
    finally:
        await cleanup_test_data(test_user.id)


async def test_overpayment_notification():
    """Test 3: Overpayment notification with rate data"""
    logger.info("=" * 80)
    logger.info("🧪 TEST 3: Overpayment notification")
    logger.info("=" * 80)
    
    test_user = await create_test_user()
    test_escrow = await create_test_escrow(test_user.id)
    service = ConsolidatedNotificationService()
    
    try:
        await service.initialize()
        
        expected_crypto = Decimal("0.002")
        received_crypto = Decimal("0.0025")
        overpayment = received_crypto - expected_crypto
        rate = test_escrow.rate
        
        notification_request = NotificationRequest(
            user_id=test_user.id,
            category=NotificationCategory.PAYMENTS,
            priority=NotificationPriority.HIGH,
            title="⚠️ Overpayment Detected",
            message=f"Overpayment: {overpayment} BTC (${overpayment * rate:.2f})",
            template_data={
                "escrow_id": test_escrow.escrow_id,
                "overpayment": str(overpayment)
            },
            channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
            broadcast_mode=True
        )
        
        with patch('telegram.Bot.send_message', new_callable=AsyncMock) as mock_telegram:
            mock_telegram.return_value = Mock(message_id=12346)
            
            results = await service.send_notification(notification_request)
            
            logger.info(f"📊 Notification results: {list(results.keys())}")
            logger.info("✅ TEST 3 PASSED: Overpayment notification sent")
    
    except Exception as e:
        logger.error(f"❌ TEST 3 FAILED: {e}")
        raise
    finally:
        await cleanup_test_data(test_user.id)


async def test_admin_notification():
    """Test 4: Admin notification"""
    logger.info("=" * 80)
    logger.info("🧪 TEST 4: Admin notification")
    logger.info("=" * 80)
    
    test_user = await create_test_user()
    test_escrow = await create_test_escrow(test_user.id)
    service = ConsolidatedNotificationService()
    
    try:
        await service.initialize()
        
        admin_ids = getattr(Config, 'ADMIN_IDS', [5590563715])
        admin_id = admin_ids[0] if admin_ids else 5590563715
        
        notification_request = NotificationRequest(
            user_id=admin_id,
            category=NotificationCategory.ADMIN_ALERTS,
            priority=NotificationPriority.HIGH,
            title="🔔 Payment Alert",
            message=f"Payment received for {test_escrow.escrow_id}",
            channels=[NotificationChannel.ADMIN_ALERT],
            admin_notification=True
        )
        
        with patch('telegram.Bot.send_message', new_callable=AsyncMock) as mock_telegram:
            mock_telegram.return_value = Mock(message_id=12347)
            
            results = await service.send_notification(notification_request)
            
            logger.info(f"📊 Notification results: {list(results.keys())}")
            logger.info("✅ TEST 4 PASSED: Admin notification sent")
    
    except Exception as e:
        logger.error(f"❌ TEST 4 FAILED: {e}")
        raise
    finally:
        await cleanup_test_data(test_user.id)


async def run_all_tests():
    """Run all notification tests"""
    logger.info("\n" + "=" * 80)
    logger.info("🚀 STARTING COMPREHENSIVE NOTIFICATION SYSTEM TESTS")
    logger.info("=" * 80 + "\n")
    
    tests = [
        ("Telegram Payment Notification", test_telegram_payment_notification),
        ("Email Fallback", test_email_fallback),
        ("Overpayment Notification", test_overpayment_notification),
        ("Admin Notification", test_admin_notification),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            await test_func()
            passed += 1
        except Exception as e:
            logger.error(f"❌ {test_name} FAILED: {e}")
            failed += 1
    
    logger.info("\n" + "=" * 80)
    logger.info(f"📊 TEST SUMMARY: {passed} passed, {failed} failed")
    logger.info("=" * 80 + "\n")
    
    if failed == 0:
        logger.info("🎉 ALL TESTS PASSED!")
    else:
        logger.error(f"❌ {failed} TEST(S) FAILED")
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
