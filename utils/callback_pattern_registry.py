"""
Callback Pattern Registry - Phase 1 Critical Fix
Implements namespaced callback patterns to prevent conflicts
"""

import logging
from typing import Dict, Set, List, Tuple, Optional

logger = logging.getLogger(__name__)

class CallbackPatternRegistry:
    """
    Registry for managing namespaced callback patterns
    Prevents pattern conflicts and provides validation
    """
    
    def __init__(self):
        # Registry of namespace -> patterns mapping
        self._patterns: Dict[str, Set[str]] = {}
        # Reverse mapping: pattern -> namespace for conflict detection
        self._pattern_to_namespace: Dict[str, str] = {}
        # Reserved prefixes that can't be used for namespacing
        self._reserved_prefixes = {'admin', 'system', 'internal'}
        
    def register_namespace(self, namespace: str, patterns: List[str]) -> List[str]:
        """
        Register patterns for a namespace with conflict detection
        
        Args:
            namespace: The namespace (e.g., 'wallet', 'escrow', 'exchange')
            patterns: List of patterns without namespace prefix
            
        Returns:
            List of fully qualified namespaced patterns
        """
        if namespace in self._reserved_prefixes:
            logger.warning(f"⚠️ Using reserved namespace '{namespace}' - proceed with caution")
            
        namespaced_patterns = []
        conflicts = []
        
        for pattern in patterns:
            # Create namespaced pattern
            if pattern.startswith('^'):
                # Handle regex patterns
                namespaced_pattern = f"^{namespace}_{pattern[1:]}"
            else:
                namespaced_pattern = f"{namespace}_{pattern}"
                
            # Check for conflicts
            if namespaced_pattern in self._pattern_to_namespace:
                existing_namespace = self._pattern_to_namespace[namespaced_pattern]
                if existing_namespace != namespace:
                    conflicts.append((namespaced_pattern, existing_namespace))
                    
            namespaced_patterns.append(namespaced_pattern)
            
        # Log conflicts
        if conflicts:
            logger.error(f"❌ Pattern conflicts detected for namespace '{namespace}':")
            for pattern, existing_ns in conflicts:
                logger.error(f"   Pattern '{pattern}' already registered in namespace '{existing_ns}'")
            raise ValueError(f"Pattern conflicts detected for namespace '{namespace}'")
            
        # Register patterns
        if namespace not in self._patterns:
            self._patterns[namespace] = set()
            
        for pattern, namespaced_pattern in zip(patterns, namespaced_patterns):
            self._patterns[namespace].add(namespaced_pattern)
            self._pattern_to_namespace[namespaced_pattern] = namespace
            
        logger.info(f"✅ Registered {len(patterns)} patterns for namespace '{namespace}'")
        return namespaced_patterns
        
    def get_pattern(self, namespace: str, pattern: str) -> str:
        """Get fully qualified pattern for namespace"""
        if pattern.startswith('^'):
            return f"^{namespace}_{pattern[1:]}"
        return f"{namespace}_{pattern}"
        
    def validate_pattern(self, pattern: str) -> Tuple[bool, Optional[str]]:
        """
        Validate if pattern follows namespacing conventions
        
        Returns:
            (is_valid, namespace_or_error)
        """
        # Check if pattern is properly namespaced
        if '_' not in pattern:
            return False, "Pattern must include namespace prefix (e.g., 'wallet_confirm')"
            
        # Extract namespace
        if pattern.startswith('^'):
            namespace = pattern[1:].split('_')[0]
        else:
            namespace = pattern.split('_')[0]
            
        # Check if namespace is registered
        if namespace not in self._patterns:
            return False, f"Namespace '{namespace}' not registered"
            
        return True, namespace
        
    def get_conflicts(self) -> List[Tuple[str, List[str]]]:
        """
        Find potential pattern conflicts
        
        Returns:
            List of (pattern_prefix, conflicting_namespaces)
        """
        conflicts = []
        pattern_prefixes = {}
        
        # Group patterns by their base prefix (after namespace)
        for namespace, patterns in self._patterns.items():
            for pattern in patterns:
                # Extract base pattern (remove namespace prefix)
                if pattern.startswith('^'):
                    base_pattern = '_'.join(pattern[1:].split('_')[1:])
                else:
                    base_pattern = '_'.join(pattern.split('_')[1:])
                    
                if base_pattern not in pattern_prefixes:
                    pattern_prefixes[base_pattern] = []
                pattern_prefixes[base_pattern].append(namespace)
                
        # Find conflicts (same base pattern in multiple namespaces)
        for base_pattern, namespaces in pattern_prefixes.items():
            if len(namespaces) > 1:
                conflicts.append((base_pattern, namespaces))
                
        return conflicts
        
    def generate_migration_map(self, old_patterns: List[str]) -> Dict[str, str]:
        """
        Generate migration mapping from old patterns to new namespaced patterns
        
        Args:
            old_patterns: List of existing patterns without namespaces
            
        Returns:
            Dictionary mapping old_pattern -> suggested_new_pattern
        """
        migration_map = {}
        
        # Define pattern -> namespace mappings based on common prefixes
        namespace_mappings = {
            'admin_': 'admin',
            'wallet_': 'wallet', 
            'escrow_': 'escrow',
            'exchange_': 'exchange',
            'confirm_': 'action',
            'cancel_': 'action',
            'select_': 'selection',
            'view_': 'navigation',
            'show_': 'navigation',
            'start_': 'flow',
            'menu_': 'navigation',
            'dispute_': 'dispute',
            'rating_': 'rating',
            'contact_': 'contact',
            'cashout_': 'wallet',
            'deposit_': 'wallet',
            'trade_': 'escrow',
        }
        
        for old_pattern in old_patterns:
            # Try to determine appropriate namespace
            suggested_namespace = None
            
            for prefix, namespace in namespace_mappings.items():
                if old_pattern.startswith(prefix) or (old_pattern.startswith('^') and old_pattern[1:].startswith(prefix)):
                    suggested_namespace = namespace
                    break
                    
            if not suggested_namespace:
                # Default to 'misc' namespace for unclassified patterns
                suggested_namespace = 'misc'
                
            # Generate new pattern
            if old_pattern.startswith('^'):
                new_pattern = f"^{suggested_namespace}_{old_pattern[1:]}"
            else:
                new_pattern = f"{suggested_namespace}_{old_pattern}"
                
            migration_map[old_pattern] = new_pattern
            
        return migration_map
        
    def get_namespace_statistics(self) -> Dict[str, int]:
        """Get statistics about registered namespaces"""
        return {namespace: len(patterns) for namespace, patterns in self._patterns.items()}

