"""
Secure Database Operations
Provides SQL injection protection and safe query execution
"""

import logging
from typing import Any, Dict, List
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import re

logger = logging.getLogger(__name__)


class SecureDatabaseOperations:
    """Secure database operations with SQL injection protection"""
    
    # Dangerous SQL keywords that should never appear in user input
    DANGEROUS_SQL_KEYWORDS = [
        'DROP', 'DELETE', 'TRUNCATE', 'ALTER', 'CREATE', 'INSERT',
        'UPDATE', 'EXEC', 'EXECUTE', 'UNION', 'SCRIPT', 'DECLARE',
        'SHUTDOWN', 'BACKUP', 'RESTORE'
    ]
    
    @staticmethod
    def validate_column_name(column_name: str) -> bool:
        """
        Validate that a column name is safe for dynamic queries
        
        Args:
            column_name: Column name to validate
            
        Returns:
            True if column name is safe
        """
        # Only allow alphanumeric characters and underscores
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column_name):
            return False
        
        # Check against dangerous keywords
        if column_name.upper() in SecureDatabaseOperations.DANGEROUS_SQL_KEYWORDS:
            return False
        
        return True
    
    @staticmethod
    def validate_table_name(table_name: str) -> bool:
        """
        Validate that a table name is safe for dynamic queries
        
        Args:
            table_name: Table name to validate
            
        Returns:
            True if table name is safe
        """
        return SecureDatabaseOperations.validate_column_name(table_name)
    
    @staticmethod
    def safe_update_user_field(session: Session, user_id: int, field_name: str, 
                              field_value: Any, table_name: str = "users") -> bool:
        """
        Safely update a user field using parameterized queries
        
        Args:
            session: Database session
            user_id: User ID
            field_name: Field name to update
            field_value: New field value
            table_name: Table name (default: users)
            
        Returns:
            True if update successful
        """
        try:
            # Validate inputs
            if not SecureDatabaseOperations.validate_table_name(table_name):
                logger.error(f"Invalid table name: {table_name}")
                return False
            
            if not SecureDatabaseOperations.validate_column_name(field_name):
                logger.error(f"Invalid column name: {field_name}")
                return False
            
            # Use parameterized query with text() and bound parameters
            # This prevents SQL injection even with dynamic field names
            query = text(f"""
                UPDATE {table_name} 
                SET {field_name} = :field_value 
                WHERE id = :user_id
            """)
            
            result = session.execute(query, {
                "field_value": field_value,
                "user_id": user_id
            })
            
            session.commit()
            
            if result.rowcount == 0:
                logger.warning(f"No rows updated for user {user_id}, field {field_name}")
                return False
            
            logger.info(f"Successfully updated {field_name} for user {user_id}")
            return True
            
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error updating user field: {e}")
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"Unexpected error updating user field: {e}")
            return False
    
    @staticmethod
    def safe_select_with_conditions(session: Session, table_name: str, 
                                   conditions: Dict[str, Any], 
                                   columns: List[str] = None) -> List[Dict[str, Any]]:
        """
        Safely select data with conditions using parameterized queries
        
        Args:
            session: Database session
            table_name: Table name
            conditions: Dictionary of column:value conditions
            columns: List of columns to select (default: all)
            
        Returns:
            List of result dictionaries
        """
        try:
            # Validate table name
            if not SecureDatabaseOperations.validate_table_name(table_name):
                logger.error(f"Invalid table name: {table_name}")
                return []
            
            # Validate column names in conditions
            for column in conditions.keys():
                if not SecureDatabaseOperations.validate_column_name(column):
                    logger.error(f"Invalid column name in conditions: {column}")
                    return []
            
            # Validate column names in select
            if columns:
                for column in columns:
                    if not SecureDatabaseOperations.validate_column_name(column):
                        logger.error(f"Invalid column name in select: {column}")
                        return []
                column_list = ", ".join(columns)
            else:
                column_list = "*"
            
            # Build WHERE clause
            where_conditions = []
            params = {}
            
            for i, (column, value) in enumerate(conditions.items()):
                param_name = f"param_{i}"
                where_conditions.append(f"{column} = :{param_name}")
                params[param_name] = value
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            
            # Execute safe parameterized query
            query = text(f"""
                SELECT {column_list}
                FROM {table_name}
                WHERE {where_clause}
            """)
            
            result = session.execute(query, params)
            
            # Convert to list of dictionaries
            columns_names = result.keys()
            rows = []
            for row in result.fetchall():
                rows.append(dict(zip(columns_names, row)))
            
            return rows
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in safe select: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in safe select: {e}")
            return []
    
    @staticmethod
    def execute_safe_query(session: Session, query: str, params: Dict[str, Any] = None) -> Any:
        """
        Execute a safe parameterized query
        
        Args:
            session: Database session
            query: SQL query with named parameters
            params: Query parameters
            
        Returns:
            Query result
        """
        try:
            # Check for dangerous patterns in the query
            query_upper = query.upper()
            for keyword in SecureDatabaseOperations.DANGEROUS_SQL_KEYWORDS:
                if keyword in query_upper and not any(
                    safe_context in query_upper for safe_context in 
                    ['SELECT', 'UPDATE SET', 'WHERE', 'INSERT INTO']
                ):
                    logger.error(f"Potentially dangerous SQL keyword detected: {keyword}")
                    raise ValueError(f"Dangerous SQL operation: {keyword}")
            
            # Execute with parameters
            result = session.execute(text(query), params or {})
            return result
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in safe query execution: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in safe query execution: {e}")
            raise
    
    @staticmethod
    def bulk_update_safe(session: Session, table_name: str, updates: List[Dict[str, Any]], 
                        id_column: str = "id") -> bool:
        """
        Safely perform bulk updates using parameterized queries
        
        Args:
            session: Database session
            table_name: Table name
            updates: List of update dictionaries with id and field values
            id_column: ID column name (default: id)
            
        Returns:
            True if all updates successful
        """
        try:
            # Validate table and column names
            if not SecureDatabaseOperations.validate_table_name(table_name):
                logger.error(f"Invalid table name: {table_name}")
                return False
            
            if not SecureDatabaseOperations.validate_column_name(id_column):
                logger.error(f"Invalid ID column name: {id_column}")
                return False
            
            success_count = 0
            
            for update_data in updates:
                if id_column not in update_data:
                    logger.error(f"Missing ID column {id_column} in update data")
                    continue
                
                record_id = update_data[id_column]
                fields_to_update = {k: v for k, v in update_data.items() if k != id_column}
                
                # Validate all field names
                if not all(SecureDatabaseOperations.validate_column_name(field) 
                          for field in fields_to_update.keys()):
                    logger.error("Invalid field name in update data")
                    continue
                
                # Build update query
                set_clauses = []
                params = {id_column: record_id}
                
                for i, (field, value) in enumerate(fields_to_update.items()):
                    param_name = f"field_{i}"
                    set_clauses.append(f"{field} = :{param_name}")
                    params[param_name] = value
                
                if not set_clauses:
                    continue
                
                set_clause = ", ".join(set_clauses)
                
                query = text(f"""
                    UPDATE {table_name}
                    SET {set_clause}
                    WHERE {id_column} = :{id_column}
                """)
                
                result = session.execute(query, params)
                
                if result.rowcount > 0:
                    success_count += 1
            
            session.commit()
            logger.info(f"Bulk update completed: {success_count}/{len(updates)} records updated")
            
            return success_count == len(updates)
            
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error in bulk update: {e}")
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"Unexpected error in bulk update: {e}")
            return False


# Global instance
secure_db = SecureDatabaseOperations()