"""
Database Migration: Add Version Columns for Optimistic Locking
Adds version columns to critical models for concurrency control
"""

import logging
from sqlalchemy import text
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import sync_engine

logger = logging.getLogger(__name__)


def add_version_columns_migration():
    """
    Add version columns to critical models for optimistic locking
    
    Models updated:
    - users: Add version column
    - escrows: Add version column  
    - wallets: Add version column
    - cashouts: Add version column
    - unified_transactions: Add version column
    """
    
    logger.info("🔄 Starting version columns migration...")
    
    migration_queries = [
        # Add version column to users table
        """
        ALTER TABLE users 
        ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
        """,
        
        # Add version column to escrows table
        """
        ALTER TABLE escrows 
        ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
        """,
        
        # Add version column to wallets table
        """
        ALTER TABLE wallets 
        ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
        """,
        
        # Add version column to cashouts table
        """
        ALTER TABLE cashouts 
        ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
        """,
        
        # Add version column to unified_transactions table
        """
        ALTER TABLE unified_transactions 
        ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
        """,
        
        # Create indexes for version columns for optimistic locking queries
        """
        CREATE INDEX IF NOT EXISTS idx_users_id_version 
        ON users(id, version);
        """,
        
        """
        CREATE INDEX IF NOT EXISTS idx_escrows_id_version 
        ON escrows(id, version);
        """,
        
        """
        CREATE INDEX IF NOT EXISTS idx_wallets_id_version 
        ON wallets(id, version);
        """,
        
        """
        CREATE INDEX IF NOT EXISTS idx_cashouts_id_version 
        ON cashouts(id, version);
        """,
        
        """
        CREATE INDEX IF NOT EXISTS idx_unified_transactions_id_version 
        ON unified_transactions(id, version);
        """,
    ]
    
    try:
        with sync_engine.connect() as conn:
            with conn.begin():  # Transaction for atomicity
                for i, query in enumerate(migration_queries, 1):
                    try:
                        logger.info(f"📝 Executing migration step {i}/{len(migration_queries)}")
                        conn.execute(text(query))
                        logger.debug(f"✅ Completed migration step {i}: {query.strip()[:100]}...")
                    except Exception as e:
                        logger.error(f"❌ Failed migration step {i}: {e}")
                        raise
                        
        logger.info("✅ Version columns migration completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Version columns migration failed: {e}")
        return False


def rollback_version_columns_migration():
    """
    Rollback version columns migration
    WARNING: This will remove all version tracking data
    """
    
    logger.warning("🔄 Starting version columns rollback...")
    
    rollback_queries = [
        # Drop indexes first
        "DROP INDEX IF EXISTS idx_users_id_version;",
        "DROP INDEX IF EXISTS idx_escrows_id_version;",
        "DROP INDEX IF EXISTS idx_wallets_id_version;",
        "DROP INDEX IF EXISTS idx_cashouts_id_version;",
        "DROP INDEX IF EXISTS idx_unified_transactions_id_version;",
        
        # Remove version columns
        "ALTER TABLE users DROP COLUMN IF EXISTS version;",
        "ALTER TABLE escrows DROP COLUMN IF EXISTS version;",
        "ALTER TABLE wallets DROP COLUMN IF EXISTS version;",
        "ALTER TABLE cashouts DROP COLUMN IF EXISTS version;",
        "ALTER TABLE unified_transactions DROP COLUMN IF EXISTS version;",
    ]
    
    try:
        with sync_engine.connect() as conn:
            with conn.begin():
                for i, query in enumerate(rollback_queries, 1):
                    try:
                        logger.info(f"📝 Executing rollback step {i}/{len(rollback_queries)}")
                        conn.execute(text(query))
                        logger.debug(f"✅ Completed rollback step {i}: {query.strip()[:100]}...")
                    except Exception as e:
                        logger.error(f"❌ Failed rollback step {i}: {e}")
                        raise
                        
        logger.warning("⚠️ Version columns rollback completed!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Version columns rollback failed: {e}")
        return False


def verify_version_columns():
    """
    Verify that version columns were added correctly
    """
    
    logger.info("🔍 Verifying version columns migration...")
    
    verification_queries = [
        # Check if version columns exist and have correct defaults
        """
        SELECT 
            table_name,
            column_name,
            data_type,
            column_default,
            is_nullable
        FROM information_schema.columns 
        WHERE table_name IN ('users', 'escrows', 'wallets', 'cashouts', 'unified_transactions')
        AND column_name = 'version'
        ORDER BY table_name;
        """,
        
        # Check if indexes were created
        """
        SELECT 
            schemaname, 
            tablename, 
            indexname, 
            indexdef
        FROM pg_indexes 
        WHERE indexname LIKE '%_id_version'
        ORDER BY tablename;
        """,
        
        # Sample version values from each table
        """
        SELECT 'users' as table_name, COUNT(*) as total_rows, MIN(version) as min_version, MAX(version) as max_version
        FROM users
        UNION ALL
        SELECT 'escrows', COUNT(*), MIN(version), MAX(version) FROM escrows
        UNION ALL  
        SELECT 'wallets', COUNT(*), MIN(version), MAX(version) FROM wallets
        UNION ALL
        SELECT 'cashouts', COUNT(*), MIN(version), MAX(version) FROM cashouts
        UNION ALL
        SELECT 'unified_transactions', COUNT(*), MIN(version), MAX(version) FROM unified_transactions;
        """,
    ]
    
    try:
        with sync_engine.connect() as conn:
            for i, query in enumerate(verification_queries, 1):
                logger.info(f"🔍 Running verification query {i}/{len(verification_queries)}")
                result = conn.execute(text(query))
                rows = result.fetchall()
                
                if i == 1:  # Column verification
                    logger.info("📋 Version columns found:")
                    for row in rows:
                        logger.info(f"  • {row.table_name}.{row.column_name}: {row.data_type}, default={row.column_default}, nullable={row.is_nullable}")
                        
                elif i == 2:  # Index verification
                    logger.info("📋 Version indexes found:")
                    for row in rows:
                        logger.info(f"  • {row.indexname} on {row.tablename}")
                        
                elif i == 3:  # Data verification
                    logger.info("📋 Version column data summary:")
                    for row in rows:
                        logger.info(f"  • {row.table_name}: {row.total_rows} rows, versions {row.min_version}-{row.max_version}")
        
        logger.info("✅ Version columns verification completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Version columns verification failed: {e}")
        return False


if __name__ == "__main__":
    # Run migration
    success = add_version_columns_migration()
    
    if success:
        # Verify the migration
        verify_version_columns()
        logger.info("🎉 Version columns migration and verification completed!")
    else:
        logger.error("💥 Version columns migration failed!")
        exit(1)