#!/usr/bin/env python3
"""
Manual Webhook Processor for Stuck Webhooks

PURPOSE: Manually process webhooks that got stuck due to rate cache misses or timeouts
"""

import sys
import os
import asyncio
import argparse
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import async_managed_session
from models import Escrow, WebhookEventLedger, Transaction
from sqlalchemy import select
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def process_stuck_webhook_direct(webhook_id: int):
    """
    Manually process a stuck webhook using direct database operations
    Bypasses the emergency fetch mechanism since rate should be cached by now
    """
    async with async_managed_session() as session:
        # Get webhook event
        stmt = select(WebhookEventLedger).where(WebhookEventLedger.id == webhook_id)
        result = await session.execute(stmt)
        webhook_event = result.scalar_one_or_none()
        
        if not webhook_event:
            logger.error(f"Webhook {webhook_id} not found")
            return {'success': False, 'error': 'Webhook not found'}
        
        logger.info(f"📋 Webhook {webhook_id} Details:")
        logger.info(f"  Reference: {webhook_event.reference_id}")
        logger.info(f"  Status: {webhook_event.status}")
        logger.info(f"  Amount: {webhook_event.amount} {webhook_event.currency}")
        logger.info(f"  TxID: {webhook_event.txid}")
        
        if webhook_event.status == 'completed':
            logger.warning("Webhook already completed successfully")
            return {'success': False, 'error': 'Webhook already completed'}
        
        # Parse the payload
        import json
        webhook_data = json.loads(webhook_event.payload.replace('"""', '"'))
        
        paid_amount = Decimal(str(webhook_data.get('paid_amount')))
        paid_currency = webhook_data.get('paid_currency')
        transaction_id = webhook_data.get('id')
        reference_id = webhook_event.reference_id
        
        # Get escrow
        stmt = select(Escrow).where(Escrow.utid == reference_id)
        result = await session.execute(stmt)
        escrow = result.scalar_one_or_none()
        
        if not escrow:
            logger.error(f"Escrow {reference_id} not found")
            return {'success': False, 'error': 'Escrow not found'}
        
        logger.info(f"✅ Found escrow ID {escrow.id}, status: {escrow.status}")
        
        # Check if already processed
        stmt = select(Transaction).where(
            Transaction.escrow_id == escrow.id,
            Transaction.external_id == transaction_id
        )
        result = await session.execute(stmt)
        existing_tx = result.scalar_one_or_none()
        
        if existing_tx:
            logger.warning(f"Already processed - transaction {existing_tx.transaction_id} exists")
            webhook_event.status = 'completed'
            webhook_event.completed_at = datetime.utcnow()
            await session.commit()
            return {'success': True, 'message': 'Already processed'}
        
        # Get exchange rate from cache
        from services.fastforex_service import fastforex_service, emergency_fetch_rate_with_circuit_breaker
        usd_rate = await fastforex_service.get_crypto_to_usd_rate_webhook_optimized(paid_currency)
        
        if usd_rate is None:
            logger.warning(f"⚠️ Cache miss for {paid_currency}, attempting emergency fetch...")
            usd_rate = await emergency_fetch_rate_with_circuit_breaker(paid_currency)
            
            if usd_rate is None:
                logger.error(f"❌ Rate still not available for {paid_currency}")
                return {'success': False, 'error': f'Rate not available for {paid_currency}'}
        
        logger.info(f"📊 Using cached rate for {paid_currency}: ${usd_rate:.4f}")
        
        usd_amount = paid_amount * Decimal(str(usd_rate))
        logger.info(f"💰 Payment: {paid_amount} {paid_currency} = ${usd_amount:.2f} USD")
        
        # Now use unified payment processor
        from services.unified_payment_processor import unified_processor
        
        logger.info("🔄 Processing payment via unified processor...")
        
        processing_result = await unified_processor.process_escrow_payment(
            escrow=escrow,
            received_amount=paid_amount,
            received_usd=float(usd_amount),
            crypto_currency=paid_currency,
            tx_hash=transaction_id,
            price_usd=float(usd_rate),
            session=session
        )
        
        # ProcessingResult is a dataclass with .success attribute
        if processing_result.success:
            logger.info("✅ Payment processed successfully")
            
            # Update webhook status
            webhook_event.status = 'completed'
            webhook_event.completed_at = datetime.utcnow()
            webhook_event.processing_result = str(processing_result)
            
            await session.commit()
            
            logger.info(f"✅ Webhook {webhook_id} marked as completed")
            
            return {
                'success': True,
                'webhook_id': webhook_id,
                'reference_id': reference_id,
                'result': str(processing_result)
            }
        else:
            logger.error(f"❌ Processing failed: {processing_result}")
            return {'success': False, 'error': processing_result.error_message}


async def list_stuck_webhooks():
    """List all webhooks stuck in processing or failed status"""
    async with async_managed_session() as session:
        stmt = select(WebhookEventLedger).where(
            WebhookEventLedger.status.in_(['processing', 'failed'])
        ).order_by(WebhookEventLedger.created_at.desc())
        
        result = await session.execute(stmt)
        webhooks = result.scalars().all()
        
        if not webhooks:
            print("✅ No stuck webhooks found!")
            return []
        
        print(f"\n⚠️  Found {len(webhooks)} stuck webhook(s):\n")
        stuck_list = []
        for wh in webhooks:
            print(f"ID: {wh.id} | Reference: {wh.reference_id} | Amount: {wh.amount} {wh.currency}")
            print(f"  Created: {wh.created_at}")
            print(f"  TxID: {wh.txid}")
            print()
            stuck_list.append({
                'id': wh.id,
                'reference_id': wh.reference_id,
                'amount': wh.amount,
                'currency': wh.currency,
                'txid': wh.txid,
                'created_at': wh.created_at
            })
        
        return stuck_list


async def main():
    parser = argparse.ArgumentParser(description='Manually process stuck webhooks')
    parser.add_argument('--list', action='store_true', help='List stuck webhooks')
    parser.add_argument('--process', type=int, help='Process webhook by ID')
    parser.add_argument('--process-reference', type=str, help='Process webhook by reference ID (escrow ID)')
    
    args = parser.parse_args()
    
    if args.list:
        await list_stuck_webhooks()
    
    elif args.process:
        result = await process_stuck_webhook_direct(args.process)
        if result['success']:
            print(f"\n✅ Successfully processed webhook {args.process}")
            print(f"  Reference: {result.get('reference_id')}")
            print(f"  Result: {result.get('result')}")
        else:
            print(f"\n❌ Processing failed: {result.get('error')}")
    
    elif args.process_reference:
        # Find webhook by reference ID
        async with async_managed_session() as session:
            stmt = select(WebhookEventLedger).where(
                WebhookEventLedger.reference_id == args.process_reference,
                WebhookEventLedger.status.in_(['processing', 'failed'])
            ).order_by(WebhookEventLedger.created_at.desc())
            
            result = await session.execute(stmt)
            webhook_event = result.scalar_one_or_none()
            
            if not webhook_event:
                print(f"\n❌ No stuck webhook found for reference {args.process_reference}")
                return
            
            print(f"Found webhook ID {webhook_event.id} for reference {args.process_reference}")
            result = await process_stuck_webhook_direct(webhook_event.id)
            if result['success']:
                print("\n✅ Successfully processed webhook")
                print(f"  Result: {result.get('result')}")
            else:
                print(f"\n❌ Processing failed: {result.get('error')}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
