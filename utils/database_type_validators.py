"""
Database type validation utilities for foreign key integrity

Ensures type consistency between primary keys and foreign keys to prevent
database constraint violations and performance issues.
"""

from typing import Union, Optional, Type, Any
import logging
from sqlalchemy.orm import Session
from sqlalchemy import BigInteger, Integer, String

logger = logging.getLogger(__name__)


def validate_user_id_type(user_id: Union[int, str, None], allow_null: bool = True) -> Optional[int]:
    """
    Validate user_id for foreign key operations to users.id (BigInteger).
    
    Ensures user_id foreign keys are properly typed to match the BigInteger
    primary key in the users table.
    
    Args:
        user_id: User ID value to validate
        allow_null: Whether None values are acceptable
        
    Returns:
        Validated integer user_id or None if allow_null=True
        
    Raises:
        ValueError: If user_id is invalid and required
        TypeError: If user_id type is unsupported
    """
    if user_id is None:
        if allow_null:
            return None
        raise ValueError("user_id is required but None was provided")
    
    if isinstance(user_id, int):
        if user_id <= 0:
            raise ValueError(f"user_id must be positive: {user_id}")
        return user_id
    
    if isinstance(user_id, str):
        if not user_id.strip():
            if allow_null:
                return None
            raise ValueError("user_id cannot be empty string")
        
        try:
            validated_id = int(user_id.strip())
            if validated_id <= 0:
                raise ValueError(f"user_id must be positive: {validated_id}")
            return validated_id
        except ValueError as e:
            raise ValueError(f"Invalid user_id format '{user_id}': {e}")
    
    raise TypeError(f"user_id must be int or str, got {type(user_id)}: {user_id}")


def validate_foreign_key_type(
    fk_value: Any, 
    target_table: str, 
    target_column: str, 
    expected_type: Type,
    allow_null: bool = True
) -> Any:
    """
    Generic foreign key type validator for database integrity.
    
    Validates that foreign key values match the type of their target primary key
    to prevent constraint violations and ensure optimal performance.
    
    Args:
        fk_value: Foreign key value to validate
        target_table: Name of the target table
        target_column: Name of the target column  
        expected_type: Expected SQLAlchemy type (Integer, BigInteger, String, etc.)
        allow_null: Whether None values are acceptable
        
    Returns:
        Validated foreign key value
        
    Raises:
        ValueError: If foreign key value is invalid
        TypeError: If foreign key type doesn't match expected type
    """
    if fk_value is None:
        if allow_null:
            return None
        raise ValueError(f"Foreign key to {target_table}.{target_column} cannot be None")
    
    # Handle BigInteger foreign keys (e.g., user_id references)
    if expected_type == BigInteger:
        if isinstance(fk_value, int):
            if fk_value <= 0:
                raise ValueError(f"Foreign key to {target_table}.{target_column} must be positive: {fk_value}")
            return fk_value
        
        if isinstance(fk_value, str):
            try:
                validated = int(fk_value.strip())
                if validated <= 0:
                    raise ValueError(f"Foreign key to {target_table}.{target_column} must be positive: {validated}")
                return validated
            except ValueError as e:
                raise ValueError(f"Invalid foreign key format for {target_table}.{target_column} '{fk_value}': {e}")
    
    # Handle Integer foreign keys
    elif expected_type == Integer:
        if isinstance(fk_value, int):
            return fk_value
        
        if isinstance(fk_value, str):
            try:
                return int(fk_value.strip())
            except ValueError as e:
                raise ValueError(f"Invalid integer foreign key for {target_table}.{target_column} '{fk_value}': {e}")
    
    # Handle String foreign keys
    elif expected_type == String:
        if isinstance(fk_value, str):
            return fk_value.strip()
        
        if isinstance(fk_value, (int, float)):
            return str(fk_value)
    
    raise TypeError(
        f"Foreign key to {target_table}.{target_column} has incompatible type. "
        f"Expected: {expected_type.__name__}, Got: {type(fk_value).__name__} (value: {fk_value})"
    )


def audit_foreign_key_types(session: Session, table_name: str) -> dict:
    """
    Audit foreign key types in a specific table for consistency issues.
    
    Identifies foreign keys that don't match their target primary key types,
    which can cause performance issues and constraint violations.
    
    Args:
        session: SQLAlchemy session for database queries
        table_name: Name of table to audit
        
    Returns:
        Dictionary with audit results and recommendations
    """
    audit_results = {
        "table": table_name,
        "foreign_key_issues": [],
        "recommendations": [],
        "performance_impact": "medium"
    }
    
    try:
        # Known critical foreign key type mismatches from audit
        critical_issues = {
            "unified_transactions": [
                {"column": "user_id", "current_type": "INTEGER", "required_type": "BIGINT", 
                 "references": "users.id", "severity": "HIGH"},
                {"column": "admin_approved_by", "current_type": "INTEGER", "required_type": "BIGINT",
                 "references": "users.id", "severity": "HIGH"}
            ],
            "escrows": [
                {"column": "buyer_id", "current_type": "INTEGER", "required_type": "BIGINT",
                 "references": "users.id", "severity": "CRITICAL"},
                {"column": "seller_id", "current_type": "INTEGER", "required_type": "BIGINT",
                 "references": "users.id", "severity": "CRITICAL"}
            ],
            "balance_audit_log": [
                {"column": "escrow_id", "current_type": "VARCHAR(20)", "required_type": "INTEGER FK",
                 "references": "escrows.id", "severity": "HIGH", "note": "Add escrow_pk column"}
            ]
        }
        
        if table_name in critical_issues:
            audit_results["foreign_key_issues"] = critical_issues[table_name]
            audit_results["performance_impact"] = "high"
            
            for issue in critical_issues[table_name]:
                if issue["severity"] == "CRITICAL":
                    audit_results["recommendations"].append(
                        f"URGENT: Fix {issue['column']} type mismatch - impacts escrow operations"
                    )
                else:
                    audit_results["recommendations"].append(
                        f"Fix {issue['column']} from {issue['current_type']} to {issue['required_type']}"
                    )
        else:
            audit_results["status"] = "no_known_issues"
            audit_results["recommendations"].append(
                "No critical foreign key issues identified for this table"
            )
        
        logger.info(f"Audited foreign key types for {table_name}: {len(audit_results['foreign_key_issues'])} issues found")
        
    except Exception as e:
        logger.error(f"Foreign key audit failed for {table_name}: {e}")
        audit_results["error"] = str(e)
    
    return audit_results


# Type aliases for common database ID patterns
UserIdType = Optional[int]  # References users.id (BigInteger)
EscrowIdType = Optional[int]  # References escrows.id (Integer) 
CashoutIdType = Optional[str]  # References cashouts.cashout_id (String)
TelegramIdType = Optional[int]  # Telegram user ID (BigInteger storage)
ChatIdType = Optional[int]  # Telegram chat ID (can be negative)


class DatabaseTypeError(Exception):
    """Raised when database type validation fails"""
    pass


class ForeignKeyTypeError(DatabaseTypeError):
    """Raised when foreign key types don't match target primary key types"""
    pass