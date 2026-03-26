"""
Migration 008: Add version columns for optimistic locking

This migration adds version columns to critical models for implementing
optimistic locking in financial operations to prevent race conditions.
"""

from sqlalchemy import text
from database import sync_engine
import logging

logger = logging.getLogger(__name__)

def upgrade():
    """Add version columns to critical models for optimistic locking"""
    
    logger.info("🔄 Starting migration 008: Adding version columns for optimistic locking")
    
    with sync_engine.connect() as connection:
        try:
            # Start transaction
            trans = connection.begin()
            
            # Add version column to users table
            logger.info("📊 Adding version column to users table")
            connection.execute(text("""
                ALTER TABLE users 
                ADD COLUMN version INTEGER DEFAULT 1 NOT NULL;
            """))
            
            # Add index for better performance
            connection.execute(text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_version 
                ON users (version);
            """))
            
            # Add version column to wallets table
            logger.info("📊 Adding version column to wallets table")
            connection.execute(text("""
                ALTER TABLE wallets 
                ADD COLUMN version INTEGER DEFAULT 1 NOT NULL;
            """))
            
            # Add index for better performance  
            connection.execute(text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wallets_version 
                ON wallets (version);
            """))
            
            # Add version column to escrows table
            logger.info("📊 Adding version column to escrows table")
            connection.execute(text("""
                ALTER TABLE escrows 
                ADD COLUMN version INTEGER DEFAULT 1 NOT NULL;
            """))
            
            # Add index for better performance
            connection.execute(text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_escrows_version 
                ON escrows (version);
            """))
            
            # Add version column to cashouts table
            logger.info("📊 Adding version column to cashouts table")
            connection.execute(text("""
                ALTER TABLE cashouts 
                ADD COLUMN version INTEGER DEFAULT 1 NOT NULL;
            """))
            
            # Add index for better performance
            connection.execute(text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cashouts_version 
                ON cashouts (version);
            """))
            
            # Add version column to unified_transactions table
            logger.info("📊 Adding version column to unified_transactions table")
            connection.execute(text("""
                ALTER TABLE unified_transactions 
                ADD COLUMN version INTEGER DEFAULT 1 NOT NULL;
            """))
            
            # Add index for better performance
            connection.execute(text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_unified_transactions_version 
                ON unified_transactions (version);
            """))
            
            # Commit the transaction
            trans.commit()
            logger.info("✅ Migration 008 completed successfully: Version columns added to all critical models")
            
        except Exception as e:
            # Rollback on error
            trans.rollback()
            logger.error(f"❌ Migration 008 failed: {e}")
            raise

def downgrade():
    """Remove version columns (rollback migration)"""
    
    logger.info("🔄 Starting migration 008 rollback: Removing version columns")
    
    with sync_engine.connect() as connection:
        try:
            # Start transaction
            trans = connection.begin()
            
            # Remove version columns and indexes
            tables = ['users', 'wallets', 'escrows', 'cashouts', 'unified_transactions']
            
            for table in tables:
                logger.info(f"📊 Removing version column from {table} table")
                
                # Drop index first
                connection.execute(text(f"""
                    DROP INDEX IF EXISTS idx_{table}_version;
                """))
                
                # Drop column
                connection.execute(text(f"""
                    ALTER TABLE {table} DROP COLUMN IF EXISTS version;
                """))
            
            # Commit the transaction
            trans.commit()
            logger.info("✅ Migration 008 rollback completed successfully")
            
        except Exception as e:
            # Rollback on error
            trans.rollback()
            logger.error(f"❌ Migration 008 rollback failed: {e}")
            raise

if __name__ == "__main__":
    # Run the migration
    upgrade()