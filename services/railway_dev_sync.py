"""
Railway → Development Database Automated Sync System
Syncs Railway production database to Replit development database for testing
Uses RAILWAY_DATABASE_URL (source) and DATABASE_URL (destination)
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


class RailwayDevSync:
    """Automated sync from Railway (production) to Replit development database"""
    
    railway_url: str
    dev_url: str
    neon_production_url: Optional[str]
    
    def __init__(self):
        # Get database URLs from environment
        railway_url = os.getenv("RAILWAY_DATABASE_URL")
        dev_url = os.getenv("DATABASE_URL")  # Replit development database
        neon_production_url = os.getenv("NEON_PRODUCTION_DATABASE_URL")  # Optional: Neon Production source
        
        # Validate configuration
        if not railway_url:
            raise ValueError("RAILWAY_DATABASE_URL not configured")
        if not dev_url:
            raise ValueError("DATABASE_URL (development) not configured")
        
        # Assign validated URLs (now guaranteed to be strings)
        self.railway_url = railway_url
        self.dev_url = dev_url
        self.neon_production_url = neon_production_url
        
        # Create backup directory
        self.backup_dir = Path("backups/railway_dev_sync")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("✅ Railway → Development Sync initialized")
        logger.info(f"   Source (Railway): {self._mask_url(self.railway_url)}")
        logger.info(f"   Destination (Dev): {self._mask_url(self.dev_url)}")
        if neon_production_url:
            logger.info(f"   Source (Neon Production): {self._mask_url(neon_production_url)}")
    
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
            # Test Railway connection
            railway_engine = create_engine(self.railway_url, pool_pre_ping=True)
            with railway_engine.connect() as conn:
                result = conn.execute(text("SELECT current_database(), COUNT(*) as users FROM users"))
                row = result.fetchone()
                if row:
                    logger.info(f"✅ Railway accessible: {row[0]} ({row[1]} users)")
                else:
                    logger.warning("⚠️ Railway accessible but no data returned")
            railway_engine.dispose()
            
            # Test Development DB connection
            dev_engine = create_engine(self.dev_url, pool_pre_ping=True)
            with dev_engine.connect() as conn:
                result = conn.execute(text("SELECT current_database()"))
                db_name = result.scalar()
                logger.info(f"✅ Development DB accessible: {db_name}")
            dev_engine.dispose()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Connection verification failed: {e}")
            return False
    
    async def get_railway_stats(self) -> Dict[str, Any]:
        """Get current Railway database statistics"""
        try:
            railway_engine = create_engine(self.railway_url, pool_pre_ping=True)
            
            with railway_engine.connect() as conn:
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
            
            railway_engine.dispose()
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get Railway stats: {e}")
            return {}
    
    async def dump_railway_database(self) -> Optional[Path]:
        """Dump Railway database to SQL file"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dump_file = self.backup_dir / f"railway_dev_dump_{timestamp}.sql"
            
            logger.info(f"📦 Dumping Railway database to {dump_file.name}...")
            
            # Parse Railway URL for pg_dump
            import urllib.parse
            parsed = urllib.parse.urlparse(self.railway_url)
            
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
            logger.info(f"✅ Railway database dumped: {size_mb:.2f} MB")
            
            return dump_file
            
        except Exception as e:
            logger.error(f"❌ Railway dump failed: {e}")
            return None
    
    async def _clean_dump_file(self, dump_file: Path) -> Path:
        """Clean dump file to remove incompatible PostgreSQL parameters"""
        try:
            cleaned_file = dump_file.with_suffix('.cleaned.sql')
            
            # Incompatible parameters to remove
            incompatible_params = [
                'transaction_timeout',
                'idle_in_transaction_session_timeout',
                'lock_timeout'
            ]
            
            logger.info("🧹 Cleaning dump file to remove incompatible parameters...")
            
            with open(dump_file, 'r') as f_in:
                with open(cleaned_file, 'w') as f_out:
                    for line in f_in:
                        # Skip lines that set incompatible parameters
                        skip_line = False
                        for param in incompatible_params:
                            if f"SET {param}" in line or f"set {param}" in line.lower():
                                skip_line = True
                                logger.debug(f"Skipping incompatible parameter: {param}")
                                break
                        
                        if not skip_line:
                            f_out.write(line)
            
            logger.info(f"✅ Dump file cleaned: {cleaned_file.name}")
            return cleaned_file
            
        except Exception as e:
            logger.error(f"❌ Failed to clean dump file: {e}")
            # Return original file as fallback
            return dump_file
    
    async def restore_to_dev(self, dump_file: Path) -> bool:
        """Restore SQL dump to development database with automatic rollback on failure"""
        safety_backup = None
        
        try:
            logger.info(f"📥 Restoring {dump_file.name} to Development DB...")
            
            # Clean the dump file first
            cleaned_dump = await self._clean_dump_file(dump_file)
            
            # Parse Dev URL for psql
            import urllib.parse
            parsed = urllib.parse.urlparse(self.dev_url)
            
            # SAFETY: Create backup of current dev database first
            logger.info("🛡️  Creating safety backup of current development database...")
            safety_backup = await self.dump_dev_database()
            if not safety_backup:
                logger.warning("⚠️ Failed to create safety backup - proceeding anyway (dev database)")
            else:
                # Integrity check: Verify safety backup has content
                backup_size_mb = safety_backup.stat().st_size / (1024 * 1024)
                if backup_size_mb < 0.01:  # Less than 10KB is suspicious
                    logger.warning(f"⚠️ Safety backup is suspiciously small ({backup_size_mb:.3f} MB)")
                else:
                    logger.info(f"✅ Safety backup created: {safety_backup.name} ({backup_size_mb:.2f} MB)")
            
            # Drop and recreate schema
            logger.warning("⚠️  Dropping all tables in development database...")
            
            dev_engine = create_engine(self.dev_url, pool_pre_ping=True)
            with dev_engine.connect() as conn:
                # Drop all tables
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
                conn.commit()
            dev_engine.dispose()
            
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
                "-f", str(cleaned_dump),  # Use cleaned dump file
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
                if safety_backup and safety_backup.exists():
                    logger.warning("🔄 ROLLING BACK: Restoring from safety backup...")
                    rollback_success = await self._restore_from_safety_backup(safety_backup)
                    if rollback_success:
                        logger.info("✅ Rollback successful - Development database preserved")
                    else:
                        logger.error("❌ Rollback FAILED - Development database may be empty!")
                
                return False
            
            logger.info("✅ Development database restored successfully")
            
            # Verify restore
            if not await self.verify_dev_restore():
                logger.error("❌ Restore verification failed")
                
                # Restore from safety backup
                if safety_backup and safety_backup.exists():
                    logger.warning("🔄 ROLLING BACK: Restoring from safety backup...")
                    rollback_success = await self._restore_from_safety_backup(safety_backup)
                    if rollback_success:
                        logger.info("✅ Rollback successful - Development database preserved")
                    else:
                        logger.error("❌ Rollback FAILED - Development database may be corrupted!")
                
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Development restore failed with exception: {e}")
            
            # CRITICAL: Restore from safety backup
            if safety_backup and safety_backup.exists():
                logger.warning("🔄 ROLLING BACK: Restoring from safety backup after exception...")
                rollback_success = await self._restore_from_safety_backup(safety_backup)
                if rollback_success:
                    logger.info("✅ Rollback successful - Development database preserved")
                else:
                    logger.error("❌ Rollback FAILED - Development database may be empty!")
            
            return False
    
    async def _restore_from_safety_backup(self, safety_backup: Path) -> bool:
        """Restore development database from safety backup (used during rollback)"""
        try:
            logger.info(f"🔄 Restoring from safety backup: {safety_backup.name}")
            
            import urllib.parse
            parsed = urllib.parse.urlparse(self.dev_url)
            
            # Drop and recreate schema
            dev_engine = create_engine(self.dev_url, pool_pre_ping=True)
            with dev_engine.connect() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
                conn.commit()
            dev_engine.dispose()
            
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
    
    async def dump_dev_database(self) -> Optional[Path]:
        """Create safety backup of development database before overwriting"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dump_file = self.backup_dir / f"dev_safety_backup_{timestamp}.sql"
            
            import urllib.parse
            parsed = urllib.parse.urlparse(self.dev_url)
            
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
    
    async def verify_dev_restore(self) -> bool:
        """Verify development database restore was successful"""
        try:
            dev_engine = create_engine(self.dev_url, pool_pre_ping=True)
            
            with dev_engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT 
                        (SELECT COUNT(*) FROM users) as users,
                        (SELECT COUNT(*) FROM escrows) as escrows,
                        (SELECT COUNT(*) FROM wallets) as wallets
                """))
                row = result.fetchone()
                
                if row:
                    logger.info(f"✅ Development DB verification: {row[0]} users, {row[1]} escrows, {row[2]} wallets")
                else:
                    logger.warning("⚠️ Development DB verification: No data returned")
            
            dev_engine.dispose()
            return True
            
        except Exception as e:
            logger.error(f"❌ Development DB verification failed: {e}")
            return False
    
    async def sync_railway_to_dev(self) -> Dict[str, Any]:
        """Complete sync: Railway → Development DB"""
        start_time = datetime.now()
        result = {
            "success": False,
            "start_time": start_time.isoformat(),
            "railway_stats": {},
            "dev_stats": {},
            "duration_seconds": 0,
            "error": None
        }
        
        try:
            logger.info("=" * 80)
            logger.info("🔄 RAILWAY → DEVELOPMENT DB SYNC STARTED")
            logger.info("=" * 80)
            
            # Step 1: Verify connections
            if not await self.verify_connections():
                result["error"] = "Connection verification failed"
                return result
            
            # Step 2: Get Railway stats (before backup)
            result["railway_stats"] = await self.get_railway_stats()
            logger.info(f"📊 Railway stats: {result['railway_stats']}")
            
            # Step 3: Dump Railway database
            dump_file = await self.dump_railway_database()
            if not dump_file:
                result["error"] = "Railway dump failed"
                return result
            
            # Step 4: Restore to Development DB
            if not await self.restore_to_dev(dump_file):
                result["error"] = "Development DB restore failed"
                return result
            
            # Step 5: Verify Development DB
            if not await self.verify_dev_restore():
                result["error"] = "Development DB verification failed"
                return result
            
            # Success!
            result["success"] = True
            result["dev_stats"] = await self.get_dev_stats()
            
            # Cleanup old backup files (keep last 3 days)
            await self.cleanup_old_backups(days=3)
            
            end_time = datetime.now()
            result["end_time"] = end_time.isoformat()
            result["duration_seconds"] = (end_time - start_time).total_seconds()
            
            logger.info("=" * 80)
            logger.info(f"✅ RAILWAY → DEVELOPMENT DB SYNC COMPLETE ({result['duration_seconds']:.1f}s)")
            logger.info("=" * 80)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Sync failed: {e}")
            result["error"] = str(e)
            return result
    
    async def get_dev_stats(self) -> Dict[str, Any]:
        """Get development database statistics"""
        try:
            dev_engine = create_engine(self.dev_url, pool_pre_ping=True)
            
            with dev_engine.connect() as conn:
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
            
            dev_engine.dispose()
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get development DB stats: {e}")
            return {}
    
    async def dump_neon_production_database(self) -> Optional[Path]:
        """Dump Neon Production database to SQL file"""
        try:
            if not self.neon_production_url:
                logger.error("❌ NEON_PRODUCTION_DATABASE_URL not configured")
                return None
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dump_file = self.backup_dir / f"neon_production_dev_dump_{timestamp}.sql"
            
            logger.info(f"📦 Dumping Neon Production database to {dump_file.name}...")
            
            # Parse Neon Production URL for pg_dump
            import urllib.parse
            parsed = urllib.parse.urlparse(self.neon_production_url)
            
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
            logger.info(f"✅ Neon Production database dumped: {size_mb:.2f} MB")
            
            return dump_file
            
        except Exception as e:
            logger.error(f"❌ Neon Production dump failed: {e}")
            return None
    
    async def get_neon_production_stats(self) -> Dict[str, Any]:
        """Get current Neon Production database statistics"""
        try:
            if not self.neon_production_url:
                logger.error("❌ NEON_PRODUCTION_DATABASE_URL not configured")
                return {}
            
            neon_prod_engine = create_engine(self.neon_production_url, pool_pre_ping=True)
            
            with neon_prod_engine.connect() as conn:
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
            
            neon_prod_engine.dispose()
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get Neon Production stats: {e}")
            return {}
    
    async def sync_neon_production_to_dev(self) -> Dict[str, Any]:
        """Complete sync: Neon Production → Development DB"""
        start_time = datetime.now()
        result = {
            "success": False,
            "start_time": start_time.isoformat(),
            "neon_production_stats": {},
            "dev_stats": {},
            "duration_seconds": 0,
            "error": None
        }
        
        try:
            logger.info("=" * 80)
            logger.info("🔄 NEON PRODUCTION → DEV SYNC STARTED")
            logger.info("=" * 80)
            
            # Step 1: Verify Neon Production URL is configured
            if not self.neon_production_url:
                result["error"] = "NEON_PRODUCTION_DATABASE_URL not configured"
                logger.error(f"❌ {result['error']}")
                return result
            
            # Step 2: Get Neon Production stats (before backup)
            result["neon_production_stats"] = await self.get_neon_production_stats()
            logger.info(f"📊 Neon Production stats: {result['neon_production_stats']}")
            
            # Step 3: Dump Neon Production database
            dump_file = await self.dump_neon_production_database()
            if not dump_file:
                result["error"] = "Neon Production dump failed"
                return result
            
            # Step 4: Restore to Development DB
            if not await self.restore_to_dev(dump_file):
                result["error"] = "Development DB restore failed"
                return result
            
            # Step 5: Verify Development DB
            if not await self.verify_dev_restore():
                result["error"] = "Development DB verification failed"
                return result
            
            # Success!
            result["success"] = True
            result["dev_stats"] = await self.get_dev_stats()
            
            # Cleanup old backup files (keep last 3 days)
            await self.cleanup_old_backups(days=3)
            
            end_time = datetime.now()
            result["end_time"] = end_time.isoformat()
            result["duration_seconds"] = (end_time - start_time).total_seconds()
            
            logger.info("=" * 80)
            logger.info(f"✅ NEON PRODUCTION → DEV SYNC COMPLETE ({result['duration_seconds']:.1f}s)")
            logger.info("=" * 80)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Sync failed: {e}")
            result["error"] = str(e)
            return result
    
    async def cleanup_old_backups(self, days: int = 3):
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


async def run_dev_sync():
    """Standalone function to run the development sync"""
    try:
        sync = RailwayDevSync()
        result = await sync.sync_railway_to_dev()
        
        if result["success"]:
            print(f"✅ Development sync completed in {result['duration_seconds']:.1f}s")
            print(f"Railway: {result['railway_stats']}")
            print(f"Development DB: {result['dev_stats']}")
            return 0
        else:
            print(f"❌ Development sync failed: {result['error']}")
            return 1
            
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        return 1


if __name__ == "__main__":
    # Can be run standalone: python -m services.railway_dev_sync
    import sys
    exit_code = asyncio.run(run_dev_sync())
    sys.exit(exit_code)
