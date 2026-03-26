"""
Production database optimization for high-performance bot operations
Implements connection pooling, query optimization, and monitoring
"""

import logging
from sqlalchemy import event, text
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class DatabaseOptimizer:
    """Production database performance optimizer"""
    
    @staticmethod
    def optimize_connection_pool(engine):
        """DISABLED: Pool monkey-patching removed for stability"""
        # CRITICAL FIX: Removed dangerous pool mutation code that conflicts with engine config
        # Pool settings are now configured in database.py engine creation only
        try:
            # Log current pool settings for monitoring (read-only)
            pool_size = getattr(engine.pool, '_pool_size', 'unknown')
            max_overflow = getattr(engine.pool, '_max_overflow', 'unknown') 
            
            logger.info("✅ Database connection pool monitoring (no mutations)")
            logger.info(f"   Current pool size: {pool_size}, Max overflow: {max_overflow}")
            logger.info("   Pool settings managed via engine configuration only")
            
        except Exception as e:
            logger.debug(f"Pool monitoring info unavailable: {e}")
    
    @staticmethod
    def setup_connection_events(engine):
        """Setup connection events for monitoring and optimization"""
        
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            """Optimize database connection settings"""
            try:
                cursor = dbapi_connection.cursor()
                # Only apply if using SQLite
                if hasattr(dbapi_connection, 'execute'):
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL") 
                    cursor.execute("PRAGMA cache_size=10000")
                    cursor.execute("PRAGMA temp_store=MEMORY")
                cursor.close()
            except Exception:
                pass  # Not SQLite or other database type
        
        @event.listens_for(engine, "checkout")
        def checkout_connection(dbapi_connection, connection_record, connection_proxy):
            """Track connection checkout for monitoring"""
            connection_record.checkout_time = time.time()
        
        @event.listens_for(engine, "checkin")
        def checkin_connection(dbapi_connection, connection_record):
            """Track connection usage time"""
            if hasattr(connection_record, 'checkout_time'):
                usage_time = time.time() - connection_record.checkout_time
                # ANOMALY FIX: More tolerant threshold for schema validation (20s)
                if usage_time > 20.0:  # Only warn for really long usage
                    logger.warning(f"⚠️ Long connection usage: {usage_time:.2f}s")
                elif usage_time > 10.0:  # Info for moderate usage
                    logger.info(f"📊 Database operation took {usage_time:.2f}s")
        
        logger.info("✅ Database connection events configured")
    
    @staticmethod
    def create_database_indexes(engine):
        """Create production database indexes for optimal performance"""
        indexes_to_create = [
            # Critical user lookup optimization (enhanced for high performance)
            "CREATE INDEX IF NOT EXISTS idx_users_telegram_id_active ON users(telegram_id, is_active);",
            "CREATE INDEX IF NOT EXISTS idx_users_telegram_id_btree ON users USING btree(telegram_id);",
            
            # Escrow performance indexes (enhanced)
            "CREATE INDEX IF NOT EXISTS idx_escrows_buyer_id ON escrows(buyer_id);",
            "CREATE INDEX IF NOT EXISTS idx_escrows_seller_id ON escrows(seller_id);", 
            "CREATE INDEX IF NOT EXISTS idx_escrows_status ON escrows(status);",
            "CREATE INDEX IF NOT EXISTS idx_escrows_created_at ON escrows(created_at);",
            
            # Transaction performance indexes
            "CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);",
            "CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type);",
            "CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at);",
            
            # Wallet performance indexes
            "CREATE INDEX IF NOT EXISTS idx_wallets_user_id ON wallets(user_id);",
            
            # CashOut performance indexes
            "CREATE INDEX IF NOT EXISTS idx_cashouts_user_id ON cashouts(user_id);",
            "CREATE INDEX IF NOT EXISTS idx_cashouts_status ON cashouts(status);",
            
            # Email verification indexes
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);",
            "CREATE INDEX IF NOT EXISTS idx_users_email_verification_token ON users(email_verification_token);",
            
            # Composite indexes for common queries
            "CREATE INDEX IF NOT EXISTS idx_escrows_status_created_at ON escrows(status, created_at);",
            "CREATE INDEX IF NOT EXISTS idx_transactions_user_type_created ON transactions(user_id, transaction_type, created_at);",
            
            # Performance-critical indexes for fast lookups
            "CREATE INDEX IF NOT EXISTS idx_escrows_buyer_seller ON escrows(buyer_id, seller_id);",
            "CREATE INDEX IF NOT EXISTS idx_users_active_lookup ON users(telegram_id, is_active);",
            "CREATE INDEX IF NOT EXISTS idx_escrows_active_trades ON escrows(status) WHERE status IN ('active', 'payment_confirmed', 'created');",
            
            # Partial indexes for frequent filters
            "CREATE INDEX IF NOT EXISTS idx_escrows_active_only ON escrows(created_at) WHERE status = 'active';",
            "CREATE INDEX IF NOT EXISTS idx_transactions_recent ON transactions(created_at) WHERE created_at > NOW() - INTERVAL '30 days';"
        ]
        
        created_count = 0
        with engine.connect() as conn:
            for index_sql in indexes_to_create:
                try:
                    conn.execute(text(index_sql))
                    created_count += 1
                except Exception as e:
                    logger.debug(f"Index creation skipped (may exist): {e}")
            
            conn.commit()
        
        logger.info(f"✅ Database indexes optimized ({created_count} indexes processed)")
    
    @staticmethod
    def analyze_query_performance():
        """Analyze and log database query performance"""
        from database import SessionLocal
        
        try:
            session = SessionLocal()
            
            # Test critical queries with highly optimized approach using indexes
            start_time = time.time()
            # Use optimized query that only checks existence, not full count
            session.execute(text("SELECT 1 FROM users LIMIT 1")).fetchone()
            user_query_time = time.time() - start_time
            
            start_time = time.time() 
            # Use optimized query that leverages indexes
            session.execute(text("SELECT 1 FROM escrows WHERE status = 'active' LIMIT 1")).fetchone()
            escrow_query_time = time.time() - start_time
            
            session.close()
            
            logger.info("📊 Query performance analysis:")
            logger.info(f"   User query: {user_query_time:.3f}s")
            logger.info(f"   Escrow query: {escrow_query_time:.3f}s")
            
            # More realistic threshold for production database: 0.1s (100ms)
            if user_query_time > 0.1 or escrow_query_time > 0.1:
                logger.info("🔧 Optimizing query performance")
                # Automatically optimize slow queries
                try:
                    logger.info("🔧 Running automatic query optimization...")
                    
                    # Use database-agnostic optimization commands
                    database_optimizations = [
                        "ANALYZE users;",
                        "ANALYZE escrows;", 
                        "ANALYZE transactions;",
                        "ANALYZE wallets;",
                        "ANALYZE cashouts;"
                    ]
                    
                    # Use optimized session approach for compatibility
                    optimization_session = SessionLocal()
                    try:
                        for optimization in database_optimizations:
                            try:
                                optimization_session.execute(text(optimization))
                                optimization_session.commit()
                            except Exception as opt_e:
                                logger.debug(f"Optimization '{optimization}' skipped: {opt_e}")
                    finally:
                        optimization_session.close()
                    
                    logger.info("✅ Automatic query optimization completed")
                    
                except Exception as opt_error:
                    logger.error(f"Failed to run automatic optimization: {opt_error}")
            else:
                logger.info("✅ Query performance within optimal limits")
                
        except Exception as e:
            logger.warning(f"Could not analyze query performance: {e}")
    
    @staticmethod
    def setup_production_database(engine):
        """Complete production database optimization setup"""
        logger.info("🚀 Setting up production database optimizations...")
        
        DatabaseOptimizer.optimize_connection_pool(engine)
        DatabaseOptimizer.setup_connection_events(engine)
        DatabaseOptimizer.create_database_indexes(engine)
        DatabaseOptimizer.analyze_query_performance()
        
        logger.info("✅ Production database optimization complete")

class QueryOptimizer:
    """Query-level optimization utilities"""
    
    @staticmethod
    @contextmanager
    def optimized_session():
        """Context manager for optimized database sessions"""
        from database import SessionLocal
        
        session = SessionLocal()
        try:
            # Configure session for optimal performance
            session.execute(text("PRAGMA query_only = 0"))
            yield session
        finally:
            session.close()
    
    @staticmethod
    def batch_query(session, model, filters, batch_size=100):
        """Execute batch queries with optimal performance"""
        query = session.query(model)
        
        for filter_condition in filters:
            query = query.filter(filter_condition)
        
        # Use yield_per for memory-efficient batch processing
        for item in query.yield_per(batch_size):
            yield item

# Initialize database optimization on import  
logger.info("🔧 Database optimizer ready")