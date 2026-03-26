"""Enhanced Kraken withdrawal service with proper data structure handling"""

import logging
from typing import Dict, List, Any, Tuple
from decimal import Decimal
from services.kraken_service import get_kraken_service

logger = logging.getLogger(__name__)


class KrakenWithdrawalService:
    """High-level Kraken withdrawal service for LockBay integration"""
    
    def __init__(self):
        self.kraken = get_kraken_service()
        logger.info("🦑 Kraken withdrawal service initialized")
    
    def map_currency_network_to_asset_method(self, currency: str, network: str = None) -> Tuple[str, str]:
        """Map currency and network to Kraken asset and method - CRITICAL FIX for key validation"""
        try:
            currency = currency.upper()
            network = network.upper() if network else None
            
            # Explicit mappings following architect's specifications
            if currency == 'USDT' and network == 'TRC20':
                return ('USDT', 'Tether USD (TRC20)')
            elif currency == 'USDT' and network == 'ERC20':
                return ('USDT', 'Tether USD (ERC20)')
            elif currency == 'USDT':
                # Default to TRC20 for USDT if no network specified
                return ('USDT', 'Tether USD (TRC20)')
            elif currency == 'BTC':
                return ('XXBT', 'Bitcoin')  # Try XXBT first
            elif currency == 'ETH':
                return ('XETH', 'Ether')
            elif currency == 'LTC':
                return ('XLTC', 'Litecoin')
            elif currency == 'DOGE':
                return ('XXDG', 'Dogecoin')
            elif currency == 'TRX':
                return ('TRX', 'Tron')
            else:
                # Fallback mapping
                return (currency, f"{currency} withdrawal")
                
        except Exception as e:
            logger.error(f"❌ Error mapping currency {currency}/{network}: {str(e)}")
            return (currency, f"{currency} withdrawal")
    
    def get_asset_code_variants(self, currency: str) -> List[str]:
        """CRITICAL FIX: Get all possible asset code variants to handle Kraken's inconsistent naming"""
        currency = currency.upper()
        
        # Asset code variants for different currencies
        variants = {
            'BTC': ['XXBT', 'XBT'],  # Both forms used by Kraken
            'ETH': ['XETH', 'ETH'],  
            'LTC': ['XLTC', 'LTC'],
            'DOGE': ['XXDG', 'DOGE'],
            'USDT': ['USDT'],
            'TRX': ['TRX']
        }
        
        return variants.get(currency, [currency])

    async def resolve_withdraw_key(self, currency: str, network: str = None, address: str = None) -> Dict[str, Any]:
        """CRITICAL FIX: Resolve withdrawal key with mandatory validation - NO WITHDRAW without verified key"""
        try:
            logger.info(f"🔍 CRITICAL_VALIDATION: Resolving withdraw key for {currency}/{network} address {address[:10] if address else 'none'}...")
            
            # CRITICAL FIX: Try all asset code variants to handle Kraken's inconsistent naming
            asset_variants = self.get_asset_code_variants(currency)
            addresses = []
            successful_asset = None
            
            for asset_variant in asset_variants:
                try:
                    logger.info(f"🔍 Trying asset variant: {asset_variant}")
                    variant_addresses = await self.kraken.get_withdrawal_addresses(asset=asset_variant)
                    if variant_addresses:
                        addresses = variant_addresses
                        successful_asset = asset_variant
                        logger.info(f"✅ Found {len(addresses)} addresses for {asset_variant}")
                        break
                except Exception as e:
                    logger.warning(f"⚠️ Asset variant {asset_variant} failed: {str(e)}")
                    continue
            
            if not addresses and not successful_asset:
                logger.error(f"❌ Failed to fetch addresses for any {currency} variant: {asset_variants}")
                return {
                    'success': False,
                    'error': f'Could not fetch withdrawal addresses for {currency}',
                    'error_type': 'api_error',
                    'actionable_message': 'Unable to validate withdrawal addresses. Please check your Kraken account configuration.',
                    'setup_instructions': {
                        'step1': 'Login to your Kraken account',
                        'step2': 'Go to Funding > Withdraw',
                        'step3': f'Configure {currency} withdrawal addresses',
                        'step4': 'Complete email/SMS verification',
                        'step5': 'Retry the withdrawal'
                    }
                }
            
            # Use the successful asset variant for the rest of the validation
            asset = successful_asset
            method = f"{currency} withdrawal"
            
            # If specific address provided, find matching address
            if address:
                logger.info(f"🔍 Searching for address: {address}")
                for addr_data in addresses:
                    stored_address = addr_data.get('address', '').strip()
                    if stored_address.lower() == address.lower():
                        key = addr_data.get('key', '')
                        is_verified = addr_data.get('verified', False)
                        
                        if not is_verified:
                            logger.error(f"❌ Address found but NOT VERIFIED: {address[:10]}...")
                            return {
                                'success': False,
                                'error': f'Address {address} is not verified in Kraken account',
                                'error_type': 'address_not_verified',
                                'actionable_message': f'The address {address} exists in your Kraken account but requires verification. Please complete the verification process.',
                                'setup_instructions': {
                                    'step1': 'Login to your Kraken account',
                                    'step2': 'Go to Funding > Withdraw',
                                    'step3': f'Find address {address}',
                                    'step4': 'Complete email/SMS verification for this address',
                                    'step5': 'Retry the withdrawal'
                                }
                            }
                        
                        logger.info(f"✅ VALIDATION_SUCCESS: Key '{key}' resolved for verified address {address[:10]}...")
                        return {
                            'success': True,
                            'key': key,
                            'verified': True,
                            'address': stored_address,
                            'asset': asset,
                            'method': method
                        }
                
                # Address not found in configured addresses
                logger.error(f"❌ Address NOT CONFIGURED: {address[:10]}...")
                return {
                    'success': False,
                    'error': f'Address {address} is not configured in Kraken account for {currency}',
                    'error_type': 'address_not_configured',
                    'actionable_message': f'The address {address} is not configured in your Kraken account. Please add and verify it first.',
                    'setup_instructions': {
                        'step1': 'Login to your Kraken account',
                        'step2': 'Go to Funding > Withdraw',
                        'step3': f'Select {currency}',
                        'step4': f'Add new address: {address}',
                        'step5': 'Complete email/SMS verification',
                        'step6': 'Retry the withdrawal'
                    }
                }
            else:
                # No specific address - return first verified address key if available
                verified_addresses = [addr for addr in addresses if addr.get('verified', False)]
                if verified_addresses:
                    first_verified = verified_addresses[0]
                    logger.info(f"✅ Using first verified address key: {first_verified.get('key')}")
                    return {
                        'success': True,
                        'key': first_verified.get('key'),
                        'verified': True,
                        'address': first_verified.get('address'),
                        'asset': asset,
                        'method': method
                    }
                else:
                    logger.error(f"❌ No verified addresses configured for {currency}")
                    return {
                        'success': False,
                        'error': f'No verified withdrawal addresses configured for {currency}',
                        'error_type': 'no_verified_addresses',
                        'actionable_message': f'You need to configure and verify at least one {currency} withdrawal address in your Kraken account.',
                        'setup_instructions': {
                            'step1': 'Login to your Kraken account',
                            'step2': 'Go to Funding > Withdraw',
                            'step3': f'Select {currency}',
                            'step4': 'Add a withdrawal address',
                            'step5': 'Complete email/SMS verification',
                            'step6': 'Retry the withdrawal'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"❌ CRITICAL_ERROR: Key resolution failed for {currency}: {str(e)}")
            return {
                'success': False,
                'error': f'Unable to resolve withdrawal key: {str(e)}',
                'error_type': 'validation_failed',
                'actionable_message': 'Could not validate withdrawal configuration due to system error. Please check your Kraken account.',
                'setup_instructions': {
                    'step1': 'Login to your Kraken account',
                    'step2': 'Verify your withdrawal addresses are properly configured',
                    'step3': 'Ensure addresses are verified',
                    'step4': 'Contact support if the issue persists'
                }
            }

    async def get_supported_currencies(self) -> List[str]:
        """Get list of cryptocurrencies supported for withdrawal"""
        try:
            # Use hardcoded list since we need specific asset support
            supported = ['BTC', 'ETH', 'USDT', 'LTC', 'DOGE', 'TRX']
            logger.info(f"✅ Kraken supports {len(supported)} crypto withdrawals: {supported}")
            return supported
            
        except Exception as e:
            logger.error(f"❌ Failed to get supported currencies: {str(e)}")
            return ['BTC', 'ETH', 'USDT', 'LTC']  # Fallback list
    
    async def get_withdrawal_addresses_for_currency(self, currency: str) -> List[Dict[str, str]]:
        """Get available withdrawal addresses for a specific currency - OPTIMIZED to query only needed assets"""
        try:
            logger.info(f"📍 Getting withdrawal addresses for {currency}...")
            
            # Map currency to Kraken asset codes BEFORE API call
            kraken_currency_map = {
                'BTC': ['XXBT', 'XBT'],  # Kraken uses both XBT and XXBT for Bitcoin
                'ETH': ['XETH'], 
                'LTC': ['XLTC'],
                'DOGE': ['XXDG'],
                'USDT': ['USDT'],  # USDT (generic - matches both ERC20 and TRC20)
                'USDT-ERC20': ['USDT'],  # USDT on Ethereum 
                'USDT-TRC20': ['USDT'],  # USDT on Tron
                'TRX': ['TRX'],  # Tron native token
                # 'XMR': ['XMR']  # Removed: FastForex API doesn't support XMR
            }
            
            valid_assets = kraken_currency_map.get(currency.upper(), [currency.upper()])
            
            # CRITICAL OPTIMIZATION: Only query addresses for the specific assets needed
            # This reduces API calls from 20+ to ~2 for most currencies
            addresses = await self.kraken.get_all_withdrawal_addresses(assets=valid_assets)
            
            # Post-filter to ensure addresses match the expected currency/asset
            # This protects against Kraken returning unexpected variants (e.g., ETH2, alt networks)
            currency_addresses = []
            for address in addresses:
                asset = address.get('asset', '').upper()
                # Validate asset is in our expected list for this currency
                if asset in valid_assets or asset == currency.upper():
                    currency_addresses.append({
                        'key': address.get('key', ''),
                        'address': address.get('address', ''),
                        'method': address.get('method', ''),
                        'verified': address.get('verified', False)
                    })
            
            logger.info(f"✅ Found {len(currency_addresses)} addresses for {currency} (optimized query with validation)")
            return currency_addresses
            
        except Exception as e:
            logger.error(f"❌ Failed to get addresses for {currency}: {str(e)}")
            return []
    
    async def estimate_withdrawal_fee(self, currency: str, amount: Decimal, address_key: str = None) -> Dict[str, Any]:
        """Estimate withdrawal fee for a specific amount and currency"""
        try:
            # Handle cases where address_key is not provided or addresses aren't configured
            if not address_key:
                logger.info(f"⚠️ No address key provided for {currency} fee estimation")
                raise Exception(f"Kraken account needs withdrawal addresses configured for {currency}")
            
            # Map standard currency to Kraken format - try XBT first as it's more common
            kraken_currency_map = {
                'BTC': 'XBT',  # Kraken primarily uses XBT for Bitcoin
                'ETH': 'XETH',
                'LTC': 'XLTC', 
                'DOGE': 'XXDG',
                'USDT': 'USDT',
                'USDT-ERC20': 'USDT',  # Both USDT variants use same asset code
                'USDT-TRC20': 'USDT',
                'TRX': 'TRX',
                # 'XMR': 'XMR'  # Removed: FastForex API doesn't support XMR
            }
            
            kraken_asset = kraken_currency_map.get(currency.upper(), currency.upper())
            
            # FIXED: Round amount to 8 decimal places before sending to Kraken API
            rounded_amount = amount.quantize(Decimal('0.00000001'))
            
            info = await self.kraken.get_withdrawal_info(
                asset=kraken_asset,
                key=address_key,
                amount=str(rounded_amount)
            )
            
            return {
                'success': True,
                'fee': Decimal(info.get('fee', '0')),
                'amount_after_fee': Decimal(info.get('amount', str(amount))),
                'method': info.get('method', 'Unknown'),
                'limit': Decimal(info.get('limit', str(amount)))
            }
            
        except Exception as e:
            error_msg = str(e)
            if "address" in error_msg.lower() or "configured" in error_msg.lower():
                logger.warning(f"⚠️ {currency} withdrawal addresses not configured in Kraken - Will use fallback fees")
            else:
                logger.error(f"❌ Fee estimation failed for {currency}: {error_msg}")
            
            return {
                'success': False,
                'error': error_msg
            }
    
    async def execute_withdrawal(self, currency: str, amount: Decimal, address: str = None, network: str = None,
                                session=None, cashout_id: str = None, transaction_id: str = None) -> Dict[str, Any]:
        """MANDATORY VALIDATION: Execute withdrawal with complete key validation - NO WITHDRAW without verified key"""
        try:
            logger.info(f"💸 CRITICAL_FLOW: Starting withdrawal {amount} {currency} to {address[:10] if address else 'none'}...")
            
            # MANDATORY STEP 1: Resolve withdrawal key with full validation
            key_resolution = await self.resolve_withdraw_key(currency, network, address)
            
            if not key_resolution.get('success'):
                # CRITICAL: DO NOT PROCEED if key resolution fails
                logger.error("❌ VALIDATION_BLOCKED: Key resolution failed, ABORTING withdrawal")
                return {
                    'success': False,
                    'error': key_resolution.get('error'),
                    'error_type': key_resolution.get('error_type'),
                    'actionable_message': key_resolution.get('actionable_message'),
                    'setup_instructions': key_resolution.get('setup_instructions'),
                    'provider': 'kraken'
                }
            
            # MANDATORY STEP 2: Extract validated key and asset info
            validated_key = key_resolution.get('key')
            kraken_asset = key_resolution.get('asset')
            is_verified = key_resolution.get('verified')
            
            if not validated_key or not is_verified:
                logger.error("❌ VALIDATION_FAILED: Invalid key or unverified address")
                return {
                    'success': False,
                    'error': 'Withdrawal key validation failed',
                    'error_type': 'validation_failed',
                    'actionable_message': 'Could not validate withdrawal address. Please check your Kraken account configuration.',
                    'provider': 'kraken'
                }
            
            logger.info(f"✅ VALIDATION_PASSED: Using verified key '{validated_key}' for {kraken_asset}")
            
            # MANDATORY STEP 3: Execute withdrawal with validated key (bypass low-level validation since we already validated)
            try:
                result = await self.kraken.withdraw(
                    asset=kraken_asset,
                    key=validated_key, 
                    amount=str(amount.quantize(Decimal('0.00000001'))),
                    _bypass_validation=True,  # Safe to bypass since we already validated above
                    session=session,
                    cashout_id=cashout_id,
                    transaction_id=transaction_id
                )
                
                if not result.get('success'):
                    # Handle withdrawal failure
                    logger.error(f"❌ Kraken withdrawal failed: {result.get('error')}")
                    return {
                        'success': False,
                        'error': result.get('error'),
                        'error_type': 'kraken_api_error',
                        'actionable_message': f"Kraken API error: {result.get('error')}",
                        'provider': 'kraken'
                    }
                
                withdrawal_id = result.get('withdrawal_id', 'unknown')
                logger.info(f"✅ WITHDRAWAL_SUCCESS: Kraken withdrawal submitted - ID: {withdrawal_id}")
                
                return {
                    'success': True,
                    'withdrawal_id': withdrawal_id,
                    'provider': 'kraken',
                    'currency': currency,
                    'amount': amount,
                    'txid': withdrawal_id,
                    'key_used': validated_key,
                    'asset_used': kraken_asset
                }
                
            except Exception as withdrawal_error:
                error_msg = str(withdrawal_error)
                
                # Map Kraken-specific errors to structured error types
                if 'EFunding:Insufficient funds' in error_msg:
                    return {
                        'success': False,
                        'error': 'Insufficient funds in Kraken account',
                        'error_type': 'insufficient_funds',
                        'actionable_message': f'Your Kraken account does not have enough {currency} balance for this withdrawal.',
                        'provider': 'kraken'
                    }
                elif 'EFunding:Unknown withdraw key' in error_msg:
                    # This should be impossible now with proper validation
                    logger.error("🚨 IMPOSSIBLE_ERROR: Unknown withdraw key despite validation!")
                    return {
                        'success': False,
                        'error': 'Withdrawal key validation bypass detected',
                        'error_type': 'validation_bypass',
                        'actionable_message': 'Critical error in withdrawal validation. Please contact support.',
                        'provider': 'kraken'
                    }
                else:
                    return {
                        'success': False,
                        'error': error_msg,
                        'error_type': 'kraken_api_error',
                        'actionable_message': f'Kraken API error: {error_msg}',
                        'provider': 'kraken'
                    }
                    
        except Exception as e:
            logger.error(f"❌ CRITICAL_ERROR: Withdrawal execution error: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'error_type': 'execution_error',
                'actionable_message': f'Withdrawal could not be executed: {str(e)}',
                'provider': 'kraken'
            }
    
    async def check_withdrawal_status(self, withdrawal_id: str, currency: str = None) -> Dict[str, Any]:
        """Check status of a Kraken withdrawal"""
        try:
            status_data = await self.kraken.get_withdrawal_status(asset=currency)
            
            # Find our specific withdrawal in the status list
            for withdrawal in status_data:
                if withdrawal.get('refid') == withdrawal_id:
                    return {
                        'success': True,
                        'status': withdrawal.get('status', 'unknown'),
                        'withdrawal': withdrawal
                    }
            
            return {
                'success': False,
                'error': f'Withdrawal {withdrawal_id} not found'
            }
            
        except Exception as e:
            logger.error(f"❌ Status check failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }


# Global instance
kraken_withdrawal_service = None

def get_kraken_withdrawal_service() -> KrakenWithdrawalService:
    """Get or create Kraken withdrawal service instance"""
    global kraken_withdrawal_service
    if kraken_withdrawal_service is None:
        kraken_withdrawal_service = KrakenWithdrawalService()
    return kraken_withdrawal_service

async def mark_crypto_address_verified(user_id: int, address: str, session):
    """VERIFICATION FIX: Helper function to mark crypto address as verified"""
    try:
        from models import SavedAddress
        crypto_address = session.query(SavedAddress).filter_by(
            user_id=user_id,
            address=address
        ).first()
        
        if crypto_address and not crypto_address.is_verified:
            crypto_address.is_verified = True
            session.commit()
            logger.info(f"✅ VERIFICATION_UPDATE: Crypto address {address[:12]}... marked as verified for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error marking crypto address as verified: {e}")
        # Don't fail the main process for verification errors