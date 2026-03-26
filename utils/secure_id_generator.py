#!/usr/bin/env python3
"""
Production-ready secure ID generation system
Prevents collisions and ensures unique identifiers across all components

DEPRECATED: This module now delegates to UniversalIDGenerator for consistency.
Use UniversalIDGenerator directly for new code.
"""

import uuid
from typing import Optional
from utils.universal_id_generator import UniversalIDGenerator


class SecureIDGenerator:
    """Production-ready ID generator with collision prevention"""
    
    @classmethod
    def generate_escrow_id(cls, user_id: Optional[int] = None, retry_count: int = 0) -> str:
        """
        Generate collision-resistant escrow ID using UniversalIDGenerator
        
        DEPRECATED: This method now delegates to UniversalIDGenerator for consistency.
        Use UniversalIDGenerator.generate_escrow_id() directly for new code.
        
        Args:
            user_id: Optional user ID (ignored, kept for backward compatibility)
            retry_count: Optional retry count (ignored, kept for backward compatibility)
            
        Returns:
            Unified escrow ID from UniversalIDGenerator
        """
        # Delegate to UniversalIDGenerator for consistent ID generation
        return UniversalIDGenerator.generate_escrow_id()
    
    # generate_cashout_id() removed - was unused and generated CW-prefixed IDs
    # Current system uses LTC records via AutoCashoutService
    # WD backward compatibility handled via generate_utid('WD') in utils/helpers.py

    @classmethod
    def generate_transaction_reference(cls) -> str:
        """Generate unique transaction reference using UniversalIDGenerator"""
        # Delegate to UniversalIDGenerator for consistent ID generation
        return UniversalIDGenerator.generate_transaction_id()
    
    @classmethod 
    def generate_invitation_token(cls) -> str:
        """Generate secure invitation token"""
        return uuid.uuid4().hex.replace('-', '')[:32]