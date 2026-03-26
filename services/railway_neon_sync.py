"""
Unified Database → Railway Backup Automated Sync System
Syncs unified database to Railway backup database for disaster recovery
Uses DATABASE_URL (source) and RAILWAY_BACKUP_DB_URL (destination)
"""

import os
import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from sqlalchemy import text, create_engine

logger = logging.getLogger(__name__)


class RailwayNeonSync:
    """Automated backup sync from unified database to Railway backup (disaster recovery)"""
    
    source_url: str
    backup_url: str
    
    def __init__(self):
        # Get database URLs from environment
        source_url = os.getenv("DATABASE_URL")  # Unified database source
        backup_url = os.getenv("RAILWAY_BACKUP_DB_URL")  # Backup database destination
        
        # Validate configuration
        if not source_url:
            raise ValueError("DATABASE_URL not configured")
        if not backup_url:
            raise ValueError("RAILWAY_BACKUP_DB_URL not configured")
        
        # Assign validated URLs (now guaranteed to be strings)
        self.source_url = source_url
        self.backup_url = backup_url
        
        # Create backup directory
        self.backup_dir = Path("backups/unified_database_backup")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("✅ Unified Database → Railway Backup Sync initialized")
        logger.info(f"   Source (Unified DB): {self._mask_url(self.source_url)}")
        logger.info(f"   Destination (Railway Backup): {self._mask_url(self.backup_url)}")
    
    def _mask_url(self, url: str) -> str:
        """Mask sensitive parts of database URL for logging"""
        if not url:
            return "Not configured"
        
        # postgresql://user:pass@host:port/db → postgresql://user:***@host:port/db
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        
        masked_netloc = f"{parsed.username}:***@{parsed.hostname}"
        if parsed.port:
            masked_netloc += f":{parsed.port}"
        
        return f"{parsed.scheme}://{masked_netloc}{parsed.path}"
    
    async def verify_connections(self) -> bool:
        """Verify both databases are accessible"""
        try:
            # Test source database connection
            source_engine = create_engine(self.source_url, pool_pre_ping=True)
            with source_engine.connect() as conn:
                result = conn.execute(text("SELECT current_database(), COUNT(*) as users FROM users"))
                row = result.fetchone()
                if row:
                    logger.info(f"✅ Source database accessible: {row[0]} ({row[1]} users)")
                else:
                    logger.warning("⚠️ Source database accessible but no data returned")
            source_engine.dispose()
            
            # Test Railway backup connection
            backup_engine = create_engine(self.backup_url, pool_pre_ping=True)
            with backup_engine.connect() as conn:
                result = conn.execute(text("SELECT current_database()"))
                db_name = result.scalar()
                logger.info(f"✅ Railway backup accessible: {db_name}")
            backup_engine.dispose()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Connection verification failed: {e}")
            return False
    
    async def get_source_stats(self) -> Dict[str, Any]:
        """Get current source database statistics"""
        try:
            source_engine = create_engine(self.source_url, pool_pre_ping=True)
            
            with source_engine.connect() as conn:
                # Get table counts
                result = conn.execute(text("""
                    SELECT 
                        (SELECT COUNT(*) FROM users) as users,
                        (SELECT COUNT(*) FROM escrows) as escrows,
                        (SELECT COUNT(*) FROM wallets) as wallets,
                        (SELECT COUNT(*) FROM transactions) as transactions
                """))
                row = result.fetchone()
                
                if not row:
                    return {}
                
                stats = {
                    "users": row[0],
                    "escrows": row[1],
                    "wallets": row[2],
                    "transactions": row[3],
                    "timestamp": datetime.now().isoformat()
                }
            
            source_engine.dispose()
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get source database stats: {e}")
            return {}
    
    async def dump_source_database(self) -> Optional[Path]:
        """Dump source (unified) database to SQL file"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dump_file = self.backup_dir / f"source_db_backup_{timestamp}.sql"
            
            logger.info(f"📦 Dumping source database to {dump_file.name}...")
            
            # Parse source URL for pg_dump
            import urllib.parse
            parsed = urllib.parse.urlparse(self.source_url)
            
            # Use pg_dump to create backup
            env = os.environ.copy()
            password = parsed.password
            if password:
                env["PGPASSWORD"] = password if isinstance(password, str) else password.decode('utf-8')
            else:
                env["PGPASSWORD"] = ""
            
            # Security: Using list arguments (not shell=True) - already safe from injection
            cmd = [
                "pg_dump",
                "-h", parsed.hostname,
                "-p", str(parsed.port or 5432),
                "-U", parsed.username,
                "-d", parsed.path[1:],  # Remove leading slash
                "-F", "p",  # Plain text format
                "-f", str(dump_file),
                "--no-owner",
                "--no-acl",
                "-v"  # Verbose
            ]
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"❌ pg_dump failed: {result.stderr}")
                return None
            
            # Verify dump file exists and has content
            if not dump_file.exists() or dump_file.stat().st_size == 0:
                logger.error("❌ Dump file is empty or doesn't exist")
                return None
            
            size_mb = dump_file.stat().st_size / (1024 * 1024)
            logger.info(f"✅ Source database dumped: {size_mb:.2f} MB")
            
            return dump_file
            
        except Exception as e:
            logger.error(f"❌ Source database dump failed: {e}")
            return None
    
    async def restore_to_backup(self, dump_file: Path) -> bool:
        """Restore SQL dump to Railway backup database with automatic rollback on failure"""
        safety_backup = None
        
        try:
            logger.info(f"📥 Restoring {dump_file.name} to Railway backup...")
            
            # Parse backup URL for psql
            import urllib.parse
            parsed = urllib.parse.urlparse(self.backup_url)
            
            # SAFETY: Create backup of current Railway backup database first
            logger.info("🛡️  Creating safety backup of current Railway backup database...")
            safety_backup = await self.dump_backup_database()
            if not safety_backup:
                logger.error("❌ Failed to create safety backup - ABORTING restore for safety")
                return False
            
            # Integrity check: Verify safety backup has content
            backup_size_mb = safety_backup.stat().st_size / (1024 * 1024)
            if backup_size_mb < 0.01:  # Less than 10KB is suspicious
                logger.error(f"❌ Safety backup is suspiciously small ({backup_size_mb:.3f} MB) - ABORTING")
                return False
            
            logger.info(f"✅ Safety backup created: {safety_backup.name} ({backup_size_mb:.2f} MB)")
            
            # Drop and recreate schema (DANGEROUS - only for DR database!)
            logger.warning("⚠️  Dropping all tables in Railway backup database...")
            
            backup_engine = create_engine(self.backup_url, pool_pre_ping=True)
            with backup_engine.connect() as conn:
                # Drop all tables
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
                conn.commit()
            backup_engine.dispose()
            
            # Restore from dump
            env = os.environ.copy()
            password = parsed.password
            if password:
                env["PGPASSWORD"] = password if isinstance(password, str) else password.decode('utf-8')
            else:
                env["PGPASSWORD"] = ""
            
            # Security: Using list arguments (not shell=True) - already safe from injection
            cmd = [
                "psql",
                "-h", parsed.hostname,
                "-p", str(parsed.port or 5432),
                "-U", parsed.username,
                "-d", parsed.path[1:],  # Remove leading slash
                "-f", str(dump_file),
                "-v", "ON_ERROR_STOP=1",  # Stop on first error
                "--single-transaction"  # Atomic restore
            ]
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"❌ psql restore failed: {result.stderr}")
                logger.error(f"📋 Restore stdout: {result.stdout}")
                
                # CRITICAL: Restore from safety backup to avoid empty database
                logger.warning("🔄 ROLLING BACK: Restoring from safety backup...")
                if safety_backup and safety_backup.exists():
                    rollback_success = await self._restore_from_safety_backup(safety_backup)
                    if rollback_success:
                        logger.info("✅ Rollback successful - Railway backup database preserved")
                    else:
                        logger.error("❌ Rollback FAILED - Railway backup database may be empty!")
                
                return False
            
            logger.info("✅ Railway backup database restored successfully")
            
            # Verify restore
            if not await self.verify_backup_restore():
                logger.error("❌ Restore verification failed")
                
                # Restore from safety backup
                logger.warning("🔄 ROLLING BACK: Restoring from safety backup...")
                if safety_backup and safety_backup.exists():
                    rollback_success = await self._restore_from_safety_backup(safety_backup)
                    if rollback_success:
                        logger.info("✅ Rollback successful - Railway backup database preserved")
                    else:
                        logger.error("❌ Rollback FAILED - Railway backup database may be corrupted!")
                
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Railway backup restore failed with exception: {e}")
            
            # CRITICAL: Restore from safety backup
            if safety_backup and safety_backup.exists():
                logger.warning("🔄 ROLLING BACK: Restoring from safety backup after exception...")
                rollback_success = await self._restore_from_safety_backup(safety_backup)
                if rollback_success:
                    logger.info("✅ Rollback successful - Railway backup database preserved")
                else:
                    logger.error("❌ Rollback FAILED - Railway backup database may be empty!")
            
            return False
    
    async def _restore_from_safety_backup(self, safety_backup: Path) -> bool:
        """Restore Railway backup database from safety backup (used during rollback)"""
        try:
            logger.info(f"🔄 Restoring from safety backup: {safety_backup.name}")
            
            import urllib.parse
            parsed = urllib.parse.urlparse(self.backup_url)
            
            # Drop and recreate schema
            backup_engine = create_engine(self.backup_url, pool_pre_ping=True)
            with backup_engine.connect() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
                conn.commit()
            backup_engine.dispose()
            
            # Restore from safety backup
            env = os.environ.copy()
            password = parsed.password
            if password:
                env["PGPASSWORD"] = password if isinstance(password, str) else password.decode('utf-8')
            else:
                env["PGPASSWORD"] = ""
            
            # Security: Using list arguments (not shell=True) - already safe from injection
            cmd = [
                "psql",
                "-h", parsed.hostname,
                "-p", str(parsed.port or 5432),
                "-U", parsed.username,
                "-d", parsed.path[1:],
                "-f", str(safety_backup),
                "-v", "ON_ERROR_STOP=1",
                "--single-transaction"
            ]
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                logger.error(f"❌ Safety backup restore failed: {result.stderr}")
                return False
            
            logger.info("✅ Safety backup restored successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Safety backup restore exception: {e}")
            return False
    
    async def dump_backup_database(self) -> Optional[Path]:
        """Create safety backup of Railway backup database before overwriting"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dump_file = self.backup_dir / f"backup_safety_{timestamp}.sql"
            
            import urllib.parse
            parsed = urllib.parse.urlparse(self.backup_url)
            
            env = os.environ.copy()
            password = parsed.password
            if password:
                env["PGPASSWORD"] = password if isinstance(password, str) else password.decode('utf-8')
            else:
                env["PGPASSWORD"] = ""
            
            cmd = [
                "pg_dump",
                "-h", parsed.hostname,
                "-p", str(parsed.port or 5432),
                "-U", parsed.username,
                "-d", parsed.path[1:],
                "-F", "p",
                "-f", str(dump_file),
                "--no-owner",
                "--no-acl"
            ]
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=180
            )
            
            if result.returncode == 0 and dump_file.exists():
                return dump_file
            
            return None
            
        except Exception as e:
            logger.warning(f"⚠️  Safety backup failed (non-critical): {e}")
            return None
    
    async def verify_backup_restore(self) -> bool:
        """Verify Railway backup restore was successful"""
        try:
            backup_engine = create_engine(self.backup_url, pool_pre_ping=True)
            
            with backup_engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT 
                        (SELECT COUNT(*) FROM users) as users,
                        (SELECT COUNT(*) FROM escrows) as escrows,
                        (SELECT COUNT(*) FROM wallets) as wallets
                """))
                row = result.fetchone()
                
                if row:
                    logger.info(f"✅ Railway backup verification: {row[0]} users, {row[1]} escrows, {row[2]} wallets")
                else:
                    logger.warning("⚠️ Railway backup verification: No data returned")
            
            backup_engine.dispose()
            return True
            
        except Exception as e:
            logger.error(f"❌ Railway backup verification failed: {e}")
            return False
    
    async def sync_source_to_backup(self) -> Dict[str, Any]:
        """Complete sync: Unified DB → Railway Backup"""
        start_time = datetime.now()
        result = {
            "success": False,
            "start_time": start_time.isoformat(),
            "source_stats": {},
            "backup_stats": {},
            "duration_seconds": 0,
            "error": None
        }
        
        try:
            logger.info("=" * 80)
            logger.info("🔄 UNIFIED DB → RAILWAY BACKUP SYNC STARTED")
            logger.info("=" * 80)
            
            # Step 1: Verify connections
            if not await self.verify_connections():
                result["error"] = "Connection verification failed"
                return result
            
            # Step 2: Get source stats (before backup)
            result["source_stats"] = await self.get_source_stats()
            logger.info(f"📊 Source DB stats: {result['source_stats']}")
            
            # Step 3: Dump source database
            dump_file = await self.dump_source_database()
            if not dump_file:
                result["error"] = "Source database dump failed"
                return result
            
            # Step 4: Restore to Railway backup
            if not await self.restore_to_backup(dump_file):
                result["error"] = "Railway backup restore failed"
                return result
            
            # Step 5: Verify Railway backup
            if not await self.verify_backup_restore():
                result["error"] = "Railway backup verification failed"
                return result
            
            # Success!
            result["success"] = True
            result["backup_stats"] = await self.get_backup_stats()
            
            # Cleanup old backup files (keep last 7 days)
            await self.cleanup_old_backups(days=7)
            
            end_time = datetime.now()
            result["end_time"] = end_time.isoformat()
            result["duration_seconds"] = (end_time - start_time).total_seconds()
            
            logger.info("=" * 80)
            logger.info(f"✅ UNIFIED DB → RAILWAY BACKUP SYNC COMPLETE ({result['duration_seconds']:.1f}s)")
            logger.info("=" * 80)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Sync failed: {e}")
            result["error"] = str(e)
            return result
    
    async def get_backup_stats(self) -> Dict[str, Any]:
        """Get Railway backup database statistics"""
        try:
            backup_engine = create_engine(self.backup_url, pool_pre_ping=True)
            
            with backup_engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT 
                        (SELECT COUNT(*) FROM users) as users,
                        (SELECT COUNT(*) FROM escrows) as escrows,
                        (SELECT COUNT(*) FROM wallets) as wallets,
                        (SELECT COUNT(*) FROM transactions) as transactions
                """))
                row = result.fetchone()
                
                if not row:
                    return {}
                
                stats = {
                    "users": row[0],
                    "escrows": row[1],
                    "wallets": row[2],
                    "transactions": row[3],
                    "timestamp": datetime.now().isoformat()
                }
            
            backup_engine.dispose()
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get Railway backup stats: {e}")
            return {}
    
    async def cleanup_old_backups(self, days: int = 7):
        """Remove backup files older than specified days"""
        try:
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(days=days)
            
            removed_count = 0
            for backup_file in self.backup_dir.glob("*.sql"):
                if backup_file.stat().st_mtime < cutoff.timestamp():
                    backup_file.unlink()
                    removed_count += 1
            
            if removed_count > 0:
                logger.info(f"🧹 Cleaned up {removed_count} old backup files (>{days} days)")
                
        except Exception as e:
            logger.warning(f"⚠️  Backup cleanup failed: {e}")


async def run_sync():
    """Standalone function to run the sync"""
    try:
        sync = RailwayNeonSync()
        result = await sync.sync_source_to_backup()
        
        if result["success"]:
            print(f"✅ Sync completed in {result['duration_seconds']:.1f}s")
            print(f"Source DB: {result['source_stats']}")
            print(f"Railway Backup: {result['backup_stats']}")
            return 0
        else:
            print(f"❌ Sync failed: {result['error']}")
            return 1
            
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        return 1


if __name__ == "__main__":
    # Can be run standalone: python -m services.railway_neon_sync
    import sys
    exit_code = asyncio.run(run_sync())
    sys.exit(exit_code)
