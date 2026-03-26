#!/usr/bin/env python3
"""
Foreign Key Reference Counter

Counts and validates foreign key references before migration to ensure data integrity.
Provides detailed statistics for migration planning.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)


def count_foreign_key_references():
    """
    Count foreign key references to understand migration impact
    
    Returns:
        Dictionary with reference counts and statistics
    """
    session = SessionLocal()
    
    try:
        print("📊 Counting Foreign Key References...")
        print("=" * 50)
        
        counts = {}
        
        # Count references to users.id (BigInteger PK)
        user_reference_queries = {
            "unified_transactions.user_id": "SELECT COUNT(*) FROM unified_transactions WHERE user_id IS NOT NULL",
            "unified_transactions.admin_approved_by": "SELECT COUNT(*) FROM unified_transactions WHERE admin_approved_by IS NOT NULL", 
            "escrows.buyer_id": "SELECT COUNT(*) FROM escrows WHERE buyer_id IS NOT NULL",
            "escrows.seller_id": "SELECT COUNT(*) FROM escrows WHERE seller_id IS NOT NULL",
            "disputes.initiator_id": "SELECT COUNT(*) FROM disputes WHERE initiator_id IS NOT NULL",
            "transactions.user_id": "SELECT COUNT(*) FROM transactions WHERE user_id IS NOT NULL"
        }
        
        print("\n🔗 References to users.id (BigInteger PK):")
        user_total = 0
        for ref_name, query in user_reference_queries.items():
            try:
                result = session.execute(text(query)).scalar()
                result = result if result is not None else 0
                counts[ref_name] = result
                user_total += result
                print(f"  {ref_name}: {result:,} records")
            except Exception as e:
                print(f"  {ref_name}: Error - {e}")
                counts[ref_name] = 0
        
        print(f"  📈 Total user references: {user_total:,}")
        
        # Count business ID references that need PK relationships
        business_id_queries = {
            "balance_audit_log.escrow_id": "SELECT COUNT(*) FROM balance_audit_log WHERE escrow_id IS NOT NULL",
            "admin_action_token.cashout_id": "SELECT COUNT(*) FROM admin_action_token WHERE cashout_id IS NOT NULL"
        }
        
        print("\n🏷️  Business ID references needing PK relationships:")
        business_total = 0
        for ref_name, query in business_id_queries.items():
            try:
                result = session.execute(text(query)).scalar()
                result = result if result is not None else 0
                counts[ref_name] = result
                business_total += result
                print(f"  {ref_name}: {result:,} records")
            except Exception as e:
                print(f"  {ref_name}: Error - {e}")
                counts[ref_name] = 0
                
        print(f"  📈 Total business ID references: {business_total:,}")
        
        # Check for potential orphaned records
        orphan_queries = {
            "orphaned_unified_transactions": """
                SELECT COUNT(*) FROM unified_transactions ut 
                LEFT JOIN users u ON ut.user_id = u.id 
                WHERE u.id IS NULL AND ut.user_id IS NOT NULL
            """,
            "orphaned_escrow_buyers": """
                SELECT COUNT(*) FROM escrows e
                LEFT JOIN users u ON e.buyer_id = u.id
                WHERE u.id IS NULL AND e.buyer_id IS NOT NULL  
            """,
            "orphaned_escrow_sellers": """
                SELECT COUNT(*) FROM escrows e
                LEFT JOIN users u ON e.seller_id = u.id
                WHERE u.id IS NULL AND e.seller_id IS NOT NULL
            """
        }
        
        print("\n🔍 Checking for orphaned records:")
        orphan_total = 0
        for check_name, query in orphan_queries.items():
            try:
                result = session.execute(text(query)).scalar()
                result = result if result is not None else 0
                counts[check_name] = result
                orphan_total += result
                if result > 0:
                    print(f"  ❌ {check_name}: {result:,} orphaned records")
                else:
                    print(f"  ✅ {check_name}: No orphaned records")
            except Exception as e:
                print(f"  ⚠️  {check_name}: Could not check - {e}")
                counts[check_name] = -1
        
        # Migration impact assessment
        print("\n📋 Migration Impact Assessment:")
        print(f"  Records affected by BigInteger FK fixes: {user_total:,}")
        print(f"  Records needing new PK relationships: {business_total:,}")
        print(f"  Orphaned records to investigate: {orphan_total:,}")
        
        if orphan_total > 0:
            print("  ⚠️  WARNING: Orphaned records found - investigate before migration")
        
        # Estimate migration time
        estimated_minutes = max(1, (user_total + business_total) // 10000)  # Rough estimate
        print(f"  ⏱️  Estimated migration time: {estimated_minutes} minutes")
        
        return counts
        
    except Exception as e:
        logger.error(f"Reference counting failed: {e}")
        print(f"❌ Reference counting failed: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def validate_data_integrity():
    """
    Validate data integrity before migration
    """
    session = SessionLocal()
    
    try:
        print("\n🛡️  Data Integrity Validation:")
        
        # Check for NULL user_ids in critical tables
        null_checks = {
            "unified_transactions": "SELECT COUNT(*) FROM unified_transactions WHERE user_id IS NULL",
            "escrows_missing_buyer": "SELECT COUNT(*) FROM escrows WHERE buyer_id IS NULL", 
            "transactions": "SELECT COUNT(*) FROM transactions WHERE user_id IS NULL"
        }
        
        null_issues = 0
        for check_name, query in null_checks.items():
            try:
                result = session.execute(text(query)).scalar()
                result = result if result is not None else 0
                if result > 0:
                    print(f"  ⚠️  {check_name}: {result:,} NULL values")
                    null_issues += result
                else:
                    print(f"  ✅ {check_name}: No NULL issues")
            except Exception as e:
                print(f"  ❌ {check_name}: Error - {e}")
        
        if null_issues == 0:
            print("  ✅ Data integrity validation passed")
        else:
            print(f"  ⚠️  Found {null_issues:,} potential data integrity issues")
        
        return null_issues == 0
        
    except Exception as e:
        print(f"❌ Data integrity validation failed: {e}")
        return False
    finally:
        session.close()


if __name__ == "__main__":
    print("📊 Database Foreign Key Reference Analysis")
    print("=" * 60)
    
    # Count references
    counts = count_foreign_key_references()
    
    # Validate data integrity
    integrity_ok = validate_data_integrity()
    
    print("\n" + "=" * 60)
    if counts.get("error") or not integrity_ok:
        print("❌ ANALYSIS FAILED - Resolve issues before proceeding with migration")
        sys.exit(1)
    else:
        print("✅ ANALYSIS COMPLETE - Database ready for foreign key migration")
        sys.exit(0)