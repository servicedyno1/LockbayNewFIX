"""
Payment Configuration Validator
Ensures consistency and correctness of payment configuration across all services
"""

import logging
from decimal import Decimal
from typing import Dict, Any
from config import Config

logger = logging.getLogger(__name__)


class PaymentConfigValidator:
    """Validates payment configuration consistency across all services"""
    
    @classmethod
    def validate_all_configurations(cls) -> Dict[str, Any]:
        """
        Perform comprehensive validation of all payment configurations
        
        Returns:
            Dict with validation results and any inconsistencies found
        """
        results = {
            'valid': True,
            'warnings': [],
            'errors': [],
            'configuration_summary': {},
            'consistency_check': {}
        }
        
        try:
            # 1. Validate tolerance settings
            cls._validate_tolerance_settings(results)
            
            # 2. Validate markup percentages
            cls._validate_markup_settings(results)
            
            # 3. Validate threshold values
            cls._validate_threshold_settings(results)
            
            # 4. Validate cross-service consistency
            cls._validate_cross_service_consistency(results)
            
            # 5. Validate bounds and ranges
            cls._validate_bounds_and_ranges(results)
            
            # Log validation summary
            if results['errors']:
                results['valid'] = False
                logger.error(f"Payment configuration validation failed: {len(results['errors'])} errors found")
            elif results['warnings']:
                logger.warning(f"Payment configuration validation completed with {len(results['warnings'])} warnings")
            else:
                logger.info("✅ Payment configuration validation passed - all settings consistent")
                
        except Exception as e:
            results['valid'] = False
            results['errors'].append(f"Validation process failed: {str(e)}")
            logger.error(f"Payment configuration validation error: {e}")
        
        return results
    
    @classmethod
    def _validate_tolerance_settings(cls, results: Dict):
        """Validate tolerance configuration"""
        try:
            tolerance_usd = Config.UNDERPAYMENT_TOLERANCE_USD
            
            # Check bounds
            if tolerance_usd < 0:
                results['errors'].append("UNDERPAYMENT_TOLERANCE_USD cannot be negative")
            elif tolerance_usd > 10.0:
                results['warnings'].append(f"UNDERPAYMENT_TOLERANCE_USD is high: ${tolerance_usd} (consider reducing)")
            elif tolerance_usd == 0:
                results['warnings'].append("UNDERPAYMENT_TOLERANCE_USD is zero (very strict)")
            
            results['configuration_summary']['underpayment_tolerance_usd'] = tolerance_usd
            
            # Validate rate tolerance
            rate_tolerance = Config.DEFAULT_RATE_TOLERANCE
            if rate_tolerance < 0:
                results['errors'].append("DEFAULT_RATE_TOLERANCE cannot be negative")
            elif rate_tolerance > Decimal("0.2"):  # 20%
                results['warnings'].append(f"DEFAULT_RATE_TOLERANCE is high: {rate_tolerance*100}%")
            
            results['configuration_summary']['default_rate_tolerance'] = float(rate_tolerance)
            
        except AttributeError as e:
            results['errors'].append(f"Missing tolerance configuration: {e}")
    
    @classmethod
    def _validate_markup_settings(cls, results: Dict):
        """Validate markup percentage configuration"""
        try:
            # Exchange markup
            exchange_markup = Config.EXCHANGE_MARKUP_PERCENTAGE
            if exchange_markup < 0:
                results['errors'].append("EXCHANGE_MARKUP_PERCENTAGE cannot be negative")
            elif exchange_markup > 15.0:
                results['warnings'].append(f"EXCHANGE_MARKUP_PERCENTAGE is high: {exchange_markup}%")
            
            results['configuration_summary']['exchange_markup_percentage'] = exchange_markup
            
            # Wallet deposit markup
            wallet_markup = Config.WALLET_DEPOSIT_MARKUP_PERCENTAGE
            if wallet_markup < 0:
                results['errors'].append("WALLET_DEPOSIT_MARKUP_PERCENTAGE cannot be negative")
            elif wallet_markup > 10.0:
                results['warnings'].append(f"WALLET_DEPOSIT_MARKUP_PERCENTAGE is high: {wallet_markup}%")
            
            results['configuration_summary']['wallet_deposit_markup_percentage'] = wallet_markup
            
            # Escrow fee percentage
            escrow_fee = Config.ESCROW_FEE_PERCENTAGE
            if escrow_fee < 0:
                results['errors'].append("ESCROW_FEE_PERCENTAGE cannot be negative")
            elif escrow_fee > 10.0:
                results['warnings'].append(f"ESCROW_FEE_PERCENTAGE is high: {escrow_fee}%")
            
            results['configuration_summary']['escrow_fee_percentage'] = escrow_fee
            
        except AttributeError as e:
            results['errors'].append(f"Missing markup configuration: {e}")
    
    @classmethod
    def _validate_threshold_settings(cls, results: Dict):
        """Validate threshold configuration"""
        try:
            # Minimum cashout amounts
            min_cashout_usd = getattr(Config, 'MIN_CASHOUT_USD', 10.0)
            if min_cashout_usd < 1.0:
                results['warnings'].append(f"MIN_CASHOUT_USD is low: ${min_cashout_usd}")
            
            results['configuration_summary']['min_cashout_usd'] = min_cashout_usd
            
            # NGN minimum amounts
            if hasattr(Config, 'FINCRA_MIN_AMOUNT_NGN'):
                fincra_min = float(Config.FINCRA_MIN_AMOUNT_NGN)
                if fincra_min < 50.0:
                    results['warnings'].append(f"FINCRA_MIN_AMOUNT_NGN is low: ₦{fincra_min}")
                results['configuration_summary']['fincra_min_amount_ngn'] = fincra_min
            
        except Exception as e:
            results['warnings'].append(f"Could not validate all threshold settings: {e}")
    
    @classmethod
    def _validate_cross_service_consistency(cls, results: Dict):
        """Validate consistency across different services"""
        consistency_issues = []
        
        try:
            # Check if all services would use the same tolerance
            from services.overpayment_service import OverpaymentService
            service_tolerance = OverpaymentService._get_tolerance()
            config_tolerance = Config.UNDERPAYMENT_TOLERANCE_USD
            
            if abs(service_tolerance - config_tolerance) > 0.001:
                consistency_issues.append(
                    f"OverpaymentService tolerance (${service_tolerance}) != Config tolerance (${config_tolerance})"
                )
            
            # Check payment edge case thresholds
            from services.payment_edge_cases import PaymentEdgeCaseHandler
            if hasattr(PaymentEdgeCaseHandler, 'PARTIAL_PAYMENT_MIN_THRESHOLD'):
                partial_threshold = PaymentEdgeCaseHandler.PARTIAL_PAYMENT_MIN_THRESHOLD
                if partial_threshold < 0.05 or partial_threshold > 0.5:
                    consistency_issues.append(
                        f"PARTIAL_PAYMENT_MIN_THRESHOLD unusual value: {partial_threshold*100}%"
                    )
            
            results['consistency_check'] = {
                'issues_found': len(consistency_issues),
                'details': consistency_issues
            }
            
            if consistency_issues:
                results['warnings'].extend(consistency_issues)
                
        except Exception as e:
            results['warnings'].append(f"Could not complete cross-service consistency check: {e}")
    
    @classmethod
    def _validate_bounds_and_ranges(cls, results: Dict):
        """Validate that all values are within reasonable bounds"""
        try:
            # Validate currency precision
            usd_precision_places = 2
            crypto_precision_places = 8
            
            # These are implementation constants, just log for visibility
            results['configuration_summary']['currency_precision'] = {
                'usd_places': usd_precision_places,
                'crypto_places': crypto_precision_places
            }
            
            # Validate network fee ranges
            if hasattr(Config, 'NETWORK_FEE_BUFFER_PERCENTAGE'):
                fee_buffer = getattr(Config, 'NETWORK_FEE_BUFFER_PERCENTAGE', 10.0)
                if fee_buffer < 5.0 or fee_buffer > 50.0:
                    results['warnings'].append(
                        f"NETWORK_FEE_BUFFER_PERCENTAGE outside normal range: {fee_buffer}%"
                    )
            
        except Exception as e:
            results['warnings'].append(f"Could not validate all bounds and ranges: {e}")
    
    @classmethod
    def get_configuration_summary(cls) -> Dict[str, Any]:
        """Get a summary of current payment configuration"""
        validation = cls.validate_all_configurations()
        return {
            'is_valid': validation['valid'],
            'configuration': validation['configuration_summary'],
            'issues': {
                'errors': validation['errors'],
                'warnings': validation['warnings']
            },
            'consistency': validation['consistency_check']
        }


# Auto-validate on import in development
if __name__ == "__main__":
    validator = PaymentConfigValidator()
    result = validator.validate_all_configurations()
    print(f"Validation result: {result}")