# Predefined namespace configurations for common handlers
STANDARD_NAMESPACES = {
    'wallet': [
        'deposit_crypto',
        'deposit_qr',
        'withdraw_crypto',
        'withdraw_ngn',
        'confirm_withdrawal',
        'cancel_withdrawal',
        'view_balance',
        'transaction_history',
        'add_bank_account',
        'remove_bank_account',
        'confirm_remove_bank',
        'add_crypto_address',
        'remove_crypto_address',
        'confirm_remove_crypto',
        'confirm_wallet_payment',
    ],
    'escrow': [
        'create_escrow',
        'join_escrow',
        'cancel_escrow',
        'confirm_cancel',
        'mark_delivered',
        'release_funds',
        'confirm_release',
        'decline_trade',
        'confirm_decline',
        'view_details',
        'share_link',
        'buyer_cancel',
        'confirm_buyer_cancel',
    ],
    'exchange': [
        'start_exchange',
        'select_currency',
        'confirm_exchange',
        'cancel_exchange',
        'resume_exchange',
        'view_rate',
        'confirm_rate',
    ],
    'admin': [
        'main',
        'health',
        'health_refresh',
        'sysinfo',
        'disputes',
        'reports',
        'transactions',
        'trans_cashouts',
        'trans_escrows',
        'trans_exchanges',
        'trans_analytics',
        'trans_actions',
        'confirm_approve',
        'confirm_refund',
        'confirm_release',
        'split_confirm',
        'referrals',
        'referral_analytics',
        'referral_config',
        'referral_alerts',
        'referral_users',
        'config_rewards',
        'config_system',
        'trade_chats',
    ],
    'navigation': [
        'main_menu',
        'my_escrows',
        'view_active_trades',
        'trades_messages_hub',
        'invite_friends',
        'referral_stats',
        'referral_leaderboard',
    ],
    'action': [
        'confirm',
        'cancel',
        'approve',
        'decline',
        'retry',
        'delete',
    ],
    'dispute': [
        'start_dispute',
        'dispute_reason',
        'trade_chat_open',
        'dispute_trade',
    ],
    'contact': [
        'add_contact',
        'verify_contact',
        'remove_contact',
        'notification_preferences',
    ]
}

# Global registry instance
callback_pattern_registry = CallbackPatternRegistry()

def initialize_standard_namespaces():
    """Initialize the registry with standard namespace patterns"""
    for namespace, patterns in STANDARD_NAMESPACES.items():
        try:
            callback_pattern_registry.register_namespace(namespace, patterns)
        except ValueError as e:
            logger.error(f"Failed to register namespace '{namespace}': {e}")
            
logger.info("🏷️ Callback pattern registry initialized")