"""
Optimistic Locking Infrastructure
Version-based concurrency control to prevent race conditions in database operations
"""

import logging
from typing import Any, Optional, Dict, Type, List
from datetime import datetime
from contextlib import contextmanager
from functools import wraps
from sqlalchemy import Column, Integer, DateTime, func, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from models import Base

logger = logging.getLogger(__name__)


class OptimisticLockingError(Exception):
    """Raised when optimistic locking fails due to version conflict"""
    pass


class VersionMixin:
    """
    Mixin class to add version tracking to database models
    Provides automatic version incrementing and conflict detection
    """
    
    version = Column(Integer, nullable=False, default=1, server_default="1")
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    
    def increment_version(self):
        """Increment version for manual version control"""
        self.version = (self.version or 0) + 1
        self.updated_at = datetime.utcnow()
        
    def reset_version(self):
        """Reset version to 1 (use with caution)"""
        self.version = 1
        self.updated_at = datetime.utcnow()


class OptimisticLockManager:
    """
    Manager for optimistic locking operations
    Handles version-based updates, conflict detection, and retry logic
    """
    
    def __init__(self, session: Session):
        self.session = session
        
    def versioned_update(
        self, 
        model_class: Type[Base], 
        entity_id: Any, 
        updates: Dict[str, Any], 
        current_version: Optional[int] = None
    ) -> bool:
        """
        Perform version-controlled update
        
        Args:
            model_class: SQLAlchemy model class
            entity_id: Primary key value
            updates: Dictionary of field updates
            current_version: Expected current version (fetched if not provided)
            
        Returns:
            bool: True if update successful, False if version conflict
            
        Raises:
            OptimisticLockingError: If version conflict detected
        """
        try:
            # Get current version if not provided
            if current_version is None:
                current_obj = self.session.query(model_class).filter(
                    model_class.id == entity_id
                ).first()
                
                if not current_obj:
                    raise ValueError(f"Entity {model_class.__name__} with id {entity_id} not found")
                
                current_version = current_obj.version
            
            # Prepare update with version increment
            update_values = {
                **updates,
                'version': current_version + 1,
                'updated_at': datetime.utcnow()
            }
            
            # Execute version-controlled update
            stmt = update(model_class).where(
                model_class.id == entity_id,
                model_class.version == current_version
            ).values(update_values)
            
            result = self.session.execute(stmt)
            
            if result.rowcount == 0:
                # No rows updated - version conflict
                logger.warning(
                    f"🔒 Optimistic lock conflict: {model_class.__name__} id={entity_id} "
                    f"expected_version={current_version}"
                )
                raise OptimisticLockingError(
                    f"Version conflict for {model_class.__name__} id={entity_id}. "
                    f"Expected version {current_version} but entity was modified by another process."
                )
            
            logger.debug(
                f"✅ Versioned update successful: {model_class.__name__} id={entity_id} "
                f"v{current_version} → v{current_version + 1}"
            )
            return True
            
        except SQLAlchemyError as e:
            logger.error(f"❌ Database error during versioned update: {e}")
            raise
    
    def versioned_delete(
        self, 
        model_class: Type[Base], 
        entity_id: Any, 
        current_version: Optional[int] = None
    ) -> bool:
        """
        Perform version-controlled delete
        
        Args:
            model_class: SQLAlchemy model class
            entity_id: Primary key value
            current_version: Expected current version (fetched if not provided)
            
        Returns:
            bool: True if delete successful
            
        Raises:
            OptimisticLockingError: If version conflict detected
        """
        try:
            # Get current version if not provided
            if current_version is None:
                current_obj = self.session.query(model_class).filter(
                    model_class.id == entity_id
                ).first()
                
                if not current_obj:
                    raise ValueError(f"Entity {model_class.__name__} with id {entity_id} not found")
                
                current_version = current_obj.version
            
            # Execute version-controlled delete
            stmt = model_class.__table__.delete().where(
                model_class.id == entity_id,
                model_class.version == current_version
            )
            
            result = self.session.execute(stmt)
            
            if result.rowcount == 0:
                # No rows deleted - version conflict
                logger.warning(
                    f"🔒 Optimistic lock conflict on delete: {model_class.__name__} id={entity_id} "
                    f"expected_version={current_version}"
                )
                raise OptimisticLockingError(
                    f"Version conflict for {model_class.__name__} id={entity_id}. "
                    f"Expected version {current_version} but entity was modified by another process."
                )
            
            logger.debug(
                f"🗑️ Versioned delete successful: {model_class.__name__} id={entity_id} "
                f"v{current_version}"
            )
            return True
            
        except SQLAlchemyError as e:
            logger.error(f"❌ Database error during versioned delete: {e}")
            raise
    
    def get_with_version(
        self, 
        model_class: Type[Base], 
        entity_id: Any
    ) -> Optional[tuple]:
        """
        Get entity with its current version
        
        Returns:
            tuple: (entity, version) or None if not found
        """
        try:
            entity = self.session.query(model_class).filter(
                model_class.id == entity_id
            ).first()
            
            if entity:
                return (entity, entity.version)
            return None
            
        except SQLAlchemyError as e:
            logger.error(f"❌ Database error during get_with_version: {e}")
            raise
    
    def validate_version(
        self, 
        model_class: Type[Base], 
        entity_id: Any, 
        expected_version: int
    ) -> bool:
        """
        Validate that entity has expected version
        
        Returns:
            bool: True if version matches, False otherwise
        """
        try:
            current_version = self.session.query(model_class.version).filter(
                model_class.id == entity_id
            ).scalar()
            
            return current_version == expected_version
            
        except SQLAlchemyError as e:
            logger.error(f"❌ Database error during version validation: {e}")
            return False


