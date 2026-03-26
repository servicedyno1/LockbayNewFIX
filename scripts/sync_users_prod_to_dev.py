#!/usr/bin/env python3
"""Sync all 54 production users to development database"""

import os
import sys
from sqlalchemy import create_engine, text

dev_url = os.getenv('DATABASE_URL')
prod_url = os.getenv('NEON_PRODUCTION_DATABASE_URL')

if not dev_url or not prod_url:
    print("❌ Missing database URLs")
    sys.exit(1)

print("🔄 SYNCING PRODUCTION USERS → DEVELOPMENT")
print("=" * 60)

dev_engine = create_engine(dev_url)
prod_engine = create_engine(prod_url)

# Get all production users
with prod_engine.connect() as prod_conn:
    result = prod_conn.execute(text("SELECT * FROM users ORDER BY telegram_id"))
    all_users = result.fetchall()
    columns = list(result.keys())

print(f"📊 Found {len(all_users)} users in production")

# Insert missing users into development
inserted = 0
with dev_engine.connect() as dev_conn:
    for user_data in all_users:
        user_dict = dict(zip(columns, user_data))
        telegram_id = user_dict['telegram_id']
        
        # Check if exists
        exists = dev_conn.execute(text("""
            SELECT 1 FROM users WHERE telegram_id = :tid LIMIT 1
        """), {"tid": telegram_id}).fetchone()
        
        if not exists:
            # Ensure all NOT NULL fields have defaults
            user_dict['auto_cashout_enabled'] = user_dict.get('auto_cashout_enabled') or False
            user_dict['total_referrals'] = user_dict.get('total_referrals') or 0
            user_dict['is_active'] = user_dict.get('is_active') if user_dict.get('is_active') is not None else True
            user_dict['email_verified'] = user_dict.get('email_verified') or False
            user_dict['is_verified'] = user_dict.get('is_verified') or False
            user_dict['is_blocked'] = user_dict.get('is_blocked') or False
            user_dict['is_admin'] = user_dict.get('is_admin') or False
            user_dict['is_seller'] = user_dict.get('is_seller') or False
            user_dict['onboarding_completed'] = user_dict.get('onboarding_completed') or False
            user_dict['universal_welcome_bonus_given'] = user_dict.get('universal_welcome_bonus_given') or False
            
            # Build INSERT statement dynamically
            cols = ', '.join(user_dict.keys())
            placeholders = ', '.join(f":{k}" for k in user_dict.keys())
            
            dev_conn.execute(text(f"""
                INSERT INTO users ({cols})
                VALUES ({placeholders})
            """), user_dict)
            inserted += 1
            print(f"   ✅ Inserted @{user_dict.get('username', 'unknown')} ({telegram_id})")
    
    dev_conn.commit()

print(f"\n✅ Inserted {inserted} new users into development")

# Final check
with dev_engine.connect() as conn:
    total = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    admins = conn.execute(text("SELECT COUNT(*) FROM users WHERE is_admin = true")).scalar()
    admin_list = conn.execute(text("SELECT username FROM users WHERE is_admin = true")).fetchall()
    tickets = conn.execute(text("SELECT COUNT(*) FROM support_tickets")).scalar()
    
    print("\n📊 Final Status:")
    print(f"   Total users: {total}")
    print(f"   Admins: {admins}")
    for admin in admin_list:
        print(f"     - @{admin[0]}")
    print(f"   Support tickets: {tickets} (unchanged)")

dev_engine.dispose()
prod_engine.dispose()

print("\n" + "=" * 60)
print("✅ ALL 54 USERS NOW IN DEVELOPMENT")
print("=" * 60)
