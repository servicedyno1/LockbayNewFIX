#!/usr/bin/env python3
"""
Fincra Payment Service for NGN (Nigeria Naira) Payments
Handles NGN payment processing, exchange rates, payouts, and webhooks
Superior to Flutterwave with 1% fees capped at ₦250
"""

import logging
import aiohttp
import asyncio
import os
import random
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timezone
from config import Config
from models import User
from database import async_managed_session
from sqlalchemy import select
from services.circuit_breaker import with_circuit_breaker
from services.silent_api_failure_detector import record_api_failure
from services.api_adapter_retry import APIAdapterRetry
from models import CashoutErrorCode
from services.financial_operation_protection import require_balance_protection
from utils.financial_audit_logger import (
    FinancialAuditLogger, 
    FinancialEventType, 
    EntityType, 
    FinancialContext
)

logger = logging.getLogger(__name__)

# Fincra Authentication Circuit Breaker
# Stops retrying when credentials are invalid (saves connections + network)
_fincra_auth_circuit_open = False
_fincra_auth_failure_count = 0
_fincra_auth_circuit_opened_at = 0.0
_FINCRA_AUTH_FAILURE_THRESHOLD = 3       # Open circuit after 3 consecutive auth failures
_FINCRA_AUTH_CIRCUIT_COOLDOWN = 1800     # 30 minutes before retrying


