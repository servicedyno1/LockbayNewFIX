#!/usr/bin/env python3
"""
Production validation system for data integrity and business rules
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List
from config import Config

logger = logging.getLogger(__name__)


class ProductionValidator:
    """Production-grade validation for all critical operations"""
    
    @classmethod
    def validate_escrow_creation(cls, escrow_data: Dict[str, Any], user_id: int) -> tuple[bool, List[str]]:
        """
        Comprehensive validation before escrow creation
        Returns (is_valid, error_messages)
        """
        errors = []
        
        # Required fields validation
        required_fields = [
            ('amount', 'Escrow amount'),
            ('description', 'Trade description'),
            ('seller_type', 'Seller contact method'),
            ('seller_identifier', 'Seller contact information'),
            ('delivery_hours', 'Delivery timeframe')
        ]
        
        for field, display_name in required_fields:
            if field not in escrow_data or escrow_data[field] is None:
                errors.append(f"{display_name} is required")
                continue
                
            # String fields cannot be empty
            if isinstance(escrow_data[field], str) and not escrow_data[field].strip():
                errors.append(f"{display_name} cannot be empty")
        
        # Amount validation
        if 'amount' in escrow_data:
            try:
                amount = Decimal(str(escrow_data['amount']))
                if amount <= 0:
                    errors.append("Amount must be greater than $0")
                elif amount < Decimal(str(Config.MIN_ESCROW_AMOUNT_USD)):
                    errors.append(f"Minimum amount is ${Config.MIN_ESCROW_AMOUNT_USD}")
                elif amount > Config.MAX_ESCROW_AMOUNT_USD:
                    errors.append(f"Amount exceeds maximum limit of ${Config.MAX_ESCROW_AMOUNT_USD:,.0f}")
            except (ValueError, TypeError, Exception):
                errors.append("Invalid amount format")
        
        # Description validation
        if 'description' in escrow_data:
            desc = str(escrow_data['description']).strip()
            if len(desc) > 500:
                errors.append("Description too long (max 500 characters)")
            elif len(desc) < 5:
                errors.append("Description too short (min 5 characters)")
        
        # Delivery hours validation
        if 'delivery_hours' in escrow_data:
            try:
                hours = int(escrow_data['delivery_hours'])
                if hours < 1:
                    errors.append("Delivery time must be at least 1 hour")
                elif hours > 8760:  # 1 year
                    errors.append("Delivery time cannot exceed 1 year")
            except (ValueError, TypeError):
                errors.append("Invalid delivery timeframe")
        
        # Seller contact validation
        if 'seller_type' in escrow_data and 'seller_identifier' in escrow_data:
            seller_type = escrow_data['seller_type']
            identifier = str(escrow_data['seller_identifier']).strip()
            
            if seller_type == 'email':
                if not cls._validate_email(identifier):
                    errors.append("Invalid email address format")
            elif seller_type == 'username':
                if not cls._validate_telegram_username(identifier):
                    errors.append("Invalid Telegram username format")
            elif seller_type == 'phone':
                if not cls._validate_phone_number(identifier):
                    errors.append("Invalid phone number format")
        
        # User-specific validations
        if user_id:
            # Could add rate limiting, user level checks, etc.
            pass
        
        return len(errors) == 0, errors
    
    @classmethod
    def validate_fee_calculation(cls, amount: Decimal, fee_amount: Decimal, total_amount: Decimal) -> tuple[bool, List[str]]:
        """Validate fee calculations are correct"""
        errors = []
        
        try:
            # Calculate expected fee
            fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
            expected_fee = (amount * fee_percentage).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            expected_total = amount + expected_fee
            
            # Allow small rounding differences (1 cent)
            fee_diff = abs(fee_amount - expected_fee)
            total_diff = abs(total_amount - expected_total)
            
            if fee_diff > Decimal("0.01"):
                errors.append(f"Fee calculation error: expected {expected_fee}, got {fee_amount}")
            
            if total_diff > Decimal("0.01"):
                errors.append(f"Total calculation error: expected {expected_total}, got {total_amount}")
                
        except Exception as e:
            errors.append(f"Fee validation error: {str(e)}")
        
        return len(errors) == 0, errors
    
    @classmethod
    def _validate_email(cls, email: str) -> bool:
        """Validate email format"""
        import re
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(pattern, email))
    
    @classmethod
    def _validate_telegram_username(cls, username: str) -> bool:
        """Validate Telegram username format"""
        import re
        username = username.lstrip('@')
        pattern = r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$"
        return bool(re.match(pattern, username)) and not username.isdigit()
    
    @classmethod
    def _validate_phone_number(cls, phone: str) -> bool:
        """Validate phone number format"""
        import re
        # Must start with + and contain at least 10 digits
        if not phone.startswith('+'):
            return False
        digits = re.sub(r'[^\d]', '', phone[1:])
        return len(digits) >= 10 and len(digits) <= 15