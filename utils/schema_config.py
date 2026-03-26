"""
Schema Management Configuration
Centralized configuration for database schema monitoring and validation
"""

import os
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class SchemaConfig:
    """Configuration for schema management system"""
    
    # Validation settings
    auto_fix_enabled: bool = True
    strict_validation: bool = False
    validation_on_startup: bool = True
    
    # Monitoring settings
    monitoring_interval_hours: int = 6
    alert_check_interval_minutes: int = 5
    
    # Alert settings
    enable_schema_alerts: bool = True
    alert_cooldown_minutes: int = 30
    escalation_threshold: int = 3
    
    # Auto-fix settings
    auto_fix_missing_columns: bool = True
    auto_fix_safe_types_only: bool = True
    backup_before_fix: bool = True
    
    # Critical tables that require immediate attention
    critical_tables: Optional[List[str]] = None
    
    # Safe column types for auto-fixing
    safe_column_types: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.critical_tables is None:
            self.critical_tables = [
                'users', 'transactions', 'escrows', 'wallets', 'cashouts',
                'exchange_orders', 'user_ratings', 'disputes'
            ]
        
        if self.safe_column_types is None:
            self.safe_column_types = [
                'TEXT', 'VARCHAR', 'INTEGER', 'DECIMAL', 'BOOLEAN', 
                'TIMESTAMP', 'DATETIME', 'JSON'
            ]

def load_schema_config() -> SchemaConfig:
    """Load schema configuration from environment variables"""
    return SchemaConfig(
        auto_fix_enabled=os.getenv('SCHEMA_AUTO_FIX_ENABLED', 'true').lower() == 'true',
        strict_validation=os.getenv('STRICT_SCHEMA_VALIDATION', 'false').lower() == 'true',
        validation_on_startup=os.getenv('SCHEMA_VALIDATION_ON_STARTUP', 'true').lower() == 'true',
        
        monitoring_interval_hours=int(os.getenv('SCHEMA_MONITORING_INTERVAL_HOURS', '6')),
        alert_check_interval_minutes=int(os.getenv('SCHEMA_ALERT_CHECK_INTERVAL_MINUTES', '5')),
        
        enable_schema_alerts=os.getenv('ENABLE_SCHEMA_ALERTS', 'true').lower() == 'true',
        alert_cooldown_minutes=int(os.getenv('SCHEMA_ALERT_COOLDOWN_MINUTES', '30')),
        escalation_threshold=int(os.getenv('SCHEMA_ESCALATION_THRESHOLD', '3')),
        
        auto_fix_missing_columns=os.getenv('SCHEMA_AUTO_FIX_COLUMNS', 'true').lower() == 'true',
        auto_fix_safe_types_only=os.getenv('SCHEMA_AUTO_FIX_SAFE_ONLY', 'true').lower() == 'true',
        backup_before_fix=os.getenv('SCHEMA_BACKUP_BEFORE_FIX', 'true').lower() == 'true'
    )

# Global config instance
schema_config = load_schema_config()