def with_optimistic_locking(
    max_retries: int = 3, 
    retry_delay: float = 0.1, 
    backoff_factor: float = 2.0
):
    """
    Decorator to automatically handle optimistic locking with retry logic
    
    Args:
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries in seconds
        backoff_factor: Exponential backoff multiplier
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time
            
            last_exception = None
            current_delay = retry_delay
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                    
                except OptimisticLockingError as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        logger.info(
                            f"🔄 Optimistic lock retry {attempt + 1}/{max_retries} "
                            f"for {func.__name__}: {e}"
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logger.error(
                            f"❌ Optimistic lock failed after {max_retries} retries "
                            f"for {func.__name__}: {e}"
                        )
                        raise
                        
                except Exception as e:
                    # Don't retry for non-locking errors
                    logger.error(f"❌ Non-retryable error in {func.__name__}: {e}")
                    raise
            
            # This should never be reached, but just in case
            raise last_exception
            
        return wrapper
    return decorator


def with_async_optimistic_locking(
    max_retries: int = 3, 
    retry_delay: float = 0.1, 
    backoff_factor: float = 2.0
):
    """
    Async version of optimistic locking decorator
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            import asyncio
            
            last_exception = None
            current_delay = retry_delay
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                    
                except OptimisticLockingError as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        logger.info(
                            f"🔄 Async optimistic lock retry {attempt + 1}/{max_retries} "
                            f"for {func.__name__}: {e}"
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logger.error(
                            f"❌ Async optimistic lock failed after {max_retries} retries "
                            f"for {func.__name__}: {e}"
                        )
                        raise
                        
                except Exception as e:
                    # Don't retry for non-locking errors
                    logger.error(f"❌ Non-retryable async error in {func.__name__}: {e}")
                    raise
            
            # This should never be reached, but just in case
            raise last_exception
            
        return wrapper
    return decorator


@contextmanager
def optimistic_transaction(
    session: Session, 
    commit_on_success: bool = True,
    max_retries: int = 3
):
    """
    Context manager for transactions with optimistic locking support
    
    Args:
        session: SQLAlchemy session
        commit_on_success: Whether to auto-commit on successful completion
        max_retries: Number of retries on version conflicts
    """
    lock_manager = OptimisticLockManager(session)
    
    for attempt in range(max_retries + 1):
        try:
            yield lock_manager
            
            if commit_on_success:
                session.commit()
                logger.debug("✅ Optimistic transaction committed successfully")
            break
            
        except OptimisticLockingError as e:
            session.rollback()
            
            if attempt < max_retries:
                logger.info(f"🔄 Retrying transaction due to optimistic lock conflict (attempt {attempt + 1})")
                continue
            else:
                logger.error(f"❌ Transaction failed after {max_retries} retries: {e}")
                raise
                
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Transaction failed with error: {e}")
            raise


class VersionedEntityManager:
    """
    High-level manager for versioned entities
    Provides convenient methods for common versioned operations
    """
    
    def __init__(self, session: Session):
        self.session = session
        self.lock_manager = OptimisticLockManager(session)
    
    @with_optimistic_locking(max_retries=3)
    def safe_update(
        self, 
        entity: Base, 
        updates: Dict[str, Any]
    ) -> bool:
        """
        Safely update entity with automatic version checking
        
        Args:
            entity: Entity instance with version
            updates: Dictionary of field updates
            
        Returns:
            bool: True if successful
        """
        if not hasattr(entity, 'version'):
            raise ValueError("Entity must have version attribute for safe updates")
        
        return self.lock_manager.versioned_update(
            entity.__class__,
            entity.id,
            updates,
            entity.version
        )
    
    @with_optimistic_locking(max_retries=3)
    def safe_delete(self, entity: Base) -> bool:
        """
        Safely delete entity with version checking
        
        Args:
            entity: Entity instance with version
            
        Returns:
            bool: True if successful
        """
        if not hasattr(entity, 'version'):
            raise ValueError("Entity must have version attribute for safe deletes")
        
        return self.lock_manager.versioned_delete(
            entity.__class__,
            entity.id,
            entity.version
        )
    
    def batch_versioned_update(
        self, 
        updates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Perform batch updates with version control
        
        Args:
            updates: List of update dictionaries with keys:
                - model_class: SQLAlchemy model class
                - entity_id: Primary key value  
                - updates: Field updates
                - current_version: Expected version (optional)
                
        Returns:
            dict: Results summary with success/failure counts
        """
        results = {
            'total': len(updates),
            'successful': 0,
            'failed': 0,
            'conflicts': 0,
            'errors': []
        }
        
        for update_data in updates:
            try:
                self.lock_manager.versioned_update(
                    update_data['model_class'],
                    update_data['entity_id'],
                    update_data['updates'],
                    update_data.get('current_version')
                )
                results['successful'] += 1
                
            except OptimisticLockingError as e:
                results['failed'] += 1
                results['conflicts'] += 1
                results['errors'].append({
                    'type': 'version_conflict',
                    'entity_id': update_data['entity_id'],
                    'error': str(e)
                })
                
            except Exception as e:
                results['failed'] += 1
                results['errors'].append({
                    'type': 'general_error',
                    'entity_id': update_data['entity_id'],
                    'error': str(e)
                })
        
        logger.info(
            f"📊 Batch versioned update completed: "
            f"{results['successful']}/{results['total']} successful, "
            f"{results['conflicts']} conflicts"
        )
        
        return results


def add_version_tracking(model_class: Type[Base]) -> Type[Base]:
    """
    Dynamically add version tracking to an existing model class
    
    Args:
        model_class: SQLAlchemy model class
        
    Returns:
        Modified model class with version tracking
    """
    if hasattr(model_class, 'version'):
        logger.debug(f"Model {model_class.__name__} already has version tracking")
        return model_class
    
    # Add version and updated_at columns
    model_class.version = Column(Integer, nullable=False, default=1, server_default="1")
    model_class.updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    
    # Add helper methods
    def increment_version(self):
        self.version = (self.version or 0) + 1
        self.updated_at = datetime.utcnow()
    
    def reset_version(self):
        self.version = 1
        self.updated_at = datetime.utcnow()
    
    model_class.increment_version = increment_version
    model_class.reset_version = reset_version
    
    logger.info(f"✅ Added version tracking to {model_class.__name__}")
    return model_class


# Utility functions for version checking

def check_version_compatibility(
    session: Session,
    model_class: Type[Base], 
    entity_id: Any, 
    expected_version: int
) -> bool:
    """
    Check if entity version matches expected version
    
    Returns:
        bool: True if versions match
    """
    lock_manager = OptimisticLockManager(session)
    return lock_manager.validate_version(model_class, entity_id, expected_version)


def get_entity_version(
    session: Session,
    model_class: Type[Base], 
    entity_id: Any
) -> Optional[int]:
    """
    Get current version of an entity
    
    Returns:
        int: Current version or None if entity not found
    """
    try:
        version = session.query(model_class.version).filter(
            model_class.id == entity_id
        ).scalar()
        return version
    except Exception as e:
        logger.error(f"❌ Error getting entity version: {e}")
        return None


def increment_version_bulk(
    session: Session,
    model_class: Type[Base], 
    entity_ids: List[Any]
) -> int:
    """
    Bulk increment versions for multiple entities
    
    Returns:
        int: Number of entities updated
    """
    try:
        stmt = update(model_class).where(
            model_class.id.in_(entity_ids)
        ).values(
            version=model_class.version + 1,
            updated_at=datetime.utcnow()
        )
        
        result = session.execute(stmt)
        logger.info(f"📈 Bulk version increment: {result.rowcount} entities updated")
        return result.rowcount
        
    except Exception as e:
        logger.error(f"❌ Error in bulk version increment: {e}")
        return 0