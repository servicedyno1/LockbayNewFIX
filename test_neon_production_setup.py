"""
Test Neon Production Database Setup
Verifies all three database connections are configured and accessible
"""

import os
import logging
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def mask_url(url: str) -> str:
    """Mask sensitive parts of database URL for logging"""
    if not url:
        return "Not configured"
    
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    
    masked_netloc = f"{parsed.username}:***@{parsed.hostname}"
    if parsed.port:
        masked_netloc += f":{parsed.port}"
    
    return f"{parsed.scheme}://{masked_netloc}{parsed.path}"


def test_database_connection(name: str, url: str) -> dict:
    """Test connection to a database"""
    result = {
        "name": name,
        "url_masked": mask_url(url),
        "accessible": False,
        "database_name": None,
        "user_count": None,
        "error": None
    }
    
    try:
        engine = create_engine(url, pool_pre_ping=True, pool_size=1)
        
        with engine.connect() as conn:
            # Get database name
            db_result = conn.execute(text("SELECT current_database()"))
            result["database_name"] = db_result.scalar()
            
            # Get user count
            try:
                user_result = conn.execute(text("SELECT COUNT(*) FROM users"))
                result["user_count"] = user_result.scalar()
            except Exception:
                result["user_count"] = "N/A (table may not exist)"
            
            result["accessible"] = True
        
        engine.dispose()
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def main():
    print("=" * 80)
    print("🔍 NEON PRODUCTION DATABASE SETUP VERIFICATION")
    print("=" * 80)
    print()
    
    # Get environment variables
    neon_production_url = os.getenv("NEON_PRODUCTION_DATABASE_URL")
    neon_dev_url = os.getenv("DATABASE_URL")
    railway_backup_url = os.getenv("RAILWAY_BACKUP_DB_URL")
    
    # Test all three databases
    databases = [
        ("Neon Production", neon_production_url),
        ("Neon Development", neon_dev_url),
        ("Railway Backup", railway_backup_url)
    ]
    
    results = []
    for name, url in databases:
        if not url:
            results.append({
                "name": name,
                "url_masked": "Not configured",
                "accessible": False,
                "error": f"{name} URL not found in environment"
            })
        else:
            results.append(test_database_connection(name, url))
    
    # Print results
    for result in results:
        print(f"📊 {result['name']}")
        print(f"   URL: {result['url_masked']}")
        
        if result['accessible']:
            print("   ✅ Status: Accessible")
            print(f"   Database: {result['database_name']}")
            print(f"   Users: {result['user_count']}")
        else:
            print("   ❌ Status: Not accessible")
            print(f"   Error: {result['error']}")
        print()
    
    # Summary
    print("=" * 80)
    print("📋 SUMMARY")
    print("=" * 80)
    
    accessible_count = sum(1 for r in results if r['accessible'])
    total_count = len(results)
    
    if accessible_count == total_count:
        print(f"✅ All {total_count} databases are accessible and configured correctly!")
        print()
        print("🚀 Next steps:")
        print("   1. Set IS_PRODUCTION=true or ENVIRONMENT=production in production")
        print("   2. Bot will automatically use Neon Production database")
        print("   3. Backups will sync Neon Prod → Railway Backup (6 AM/PM UTC)")
        print("   4. Development sync: Neon Prod → Neon Dev (6:15 AM/PM UTC)")
        return True
    else:
        print(f"⚠️  Only {accessible_count}/{total_count} databases are accessible")
        print()
        print("🔧 Action required:")
        for result in results:
            if not result['accessible']:
                print(f"   • Configure {result['name']}")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
