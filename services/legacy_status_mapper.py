"""
Legacy Status Mapping System
Comprehensive mapping from 38+ legacy statuses to unified transaction status system
Supports bidirectional mapping and dual-write operations during transition
"""

from typing import Dict, List, Tuple, Any
from datetime import datetime
from enum import Enum
import logging

from models import (
    EscrowStatus, 
    CashoutStatus, 
    ExchangeStatus, 
    UnifiedTransactionStatus
)

logger = logging.getLogger(__name__)


class LegacySystemType(Enum):
    """Types of legacy transaction systems"""
    CASHOUT = "cashout"
    ESCROW = "escrow" 
    EXCHANGE = "exchange"


class LegacyStatusMapper:
    """
    Comprehensive mapping system for legacy statuses to unified transaction statuses
    Handles 38+ legacy statuses across cashout, escrow, and exchange systems
    """
    
    # =============== CASHOUT STATUS MAPPINGS (15 statuses) ===============
    CASHOUT_TO_UNIFIED = {
        # Initiation Phase
        CashoutStatus.PENDING: UnifiedTransactionStatus.PENDING,
        
        # Authorization Phase
        CashoutStatus.OTP_PENDING: UnifiedTransactionStatus.OTP_PENDING,
        CashoutStatus.USER_CONFIRM_PENDING: UnifiedTransactionStatus.AWAITING_APPROVAL,
        CashoutStatus.ADMIN_PENDING: UnifiedTransactionStatus.ADMIN_PENDING,
        CashoutStatus.PENDING_ADDRESS_CONFIG: UnifiedTransactionStatus.ADMIN_PENDING,  # Admin needs to configure address
        CashoutStatus.PENDING_SERVICE_FUNDING: UnifiedTransactionStatus.ADMIN_PENDING,  # Admin needs to fund service
        CashoutStatus.APPROVED: UnifiedTransactionStatus.FUNDS_HELD,  # Approved, funds secured
        
        # Processing Phase
        CashoutStatus.PROCESSING: UnifiedTransactionStatus.PROCESSING,  # IMMEDIATE cashouts being processed
        CashoutStatus.EXECUTING: UnifiedTransactionStatus.PROCESSING,  # Alternative processing status
        CashoutStatus.AWAITING_RESPONSE: UnifiedTransactionStatus.AWAITING_RESPONSE,
        
        # Terminal Phase
        CashoutStatus.SUCCESS: UnifiedTransactionStatus.SUCCESS,
        CashoutStatus.COMPLETED: UnifiedTransactionStatus.SUCCESS,  # Deprecated but maps to SUCCESS
        CashoutStatus.FAILED: UnifiedTransactionStatus.FAILED,
        CashoutStatus.CANCELLED: UnifiedTransactionStatus.CANCELLED,
        CashoutStatus.EXPIRED: UnifiedTransactionStatus.EXPIRED,
    }
    
    # =============== ESCROW STATUS MAPPINGS (13 statuses) ===============
    ESCROW_TO_UNIFIED = {
        # Initiation Phase
        EscrowStatus.CREATED: UnifiedTransactionStatus.PENDING,
        EscrowStatus.PAYMENT_PENDING: UnifiedTransactionStatus.AWAITING_PAYMENT,
        EscrowStatus.PAYMENT_CONFIRMED: UnifiedTransactionStatus.PAYMENT_CONFIRMED,
        EscrowStatus.PARTIAL_PAYMENT: UnifiedTransactionStatus.PARTIAL_PAYMENT,
        EscrowStatus.PENDING_DEPOSIT: UnifiedTransactionStatus.AWAITING_PAYMENT,  # Waiting for deposit
        
        # Authorization/Processing Phase
        EscrowStatus.AWAITING_SELLER: UnifiedTransactionStatus.AWAITING_APPROVAL,  # Seller needs to accept
        EscrowStatus.PENDING_SELLER: UnifiedTransactionStatus.AWAITING_APPROVAL,   # Similar to awaiting seller
        EscrowStatus.ACTIVE: UnifiedTransactionStatus.FUNDS_HELD,  # Escrow active, funds secured
        
        # Terminal Phase
        EscrowStatus.COMPLETED: UnifiedTransactionStatus.SUCCESS,
        EscrowStatus.DISPUTED: UnifiedTransactionStatus.DISPUTED,
        EscrowStatus.CANCELLED: UnifiedTransactionStatus.CANCELLED,
        EscrowStatus.REFUNDED: UnifiedTransactionStatus.SUCCESS,  # Refund completed successfully
        EscrowStatus.EXPIRED: UnifiedTransactionStatus.EXPIRED,
    }
    
    # =============== EXCHANGE STATUS MAPPINGS (11 statuses) ===============
    EXCHANGE_TO_UNIFIED = {
        # Initiation Phase
        ExchangeStatus.CREATED: UnifiedTransactionStatus.PENDING,
        ExchangeStatus.AWAITING_DEPOSIT: UnifiedTransactionStatus.AWAITING_PAYMENT,
        ExchangeStatus.PAYMENT_RECEIVED: UnifiedTransactionStatus.PAYMENT_CONFIRMED,
        ExchangeStatus.PAYMENT_CONFIRMED: UnifiedTransactionStatus.PAYMENT_CONFIRMED,
        ExchangeStatus.RATE_LOCKED: UnifiedTransactionStatus.FUNDS_HELD,  # Rate locked, proceeding
        
        # Authorization Phase  
        ExchangeStatus.PENDING_APPROVAL: UnifiedTransactionStatus.AWAITING_APPROVAL,
        
        # Processing Phase
        ExchangeStatus.PROCESSING: UnifiedTransactionStatus.PROCESSING,
        ExchangeStatus.ADDRESS_GENERATION_FAILED: UnifiedTransactionStatus.FAILED,  # Technical failure
        
        # Terminal Phase
        ExchangeStatus.COMPLETED: UnifiedTransactionStatus.SUCCESS,
        ExchangeStatus.FAILED: UnifiedTransactionStatus.FAILED,
        ExchangeStatus.CANCELLED: UnifiedTransactionStatus.CANCELLED,
    }
    
    # =============== REVERSE MAPPINGS (Unified → Legacy) ===============
    
    @classmethod
    def _build_reverse_mapping(cls, forward_mapping: Dict) -> Dict:
        """Build reverse mapping from unified status back to legacy statuses"""
        reverse_map = {}
        for legacy_status, unified_status in forward_mapping.items():
            if unified_status not in reverse_map:
                reverse_map[unified_status] = []
            reverse_map[unified_status].append(legacy_status)
        return reverse_map
    
    # Build reverse mappings using proper class method call
    UNIFIED_TO_CASHOUT = {}
    UNIFIED_TO_ESCROW = {}
    UNIFIED_TO_EXCHANGE = {}
    
    @classmethod
    def _initialize_reverse_mappings(cls):
        """Initialize reverse mappings after class definition"""
        cls.UNIFIED_TO_CASHOUT = cls._build_reverse_mapping(cls.CASHOUT_TO_UNIFIED)
        cls.UNIFIED_TO_ESCROW = cls._build_reverse_mapping(cls.ESCROW_TO_UNIFIED)
        cls.UNIFIED_TO_EXCHANGE = cls._build_reverse_mapping(cls.EXCHANGE_TO_UNIFIED)
    
    # =============== VALIDATION SETS ===============
    
    ALL_CASHOUT_STATUSES = set([status for status in CashoutStatus])
    ALL_ESCROW_STATUSES = set([status for status in EscrowStatus])
    ALL_EXCHANGE_STATUSES = set([status for status in ExchangeStatus])
    ALL_UNIFIED_STATUSES = set([status for status in UnifiedTransactionStatus])
    
    # =============== CORE MAPPING METHODS ===============
    
    @classmethod
    def map_to_unified(cls, legacy_status: Any, system_type: LegacySystemType) -> UnifiedTransactionStatus:
        """
        Map legacy status to unified status
        
        Args:
            legacy_status: Legacy status enum value
            system_type: Type of legacy system (cashout, escrow, exchange)
            
        Returns:
            UnifiedTransactionStatus enum value
            
        Raises:
            ValueError: If mapping not found or invalid system type
        """
        try:
            if system_type == LegacySystemType.CASHOUT:
                if legacy_status not in cls.CASHOUT_TO_UNIFIED:
                    raise ValueError(f"Unknown CashoutStatus: {legacy_status}")
                return cls.CASHOUT_TO_UNIFIED[legacy_status]
                
            elif system_type == LegacySystemType.ESCROW:
                if legacy_status not in cls.ESCROW_TO_UNIFIED:
                    raise ValueError(f"Unknown EscrowStatus: {legacy_status}")
                return cls.ESCROW_TO_UNIFIED[legacy_status]
                
            elif system_type == LegacySystemType.EXCHANGE:
                if legacy_status not in cls.EXCHANGE_TO_UNIFIED:
                    raise ValueError(f"Unknown ExchangeStatus: {legacy_status}")
                return cls.EXCHANGE_TO_UNIFIED[legacy_status]
                
            else:
                raise ValueError(f"Unknown system type: {system_type}")
                
        except Exception as e:
            logger.error(f"Failed to map legacy status {legacy_status} from {system_type}: {e}")
            raise
    
    @classmethod  
    def map_from_unified(cls, unified_status: UnifiedTransactionStatus, 
                        system_type: LegacySystemType,
                        prefer_primary: bool = True) -> Any:
        """
        Map unified status back to legacy status
        
        Args:
            unified_status: Unified status to map back
            system_type: Target legacy system type
            prefer_primary: If multiple legacy statuses map to same unified, prefer first one
            
        Returns:
            Legacy status enum value
            
        Raises:
            ValueError: If mapping not found or invalid system type
        """
        try:
            if system_type == LegacySystemType.CASHOUT:
                reverse_map = cls.UNIFIED_TO_CASHOUT
            elif system_type == LegacySystemType.ESCROW:
                reverse_map = cls.UNIFIED_TO_ESCROW
            elif system_type == LegacySystemType.EXCHANGE:
                reverse_map = cls.UNIFIED_TO_EXCHANGE
            else:
                raise ValueError(f"Unknown system type: {system_type}")
            
            if unified_status not in reverse_map:
                # CRITICAL FIX: Handle unified-only statuses with no reverse mapping
                logger.warning(f"No reverse mapping for unified status {unified_status} to {system_type}. Using fallback strategy.")
                
                # Return most appropriate fallback status for unified-only statuses
                if unified_status == UnifiedTransactionStatus.RELEASE_PENDING:
                    if system_type == LegacySystemType.CASHOUT:
                        return CashoutStatus.EXECUTING  # Best approximation
                    elif system_type == LegacySystemType.ESCROW:
                        return EscrowStatus.ACTIVE      # Escrow is active, ready for release
                    elif system_type == LegacySystemType.EXCHANGE:
                        return ExchangeStatus.PROCESSING  # Exchange processing
                
                # For other unmapped statuses, raise with clear error
                raise ValueError(f"No reverse mapping defined for unified status {unified_status} to {system_type}. Add explicit mapping or fallback logic.")
            
            legacy_options = reverse_map[unified_status]
            
            if prefer_primary or len(legacy_options) == 1:
                return legacy_options[0]  # Return first/primary mapping
            else:
                return legacy_options  # Return all options for manual selection
                
        except Exception as e:
            logger.error(f"Failed to map unified status {unified_status} to {system_type}: {e}")
            raise
    
    # =============== VALIDATION METHODS ===============
    
    @classmethod
    def validate_mapping_completeness(cls) -> Dict[str, Any]:
        """
        Validate that all legacy statuses have unified mappings
        Returns comprehensive validation report
        """
        report = {
            "validation_timestamp": datetime.utcnow(),
            "total_legacy_statuses": 0,
            "mapped_statuses": 0,
            "unmapped_statuses": [],
            "mapping_gaps": {},
            "reverse_mapping_issues": {},
            "validation_passed": True,
            "detailed_analysis": {}
        }
        
        # Validate Cashout mappings
        cashout_mapped = set(cls.CASHOUT_TO_UNIFIED.keys())
        cashout_unmapped = cls.ALL_CASHOUT_STATUSES - cashout_mapped
        
        report["detailed_analysis"]["cashout"] = {
            "total_statuses": len(cls.ALL_CASHOUT_STATUSES),
            "mapped_count": len(cashout_mapped),
            "unmapped_count": len(cashout_unmapped),
            "unmapped_statuses": list(cashout_unmapped),
            "coverage_percentage": (len(cashout_mapped) / len(cls.ALL_CASHOUT_STATUSES)) * 100
        }
        
        # Validate Escrow mappings
        escrow_mapped = set(cls.ESCROW_TO_UNIFIED.keys())
        escrow_unmapped = cls.ALL_ESCROW_STATUSES - escrow_mapped
        
        report["detailed_analysis"]["escrow"] = {
            "total_statuses": len(cls.ALL_ESCROW_STATUSES),
            "mapped_count": len(escrow_mapped),
            "unmapped_count": len(escrow_unmapped),
            "unmapped_statuses": list(escrow_unmapped),
            "coverage_percentage": (len(escrow_mapped) / len(cls.ALL_ESCROW_STATUSES)) * 100
        }
        
        # Validate Exchange mappings
        exchange_mapped = set(cls.EXCHANGE_TO_UNIFIED.keys())
        exchange_unmapped = cls.ALL_EXCHANGE_STATUSES - exchange_mapped
        
        report["detailed_analysis"]["exchange"] = {
            "total_statuses": len(cls.ALL_EXCHANGE_STATUSES),
            "mapped_count": len(exchange_mapped),
            "unmapped_count": len(exchange_unmapped),
            "unmapped_statuses": list(exchange_unmapped),
            "coverage_percentage": (len(exchange_mapped) / len(cls.ALL_EXCHANGE_STATUSES)) * 100
        }
        
        # CRITICAL: Check reverse mapping coverage for ALL unified statuses
        # Initialize reverse mappings if not done
        if not cls.UNIFIED_TO_CASHOUT:
            cls._initialize_reverse_mappings()
        
        all_unified_statuses = set([status for status in UnifiedTransactionStatus])
        cashout_reverse_covered = set(cls.UNIFIED_TO_CASHOUT.keys())
        escrow_reverse_covered = set(cls.UNIFIED_TO_ESCROW.keys())
        exchange_reverse_covered = set(cls.UNIFIED_TO_EXCHANGE.keys())
        
        # Identify unified statuses without reverse mappings
        cashout_missing_reverse = all_unified_statuses - cashout_reverse_covered
        escrow_missing_reverse = all_unified_statuses - escrow_reverse_covered
        exchange_missing_reverse = all_unified_statuses - exchange_reverse_covered
        
        report["reverse_mapping_coverage"] = {
            "cashout_missing": list(cashout_missing_reverse),
            "escrow_missing": list(escrow_missing_reverse),
            "exchange_missing": list(exchange_missing_reverse),
            "all_covered": len(cashout_missing_reverse) == 0 and len(escrow_missing_reverse) == 0 and len(exchange_missing_reverse) == 0
        }
        
        # Calculate totals
        report["total_legacy_statuses"] = (
            len(cls.ALL_CASHOUT_STATUSES) + 
            len(cls.ALL_ESCROW_STATUSES) + 
            len(cls.ALL_EXCHANGE_STATUSES)
        )
        report["mapped_statuses"] = len(cashout_mapped) + len(escrow_mapped) + len(exchange_mapped)
        report["unmapped_statuses"] = list(cashout_unmapped) + list(escrow_unmapped) + list(exchange_unmapped)
        
        # Check for validation failures
        if report["unmapped_statuses"]:
            report["validation_passed"] = False
            report["mapping_gaps"]["total_unmapped"] = len(report["unmapped_statuses"])
        
        # Validate unified status coverage
        used_unified_statuses = set()
        used_unified_statuses.update(cls.CASHOUT_TO_UNIFIED.values())
        used_unified_statuses.update(cls.ESCROW_TO_UNIFIED.values())
        used_unified_statuses.update(cls.EXCHANGE_TO_UNIFIED.values())
        
        unused_unified = cls.ALL_UNIFIED_STATUSES - used_unified_statuses
        report["unused_unified_statuses"] = list(unused_unified)
        report["unified_status_coverage"] = (len(used_unified_statuses) / len(cls.ALL_UNIFIED_STATUSES)) * 100
        
        # Set validation passed based on reverse mapping coverage
        if not report["reverse_mapping_coverage"]["all_covered"]:
            report["validation_passed"] = False
            report["critical_issues"] = ["Incomplete reverse mapping coverage - will break dual-write operations"]
        
        return report
    
    @classmethod
    def get_mapping_conflicts(cls) -> Dict[str, List[Tuple[Any, UnifiedTransactionStatus]]]:
        """
        Identify potential mapping conflicts where multiple legacy statuses map to same unified status
        This is not necessarily an error but should be documented
        """
        conflicts = {}
        
        # Check for many-to-one mappings in each system
        all_mappings = [
            ("cashout", cls.CASHOUT_TO_UNIFIED),
            ("escrow", cls.ESCROW_TO_UNIFIED),
            ("exchange", cls.EXCHANGE_TO_UNIFIED)
        ]
        
        for system_name, mapping_dict in all_mappings:
            unified_to_legacy = {}
            
            for legacy_status, unified_status in mapping_dict.items():
                if unified_status not in unified_to_legacy:
                    unified_to_legacy[unified_status] = []
                unified_to_legacy[unified_status].append(legacy_status)
            
            # Find many-to-one mappings (multiple legacy → one unified)
            system_conflicts = []
            for unified_status, legacy_statuses in unified_to_legacy.items():
                if len(legacy_statuses) > 1:
                    system_conflicts.append((unified_status, legacy_statuses))
            
            if system_conflicts:
                conflicts[system_name] = system_conflicts
        
        return conflicts
    
    @classmethod
    def get_transition_compatibility_matrix(cls) -> Dict[str, Dict[str, bool]]:
        """
        Generate compatibility matrix showing which legacy statuses can safely transition
        to unified system without data loss or business logic conflicts
        """
        matrix = {
            "cashout_transitions": {},
            "escrow_transitions": {},
            "exchange_transitions": {},
            "cross_system_risks": []
        }
        
        # Analyze cashout transition safety
        for cashout_status in cls.ALL_CASHOUT_STATUSES:
            if cashout_status in cls.CASHOUT_TO_UNIFIED:
                unified_status = cls.CASHOUT_TO_UNIFIED[cashout_status]
                
                # Check if reverse mapping exists and is unambiguous
                reverse_options = cls.UNIFIED_TO_CASHOUT.get(unified_status, [])
                is_safe = len(reverse_options) == 1 and reverse_options[0] == cashout_status
                
                matrix["cashout_transitions"][cashout_status.value] = {
                    "maps_to": unified_status.value,
                    "safe_transition": is_safe,
                    "reverse_options_count": len(reverse_options),
                    "potential_data_loss": not is_safe
                }
        
        # Similar analysis for escrow and exchange
        for escrow_status in cls.ALL_ESCROW_STATUSES:
            if escrow_status in cls.ESCROW_TO_UNIFIED:
                unified_status = cls.ESCROW_TO_UNIFIED[escrow_status]
                reverse_options = cls.UNIFIED_TO_ESCROW.get(unified_status, [])
                is_safe = len(reverse_options) == 1 and reverse_options[0] == escrow_status
                
                matrix["escrow_transitions"][escrow_status.value] = {
                    "maps_to": unified_status.value,
                    "safe_transition": is_safe,
                    "reverse_options_count": len(reverse_options),
                    "potential_data_loss": not is_safe
                }
        
        for exchange_status in cls.ALL_EXCHANGE_STATUSES:
            if exchange_status in cls.EXCHANGE_TO_UNIFIED:
                unified_status = cls.EXCHANGE_TO_UNIFIED[exchange_status]
                reverse_options = cls.UNIFIED_TO_EXCHANGE.get(unified_status, [])
                is_safe = len(reverse_options) == 1 and reverse_options[0] == exchange_status
                
                matrix["exchange_transitions"][exchange_status.value] = {
                    "maps_to": unified_status.value,
                    "safe_transition": is_safe,
                    "reverse_options_count": len(reverse_options),
                    "potential_data_loss": not is_safe
                }
        
        return matrix
    
    # =============== UTILITY METHODS ===============
    
    @classmethod
    def get_status_lifecycle_phase(cls, unified_status: UnifiedTransactionStatus) -> str:
        """
        Get the lifecycle phase for a unified status
        Useful for UI grouping and business logic
        """
        initiation_phase = {
            UnifiedTransactionStatus.PENDING,
            UnifiedTransactionStatus.AWAITING_PAYMENT,
            UnifiedTransactionStatus.PAYMENT_CONFIRMED
        }
        
        authorization_phase = {
            UnifiedTransactionStatus.FUNDS_HELD,
            UnifiedTransactionStatus.AWAITING_APPROVAL,
            UnifiedTransactionStatus.OTP_PENDING,
            UnifiedTransactionStatus.ADMIN_PENDING
        }
        
        processing_phase = {
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.AWAITING_RESPONSE,
            UnifiedTransactionStatus.RELEASE_PENDING
        }
        
        terminal_phase = {
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.FAILED,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.DISPUTED,
            UnifiedTransactionStatus.EXPIRED,
            UnifiedTransactionStatus.PARTIAL_PAYMENT
        }
        
        if unified_status in initiation_phase:
            return "initiation"
        elif unified_status in authorization_phase:
            return "authorization"
        elif unified_status in processing_phase:
            return "processing"
        elif unified_status in terminal_phase:
            return "terminal"
        else:
            return "unknown"
    
    @classmethod
    def is_terminal_status(cls, status: UnifiedTransactionStatus) -> bool:
        """Check if a unified status is terminal (no further transitions expected)"""
        terminal_statuses = {
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.FAILED,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.DISPUTED,
            UnifiedTransactionStatus.EXPIRED
        }
        return status in terminal_statuses
    
    @classmethod
    def get_valid_transitions(cls, current_status: UnifiedTransactionStatus) -> List[UnifiedTransactionStatus]:
        """
        Get list of valid status transitions from current status
        Based on unified transaction lifecycle rules
        """
        # Define valid transition rules
        transition_rules = {
            # Initiation Phase
            UnifiedTransactionStatus.PENDING: [
                UnifiedTransactionStatus.AWAITING_PAYMENT,
                UnifiedTransactionStatus.FUNDS_HELD,
                UnifiedTransactionStatus.OTP_PENDING,
                UnifiedTransactionStatus.ADMIN_PENDING,
                UnifiedTransactionStatus.PROCESSING,
                UnifiedTransactionStatus.FAILED,
                UnifiedTransactionStatus.CANCELLED,
                UnifiedTransactionStatus.EXPIRED
            ],
            
            UnifiedTransactionStatus.AWAITING_PAYMENT: [
                UnifiedTransactionStatus.PAYMENT_CONFIRMED,
                UnifiedTransactionStatus.PARTIAL_PAYMENT,
                UnifiedTransactionStatus.CANCELLED,
                UnifiedTransactionStatus.EXPIRED,
                UnifiedTransactionStatus.FAILED
            ],
            
            UnifiedTransactionStatus.PAYMENT_CONFIRMED: [
                UnifiedTransactionStatus.FUNDS_HELD,
                UnifiedTransactionStatus.AWAITING_APPROVAL,
                UnifiedTransactionStatus.PROCESSING,
                UnifiedTransactionStatus.SUCCESS
            ],
            
            UnifiedTransactionStatus.PARTIAL_PAYMENT: [
                UnifiedTransactionStatus.PAYMENT_CONFIRMED,
                UnifiedTransactionStatus.CANCELLED,
                UnifiedTransactionStatus.EXPIRED
            ],
            
            # Authorization Phase
            UnifiedTransactionStatus.FUNDS_HELD: [
                UnifiedTransactionStatus.AWAITING_APPROVAL,
                UnifiedTransactionStatus.OTP_PENDING,
                UnifiedTransactionStatus.ADMIN_PENDING,
                UnifiedTransactionStatus.PROCESSING,
                UnifiedTransactionStatus.SUCCESS,
                UnifiedTransactionStatus.CANCELLED,
                UnifiedTransactionStatus.DISPUTED
            ],
            
            UnifiedTransactionStatus.AWAITING_APPROVAL: [
                UnifiedTransactionStatus.FUNDS_HELD,
                UnifiedTransactionStatus.PROCESSING,
                UnifiedTransactionStatus.CANCELLED,
                UnifiedTransactionStatus.EXPIRED
            ],
            
            UnifiedTransactionStatus.OTP_PENDING: [
                UnifiedTransactionStatus.FUNDS_HELD,
                UnifiedTransactionStatus.PROCESSING,
                UnifiedTransactionStatus.FAILED,
                UnifiedTransactionStatus.CANCELLED,
                UnifiedTransactionStatus.EXPIRED
            ],
            
            UnifiedTransactionStatus.ADMIN_PENDING: [
                UnifiedTransactionStatus.FUNDS_HELD,
                UnifiedTransactionStatus.PROCESSING,
                UnifiedTransactionStatus.CANCELLED,
                UnifiedTransactionStatus.FAILED
            ],
            
            # Processing Phase
            UnifiedTransactionStatus.PROCESSING: [
                UnifiedTransactionStatus.AWAITING_RESPONSE,
                UnifiedTransactionStatus.RELEASE_PENDING,
                UnifiedTransactionStatus.SUCCESS,
                UnifiedTransactionStatus.FAILED
            ],
            
            UnifiedTransactionStatus.AWAITING_RESPONSE: [
                UnifiedTransactionStatus.RELEASE_PENDING,
                UnifiedTransactionStatus.PROCESSING,
                UnifiedTransactionStatus.SUCCESS,
                UnifiedTransactionStatus.FAILED
            ],
            
            UnifiedTransactionStatus.RELEASE_PENDING: [
                UnifiedTransactionStatus.SUCCESS,
                UnifiedTransactionStatus.FAILED
            ],
            
            # Terminal Phase (no transitions out)
            UnifiedTransactionStatus.SUCCESS: [],
            UnifiedTransactionStatus.FAILED: [],
            UnifiedTransactionStatus.CANCELLED: [],
            UnifiedTransactionStatus.DISPUTED: [],  # Could transition to SUCCESS/FAILED after resolution
            UnifiedTransactionStatus.EXPIRED: []
        }
        
        return transition_rules.get(current_status, [])


# =============== LEGACY STATUS MAPPER INSTANCE ===============

# Create global instance for easy access
legacy_status_mapper = LegacyStatusMapper()