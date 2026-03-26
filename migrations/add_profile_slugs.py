"""
Migration: Add Profile Slugs to Existing Users
==============================================
This migration adds unique profile slugs to all existing users who don't have one.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import User
from utils.profile_slug_generator import generate_profile_slug
from sqlalchemy import text


def migrate_profile_slugs():
    """Add profile slugs to all users who don't have one"""
    session = SessionLocal()
    
    try:
        print("🔧 Adding profile_slug column if not exists...")
        session.execute(text("""
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS profile_slug VARCHAR(50) UNIQUE;
        """))
        session.commit()
        print("✅ Column added/verified")
        
        print("\n🔍 Finding users without profile slugs...")
        users = session.query(User).filter(
            (User.profile_slug == None) | (User.profile_slug == '')
        ).all()
        
        print(f"📊 Found {len(users)} users needing profile slugs\n")
        
        success_count = 0
        error_count = 0
        
        for user in users:
            try:
                slug = generate_profile_slug(
                    session,
                    username=user.username,
                    first_name=user.first_name,
                    telegram_id=user.telegram_id
                )
                
                user.profile_slug = slug
                session.commit()
                
                success_count += 1
                print(f"✅ User {user.telegram_id} ({user.first_name or 'Unknown'}): {slug}")
                
            except Exception as e:
                error_count += 1
                print(f"❌ Error for user {user.telegram_id}: {e}")
                session.rollback()
        
        print("\n📊 Migration complete:")
        print(f"   ✅ Success: {success_count}")
        print(f"   ❌ Errors: {error_count}")
        
        if success_count > 0:
            print("\n🔧 Adding unique index to profile_slug...")
            try:
                session.execute(text("""
                    CREATE UNIQUE INDEX IF NOT EXISTS ix_users_profile_slug 
                    ON users(profile_slug);
                """))
                session.commit()
                print("✅ Index created")
            except Exception as e:
                print(f"⚠️  Index creation note: {e}")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        session.rollback()
        raise
    
    finally:
        session.close()


if __name__ == "__main__":
    migrate_profile_slugs()
