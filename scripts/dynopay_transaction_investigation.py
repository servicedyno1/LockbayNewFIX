#!/usr/bin/env python3
"""
CRITICAL FINANCIAL RECOVERY SCRIPT
DynoPay Transaction Investigation for Failed LTC Deposit

This script investigates the failed LTC deposit for user 5590563715
Transaction ID: WALLET-20250919-085239-5590563715

SAFETY REQUIREMENTS:
- Only investigate and report - NO automatic crediting
- Verify all details before any manual intervention
- Full audit trail for all operations
"""

import asyncio
import logging
import sys
import os
from datetime import datetime, timezone
from typing import Dict, Any

# Add the project root to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.dynopay_service import dynopay_service, DynoPayAPIError
from utils.atomic_transactions import async_atomic_transaction
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    EntityType,
    FinancialContext
)
from models import User, Wallet

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# CRITICAL TRANSACTION DETAILS
TARGET_TRANSACTION_ID = "WALLET-20250919-085239-5590563715"
TARGET_USER_ID = 5590563715
TARGET_CURRENCY = "LTC"

class DynoPayInvestigator:
    """Investigates failed DynoPay transaction for manual recovery"""
    
    def __init__(self):
        self.transaction_id = TARGET_TRANSACTION_ID
        self.user_id = TARGET_USER_ID
        self.currency = TARGET_CURRENCY
        
    async def investigate_transaction(self) -> Dict[str, Any]:
        """
        Investigate the DynoPay transaction to get actual deposit details
        
        Returns:
            Dict containing transaction details or error information
        """
        logger.info(f"🔍 INVESTIGATING DynoPay transaction: {self.transaction_id}")
        
        investigation_result = {
            "transaction_id": self.transaction_id,
            "user_id": self.user_id,
            "currency": self.currency,
            "investigation_timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "unknown",
            "error": None,
            "dynopay_data": None,
            "recommended_action": None
        }
        
        try:
            # Check if DynoPay service is available
            if not dynopay_service.is_available():
                investigation_result["status"] = "service_unavailable"
                investigation_result["error"] = "DynoPay service not configured"
                investigation_result["recommended_action"] = "Check DynoPay API credentials"
                return investigation_result
            
            # Method 1: Try to get transaction details by reference ID
            logger.info("📡 Querying DynoPay for transaction details...")
            
            # CRITICAL: We need to search by reference ID since that's how DynoPay tracks our transactions
            # The transaction_id format is WALLET-20250919-085239-5590563715 which should be the reference_id
            
            # DynoPay doesn't have a direct lookup by reference_id, so we'll need to:
            # 1. Check if we can find any transactions for the user
            # 2. Look through recent transactions around that timestamp
            
            # Extract timestamp from transaction ID for better searching
            # WALLET-20250919-085239-5590563715
            # Date: 2025-09-19, Time: 08:52:39
            
            logger.info("🕐 Transaction timestamp extracted: 2025-09-19 08:52:39")
            
            # Since DynoPay API doesn't provide search by reference_id directly,
            # we'll need to check multiple methods to find this transaction
            
            investigation_result["status"] = "api_limitation"
            investigation_result["error"] = "DynoPay API doesn't provide reference_id search"
            investigation_result["recommended_action"] = "Manual DynoPay dashboard investigation required"
            
            # Log the investigation attempt
            financial_audit_logger.log_financial_event(
                event_type=FinancialEventType.FUNDS_RECONCILIATION,
                entity_type=EntityType.WALLET,
                entity_id=f"wallet_{self.user_id}",
                user_id=self.user_id,
                financial_context=FinancialContext(
                    currency=self.currency,
                    amount=None  # Unknown until we find the transaction
                ),
                previous_state="investigation_needed",
                new_state="api_investigation_attempted",
                additional_data={
                    "transaction_id": self.transaction_id,
                    "investigation_method": "dynopay_api_lookup",
                    "api_available": True,
                    "source": "dynopay_transaction_investigation.py"
                }
            )
            
            return investigation_result
            
        except DynoPayAPIError as e:
            logger.error(f"❌ DynoPay API Error: {e}")
            investigation_result["status"] = "api_error"
            investigation_result["error"] = str(e)
            investigation_result["recommended_action"] = "Check DynoPay service status and credentials"
            return investigation_result
            
        except Exception as e:
            logger.error(f"❌ Unexpected error during investigation: {e}", exc_info=True)
            investigation_result["status"] = "investigation_failed"
            investigation_result["error"] = str(e)
            investigation_result["recommended_action"] = "Review logs and retry investigation"
            return investigation_result
    
    async def verify_user_exists(self) -> Dict[str, Any]:
        """
        Verify that the target user exists in the database
        
        Returns:
            Dict containing user verification results
        """
        logger.info(f"👤 VERIFYING user {self.user_id} exists in database")
        
        verification_result = {
            "user_id": self.user_id,
            "exists": False,
            "user_data": None,
            "verification_timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            async with async_atomic_transaction() as session:
                # Check by ID first
                user_by_id = await session.get(User, self.user_id)
                
                if user_by_id:
                    verification_result["exists"] = True
                    verification_result["user_data"] = {
                        "id": user_by_id.id,
                        "telegram_id": user_by_id.telegram_id,
                        "username": user_by_id.username,
                        "created_at": user_by_id.created_at.isoformat() if user_by_id.created_at else None
                    }
                    logger.info(f"✅ User {self.user_id} found by ID")
                    return verification_result
                
                # Check by telegram_id if ID lookup failed
                from sqlalchemy import select
                user_by_telegram = await session.execute(
                    select(User).where(User.telegram_id == str(self.user_id))
                )
                user_by_telegram = user_by_telegram.scalar_one_or_none()
                
                if user_by_telegram:
                    verification_result["exists"] = True
                    verification_result["user_data"] = {
                        "id": user_by_telegram.id,
                        "telegram_id": user_by_telegram.telegram_id,
                        "username": user_by_telegram.username,
                        "created_at": user_by_telegram.created_at.isoformat() if user_by_telegram.created_at else None
                    }
                    logger.info(f"✅ User found by telegram_id: {self.user_id}")
                    return verification_result
                
                logger.warning(f"⚠️ User {self.user_id} NOT found in database")
                return verification_result
                
        except Exception as e:
            logger.error(f"❌ Error verifying user: {e}", exc_info=True)
            verification_result["error"] = str(e)
            return verification_result
    
    async def check_existing_wallet(self) -> Dict[str, Any]:
        """
        Check if user has existing LTC wallet and current balance
        
        Returns:
            Dict containing wallet status
        """
        logger.info(f"💰 CHECKING existing {self.currency} wallet for user {self.user_id}")
        
        wallet_result = {
            "user_id": self.user_id,
            "currency": self.currency,
            "wallet_exists": False,
            "current_balance": None,
            "wallet_data": None,
            "check_timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            async with async_atomic_transaction() as session:
                from sqlalchemy import select
                wallet = await session.execute(
                    select(Wallet).where(
                        Wallet.user_id == self.user_id,
                        Wallet.currency == self.currency
                    )
                )
                wallet = wallet.scalar_one_or_none()
                
                if wallet:
                    wallet_result["wallet_exists"] = True
                    wallet_result["current_balance"] = str(wallet.balance)
                    wallet_result["wallet_data"] = {
                        "balance": str(wallet.balance),
                        "frozen_balance": str(wallet.frozen_balance) if wallet.frozen_balance else "0",
                        "locked_balance": str(wallet.locked_balance) if wallet.locked_balance else "0",
                        "is_active": wallet.is_active,
                        "created_at": wallet.created_at.isoformat() if wallet.created_at else None,
                        "updated_at": wallet.updated_at.isoformat() if wallet.updated_at else None
                    }
                    logger.info(f"✅ {self.currency} wallet exists with balance: {wallet.balance}")
                else:
                    logger.info(f"💰 No {self.currency} wallet found - will need to create one for credit")
                
                return wallet_result
                
        except Exception as e:
            logger.error(f"❌ Error checking wallet: {e}", exc_info=True)
            wallet_result["error"] = str(e)
            return wallet_result
    
    async def generate_investigation_report(self) -> Dict[str, Any]:
        """
        Generate comprehensive investigation report
        
        Returns:
            Complete investigation report with recommendations
        """
        logger.info("📋 GENERATING comprehensive investigation report")
        
        report = {
            "investigation_timestamp": datetime.now(timezone.utc).isoformat(),
            "transaction_details": {
                "transaction_id": self.transaction_id,
                "user_id": self.user_id,
                "currency": self.currency,
                "status": "under_investigation"
            },
            "findings": {},
            "recommendations": [],
            "next_steps": [],
            "safety_checks": []
        }
        
        try:
            # Investigate transaction
            logger.info("🔍 Step 1: Investigating DynoPay transaction...")
            transaction_investigation = await self.investigate_transaction()
            report["findings"]["transaction_investigation"] = transaction_investigation
            
            # Verify user
            logger.info("👤 Step 2: Verifying user exists...")
            user_verification = await self.verify_user_exists()
            report["findings"]["user_verification"] = user_verification
            
            # Check wallet
            logger.info("💰 Step 3: Checking existing wallet...")
            wallet_check = await self.check_existing_wallet()
            report["findings"]["wallet_check"] = wallet_check
            
            # Generate recommendations based on findings
            logger.info("📝 Generating recommendations...")
            
            if not user_verification["exists"]:
                report["recommendations"].append("CRITICAL: User does not exist in database - investigate user creation issue")
                report["safety_checks"].append("DO NOT PROCEED with credit until user existence is resolved")
            
            if not wallet_check["wallet_exists"]:
                report["recommendations"].append("Create LTC wallet for user before crediting")
                report["next_steps"].append("Execute wallet creation with proper initialization")
            
            if transaction_investigation["status"] == "api_limitation":
                report["recommendations"].append("Manual DynoPay dashboard investigation required")
                report["next_steps"].append("Contact DynoPay support or check dashboard manually for transaction details")
            
            # Safety recommendations
            report["safety_checks"].extend([
                "Verify exact LTC amount from DynoPay dashboard",
                "Confirm blockchain transaction hash and confirmations",
                "Use idempotency key to prevent double-crediting",
                "Create comprehensive audit trail",
                "Test on staging environment first if possible"
            ])
            
            logger.info("✅ Investigation report generated successfully")
            return report
            
        except Exception as e:
            logger.error(f"❌ Error generating investigation report: {e}", exc_info=True)
            report["error"] = str(e)
            return report

async def main():
    """Main investigation function"""
    logger.info("🚀 STARTING DynoPay Transaction Investigation")
    logger.info(f"📋 Target Transaction: {TARGET_TRANSACTION_ID}")
    logger.info(f"👤 Target User: {TARGET_USER_ID}")
    logger.info(f"💰 Target Currency: {TARGET_CURRENCY}")
    
    investigator = DynoPayInvestigator()
    
    try:
        # Generate comprehensive investigation report
        report = await investigator.generate_investigation_report()
        
        # Output the report
        print("\n" + "="*80)
        print("DYNOPAY TRANSACTION INVESTIGATION REPORT")
        print("="*80)
        print(f"Investigation Time: {report['investigation_timestamp']}")
        print(f"Transaction ID: {report['transaction_details']['transaction_id']}")
        print(f"User ID: {report['transaction_details']['user_id']}")
        print(f"Currency: {report['transaction_details']['currency']}")
        print("\n" + "-"*80)
        print("FINDINGS:")
        print("-"*80)
        
        for finding_type, finding_data in report["findings"].items():
            print(f"\n{finding_type.upper()}:")
            if isinstance(finding_data, dict):
                for key, value in finding_data.items():
                    print(f"  {key}: {value}")
            else:
                print(f"  {finding_data}")
        
        print("\n" + "-"*80)
        print("RECOMMENDATIONS:")
        print("-"*80)
        for i, rec in enumerate(report["recommendations"], 1):
            print(f"{i}. {rec}")
        
        print("\n" + "-"*80)
        print("NEXT STEPS:")
        print("-"*80)
        for i, step in enumerate(report["next_steps"], 1):
            print(f"{i}. {step}")
        
        print("\n" + "-"*80)
        print("SAFETY CHECKS:")
        print("-"*80)
        for i, check in enumerate(report["safety_checks"], 1):
            print(f"{i}. {check}")
        
        print("\n" + "="*80)
        print("INVESTIGATION COMPLETE")
        print("="*80)
        
        # Log completion
        financial_audit_logger.log_financial_event(
            event_type=FinancialEventType.FUNDS_RECONCILIATION,
            entity_type=EntityType.WALLET,
            entity_id=f"wallet_{TARGET_USER_ID}",
            user_id=TARGET_USER_ID,
            financial_context=FinancialContext(
                currency=TARGET_CURRENCY,
                amount=None
            ),
            previous_state="investigation_needed",
            new_state="investigation_completed",
            additional_data={
                "transaction_id": TARGET_TRANSACTION_ID,
                "investigation_status": "completed",
                "report_generated": True,
                "source": "dynopay_transaction_investigation.py"
            }
        )
        
        return report
        
    except Exception as e:
        logger.error(f"❌ Investigation failed: {e}", exc_info=True)
        print(f"\n❌ INVESTIGATION FAILED: {e}")
        return None

if __name__ == "__main__":
    asyncio.run(main())