"""
Address Selector Component

Handles cryptocurrency address input and selection from saved addresses.
Supports address validation, network detection, and QR code scanning.
"""

import logging
import re
from typing import Dict, Any, Optional, List

from telegram import Update
from telegram.ext import ContextTypes

from services.scene_engine import SceneState, ComponentConfig
from database import SessionLocal
from models import SavedAddress

logger = logging.getLogger(__name__)

class AddressSelectorComponent:
    """Component for handling cryptocurrency address selection"""
    
    async def process_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        scene_state: SceneState,
        component_config: ComponentConfig
    ) -> Optional[Dict[str, Any]]:
        """Process address selection message"""
        config = component_config.config
        
        # Handle callback queries (saved address selection)
        if update.callback_query:
            return await self._process_callback(update, scene_state, config)
        
        # Handle text messages (manual address entry)
        if update.message and update.message.text:
            return await self._process_text(update, scene_state, config)
        
        return None
    
    async def _process_callback(
        self, 
        update: Update, 
        scene_state: SceneState, 
        config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Process callback query for saved address selection"""
        query = update.callback_query
        if not query or not query.data:
            return None
        
        # Handle saved address selection
        if query.data.startswith('address_'):
            try:
                index = int(query.data.replace('address_', ''))
                saved_addresses = scene_state.data.get('saved_addresses', [])
                
                if 0 <= index < len(saved_addresses):
                    selected = saved_addresses[index]
                    
                    # Validate selected address
                    validation = await self._validate_address(
                        selected['address'], 
                        config.get('crypto', 'BTC'),
                        config.get('network', 'mainnet')
                    )
                    
                    if validation['valid']:
                        return {
                            'success': True,
                            'data': {
                                'address': selected['address'],
                                'label': selected['label'],
                                'source': 'saved',
                                'validation': validation
                            }
                        }
                    else:
                        return {
                            'success': False,
                            'error': f"Saved address is invalid: {validation['error']}"
                        }
                
            except (ValueError, IndexError) as e:
                logger.error(f"Invalid saved address selection: {e}")
                return {
                    'success': False,
                    'error': "Invalid address selection"
                }
        
        # Handle new address input trigger
        elif query.data == 'new_address':
            # This just triggers the manual input flow
            return {
                'success': False,
                'message': "Please enter the cryptocurrency address:"
            }
        
        return None
    
    async def _process_text(
        self, 
        update: Update, 
        scene_state: SceneState, 
        config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Process text message for manual address entry"""
        address = update.message.text.strip()
        
        # Basic address format validation
        if not address:
            return {
                'success': False,
                'error': "Please enter a valid cryptocurrency address"
            }
        
        crypto = config.get('crypto', 'BTC')
        network = config.get('network', 'mainnet')
        
        # Validate address format
        validation = await self._validate_address(address, crypto, network)
        if not validation['valid']:
            return {
                'success': False,
                'error': validation['error']
            }
        
        # Check if user wants to save this address
        save_option = config.get('allow_save', True)
        
        return {
            'success': True,
            'data': {
                'address': address,
                'crypto': crypto,
                'network': network,
                'source': 'manual',
                'validation': validation,
                'can_save': save_option
            }
        }
    
    async def _validate_address(
        self, 
        address: str, 
        crypto: str, 
        network: str
    ) -> Dict[str, Any]:
        """Validate cryptocurrency address format"""
        try:
            # Clean address
            address = address.strip()
            
            # Basic length and character checks
            if len(address) < 20:
                return {
                    'valid': False,
                    'error': f"Address too short for {crypto}"
                }
            
            if len(address) > 100:
                return {
                    'valid': False,
                    'error': f"Address too long for {crypto}"
                }
            
            # Cryptocurrency-specific validation
            if crypto == 'BTC':
                return await self._validate_bitcoin_address(address, network)
            elif crypto == 'ETH':
                return await self._validate_ethereum_address(address)
            elif crypto in ['USDT', 'USDC']:
                # These typically use Ethereum or Tron addresses
                return await self._validate_token_address(address, crypto)
            elif crypto == 'LTC':
                return await self._validate_litecoin_address(address, network)
            else:
                # Generic validation for other cryptocurrencies
                return await self._validate_generic_address(address, crypto)
        
        except Exception as e:
            logger.error(f"Address validation error: {e}")
            return {
                'valid': False,
                'error': f"Address validation failed: {e}"
            }
    
    async def _validate_bitcoin_address(self, address: str, network: str) -> Dict[str, Any]:
        """Validate Bitcoin address format"""
        # Legacy addresses (1...)
        if address.startswith('1') and 25 <= len(address) <= 34:
            if re.match(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$', address):
                return {
                    'valid': True,
                    'type': 'legacy',
                    'network': network
                }
        
        # P2SH addresses (3...)
        elif address.startswith('3') and 25 <= len(address) <= 34:
            if re.match(r'^3[a-km-zA-HJ-NP-Z1-9]{25,34}$', address):
                return {
                    'valid': True,
                    'type': 'p2sh',
                    'network': network
                }
        
        # Bech32 addresses (bc1...)
        elif address.startswith('bc1') and len(address) >= 39:
            if re.match(r'^bc1[a-z0-9]{39,59}$', address.lower()):
                return {
                    'valid': True,
                    'type': 'bech32',
                    'network': network
                }
        
        # Testnet addresses
        elif network == 'testnet':
            if (address.startswith('m') or address.startswith('n') or 
                address.startswith('2') or address.startswith('tb1')):
                return {
                    'valid': True,
                    'type': 'testnet',
                    'network': 'testnet'
                }
        
        return {
            'valid': False,
            'error': "Invalid Bitcoin address format. Expected format starts with 1, 3, or bc1"
        }
    
    async def _validate_ethereum_address(self, address: str) -> Dict[str, Any]:
        """Validate Ethereum address format"""
        # Remove 0x prefix if present
        if address.startswith('0x'):
            address = address[2:]
        
        # Check length (40 hex characters)
        if len(address) != 40:
            return {
                'valid': False,
                'error': "Ethereum address must be 40 characters (42 with 0x prefix)"
            }
        
        # Check hex format
        if not re.match(r'^[a-fA-F0-9]{40}$', address):
            return {
                'valid': False,
                'error': "Ethereum address must contain only hexadecimal characters"
            }
        
        return {
            'valid': True,
            'type': 'ethereum',
            'formatted': f"0x{address}",
            'network': 'ethereum'
        }
    
    async def _validate_token_address(self, address: str, crypto: str) -> Dict[str, Any]:
        """Validate token address (USDT/USDC on various networks)"""
        # Try Ethereum format first
        eth_validation = await self._validate_ethereum_address(address)
        if eth_validation['valid']:
            return {
                'valid': True,
                'type': f'{crypto.lower()}_ethereum',
                'formatted': eth_validation['formatted'],
                'network': 'ethereum'
            }
        
        # Try Tron format (TRC20)
        if address.startswith('T') and len(address) == 34:
            if re.match(r'^T[a-km-zA-HJ-NP-Z1-9]{33}$', address):
                return {
                    'valid': True,
                    'type': f'{crypto.lower()}_tron',
                    'formatted': address,
                    'network': 'tron'
                }
        
        return {
            'valid': False,
            'error': f"Invalid {crypto} address. Supported networks: Ethereum (0x...) or Tron (T...)"
        }
    
    async def _validate_litecoin_address(self, address: str, network: str) -> Dict[str, Any]:
        """Validate Litecoin address format"""
        # Legacy addresses (L... or M...)
        if (address.startswith('L') or address.startswith('M')) and 26 <= len(address) <= 34:
            if re.match(r'^[LM][a-km-zA-HJ-NP-Z1-9]{25,34}$', address):
                return {
                    'valid': True,
                    'type': 'legacy',
                    'network': network
                }
        
        # Bech32 addresses (ltc1...)
        elif address.startswith('ltc1') and len(address) >= 39:
            if re.match(r'^ltc1[a-z0-9]{39,59}$', address.lower()):
                return {
                    'valid': True,
                    'type': 'bech32',
                    'network': network
                }
        
        return {
            'valid': False,
            'error': "Invalid Litecoin address format. Expected format starts with L, M, or ltc1"
        }
    
    async def _validate_generic_address(self, address: str, crypto: str) -> Dict[str, Any]:
        """Generic validation for other cryptocurrencies"""
        # Basic sanity checks
        if not re.match(r'^[a-zA-Z0-9]+$', address):
            return {
                'valid': False,
                'error': f"Invalid {crypto} address format. Only alphanumeric characters allowed"
            }
        
        return {
            'valid': True,
            'type': 'generic',
            'network': 'unknown',
            'warning': f"Address format not specifically validated for {crypto}"
        }
    
    async def _load_saved_addresses(self, user_id: int, crypto: str) -> List[Dict[str, Any]]:
        """Load saved addresses for a user and cryptocurrency - Enhanced with active filtering and proper ordering"""
        try:
            session = SessionLocal()
            try:
                addresses = (
                    session.query(SavedAddress)
                    .filter(
                        SavedAddress.user_id == user_id,
                        SavedAddress.currency == crypto,
                        SavedAddress.is_active == True  # Only show active addresses
                    )
                    .order_by(
                        SavedAddress.last_used.desc().nullslast(),  # Most used first  
                        SavedAddress.created_at.desc()  # Then by creation time
                    )
                    .limit(5)  # Limit to 5 most relevant
                    .all()
                )
                
                return [
                    {
                        'id': addr.id,
                        'address': addr.address,
                        'label': addr.label or f"{crypto} Address",
                        'network': getattr(addr, 'network', 'mainnet')
                    }
                    for addr in addresses
                ]
            
            finally:
                session.close()
        
        except Exception as e:
            logger.error(f"Error loading saved addresses: {e}")
            return []