#!/usr/bin/env python3
"""
Foreign Key Consistency Validation Script

Validates foreign key type consistency across the database schema before migration.
Identifies critical type mismatches that could cause constraint violations.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from utils.database_type_validators import audit_foreign_key_types
import logging

logger = logging.getLogger(__name__)


def validate_foreign_keys():
    """
    Validate all foreign key relationships before migration
    
    Returns:
        List of critical issues that must be resolved
    """
    session = SessionLocal()
    critical_issues = []
    
    try:
        # Critical tables with known foreign key issues
        tables_to_audit = [
            "unified_transactions",
            "escrows", 
            "balance_audit_log",
            "admin_action_token",
            "disputes",
            "partial_release_audit"
        ]
        
        print("🔍 Validating Foreign Key Consistency...")
        print("=" * 50)
        
        total_issues = 0
        critical_count = 0
        
        for table_name in tables_to_audit:
            print(f"\n📊 Auditing table: {table_name}")
            audit_result = audit_foreign_key_types(session, table_name)
            
            if audit_result.get("foreign_key_issues"):
                issues = audit_result["foreign_key_issues"]
                total_issues += len(issues)
                
                for issue in issues:
                    severity = issue.get("severity", "MEDIUM")
                    if severity in ["CRITICAL", "HIGH"]:
                        critical_count += 1
                        critical_issues.append({
                            "table": table_name,
                            "column": issue["column"],
                            "issue": f"{issue['current_type']} FK → {issue['references']} (should be {issue['required_type']})",
                            "severity": severity
                        })
                    
                    print(f"  ❌ {severity}: {issue['column']} - {issue['current_type']} should be {issue['required_type']}")
                
                print("  📝 Recommendations:")
                for rec in audit_result["recommendations"]:
                    print(f"    • {rec}")
            else:
                print("  ✅ No foreign key issues found")
        
        print("\n" + "=" * 50)
        print("📈 SUMMARY:")
        print(f"  Total Issues Found: {total_issues}")
        print(f"  Critical/High Priority: {critical_count}")
        print(f"  Tables Audited: {len(tables_to_audit)}")
        
        if critical_issues:
            print("\n🚨 CRITICAL ISSUES REQUIRING IMMEDIATE ATTENTION:")
            for issue in critical_issues:
                print(f"  • {issue['table']}.{issue['column']}: {issue['issue']}")
        
        return critical_issues
        
    except Exception as e:
        logger.error(f"Foreign key validation failed: {e}")
        print(f"❌ Validation failed: {e}")
        return [{"error": str(e)}]
    finally:
        session.close()


def check_telegram_id_patterns():
    """
    Check for remaining string conversion patterns in Telegram ID handling
    """
    print("\n🔍 Checking for remaining Telegram ID string conversion patterns...")
    
    import subprocess
    
    # Search for problematic patterns
    problematic_patterns = [
        r"User\.telegram_id.*==.*str\(",
        r"str\(.*\.id\).*==.*User\.telegram_id",
        r"session\.query\(User\)\.filter\(User\.telegram_id.*==.*str\("
    ]
    
    found_issues = []
    
    for pattern in problematic_patterns:
        try:
            # Use grep to find remaining instances
            result = subprocess.run(
                ["grep", "-r", "-n", "--include=*.py", pattern, "."],
                capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                found_issues.extend([f"Pattern '{pattern}': {line}" for line in lines])
        except Exception as e:
            print(f"  ⚠️  Could not check pattern {pattern}: {e}")
    
    if found_issues:
        print(f"  ❌ Found {len(found_issues)} remaining string conversion patterns:")
        for issue in found_issues[:10]:  # Show first 10
            print(f"    • {issue}")
        if len(found_issues) > 10:
            print(f"    ... and {len(found_issues) - 10} more")
    else:
        print("  ✅ No problematic Telegram ID string conversion patterns found")
    
    return found_issues


if __name__ == "__main__":
    print("🛡️  Database Foreign Key Consistency Validation")
    print("=" * 60)
    
    # Check foreign key consistency  
    fk_issues = validate_foreign_keys()
    
    # Check Telegram ID patterns
    telegram_issues = check_telegram_id_patterns()
    
    print("\n" + "=" * 60)
    if fk_issues or telegram_issues:
        print("❌ VALIDATION FAILED - Issues found that must be resolved before migration")
        sys.exit(1)
    else:
        print("✅ VALIDATION PASSED - Ready for database migration")
        sys.exit(0)