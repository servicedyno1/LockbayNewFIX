"""
Amount Input Component

Handles amount entry with validation, currency conversion, and quick amount buttons.
Supports decimal precision, min/max validation, and real-time rate conversion.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional

from telegram import Update
from telegram.ext import ContextTypes

from services.scene_engine import SceneState, ComponentConfig
from utils.decimal_precision import MonetaryDecimal

logger = logging.getLogger(__name__)

class AmountInputComponent:
    """Component for handling amount input with validation"""
    
    async def process_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        scene_state: SceneState,
        component_config: ComponentConfig
    ) -> Optional[Dict[str, Any]]:
        """Process amount input message"""
        config = component_config.config
        
        # Handle callback queries (quick amount buttons)
        if update.callback_query:
            return await self._process_callback(update, scene_state, config)
        
        # Handle text messages (manual amount entry)
        if update.message and update.message.text:
            return await self._process_text(update, scene_state, config)
        
        return None
    
    async def _process_callback(
        self, 
        update: Update, 
        scene_state: SceneState, 
        config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Process callback query for quick amounts"""
        query = update.callback_query
        if not query or not query.data:
            return None
        
        # Parse amount from callback data
        if query.data.startswith('amount_'):
            try:
                amount_str = query.data.replace('amount_', '')
                amount = Decimal(amount_str)
                
                # Validate amount
                validation_result = await self._validate_amount(amount, config)
                if not validation_result['valid']:
                    return {
                        'success': False,
                        'error': validation_result['error']
                    }
                
                # Success - return validated amount
                return {
                    'success': True,
                    'data': {
                        'amount': str(amount),
                        'currency': config.get('currency', 'USD'),
                        'validation': validation_result
                    }
                }
                
            except (ValueError, InvalidOperation) as e:
                logger.error(f"Invalid amount in callback: {e}")
                return {
                    'success': False,
                    'error': "Invalid amount selected"
                }
        
        return None
    
    async def _process_text(
        self, 
        update: Update, 
        scene_state: SceneState, 
        config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Process text message for manual amount entry"""
        text = update.message.text.strip()
        
        # Clean and parse amount
        amount_str = self._clean_amount_string(text)
        if not amount_str:
            return {
                'success': False,
                'error': "Please enter a valid amount (numbers only)"
            }
        
        try:
            amount = Decimal(amount_str)
            
            # Validate amount
            validation_result = await self._validate_amount(amount, config)
            if not validation_result['valid']:
                return {
                    'success': False,
                    'error': validation_result['error']
                }
            
            # Apply currency conversion if needed
            converted_amount = await self._apply_currency_conversion(amount, config)
            
            # Success - return validated amount
            return {
                'success': True,
                'data': {
                    'amount': str(amount),
                    'converted_amount': str(converted_amount) if converted_amount != amount else None,
                    'currency': config.get('currency', 'USD'),
                    'validation': validation_result
                }
            }
            
        except (ValueError, InvalidOperation) as e:
            logger.error(f"Invalid amount format: {e}")
            return {
                'success': False,
                'error': "Invalid amount format. Please enter a number (e.g., 100 or 100.50)"
            }
    
    def _clean_amount_string(self, text: str) -> Optional[str]:
        """Clean and extract amount from text"""
        # Remove currency symbols and common prefixes
        text = re.sub(r'[$€£¥₦]', '', text)
        text = re.sub(r'[,\s]', '', text)
        
        # Extract decimal number
        match = re.search(r'\d+\.?\d*', text)
        return match.group() if match else None
    
    async def _validate_amount(self, amount: Decimal, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate amount against configured rules"""
        min_amount = Decimal(str(config.get('min_amount', 0.01)))
        max_amount = Decimal(str(config.get('max_amount', 10000)))
        currency = config.get('currency', 'USD')
        
        # Check minimum
        if amount < min_amount:
            return {
                'valid': False,
                'error': f"Amount must be at least ${min_amount} {currency}"
            }
        
        # Check maximum
        if amount > max_amount:
            return {
                'valid': False,
                'error': f"Amount cannot exceed ${max_amount} {currency}"
            }
        
        # Check decimal places based on currency
        max_decimals = 2  # Default for fiat currencies
        if currency in ['BTC', 'ETH']:
            max_decimals = 8
        elif currency in ['USDT', 'USDC']:
            max_decimals = 6
        
        decimal_places = len(str(amount).split('.')[-1]) if '.' in str(amount) else 0
        if decimal_places > max_decimals:
            return {
                'valid': False,
                'error': f"Maximum {max_decimals} decimal places allowed for {currency}"
            }
        
        # Additional validation rules
        if config.get('require_whole_numbers') and amount != amount.to_integral_value():
            return {
                'valid': False,
                'error': "Please enter a whole number (no decimals)"
            }
        
        return {
            'valid': True,
            'formatted_amount': MonetaryDecimal.format_currency(amount, currency),
            'decimal_places': decimal_places
        }
    
    async def _apply_currency_conversion(
        self, 
        amount: Decimal, 
        config: Dict[str, Any]
    ) -> Decimal:
        """Apply currency conversion if configured"""
        target_currency = config.get('convert_to')
        source_currency = config.get('currency', 'USD')
        
        if not target_currency or target_currency == source_currency:
            return amount
        
        try:
            # Use existing rate services
            if source_currency == 'USD' and target_currency == 'NGN':
                from services.fastforex_service import FastForexService
                fastforex = FastForexService()
                rate = await fastforex.get_usd_to_ngn_rate()
                if rate:
                    return amount * Decimal(str(rate))
            
            elif target_currency == 'USD':
                # Crypto to USD conversion
                from services.financial_gateway import financial_gateway
                rate = await financial_gateway.get_crypto_to_usd_rate(source_currency)
                if rate:
                    return amount * rate
            
        except Exception as e:
            logger.error(f"Currency conversion error: {e}")
        
        return amount  # Return original if conversion fails