"""
Database Schema Validation System
Prevents runtime failures by detecting model/database schema mismatches at startup
"""

import logging
from typing import List, Optional
from dataclasses import dataclass
from sqlalchemy import inspect, text
from models import Base
import os

logger = logging.getLogger(__name__)

@dataclass
class ColumnMismatch:
    """Represents a column mismatch between model and database"""
    table_name: str
    column_name: str
    model_type: str
    db_type: Optional[str]
    issue_type: str  # "missing", "type_mismatch", "extra"
    severity: str  # "critical", "warning", "info"

@dataclass
class SchemaValidationResult:
    """Result of schema validation"""
    is_valid: bool
    critical_issues: List[ColumnMismatch]
    warnings: List[ColumnMismatch]
    auto_fixes_applied: int
    manual_fixes_needed: List[str]

class DatabaseSchemaValidator:
    """Validates database schema against SQLAlchemy models"""
    
    def __init__(self):
        self.critical_tables = ['users', 'transactions', 'escrows', 'wallets', 'cashouts']
        self.auto_fix_enabled = os.getenv('AUTO_SCHEMA_FIX', 'true').lower() == 'true'
        
    def validate_full_schema(self) -> SchemaValidationResult:
        """Perform comprehensive schema validation"""
        logger.info("🔍 Starting comprehensive database schema validation...")
        
        critical_issues = []
        warnings = []
        auto_fixes_applied = 0
        manual_fixes_needed = []
        
        try:
            from database import managed_session
            with managed_session() as session:
                # Get database inspector
                inspector = inspect(session.bind)
                
                # Check each model table
                for table_name, table in Base.metadata.tables.items():
                    logger.debug(f"Validating table: {table_name}")
                    
                    # Check if table exists
                    if not inspector.has_table(table_name):
                        critical_issues.append(ColumnMismatch(
                            table_name=table_name,
                            column_name="*",
                            model_type="table",
                            db_type=None,
                            issue_type="missing_table",
                            severity="critical"
                        ))
                        manual_fixes_needed.append(f"CREATE TABLE {table_name}")
                        continue
                    
                    # Get database columns
                    db_columns = {col['name']: col for col in inspector.get_columns(table_name)}
                    
                    # Check each model column
                    for column in table.columns:
                        column_name = column.name
                        model_type = str(column.type)
                        
                        if column_name not in db_columns:
                            # Missing column in database
                            severity = "critical" if table_name in self.critical_tables else "warning"
                            issue = ColumnMismatch(
                                table_name=table_name,
                                column_name=column_name,
                                model_type=model_type,
                                db_type=None,
                                issue_type="missing",
                                severity=severity
                            )
                            
                            if severity == "critical":
                                critical_issues.append(issue)
                                
                                # Attempt auto-fix for missing columns
                                if self.auto_fix_enabled and self._can_auto_fix_column(column):
                                    if self._auto_fix_missing_column(session, table_name, column):
                                        auto_fixes_applied += 1
                                        logger.info(f"✅ Auto-fixed missing column: {table_name}.{column_name}")
                                    else:
                                        manual_fixes_needed.append(f"ADD COLUMN {table_name}.{column_name}")
                                else:
                                    manual_fixes_needed.append(f"ADD COLUMN {table_name}.{column_name}")
                            else:
                                warnings.append(issue)
                        else:
                            # Column exists, check type compatibility
                            db_type = str(db_columns[column_name]['type'])
                            if not self._are_types_compatible(model_type, db_type):
                                issue = ColumnMismatch(
                                    table_name=table_name,
                                    column_name=column_name,
                                    model_type=model_type,
                                    db_type=db_type,
                                    issue_type="type_mismatch",
                                    severity="warning"
                                )
                                warnings.append(issue)
                    
                    # Check for extra columns in database
                    model_columns = {col.name for col in table.columns}
                    for db_col_name in db_columns:
                        if db_col_name not in model_columns:
                            warnings.append(ColumnMismatch(
                                table_name=table_name,
                                column_name=db_col_name,
                                model_type=None,
                                db_type=str(db_columns[db_col_name]['type']),
                                issue_type="extra",
                                severity="info"
                            ))
                
        except Exception as e:
            logger.error(f"Schema validation failed: {e}")
            critical_issues.append(ColumnMismatch(
                table_name="system",
                column_name="validation",
                model_type="error",
                db_type=None,
                issue_type="validation_error",
                severity="critical"
            ))
        
        is_valid = len(critical_issues) == 0
        
        result = SchemaValidationResult(
            is_valid=is_valid,
            critical_issues=critical_issues,
            warnings=warnings,
            auto_fixes_applied=auto_fixes_applied,
            manual_fixes_needed=manual_fixes_needed
        )
        
        self._log_validation_result(result)
        return result
    
    def _can_auto_fix_column(self, column) -> bool:
        """Check if a column can be safely auto-fixed"""
        # Only auto-fix simple column additions, not complex changes
        safe_types = ['TEXT', 'VARCHAR', 'INTEGER', 'DECIMAL', 'BOOLEAN', 'TIMESTAMP', 'DATETIME']
        column_type = str(column.type).upper()
        
        # Check if it's a safe type to add
        for safe_type in safe_types:
            if safe_type in column_type:
                return True
        return False
    
    def _auto_fix_missing_column(self, session, table_name: str, column) -> bool:
        """Attempt to auto-fix a missing column"""
        try:
            column_name = column.name
            column_type = self._get_sql_type(column)
            nullable = "NULL" if column.nullable else "NOT NULL"
            default = ""
            
            # Add default value if specified
            if column.default is not None:
                if hasattr(column.default, 'arg'):
                    default = f" DEFAULT {column.default.arg}"
                elif column.default.is_scalar:
                    default = f" DEFAULT '{column.default.arg}'"
            
            sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type} {nullable}{default}"
            
            session.execute(text(sql))
            session.commit()
            
            logger.info(f"✅ Successfully added column {table_name}.{column_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to auto-fix column {table_name}.{column_name}: {e}")
            session.rollback()
            return False
    
    def _get_sql_type(self, column) -> str:
        """Convert SQLAlchemy column type to SQL type string"""
        column_type = str(column.type)
        
        # Map common SQLAlchemy types to SQL types
        type_mapping = {
            'TEXT': 'TEXT',
            'VARCHAR': 'VARCHAR(255)',
            'INTEGER': 'INTEGER',
            'DECIMAL': 'DECIMAL(20,8)',
            'BOOLEAN': 'BOOLEAN',
            'TIMESTAMP': 'TIMESTAMP',
            'DATETIME': 'TIMESTAMP'
        }
        
        for sqla_type, sql_type in type_mapping.items():
            if sqla_type in column_type.upper():
                return sql_type
        
        # If we can't map it, use the original type
        return column_type
    
    def _are_types_compatible(self, model_type: str, db_type: str) -> bool:
        """Check if model and database types are compatible"""
        # Normalize types for comparison
        model_type = model_type.upper()
        db_type = db_type.upper()
        
        # Define compatible type groups
        compatible_groups = [
            {'TEXT', 'VARCHAR', 'CHARACTER VARYING'},
            {'INTEGER', 'INT', 'SERIAL'},
            {'DECIMAL', 'NUMERIC'},
            {'FLOAT', 'DOUBLE PRECISION', 'REAL', 'DECIMAL'},
            {'TIMESTAMP', 'DATETIME', 'TIMESTAMP WITHOUT TIME ZONE'},
            {'BOOLEAN', 'BOOL'}
        ]
        
        for group in compatible_groups:
            if any(t in model_type for t in group) and any(t in db_type for t in group):
                return True
        
        return model_type == db_type
    
    def _log_validation_result(self, result: SchemaValidationResult):
        """Log the validation result"""
        if result.is_valid:
            logger.info("✅ Database schema validation PASSED - all schemas match")
        else:
            logger.error(f"❌ Database schema validation FAILED - {len(result.critical_issues)} critical issues found")
        
        if result.auto_fixes_applied > 0:
            logger.info(f"🔧 Auto-fixed {result.auto_fixes_applied} schema issues")
        
        if result.critical_issues:
            logger.error("🚨 CRITICAL SCHEMA ISSUES:")
            for issue in result.critical_issues:
                logger.error(f"  - {issue.table_name}.{issue.column_name}: {issue.issue_type} ({issue.model_type} vs {issue.db_type})")
        
        if result.warnings:
            logger.warning(f"⚠️  Schema warnings ({len(result.warnings)} found):")
            for warning in result.warnings[:5]:  # Show first 5 warnings
                logger.warning(f"  - {warning.table_name}.{warning.column_name}: {warning.issue_type}")
        
        if result.manual_fixes_needed:
            logger.error("🔧 Manual fixes needed:")
            for fix in result.manual_fixes_needed:
                logger.error(f"  - {fix}")

# Global validator instance
schema_validator = DatabaseSchemaValidator()

def validate_database_schema_on_startup():
    """Run schema validation during application startup"""
    logger.info("🚀 Running database schema validation on startup...")
    
    try:
        result = schema_validator.validate_full_schema()
        
        if not result.is_valid:
            if result.critical_issues:
                logger.critical("🚨 CRITICAL: Schema validation failed - application may not function properly")
                
                # In production, we might want to prevent startup
                if os.getenv('STRICT_SCHEMA_VALIDATION', 'false').lower() == 'true':
                    raise RuntimeError("Schema validation failed - refusing to start with critical issues")
            
        return result
        
    except Exception as e:
        logger.error(f"Schema validation error during startup: {e}")
        return SchemaValidationResult(
            is_valid=False,
            critical_issues=[],
            warnings=[],
            auto_fixes_applied=0,
            manual_fixes_needed=[]
        )