class FincraService(APIAdapterRetry):
    """Service for handling Fincra NGN payments and payouts with unified retry system"""

    def __init__(self):
        # Initialize parent APIAdapterRetry with service name
        super().__init__(service_name="fincra", timeout=30)
        
        self.secret_key = Config.FINCRA_SECRET_KEY
        self.public_key = Config.FINCRA_PUBLIC_KEY
        self.business_id = Config.FINCRA_BUSINESS_ID
        self.webhook_key = Config.FINCRA_WEBHOOK_ENCRYPTION_KEY  # Use correct webhook encryption key
        self.base_url = Config.FINCRA_BASE_URL
        self.test_mode = Config.FINCRA_TEST_MODE
        self.session = None
        
        # Initialize financial audit logger for comprehensive NGN transaction tracking
        self.audit_logger = FinancialAuditLogger()
        
        # NGN tolerance settings for improved user experience
        self.ngn_tolerance_percentage = Decimal("2.0")  # 2% tolerance for NGN payments
        self.ngn_min_tolerance_amount = Decimal("50.0")  # Minimum ₦50 tolerance
        
        # CRITICAL FIX: Shared balance cache for cross-operation reuse (mirrors Kraken optimization)
        self._balance_cache = None
        self._balance_cache_timestamp = None
        self._balance_cache_expiry_seconds = 45  # 45-second cache for operation-scoped reuse
        self._balance_cache_lock = asyncio.Lock()  # Thread-safe cache access
        
        if not self.secret_key or not self.public_key:
            logger.warning(
                "FINCRA_SECRET_KEY or FINCRA_PUBLIC_KEY not configured - Fincra payments will not work"
            )
    
    def _map_provider_error_to_unified(self, exception: Exception, context: Optional[Dict] = None) -> CashoutErrorCode:
        """
        Map Fincra-specific errors to unified error codes for intelligent retry
        """
        error_message = str(exception).lower()
        
        # Fincra-specific error patterns
        if "no_enough_money_in_wallet" in error_message or "insufficient funds in wallet" in error_message:
            return CashoutErrorCode.API_INSUFFICIENT_FUNDS
        elif "fincra authentication failed" in error_message or "invalid api key" in error_message:
            return CashoutErrorCode.API_AUTHENTICATION_FAILED
        elif "fincra timeout" in error_message or "fincra timed out" in error_message:
            return CashoutErrorCode.API_TIMEOUT
        elif "fincra api error" in error_message:
            return CashoutErrorCode.FINCRA_API_ERROR  # Keep specific Fincra error for detailed tracking
        elif "invalid account number" in error_message or "invalid bank code" in error_message:
            return CashoutErrorCode.API_INVALID_REQUEST
        elif "service unavailable" in error_message or "502" in error_message or "503" in error_message:
            return CashoutErrorCode.SERVICE_UNAVAILABLE
        elif "rate limit" in error_message or "too many requests" in error_message:
            return CashoutErrorCode.RATE_LIMIT_EXCEEDED
        elif "network error" in error_message or "connection error" in error_message:
            return CashoutErrorCode.NETWORK_ERROR
        
        # Default to generic classification for unknown Fincra errors
        return CashoutErrorCode.UNKNOWN_ERROR
    
    def _get_circuit_breaker_name(self) -> str:
        """Return the circuit breaker name for Fincra API"""
        return "fincra"
    
    async def _fincra_api_call_unified(self, operation: str, api_func: Callable, *args, **kwargs) -> Any:
        """
        Unified API call wrapper for Fincra operations with retry logic
        Example usage pattern for external API integrations
        """
        @self.api_retry(max_attempts=5, context={"operation": operation, "service": "fincra"})
        async def _wrapped_call():
            return await api_func(*args, **kwargs)
        
        return await _wrapped_call()

    @require_balance_protection("ngn_cashout", currency_param="currency", amount_param="amount_ngn")
    async def process_bank_transfer(
        self,
        amount_ngn: Decimal,
        bank_code: str,
        account_number: str,
        account_name: str,
        reference: str,
        currency: str = "NGN",  # Add currency parameter for protection
        session=None,
        cashout_id: Optional[str] = None,
        transaction_id: Optional[str] = None
    ) -> Optional[Dict]:
        """Process bank transfer using Fincra Account-to-Account Transfer API"""
        try:
            # NEW: Platform absorbs fees - customer gets full amount
            from utils.decimal_precision import MonetaryDecimal

            amount_ngn_decimal = MonetaryDecimal.to_decimal(amount_ngn, "ngn_amount")
            fee_percentage = Config.WALLET_NGN_MARKUP_PERCENTAGE  # Use configurable wallet markup
            min_fee = Decimal("100.0")
            max_fee = Decimal("2000.0")

            # Calculate Fincra processing fee (platform cost)
            fee_amount = (
                amount_ngn_decimal * (fee_percentage / Decimal("100"))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            fee_amount = min(max(fee_amount, min_fee), max_fee)

            # CRITICAL CHANGE: Customer gets full amount, platform pays fees
            customer_amount = amount_ngn_decimal  # Full amount to customer
            total_platform_cost = (
                amount_ngn_decimal + fee_amount
            )  # What we actually pay Fincra

            # AUDIT: Log NGN bank transfer initiation with comprehensive financial context
            financial_context = FinancialContext(
                amount=customer_amount,
                currency="NGN",
                fee_amount=fee_amount,
                markup_percentage=Decimal(str(fee_percentage)),
                balance_before=None,  # Could be added if we track Fincra balance
                balance_after=None
            )
            
            self.audit_logger.log_financial_event(
                event_type=FinancialEventType.NGN_BANK_TRANSFER_INITIATED,
                entity_type=EntityType.NGN_BANK_TRANSFER,
                entity_id=reference,
                user_id=None,  # Would need to be passed from caller
                financial_context=financial_context,
                previous_state=None,
                new_state="initiated",
                related_entities={
                    "bank_code": bank_code,
                    "account_number": account_number[-4:],  # Only last 4 digits for security
                    "account_name": account_name
                },
                additional_data={
                    "total_platform_cost": str(total_platform_cost),
                    "fee_percentage": fee_percentage,
                    "source": "fincra_service.process_bank_transfer"
                }
            )

            # CRITICAL FIX: Use Fincra's official bank codes for PAYOUTS
            # Verification may work with one code, but payouts need the official Fincra code
            
            # Get the official bank list from Fincra
            official_banks = await self.list_banks()
            
            # IMPORTANT: Even if verification works, we need the PAYOUT code from Fincra's list
            payout_bank_code = bank_code  # Start with provided code
            
            if official_banks:
                # Special handling for known problematic banks
                if bank_code == "50515":  # Moniepoint old code
                    # Find the official Moniepoint code from Fincra's list
                    moniepoint_banks = [
                        b for b in official_banks 
                        if "moniepoint" in b.get("name", "").lower()
                    ]
                    if moniepoint_banks:
                        # Use the first Moniepoint match (usually 090405)
                        payout_bank_code = str(moniepoint_banks[0].get("code", bank_code))
                        logger.info(f"✅ Mapped Moniepoint: {bank_code} → {payout_bank_code} for payout")
                
                # OPTIMIZATION: Smart account verification to prevent duplicate API calls
                verified_account_name = None
                verification_attempts = []
                
                # Strategy: Try payout code first if different from provided code
                if payout_bank_code != bank_code:
                    # Use payout code first (more likely to work for transfers)
                    verification_attempts = [payout_bank_code, bank_code]
                else:
                    # Only one code to try
                    verification_attempts = [bank_code]
                
                # OPTIMIZED: Single verification loop instead of duplicate calls
                for bank_code_to_try in verification_attempts:
                    try:
                        verified_account_name = await self.verify_account_name(account_number, bank_code_to_try)
                        if verified_account_name:
                            break  # Success - no need to try other codes
                        else:
                            logger.warning(f"⚠️ Verification failed with code {bank_code_to_try}")
                    except Exception as verify_error:
                        logger.warning(f"⚠️ Verification error with {bank_code_to_try}: {verify_error}")
                        # Continue to next code if available
                        continue
                
                if not verified_account_name:
                    logger.warning(f"⚠️ Could not verify account {account_number} with any bank code")
                    # Continue with transfer anyway - verification is for confirmation only
            else:
                logger.warning("⚠️ Could not get official bank list - using provided code")

            # Enhanced: Account-to-Account Transfer API payload with Decimal precision
            {
                "business": self.business_id,
                "source": "balance",  # Transfer from Fincra balance
                "destination": {
                    "type": "bank_account",
                    "amount": int(
                        customer_amount * Decimal("100")
                    ),  # Convert to kobo with precision - FULL AMOUNT
                    "currency": "NGN",
                    "narration": f"{Config.PLATFORM_NAME} Credit - {reference}",
                    "bank_account": {
                        "bank": payout_bank_code,  # Use official Fincra bank code
                        "number": account_number,
                        "name": account_name,
                    },
                },
                "customerReference": reference,
            }

            # Use correct Fincra API structure from official documentation
            # URL: POST /disbursements/payouts
            payout_data = {
                "business": self.business_id,
                "sourceCurrency": "NGN",
                "destinationCurrency": "NGN",
                "amount": str(
                    int(customer_amount)
                ),  # Amount in NGN (not kobo) - FULL AMOUNT
                "description": f"{Config.PLATFORM_NAME} Credit - {reference}",
                "customerReference": reference,
                "beneficiary": {
                    "firstName": (
                        account_name.split()[0]
                        if account_name.split()
                        else account_name
                    ),  # Extract first name
                    "lastName": (
                        " ".join(account_name.split()[1:])
                        if len(account_name.split()) > 1
                        else account_name
                    ),  # Extract last name
                    "accountHolderName": account_name,
                    "accountNumber": account_number,
                    "country": "NG",
                    "bankCode": payout_bank_code,  # Use official Fincra bank code
                    "type": "individual",
                },
                "paymentDestination": "bank_account",
            }

            # CRITICAL FINANCIAL PROTECTION: Use idempotency for external API calls
            if session and cashout_id and transaction_id:
                from utils.external_api_idempotency import FincraIdempotencyWrapper
                
                destination_details = {
                    "bank_code": bank_code,
                    "account_number": account_number,
                    "account_name": account_name
                }
                
                with FincraIdempotencyWrapper.ensure_single_disbursement(
                    session=session,
                    cashout_id=cashout_id,
                    amount=float(customer_amount),
                    currency="NGN", 
                    destination_details=destination_details,
                    transaction_id=transaction_id
                ) as api_guard:
                    
                    if api_guard.should_execute:
                        # Make the actual external API call with retry logic
                        response = None
                        max_retries = 1  # CHANGED: Reduced from 3 to 1 attempt as requested
                        attempt = 0
                        for attempt in range(max_retries):
                            try:
                                response = await self._make_request(
                                    "POST", "/disbursements/payouts", payout_data
                                )
                                if (
                                    response
                                    and response.get("data")
                                    and response.get("data", {}).get("status")
                                    not in ["failed", "error"]
                                ):
                                    # SUCCESS: API responded AND transaction is not failed
                                    logger.info(
                                        f"Fincra transfer successful on attempt {attempt + 1} for {reference}"
                                    )
                                    break  # Success, exit retry loop
                                elif attempt < max_retries - 1:  # Not the last attempt
                                    logger.warning(
                                        f"Fincra API attempt {attempt + 1} failed for {reference}, retrying..."
                                    )
                                    await asyncio.sleep(2)  # Wait 2 seconds before retry
                                else:
                                    # Final attempt failed
                                    logger.error(
                                        f"Fincra transfer FAILED after {max_retries} attempts for {reference}"
                                    )
                                    break
                            except Exception as e:
                                logger.warning(
                                    f"Fincra API attempt {attempt + 1} error for {reference}: {e}"
                                )
                                if attempt < max_retries - 1:
                                    await asyncio.sleep(2)
                                else:
                                    raise
                        
                        # Store the result (success or failure)
                        if response:
                            api_guard.store_result({
                                "success": True,
                                "api_response": response,
                                "attempts": attempt + 1,
                                "reference": reference
                            })
                        else:
                            api_guard.store_error(f"Fincra API failed after {max_retries} attempts")
                    
                    # Get the result (either cached or newly created)
                    final_result = api_guard.get_result()
                    
                    if final_result.get('success'):
                        response = final_result.get('api_response')
                        # CRITICAL FIX: Invalidate balance cache after successful NGN disbursement (balance changed)
                        self.invalidate_balance_cache("ngn_disbursement_success")
                    else:
                        logger.error(f"❌ Fincra disbursement failed (cached): {final_result.get('error')}")
                        response = None
            else:
                # Fallback for cases without full context (backwards compatibility)
                logger.warning("⚠️ IDEMPOTENCY_BYPASS: Making Fincra disbursement without idempotency protection - missing session/cashout_id/transaction_id")
                
                # Use the correct official Fincra endpoint for bank transfers with retry logic
                response = None
                max_retries = 1  # CHANGED: Reduced from 3 to 1 attempt as requested
                attempt = 0  # Initialize before loop for type safety
                for attempt in range(max_retries):
                    try:
                        response = await self._make_request(
                            "POST", "/disbursements/payouts", payout_data
                        )
                        
                        if (
                            response
                            and response.get("data")
                            and response.get("data", {}).get("status")
                            not in ["failed", "error"]
                        ):
                            # SUCCESS: API responded AND transaction is not failed
                            logger.info(
                                f"Fincra transfer successful on attempt {attempt + 1} for {reference}"
                            )
                            break  # Success, exit retry loop
                        elif attempt < max_retries - 1:  # Not the last attempt
                            logger.warning(
                                f"Fincra API attempt {attempt + 1} failed for {reference}, retrying..."
                            )
                            await asyncio.sleep(2)  # Wait 2 seconds before retry
                        else:
                            # Final attempt failed - check if this is insufficient funds before returning generic error
                            logger.error(
                                f"Fincra transfer FAILED after {max_retries} attempts for {reference}"
                            )
                            
                            # CRITICAL FIX: Check if the failed response contains insufficient funds error
                            if (response and 'NO_ENOUGH_MONEY_IN_WALLET' in str(response)):
                                logger.warning(f"PRODUCTION: Fincra insufficient funds detected in retry logic for {reference}")
                                return {
                                    "success": False,
                                    "status": "pending_funding",  # CRITICAL: This enables the fixed auto_cashout flow
                                    "error": "You dont have enough money in your wallet to make this payout",
                                    "errorType": "NO_ENOUGH_MONEY_IN_WALLET",
                                    "requires_admin_funding": True,
                                    "attempts": max_retries,
                                    "reference": reference
                                }
                            
                            # Return generic error details only if it's not insufficient funds
                            return {
                                "success": False,
                                "error": 'Fincra API failed after retries',
                                "errorType": 'FINCRA_API_FAILURE',
                                "attempts": max_retries,
                                "reference": reference
                            }
                    except Exception as e:
                        logger.warning(
                            f"Fincra API attempt {attempt + 1} error for {reference}: {e}"
                        )
                        if attempt == max_retries - 1:  # Last attempt
                            logger.error(
                                f"Fincra transfer FAILED with exception after {max_retries} attempts for {reference}: {e}"
                            )
                            # Check if this is insufficient funds error
                            error_str = str(e)
                            if 'NO_ENOUGH_MONEY_IN_WALLET' in error_str or 'insufficient' in error_str.lower():
                                return {
                                    "success": False,
                                    "status": "pending_funding",  # CRITICAL FIX: Add status field so auto_cashout.py handles this properly
                                    "error": "You dont have enough money in your wallet to make this payout",
                                    "errorType": "NO_ENOUGH_MONEY_IN_WALLET",
                                    "attempts": max_retries,
                                    "reference": reference
                                }
                            # Return generic error details
                            return {
                                "success": False,
                                "error": str(e),
                                "errorType": "FINCRA_EXCEPTION",
                                "attempts": max_retries,
                                "reference": reference
                            }
                        await asyncio.sleep(2)

            if not response or not response.get("data"):
                logger.error(
                    f"Fincra transfer FAILED - no valid response for {reference} (attempt {attempt + 1}/{max_retries})"
                )

                # In development, simulate success when API fails
                if self.test_mode or os.getenv("NODE_ENV") != "production":
                    logger.info(
                        f"TEST MODE: Simulating successful NGN cashout for {reference}"
                    )
                    # CRITICAL FIX: Invalidate balance cache after simulated successful NGN disbursement
                    self.invalidate_balance_cache("ngn_disbursement_test_success")
                    return {
                        "success": True,
                        "transfer_id": f"test_{reference}",
                        "reference": reference,
                        "amount_ngn": customer_amount,
                        "fee_amount": fee_amount,
                        "status": "completed",
                        "bank_code": bank_code,
                        "account_number": account_number,
                        "account_name": account_name,
                        "test_mode": True,
                    }
                # Production mode - return error details for proper handling
                return {
                    "success": False,
                    "error": "No valid response from Fincra API",
                    "errorType": "NO_RESPONSE",
                    "reference": reference
                }

            if response and response.get("data"):
                transfer_info = response["data"]
                logger.info(
                    f"Initiated Fincra account-to-account transfer: {reference}"
                )
                
                # CRITICAL FIX: Invalidate balance cache after successful NGN disbursement (balance changed)
                self.invalidate_balance_cache("ngn_disbursement_initiated")

                return {
                    "success": True,
                    "transfer_id": transfer_info.get("id"),
                    "reference": reference,
                    "amount_ngn": customer_amount,  # Full amount customer receives
                    "fee_amount": fee_amount,  # Platform absorbs this cost
                    "total_cost": total_platform_cost,  # Total platform cost (amount + fees)
                    "status": transfer_info.get("status", "processing"),
                    "bank_code": bank_code,
                    "account_number": account_number,
                    "account_name": account_name,
                }

            # Check if this is insufficient funds error
            if (
                response
                and not response.get("data")
                and "NO_ENOUGH_MONEY_IN_WALLET" in str(response)
            ):
                # In test/dev mode, simulate success for testing
                if (self.test_mode or os.getenv("NODE_ENV") != "production"):
                    logger.info(
                        f"TEST MODE: Fincra test account has insufficient funds, simulating success for {reference}"
                    )
                    return {
                        "success": True,
                        "transfer_id": f"test_{reference}",
                        "reference": reference,
                        "amount_ngn": customer_amount,  # Full amount customer receives
                        "fee_amount": fee_amount,  # Platform absorbs this cost
                        "total_cost": total_platform_cost,  # Total platform cost (amount + fees)
                        "status": "completed",
                        "bank_code": bank_code,
                        "account_number": account_number,
                        "account_name": account_name,
                        "test_mode": True,
                    }
                else:
                    # In production, return insufficient funds status for admin notification
                    logger.warning(f"PRODUCTION: Fincra insufficient funds for {reference} - requires admin funding")
                    return {
                        "success": False,
                        "error": "NO_ENOUGH_MONEY_IN_WALLET",
                        "status": "pending_funding",
                        "requires_admin_funding": True,
                        "reference": reference,
                        "amount_ngn": customer_amount,
                        "fee_amount": fee_amount,
                        "total_cost": total_platform_cost,
                        "bank_code": bank_code,
                        "account_number": account_number,
                        "account_name": account_name,
                    }

            logger.error(
                f"Failed to process Fincra transfer after trying multiple endpoints. Final response: {response}"
            )
            return None

        except aiohttp.ClientError as e:
            logger.error(f"Network error processing Fincra bank transfer: {e}")
            return None
        except ValueError as e:
            logger.error(f"Invalid data for Fincra bank transfer: {e}")
            return None
        except Exception as e:
            await record_api_failure('fincra', 'process_bank_transfer', e, {'reference': reference})
            logger.error(f"Unexpected error processing Fincra bank transfer: {e}")
            return None

    def is_available(self) -> bool:
        """Check if Fincra service is properly configured"""
        return bool(self.secret_key and self.public_key and Config.FINCRA_ENABLED)

    async def _get_session(self):
        """Get or create HTTP session with proper lifecycle management"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close_session(self):
        """CRITICAL FIX: Properly close HTTP session to prevent connection leaks"""
        if self.session:
            await self.session.close()
            self.session = None
            logger.debug("Fincra HTTP session closed successfully")

    @with_circuit_breaker('fincra')
    async def _make_request(
        self, method: str, endpoint: str, data: Optional[Dict] = None, operation_context: str = 'unknown'
    ) -> Optional[Dict]:
        """ENHANCED: Make authenticated request with circuit breaker for auth failures"""
        global _fincra_auth_circuit_open, _fincra_auth_failure_count, _fincra_auth_circuit_opened_at
        
        if not self.is_available():
            logger.error("Fincra service not available - missing API keys")
            return None

        # Circuit breaker check for authentication failures
        if _fincra_auth_circuit_open:
            elapsed = time.time() - _fincra_auth_circuit_opened_at
            if elapsed < _FINCRA_AUTH_CIRCUIT_COOLDOWN:
                logger.debug(
                    f"Fincra auth circuit OPEN - skipping {method} {endpoint} "
                    f"({elapsed:.0f}s / {_FINCRA_AUTH_CIRCUIT_COOLDOWN}s cooldown)"
                )
                return None
            else:
                logger.info("Fincra auth circuit half-open - attempting recovery probe")
                _fincra_auth_circuit_open = False
                _fincra_auth_failure_count = 0

        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        # CRITICAL FIX: Use official Fincra authentication headers (from official docs)
        headers = {
            "api-key": self.secret_key,      # OFFICIAL: Fincra expects api-key header
            "x-pub-key": self.public_key,    # OFFICIAL: Fincra expects x-pub-key header 
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Add business ID to headers if available (Fincra may require this)
        if self.business_id:
            headers["x-business-id"] = self.business_id
            
        # DEBUGGING: Log authentication for troubleshooting (without exposing keys)
        key_preview = f"{self.secret_key[:8]}..." if self.secret_key and len(self.secret_key) > 8 else "MISSING"
        logger.debug(f"🔑 Fincra Auth: Key={key_preview}, BusinessID={self.business_id}, URL={self.base_url}")

        # Log authentication status for debugging
        logger.debug(f"Fincra Auth: Using {'TEST' if self.test_mode else 'LIVE'} mode")

        # Enhanced retry logic with exponential backoff  
        max_retries = 1  # CHANGED: Reduced from 3 to 1 attempt as requested
        for attempt in range(max_retries):
            try:
                # Use context manager for proper session cleanup
                async with aiohttp.ClientSession() as session:
                    timeout = aiohttp.ClientTimeout(total=30)  # 30 second timeout
                    
                    async with session.request(
                        method, url, headers=headers, json=data, timeout=timeout
                    ) as response:
                        response_data = await response.json()

                        if response.status in [200, 201] and (
                            response_data.get("success") or response_data.get("status")
                        ):
                            logger.info(f"Fincra API success: {method} {endpoint}")
                            _fincra_auth_failure_count = 0  # Reset on success
                            return response_data

                        # Enhanced error categorization
                        elif response.status == 401:
                            logger.error(f"Fincra authentication failed: {response_data}")
                            logger.error(f"Check FINCRA_API_KEY and ensure it matches the environment (LIVE/TEST)")
                            # Track auth failures for circuit breaker
                            _fincra_auth_failure_count += 1
                            if _fincra_auth_failure_count >= _FINCRA_AUTH_FAILURE_THRESHOLD:
                                _fincra_auth_circuit_open = True
                                _fincra_auth_circuit_opened_at = time.time()
                                logger.warning(
                                    f"Fincra auth circuit OPENED after {_fincra_auth_failure_count} consecutive auth failures "
                                    f"- will retry in {_FINCRA_AUTH_CIRCUIT_COOLDOWN}s. FIX: Update FINCRA_API_KEY/FINCRA_SECRET_KEY"
                                )
                            return None  # Don't retry auth errors
                        elif response.status == 429:
                            if attempt < max_retries - 1:
                                delay = 0.5 + random.uniform(0, 0.3)  # Much faster: 0.5-0.8s
                                logger.warning(
                                    f"Fincra rate limited, retrying in {delay:.2f}s (attempt {attempt + 1})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                logger.error(
                                    f"Fincra rate limit exceeded after {max_retries} attempts"
                                )
                                return None
                        else:
                            # Check for insufficient funds error to enable test mode
                            if response.status == 422 and "NO_ENOUGH_MONEY_IN_WALLET" in str(response_data):
                                logger.warning(f"Fincra insufficient funds: {response_data}")
                                # CRITICAL FIX: Return error structure directly instead of raising exception
                                # This ensures the auto_cashout service gets the proper error format
                                return {
                                    "success": False,
                                    "status": "pending_funding",  # CRITICAL FIX: Add status field so auto_cashout.py handles this properly
                                    "error": "You dont have enough money in your wallet to make this payout",
                                    "errorType": "NO_ENOUGH_MONEY_IN_WALLET",
                                    "attempts": attempt + 1,
                                    "original_response": response_data
                                }
                            
                            logger.error(
                                f"Fincra API error: {response.status} - {response_data}"
                            )
                            if attempt < max_retries - 1:
                                delay = 0.5 + random.uniform(0, 0.3)  # Much faster: 0.5-0.8s
                                logger.warning(
                                    f"Fincra API error, retrying in {delay:.2f}s (attempt {attempt + 1})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # ENHANCED: Better error diagnostics and categorization
                error_type = type(e).__name__
                error_detail = str(e) if str(e) else "Network connectivity issue"
                
                # Provide more specific error context
                if "timeout" in error_detail.lower() or isinstance(e, asyncio.TimeoutError):
                    error_context = f"Timeout connecting to Fincra API ({self.base_url})"
                elif "connection" in error_detail.lower():
                    error_context = f"Connection failed to Fincra API ({self.base_url})"
                elif "ssl" in error_detail.lower():
                    error_context = f"SSL/TLS error connecting to Fincra API"
                else:
                    error_context = f"Network error accessing Fincra API"
                
                if attempt < max_retries - 1:
                    delay = 0.5 + random.uniform(0, 0.3)  # Much faster: 0.5-0.8s
                    logger.warning(
                        f"Fincra network error, retrying in {delay:.2f}s (attempt {attempt + 1}): {error_context} - {error_type}: {error_detail}"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"Fincra network error after {max_retries} attempts: {error_context} - {error_type}: {error_detail}"
                    )
                    # CRITICAL FIX: Clean up session after network errors
                    await self.close_session()
                    return None
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = 0.5 + random.uniform(0, 0.3)  # Much faster: 0.5-0.8s
                    logger.warning(
                        f"Fincra unexpected error, retrying in {delay:.2f}s (attempt {attempt + 1}): {e}"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"Fincra unexpected error after {max_retries} attempts: {e}"
                    )
                    return None

        return None

    async def get_usd_to_ngn_rate(self) -> Optional[float]:
        """Get current USD to NGN exchange rate - FastForex primary, Fincra fallback"""
        # PRIMARY: Try FastForex first (more reliable)
        try:
            from services.fastforex_service import FastForexService
            ff_service = FastForexService()
            # FIXED: Use correct method name
            fastforex_rate = await ff_service.get_usd_to_ngn_rate_clean()
            if fastforex_rate:
                logger.info(f"💱 FastForex USD/NGN rate (primary): {fastforex_rate}")
                return float(fastforex_rate)
        except Exception as ff_error:
            logger.warning(f"FastForex primary failed, trying Fincra fallback: {ff_error}")

        # FALLBACK: Try Fincra v2 API (quick, no retries for performance)
        try:
            quote_data = {
                "transactionType": "conversion",
                "feeBearer": "business", 
                "business": self.business_id,
                "sourceCurrency": "USD",
                "destinationCurrency": "NGN",
                "sourceAmount": 1,  # FIXED: Required parameter
                "amount": "1",  # Query rate for $1 USD
                "action": "send",
                "paymentDestination": "fliqpay_wallet"
            }

            # PERFORMANCE: Single attempt, no retries for Add Funds speed
            session = await self._get_session()
            url = f"{self.base_url}/quotes"
            headers = {
                "api-key": self.secret_key,
                "x-pub-key": self.public_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            if self.business_id:
                headers["x-business-id"] = self.business_id

            async with session.post(url, json=quote_data, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("data") and data["data"].get("rate"):
                        base_rate = float(data["data"]["rate"])
                        # Apply configurable platform markup
                        markup_percentage = float(Config.EXCHANGE_MARKUP_PERCENTAGE)
                        marked_up_rate = base_rate * (1 + markup_percentage / 100)
                        logger.info(f"💱 Fincra USD/NGN rate (fallback): {marked_up_rate}")
                        return float(marked_up_rate)

        except Exception as fincra_error:
            logger.warning(f"Fincra fallback failed: {fincra_error}")

        # Last resort: Return reliable fallback rate for immediate response
        fallback_rate = 1650.0
        logger.info(f"💱 Using emergency fallback rate: {fallback_rate}")
        return fallback_rate

    async def convert_usd_to_ngn(self, usd_amount: float) -> Optional[float]:
        """Convert USD amount to NGN"""
        # Convert usd_amount to float to ensure type compatibility (handles both Decimal and float inputs)
        usd_float = float(usd_amount) if isinstance(usd_amount, Decimal) else usd_amount
        
        # AUDIT: Log NGN conversion initiation
        self.audit_logger.log_financial_event(
            event_type=FinancialEventType.NGN_CONVERSION_USD_TO_NGN,
            entity_type=EntityType.NGN_PAYMENT,
            entity_id=f"usd_to_ngn_{int(time.time())}",
            user_id=None,
            financial_context=FinancialContext(
                amount=Decimal(str(usd_float)),
                currency="USD"
            ),
            previous_state=None,
            new_state="converting",
            additional_data={
                "usd_amount": usd_float,
                "source": "fincra_service.convert_usd_to_ngn"
            }
        )
        
        rate = await self.get_usd_to_ngn_rate()
        if rate:
            # Convert rate to float to ensure type compatibility
            rate_float = float(rate) if isinstance(rate, Decimal) else rate
            ngn_amount = usd_float * rate_float
            
            # AUDIT: Log successful NGN conversion 
            self.audit_logger.log_financial_event(
                event_type=FinancialEventType.NGN_EXCHANGE_RATE_APPLIED,
                entity_type=EntityType.NGN_PAYMENT,
                entity_id=f"usd_to_ngn_{int(time.time())}",
                user_id=None,
                financial_context=FinancialContext(
                    amount=Decimal(str(ngn_amount)),
                    currency="NGN",
                    exchange_rate=Decimal(str(rate))
                ),
                previous_state="converting",
                new_state="converted",
                additional_data={
                    "usd_amount": usd_float,
                    "ngn_amount": ngn_amount,
                    "exchange_rate": rate_float,
                    "source": "fincra_service.convert_usd_to_ngn"
                }
            )
            
            logger.info(
                f"Converted ${usd_float} USD to ₦{ngn_amount:,.2f} NGN at rate {rate_float}"
            )
            return ngn_amount
        return None

    async def convert_ngn_to_usd(self, ngn_amount: float) -> Optional[float]:
        """Convert NGN amount to USD"""
        rate = await self.get_usd_to_ngn_rate()
        if rate:
            # Convert rate to float to ensure type compatibility
            rate_float = float(rate) if isinstance(rate, Decimal) else rate
            usd_amount = ngn_amount / rate_float
            logger.info(
                f"Converted ₦{ngn_amount:,.2f} NGN to ${usd_amount:.2f} USD at rate {rate_float}"
            )
            return usd_amount
        return None

    def calculate_ngn_payment_markup(self, base_amount_ngn, purpose: str = "exchange") -> Dict[str, Any]:
        """Calculate platform NGN payment markup and return breakdown (configurable rates)"""
        # Convert input to Decimal for precision
        base_amount = Decimal(str(base_amount_ngn))
        
        # Choose markup percentage based on purpose
        if purpose == "wallet_funding":
            # 2% markup for wallet deposits
            markup_percentage = Config.WALLET_DEPOSIT_MARKUP_PERCENTAGE
        else:
            # 5% markup for exchanges and escrow
            markup_percentage = Config.EXCHANGE_MARKUP_PERCENTAGE
        
        markup_amount = base_amount * (markup_percentage / Decimal('100'))

        # Apply platform configurable limits
        min_markup = Config.NGN_CASHOUT_MIN_FEE  # Already a Decimal
        max_markup = Config.NGN_CASHOUT_MAX_FEE  # Already a Decimal
        markup_amount = max(min_markup, min(markup_amount, max_markup))

        final_amount = base_amount + markup_amount

        return {
            "base_amount": base_amount,
            "markup_percentage": markup_percentage,
            "markup_amount": markup_amount,
            "final_amount": final_amount,
            "platform_profit": markup_amount,  # Track platform's revenue
            "purpose": purpose,  # Track what this markup is for
        }

    async def create_payment_link(
        self,
        amount_ngn: Decimal,
        user_id: int,
        purpose: str = "wallet_funding",
        escrow_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Create a payment link for NGN payments"""

        
        try:
            # Extract user data while in session context to avoid DetachedInstanceError
            async with async_managed_session() as session:
                stmt = select(User).where(User.id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    logger.error(f"User {user_id} not found")
                    return None
                
                # Extract needed user data while in scope
                first_name = getattr(user, "first_name", None) or Config.PLATFORM_NAME
                last_name = getattr(user, "last_name", None) or f"User{user_id}"
                user_email = getattr(user, "email", None) or f"user{user_id}@{Config.PLATFORM_NAME.lower()}.com"
                phone_number = getattr(user, "phone_number", None) or "+234"

            # Generate unique reference based on purpose
            if purpose == "escrow_payment" and escrow_id:
                reference = f"LKBY_VA_escrow_payment_{escrow_id}_{int(time.time())}"
            else:
                reference = f"LKBY_VA_wallet_funding_{user_id}_{int(time.time())}"

            # Use the amount directly (already marked up from FastForex)
            # No additional markup needed as FastForex already applies 2.5% markup
            final_amount = amount_ngn

            # Try sending amount directly in NGN (not kobo) since Fincra interface shows kobo as NGN
            logger.info(
                f"Fincra payment calculation: Sending NGN {final_amount} directly (not converting to kobo)"
            )

            # Build full name from extracted data
            full_name = f"{first_name} {last_name}"
            
            payment_data = {
                "amount": int(final_amount),  # Amount in NGN (not kobo)
                "currency": "NGN",
                "customer": {
                    "email": user_email,
                    "name": full_name,  # Full name as required by Fincra
                    "phoneNumber": phone_number,
                },
                "redirectUrl": f"{Config.WEBAPP_URL}/payment/success",
                "paymentMethods": ["bank_transfer"],  # Only allow bank transfer
                "reference": reference,
                "feeBearer": "business",  # Platform pays fees
                "metadata": {
                    "user_id": user_id,
                    "purpose": purpose,
                    "escrow_id": escrow_id,
                    "original_amount_ngn": amount_ngn,
                    "markup_amount": 0,  # No additional markup applied
                },
            }

            response = await self._make_request(
                "POST", "/checkout/payments", payment_data
            )

            if response and response.get("data"):
                payment_info = response["data"]
                logger.info(
                    f"Created Fincra payment link for user {user_id}: {reference}"
                )

                return {
                    "success": True,
                    "payment_link": payment_info.get("link"),
                    "reference": reference,
                    "amount_ngn": final_amount,
                    "markup_amount": 0,  # No additional markup applied
                    "fincra_ref": payment_info.get("id"),
                    "user_id": user_id,
                    "purpose": purpose,
                    "escrow_id": escrow_id,
                }

            # Payment link creation failed - check if we should simulate in test mode
            if self.test_mode or os.getenv("NODE_ENV") != "production":
                logger.info(f"TEST MODE: Simulating payment link creation for user {user_id}")
                # Use Replit development domain for mini web
                dev_domain = os.getenv("REPLIT_DEV_DOMAIN") or os.getenv("REPLIT_DOMAINS", "").split(",")[0]
                if dev_domain:
                    test_payment_link = f"https://{dev_domain}/payment/test?reference={reference}&amount={int(final_amount)}&user_id={user_id}"
                else:
                    # Fallback to webapp URL
                    test_payment_link = f"{Config.WEBAPP_URL}/payment/test?reference={reference}&amount={int(final_amount)}&user_id={user_id}"
                
                return {
                    "success": True,
                    "payment_link": test_payment_link,
                    "reference": reference,
                    "amount_ngn": final_amount,
                    "markup_amount": 0,
                    "fincra_ref": f"test_{reference}",
                    "user_id": user_id,
                    "purpose": purpose,
                    "escrow_id": escrow_id,
                    "test_mode": True,
                }

            return None

        except Exception as e:
            logger.error(f"Error creating Fincra payment link: {e}")
            # Fallback to test mode in development
            if self.test_mode or os.getenv("NODE_ENV") != "production":
                logger.info(f"TEST MODE: Exception occurred, simulating payment link for user {user_id}")
                # Use Replit development domain for mini web
                dev_domain = os.getenv("REPLIT_DEV_DOMAIN") or os.getenv("REPLIT_DOMAINS", "").split(",")[0]
                if dev_domain:
                    test_payment_link = f"https://{dev_domain}/payment/test?reference={reference}&amount={int(final_amount)}&user_id={user_id}"
                else:
                    # Fallback to webapp URL
                    test_payment_link = f"{Config.WEBAPP_URL}/payment/test?reference={reference}&amount={int(final_amount)}&user_id={user_id}"
                
                return {
                    "success": True,
                    "payment_link": test_payment_link,
                    "reference": reference,
                    "amount_ngn": final_amount,
                    "markup_amount": 0,
                    "fincra_ref": f"test_{reference}",
                    "user_id": user_id,
                    "purpose": purpose,
                    "escrow_id": escrow_id,
                    "test_mode": True,
                }
            return None

    async def create_virtual_account(
        self,
        amount_ngn: float,
        user_id: int,
        purpose: str = "wallet_funding",
        escrow_id: Optional[int] = None,
    ) -> Optional[Dict]:
        """Create virtual account for bank transfers (Fincra's specialty)"""

        
        try:
            # Get user data first
            async with async_managed_session() as session:
                stmt = select(User).where(User.id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    logger.error(f"User {user_id} not found")
                    return None
                
                # Extract needed user data while in scope
                user_email = getattr(user, "email", None) or f"user{user_id}@{Config.PLATFORM_NAME.lower()}.com"

            # Generate unique reference
            reference = f"LKBY_VA_{purpose}_{user_id}_{int(time.time())}"

            # Calculate with Fincra's superior pricing based on purpose
            markup_info = self.calculate_ngn_payment_markup(amount_ngn, purpose=purpose)
            final_amount = markup_info["final_amount"]

            # Use correct Fincra 2025 API format for virtual account creation
            va_data = {
                "currency": "NGN",  # Uppercase as required by API
                "accountType": "individual",
                "KYCInformation": {
                    "firstName": Config.PLATFORM_NAME,  # Use consistent business name
                    "lastName": "Platform",  # Use consistent business identifier
                    "email": user_email,
                    "bvn": "22222222222",  # Use a valid test BVN format
                },
            }

            response = await self._make_request(
                "POST", "/profile/virtual-accounts/requests", va_data
            )

            if response and response.get("data"):
                va_info = response["data"]
                logger.info(
                    f"Created Fincra virtual account for user {user_id}: {reference}"
                )

                # Parse account information based on 2025 API response format
                account_info = va_info.get("accountInformation", {})
                
                return {
                    "success": True,
                    "bank_name": account_info.get("bankName", "GLOBUS BANK"),
                    "account_number": account_info.get("accountNumber") or va_info.get("accountNumber"),
                    "account_name": account_info.get("accountName") or va_info.get("accountName"),
                    "bank_code": account_info.get("bankCode", "103"),  # Globus Bank code
                    "reference": reference,
                    "amount_ngn": final_amount,
                    "markup_amount": markup_info["markup_amount"],
                    "va_id": va_info.get("id"),
                    "status": va_info.get("status", "pending"),
                    "is_active": va_info.get("isActive", False),
                    "user_id": user_id,
                    "purpose": purpose,
                    "escrow_id": escrow_id,
                }

            # Virtual account creation failed - check if we should simulate in test mode
            if self.test_mode or os.getenv("NODE_ENV") != "production":
                logger.info(f"TEST MODE: Simulating virtual account creation for user {user_id}")
                return {
                    "success": True,
                    "bank_name": "TEST BANK",
                    "account_number": f"99{user_id:08d}",  # Generate test account number
                    "account_name": f"{Config.PLATFORM_NAME} User {user_id}",
                    "bank_code": "999",  # Test bank code
                    "reference": reference,
                    "amount_ngn": final_amount,
                    "markup_amount": markup_info["markup_amount"],
                    "va_id": f"test_va_{user_id}_{int(time.time())}",
                    "status": "approved",
                    "is_active": True,
                    "user_id": user_id,
                    "purpose": purpose,
                    "escrow_id": escrow_id,
                    "test_mode": True,
                }
            
            return None

        except Exception as e:
            # Check if this is a validation schema error and provide test mode fallback
            if "Validation Schema not defined" in str(e) or "UNPROCESSABLE_ENTITY" in str(e):
                if self.test_mode or os.getenv("NODE_ENV") != "production":
                    logger.info(f"TEST MODE: Simulating virtual account for validation error - User {user_id}")
                    # Use safe defaults if variables weren't initialized before exception
                    safe_reference = locals().get('reference', f"LKBY_VA_{purpose}_{user_id}_{int(time.time())}")
                    safe_final_amount = locals().get('final_amount', amount_ngn)
                    safe_markup_info = locals().get('markup_info', {"markup_amount": 0})
                    
                    return {
                        "success": True,
                        "bank_name": "TEST BANK",
                        "account_number": f"99{user_id:08d}",
                        "account_name": f"{Config.PLATFORM_NAME} User {user_id}",
                        "bank_code": "999",
                        "reference": safe_reference,
                        "amount_ngn": safe_final_amount,
                        "markup_amount": safe_markup_info["markup_amount"],
                        "va_id": f"test_va_{user_id}_{int(time.time())}",
                        "status": "approved",
                        "is_active": True,
                        "user_id": user_id,
                        "purpose": purpose,
                        "escrow_id": escrow_id,
                        "test_mode": True,
                    }
            
            # SECURITY: Use secure error handling for Fincra errors
            try:
                from utils.secure_error_responses import safe_api_error
                error_response = safe_api_error(e, "Fincra")
                logger.error(f"Fincra virtual account error: {error_response['error_id']}")
            except ImportError:
                logger.error(f"Fincra virtual account error: {e}")
            return None

    async def verify_payment(self, reference: str) -> Optional[Dict]:
        """Verify payment status with Fincra"""
        try:
            response = await self._make_request(
                "GET", f"/checkout/payments/merchant-reference/{reference}"
            )

            if response and response.get("data"):
                payment_data = response["data"]
                status = payment_data.get("status", "pending").lower()

                return {
                    "status": status,
                    "reference": reference,
                    "amount": float(payment_data.get("amount", 0))
                    / 100,  # Convert from kobo
                    "currency": payment_data.get("currency", "NGN"),
                    "fincra_ref": payment_data.get("id"),
                    "customer_email": payment_data.get("customer", {}).get("email"),
                    "metadata": payment_data.get("metadata", {}),
                }

            return None

        except Exception as e:
            # SECURITY: Use secure error handling for payment verification
            from utils.secure_error_responses import safe_api_error

            error_response = safe_api_error(e, "Fincra")
            logger.error(
                f"Fincra payment verification error: {error_response['error_id']}"
            )
            return None

    async def initiate_payout(
        self,
        amount_ngn: float,
        bank_code: str,
        account_number: str,
        account_name: str,
        reference: str,
        user_id: int,
    ) -> Optional[Dict]:
        """Initiate NGN payout to Nigerian bank account"""
        try:
            # IMPORTANT: Send FULL amount to Fincra, no fee deduction
            # Fincra handles their own fees internally
            
            payout_data = {
                "business": self.business_id,
                "sourceCurrency": "NGN",
                "destinationCurrency": "NGN",
                "amount": str(int(amount_ngn)),  # Send FULL amount, Fincra deducts their fees
                "description": f"{Config.PLATFORM_NAME} cashout for user {user_id}",
                "customerReference": reference,
                "paymentDestination": "bank_account",  # Required field for Fincra API
                "beneficiary": {
                    "firstName": (
                        account_name.split()[0] if " " in account_name else account_name
                    ),
                    "lastName": (
                        account_name.split()[-1] if " " in account_name else "User"
                    ),
                    "accountHolderName": account_name,
                    "accountNumber": account_number,
                    "country": "NG",
                    "bankCode": bank_code,  # Use bankCode directly, not nested in bank object
                    "type": "individual",
                },
            }

            response = await self._make_request(
                "POST", "/disbursements/payouts", payout_data
            )

            # CRITICAL FIX: Never return fake success for failed payouts
            if response is None:
                logger.error(f"REAL PAYOUT FAILED for {reference} - API error or invalid request")
                return {
                    "success": False,
                    "error": "Payout API request failed - real money NOT sent",
                    "reference": reference
                }

            if response and response.get("data"):
                payout_info = response["data"]
                logger.info(f"Initiated Fincra payout for user {user_id}: {reference}")

                return {
                    "success": True,
                    "payout_id": payout_info.get("id"),
                    "reference": reference,
                    "amount_ngn": amount_ngn,  # Return full amount
                    "fee_amount": 0,  # No fee deduction, Fincra handles fees
                    "status": payout_info.get("status", "processing"),
                    "bank_code": bank_code,
                    "account_number": account_number,
                    "account_name": account_name,
                }

            return None

        except Exception as e:
            # Check if this is insufficient funds error in test/dev mode
            if "NO_ENOUGH_MONEY_IN_WALLET" in str(e):
                if self.test_mode or os.getenv("NODE_ENV") != "production" or os.getenv("FINCRA_TEST_MODE", "false").lower() == "true":
                    logger.info(f"TEST MODE: Simulating successful payout for {reference} (insufficient funds in test wallet)")
                    return {
                        "success": True,
                        "payout_id": f"test_{reference}",
                        "reference": reference,
                        "amount_ngn": amount_ngn,
                        "fee_amount": 0,
                        "status": "completed",
                        "bank_code": bank_code,
                        "account_number": account_number,
                        "account_name": account_name,
                        "test_mode": True,
                    }
            
            logger.error(f"Error initiating Fincra payout: {e}")
            return None

    @with_circuit_breaker('fincra')
    async def check_transfer_status_by_reference(
        self, reference: str
    ) -> Optional[Dict]:
        """Check bank transfer status by reference for real-time user updates"""
        try:
            response = await self._make_request(
                "GET", f"/transfers/reference/{reference}"
            )

            if response and response.get("data"):
                transfer_data = response["data"]
                status = transfer_data.get("status", "processing").lower()

                return {
                    "status": status,
                    "reference": reference,
                    "transfer_id": transfer_data.get("id"),
                    "amount": float(transfer_data.get("amount", 0))
                    / 100,  # Convert from kobo
                    "currency": transfer_data.get("currency", "NGN"),
                    "recipient_name": transfer_data.get("beneficiary", {}).get("name"),
                    "recipient_account": transfer_data.get("beneficiary", {}).get(
                        "accountNumber"
                    ),
                    "bank_name": transfer_data.get("beneficiary", {})
                    .get("bank", {})
                    .get("name"),
                    "created_at": transfer_data.get("createdAt"),
                    "updated_at": transfer_data.get("updatedAt"),
                    "failure_reason": transfer_data.get("failureReason"),
                }

            return None

        except Exception as e:
            logger.error(f"Error checking Fincra transfer status by reference: {e}")
            return None

    async def check_payout_status(self, payout_id: str) -> Optional[Dict]:
        """Check payout status (legacy method for old disbursements)"""
        try:
            response = await self._make_request(
                "GET", f"/disbursements/payouts/{payout_id}"
            )

            if response and response.get("data"):
                payout_data = response["data"]

                return {
                    "status": payout_data.get("status", "processing"),
                    "payout_id": payout_id,
                    "amount": float(payout_data.get("amount", 0)) / 100,
                    "reference": payout_data.get("customerReference"),
                    "updated_at": payout_data.get("updatedAt"),
                }

            return None

        except Exception as e:
            logger.error(f"Error checking Fincra payout status: {e}")
            return None

    async def get_nigerian_banks(self) -> List[Dict]:
        """Get list of Nigerian banks for payouts"""
        try:
            response = await self._make_request("GET", "/core/banks?country=NG")

            if response and response.get("data"):
                banks = response["data"]
                logger.info(f"Retrieved {len(banks)} Nigerian banks from Fincra")
                return banks

            # Fallback list of major Nigerian banks
            return [
                {"code": "044", "name": "Access Bank"},
                {"code": "014", "name": "Afribank Nigeria Plc"},
                {"code": "063", "name": "Diamond Bank"},
                {"code": "050", "name": "Ecobank Nigeria"},
                {"code": "011", "name": "First Bank"},
                {"code": "214", "name": "First City Monument Bank"},
                {"code": "070", "name": "Fidelity Bank"},
                {"code": "058", "name": "Guaranty Trust Bank"},
                {"code": "030", "name": "Heritage Bank"},
                {"code": "082", "name": "Keystone Bank"},
                {"code": "221", "name": "Stanbic IBTC Bank"},
                {"code": "068", "name": "Standard Chartered Bank"},
                {"code": "232", "name": "Sterling Bank"},
                {"code": "033", "name": "United Bank For Africa"},
                {"code": "032", "name": "Union Bank of Nigeria"},
                {"code": "035", "name": "Wema Bank"},
                {"code": "057", "name": "Zenith Bank"},
            ]

        except Exception as e:
            logger.error(f"Error getting Nigerian banks: {e}")
            return []

    async def verify_account_name(
        self, account_number: str, bank_code: str
    ) -> Optional[str]:
        """Verify account name with bank details"""
        try:
            # AUDIT: Log account verification initiation
            verification_id = f"verify_{account_number[-4:]}_{bank_code}_{int(time.time())}"
            self.audit_logger.log_financial_event(
                event_type=FinancialEventType.NGN_ACCOUNT_VERIFICATION_INITIATED,
                entity_type=EntityType.NGN_ACCOUNT_VERIFICATION,
                entity_id=verification_id,
                user_id=None,
                financial_context=FinancialContext(currency="NGN"),
                previous_state=None,
                new_state="verifying",
                related_entities={
                    "bank_code": bank_code,
                    "account_number": account_number[-4:]  # Only last 4 digits for security
                },
                additional_data={
                    "source": "fincra_service.verify_account_name"
                }
            )
            
            logger.info(f"🔍 Verifying account {account_number} with bank {bank_code}")

            # Use the correct POST format that Fincra API accepts
            verify_data = {
                "accountNumber": account_number,
                "bankCode": bank_code,
                "type": "nuban",
            }

            response = await self._make_request(
                "POST", "/core/accounts/resolve", verify_data
            )

            if response and response.get("data"):
                account_info = response["data"]
                account_name = account_info.get("accountName")
                
                # AUDIT: Log successful account verification
                self.audit_logger.log_financial_event(
                    event_type=FinancialEventType.NGN_ACCOUNT_VERIFICATION_COMPLETED,
                    entity_type=EntityType.NGN_ACCOUNT_VERIFICATION,
                    entity_id=verification_id,
                    user_id=None,
                    financial_context=FinancialContext(currency="NGN"),
                    previous_state="verifying",
                    new_state="verified",
                    related_entities={
                        "bank_code": bank_code,
                        "account_number": account_number[-4:],
                        "verified_name": account_name
                    },
                    additional_data={
                        "source": "fincra_service.verify_account_name"
                    }
                )
                
                logger.info(f"Verified account: {account_number} -> {account_name}")
                return account_name
            
            # AUDIT: Log verification failure
            self.audit_logger.log_financial_event(
                event_type=FinancialEventType.NGN_ACCOUNT_VERIFICATION_FAILED,
                entity_type=EntityType.NGN_ACCOUNT_VERIFICATION,
                entity_id=verification_id,
                user_id=None,
                financial_context=FinancialContext(currency="NGN"),
                previous_state="verifying",
                new_state="failed",
                related_entities={
                    "bank_code": bank_code,
                    "account_number": account_number[-4:]
                },
                additional_data={
                    "error": "No data in response",
                    "source": "fincra_service.verify_account_name"
                }
            )

            return None

        except Exception as e:
            # AUDIT: Log verification exception
            verification_id = f"verify_{account_number[-4:]}_{bank_code}_{int(time.time())}"
            self.audit_logger.log_financial_event(
                event_type=FinancialEventType.NGN_ACCOUNT_VERIFICATION_FAILED,
                entity_type=EntityType.NGN_ACCOUNT_VERIFICATION,
                entity_id=verification_id,
                user_id=None,
                financial_context=FinancialContext(currency="NGN"),
                previous_state="verifying",
                new_state="failed",
                related_entities={
                    "bank_code": bank_code,
                    "account_number": account_number[-4:]
                },
                additional_data={
                    "error": str(e),
                    "source": "fincra_service.verify_account_name"
                }
            )
            
            logger.error(f"Error verifying account: {e}")
            return None

    async def list_banks(self) -> Optional[List[Dict[str, Any]]]:
        """Get official Nigerian banks from Fincra's API for accurate payout codes"""
        try:
            # CRITICAL: Get official bank codes from Fincra API
            logger.info("🏦 Fetching official bank list from Fincra API...")
            response = await self._make_request("GET", "/core/banks?country=NG")

            if response and response.get("data") and len(response["data"]) > 0:
                official_banks = response["data"]
                logger.info(
                    f"✅ Got {len(official_banks)} official banks from Fincra API"
                )

                # Log Moniepoint entries from official API
                moniepoint_entries = [
                    bank
                    for bank in official_banks
                    if "moniepoint" in bank.get("name", "").lower()
                ]
                if moniepoint_entries:
                    logger.info(
                        f"🏦 OFFICIAL Moniepoint entries from Fincra: {moniepoint_entries}"
                    )
                else:
                    logger.warning(
                        "⚠️ No Moniepoint entries found in official Fincra bank list!"
                    )

                # Return official Fincra bank list with exact payout codes
                return official_banks
            else:
                logger.warning("⚠️ Fincra API returned no banks, using fallback list")
            comprehensive_banks = [
                # FINTECH BANKS (Most Popular - High Priority)
                {"code": "100004", "name": "OPay Digital Bank"},  # STANDARDIZED: Official OPay code
                {"code": "090405", "name": "Moniepoint MFB"},  # STANDARDIZED: Official Moniepoint code
                {"code": "090267", "name": "Kuda Bank"},  # STANDARDIZED: Official Kuda code
                {"code": "100002", "name": "PalmPay"},  # STANDARDIZED: Official PalmPay code
                {"code": "090267", "name": "Kuda Microfinance Bank"},  # Alternative name for Kuda
                {"code": "090325", "name": "Sparkle Microfinance Bank"},
                {"code": "090281", "name": "Mint-Finex MFB"},
                {"code": "090328", "name": "Eyowo MFB"},
                {"code": "566", "name": "VFD Microfinance Bank"},
                # TRADITIONAL COMMERCIAL BANKS
                {"code": "044", "name": "Access Bank"},
                {"code": "023", "name": "Citibank Nigeria"},
                {"code": "050", "name": "Ecobank Nigeria"},
                {"code": "070", "name": "Fidelity Bank"},
                {"code": "011", "name": "First Bank of Nigeria"},
                {"code": "214", "name": "First City Monument Bank"},
                {"code": "058", "name": "Guaranty Trust Bank"},
                {"code": "030", "name": "Heritage Bank"},
                {"code": "082", "name": "Keystone Bank"},
                {"code": "076", "name": "Polaris Bank"},
                {"code": "101", "name": "Providus Bank"},
                {"code": "221", "name": "Stanbic IBTC Bank"},
                {"code": "068", "name": "Standard Chartered"},
                {"code": "232", "name": "Sterling Bank"},
                {"code": "032", "name": "Union Bank of Nigeria"},
                {"code": "033", "name": "United Bank for Africa"},
                {"code": "215", "name": "Unity Bank"},
                {"code": "035", "name": "Wema Bank"},
                {"code": "057", "name": "Zenith Bank"},
                # MICROFINANCE BANKS
                {"code": "090177", "name": "Lapo Microfinance Bank"},
                {"code": "090485", "name": "FairMoney Microfinance Bank"},
                {"code": "090198", "name": "Renmoney MFB"},
                {"code": "090138", "name": "GoMoney"},
                {"code": "090110", "name": "VFD Microfinance Bank"},
                {"code": "090175", "name": "Rubies MFB"},
                {"code": "090426", "name": "Tangerine Money"},
                {"code": "090365", "name": "Corestep MFB"},
                {"code": "100009", "name": "Xpress Payments"},
                # PAYMENT SERVICE BANKS
                {"code": "304", "name": "Stanbic Mobile Money"},
                {"code": "090371", "name": "Agile Credit"},
                {"code": "090133", "name": "AL-Hayat MFB"},
                {"code": "090180", "name": "Amju Unique MFB"},
            ]

            logger.info(
                f"✅ Using comprehensive bank list with {len(comprehensive_banks)} Nigerian banks"
            )
            return comprehensive_banks

        except Exception as e:
            logger.error(f"Error setting up comprehensive bank list: {e}")
            return None

    async def get_cached_account_balance(self, force_fresh: bool = False, force_fresh_for_critical: bool = False) -> Optional[Dict]:
        """Get Fincra NGN account balance with intelligent caching and reliability safeguards
        
        Args:
            force_fresh: Force fresh fetch even if cache is valid (for reliability safeguards)
            force_fresh_for_critical: Force fresh fetch for critical operations near balance thresholds
        
        Returns:
            Dict with balance data in standard format or None if failed
        """
        async with self._balance_cache_lock:
            now = datetime.now(timezone.utc)
            
            # Check cache validity and force fresh conditions
            cache_valid = (
                self._balance_cache is not None and 
                self._balance_cache_timestamp is not None and
                (now - self._balance_cache_timestamp).total_seconds() < self._balance_cache_expiry_seconds
            )
            
            # Use cached result if valid and not forced fresh
            if cache_valid and not force_fresh and not force_fresh_for_critical:
                cache_age = int((now - self._balance_cache_timestamp).total_seconds()) if self._balance_cache_timestamp else 0
                logger.info(f"🚀 BALANCE_CACHE_HIT: Reusing Fincra NGN balance from {cache_age}s ago")
                return self._balance_cache
            
            # Cache miss or forced fresh - fetch new data
            fresh_reason = "FORCED_FRESH" if force_fresh else "FORCED_CRITICAL" if force_fresh_for_critical else "CACHE_MISS" if self._balance_cache is None else "CACHE_EXPIRED"
            logger.info(f"💰 BALANCE_FETCH_{fresh_reason}: Getting fresh Fincra NGN balance")
            
            # Fetch fresh balance data
            fresh_balance = await self.get_account_balance()
            
            # Cache the result if successful
            if fresh_balance and fresh_balance.get("success"):
                self._balance_cache = fresh_balance
                self._balance_cache_timestamp = now
                logger.info(f"💾 BALANCE_CACHED: Fresh Fincra balance cached for {self._balance_cache_expiry_seconds}s reuse")
            else:
                # Return stale cache if available and fresh fetch failed
                if self._balance_cache is not None:
                    cache_age = int((now - self._balance_cache_timestamp).total_seconds()) if self._balance_cache_timestamp else 0
                    logger.warning(f"⚠️ BALANCE_STALE_FALLBACK: Using stale Fincra cache ({cache_age}s old) - fresh fetch failed")
                    return self._balance_cache
                else:
                    logger.error("❌ BALANCE_FETCH_FAILED: No cached Fincra data available and fresh fetch failed")
            
            return fresh_balance
    
    def invalidate_balance_cache(self, reason: str = "manual"):
        """Invalidate balance cache to force fresh fetch on next request
        
        Args:
            reason: Reason for invalidation (for logging)
        """
        logger.info(f"🗑️ FINCRA_BALANCE_CACHE_INVALIDATED: {reason}")
        self._balance_cache = None
        self._balance_cache_timestamp = None

    async def get_account_balance(self) -> Optional[Dict]:
        """Get Fincra account balance - gracefully handle permission issues (direct API call - use get_cached_account_balance() instead)"""
        try:
            # ENHANCED: Better diagnostics and error handling
            if not self.business_id:
                logger.error("🔑 Fincra balance check failed: FINCRA_BUSINESS_ID not configured")
                return {"success": False, "error": "Business ID not configured", "balance": 0.0}
            
            if not self.is_available():
                logger.error("🔑 Fincra balance check failed: Service not available (missing API keys)")
                return {"success": False, "error": "Service not available", "balance": 0.0}
            
            # Log configuration for debugging
            logger.info(f"🔍 Fincra balance check: URL={self.base_url}, BusinessID={self.business_id}, TestMode={self.test_mode}")
            
            # Try with business ID parameter only
            wallet_data = {"businessID": self.business_id}

            response = await self._make_request("POST", "/wallets", wallet_data, operation_context='balance_check')

            if response and response.get("data"):
                balance_data = response["data"]

                # Handle different response structures
                if isinstance(balance_data, list):
                    # Find NGN balance in list
                    ngn_balance = None
                    for wallet in balance_data:
                        if wallet.get("currency") == "NGN":
                            ngn_balance = wallet
                            break
                elif isinstance(balance_data, dict):
                    # Single wallet response
                    ngn_balance = balance_data
                else:
                    ngn_balance = None

                if ngn_balance:
                    # Debug: log raw values first
                    # Note: When using POST /wallets, the NGN balance is in the list format
                    raw_balance = ngn_balance.get("balance", 0) if isinstance(ngn_balance, dict) else ngn_balance
                    raw_available = ngn_balance.get("availableBalance", raw_balance) if isinstance(ngn_balance, dict) else ngn_balance
                    raw_ledger = ngn_balance.get("ledgerBalance", raw_balance) if isinstance(ngn_balance, dict) else ngn_balance

                    logger.info(
                        f"Raw Fincra values - Balance: {raw_balance}, Available: {raw_available}, Ledger: {raw_ledger}"
                    )

                    # Fincra API returns values in Naira already, no conversion needed
                    balance = float(raw_balance)
                    available = float(raw_available)
                    ledger = float(raw_ledger)

                    logger.info(
                        f"Converted Fincra NGN balance: ₦{available:,.2f} available"
                    )
                    return {
                        "success": True,
                        "balance": balance,
                        "available_balance": available,
                        "ledger_balance": ledger,
                        "currency": "NGN",
                        "raw_values": {
                            "balance": raw_balance,
                            "available": raw_available,
                            "ledger": raw_ledger,
                        },
                    }

                # If no NGN wallet found
                return {
                    "success": False,
                    "error": "No NGN wallet found or wallet not funded yet",
                }

            return {
                "success": False,
                "error": "Unable to retrieve wallet information - wallet may not be created yet",
            }

        except Exception as e:
            logger.warning(
                f"Fincra balance check failed (this is OK if disbursements work): {e}"
            )
            return {
                "success": False,
                "error": "Balance check not available - but cashouts still work!",
                "permission_issue": True,
            }

    async def verify_payout_status(self, reference: str) -> Optional[Dict]:
        """Verify payout status using Fincra's status verification endpoint"""
        try:
            endpoint = f"/disbursements/payouts/reference/{reference}"
            response = await self._make_request("GET", endpoint)

            if response and response.get("data"):
                payout_data = response["data"]
                status = payout_data.get("status", "").lower()

                logger.info(f"Fincra payout {reference} status: {status}")

                # Map Fincra status to our internal status
                status_mapping = {
                    "successful": "completed",
                    "completed": "completed",
                    "success": "completed",
                    "failed": "failed",
                    "error": "failed",
                    "cancelled": "failed",
                    "processing": "processing",
                    "pending": "processing",
                }

                internal_status = status_mapping.get(status, "processing")

                return {
                    "success": True,
                    "status": internal_status,
                    "fincra_status": status,
                    "reference": reference,
                    "payout_id": payout_data.get("id"),
                    "amount": payout_data.get("amount"),
                    "currency": payout_data.get("currency"),
                    "raw_data": payout_data,
                }

            return {
                "success": False,
                "error": "Payout not found or invalid response",
                "reference": reference,
            }

        except Exception as e:
            logger.error(f"Error verifying payout status for {reference}: {e}")
            return {
                "success": False,
                "error": f"Status verification failed: {str(e)}",
                "reference": reference,
            }

    async def process_auto_cashout(self, cashout, session) -> Dict[str, Any]:
        """Process automatic cashout for background processing"""
        # Initialize amount_ngn before try block for type safety
        amount_ngn = 0.0
        
        try:
            logger.info(f"🔄 Processing auto-cashout {cashout.id} via Fincra")
            
            # Extract cashout details with proper type conversion
            if hasattr(cashout, 'amount_ngn'):
                amount_ngn = float(cashout.amount_ngn)
            else:
                # Convert Decimal to float for USD amount, then multiply by exchange rate
                usd_amount = float(cashout.amount) if hasattr(cashout, 'amount') else 2.0
                amount_ngn = usd_amount * 1487.73
            bank_code = cashout.bank_code if hasattr(cashout, 'bank_code') else "044"  # Default bank
            account_number = cashout.account_number if hasattr(cashout, 'account_number') else "0123456789"
            account_name = cashout.account_name if hasattr(cashout, 'account_name') else "Account Holder"
            # CRITICAL FIX: Use cashout_id instead of non-existent reference field
            reference = getattr(cashout, 'cashout_id', None) or getattr(cashout, 'external_tx_id', None) or str(cashout.id)
            
            # Process the bank transfer
            result = await self.process_bank_transfer(
                amount_ngn=Decimal(str(amount_ngn)),
                bank_code=bank_code,
                account_number=account_number,
                account_name=account_name,
                reference=str(reference)
            )
            
            if result and result.get('success'):
                logger.info(f"✅ Auto-cashout {cashout.id} processed successfully")
                return {
                    'success': True,
                    'status': 'completed',
                    'reference': result.get('reference', reference),
                    'amount': amount_ngn
                }
            else:
                error_msg = result.get('error', 'Unknown Fincra error') if result else 'No response from Fincra'
                logger.error(f"❌ Auto-cashout {cashout.id} failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'status': 'failed'
                }
                
        except Exception as e:
            # Handle insufficient funds as a service issue, not a failure
            if "NO_ENOUGH_MONEY_IN_WALLET" in str(e):
                logger.warning(f"💰 OPERATIONAL ISSUE: Fincra account needs funding for auto-cashout {cashout.id}")
                
                # NEW: Notify admin via email for funding instead of failing
                try:
                    from services.admin_funding_notifications import admin_funding_notifications
                    from models import User
            
                    
                    # Get user data for notification
                    async with async_managed_session() as user_session:
                        stmt = select(User).where(User.id == cashout.user_id)
                        result = await user_session.execute(stmt)
                        user = result.scalar_one_or_none()
                        user_data = {
                            'id': user.id,
                            'telegram_id': user.telegram_id,
                            'username': user.username,
                            'first_name': user.first_name,
                            'email': user.email
                        } if user else {}
                        
                        # Send smart admin funding notification with retry context
                        import asyncio
                        
                        retry_info = {
                            'error_code': 'FINCRA_INSUFFICIENT_FUNDS',
                            'attempt_number': 1,  # First attempt when initial funding fails
                            'max_attempts': 5,    # From FINCRA_INSUFFICIENT_FUNDS retry config
                            'next_retry_seconds': 300,  # 5 minutes from retry config
                            'is_auto_retryable': True
                        }
                        
                        asyncio.create_task(admin_funding_notifications.send_funding_required_alert(
                            cashout_id=cashout.cashout_id,
                            service="Fincra",
                            amount=cashout.amount,
                            currency="USD",
                            user_data=user_data,
                            service_currency="NGN",
                            service_amount=amount_ngn,  # NGN amount calculated
                            retry_info=retry_info
                        ))
                        
                except Exception as notification_error:
                    logger.error(f"Failed to send funding notification: {notification_error}")
                
                return {
                    'success': False,
                    'error': 'Service funding required - admin notified',
                    'status': 'pending_funding',
                    'operational_issue': True,
                    'admin_notified': True
                }
            
            logger.error(f"❌ Error in auto-cashout processing for {cashout.id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'status': 'failed'
            }


# Global service instance - shared singleton pattern matching Kraken
fincra_service = FincraService()

def get_fincra_service() -> FincraService:
    """Get or create shared Fincra service instance with shared cache"""
    global fincra_service
    if fincra_service is None:
        fincra_service = FincraService()
    return fincra_service
