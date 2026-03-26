#!/usr/bin/env python3
"""
Safe Development → Production Database Sync
Merges development data into production while preserving all production users
"""

import os
import sys
from sqlalchemy import create_engine, text
from typing import Dict

# Color output
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    END = '\033[0m'

def log_info(msg): print(f"{Colors.BLUE}ℹ️  {msg}{Colors.END}")
def log_success(msg): print(f"{Colors.GREEN}✅ {msg}{Colors.END}")
def log_warning(msg): print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")
def log_error(msg): print(f"{Colors.RED}❌ {msg}{Colors.END}")


class SafeDevToProdSync:
    def __init__(self):
        self.dev_url = os.getenv('DATABASE_URL')
        self.prod_url = os.getenv('NEON_PRODUCTION_DATABASE_URL')
        
        if not self.dev_url or not self.prod_url:
            raise ValueError("DATABASE_URL and NEON_PRODUCTION_DATABASE_URL must be set")
        
        self.dev_engine = create_engine(self.dev_url)
        self.prod_engine = create_engine(self.prod_url)
        
        # Statistics
        self.stats = {
            'users_preserved': 0,
            'users_updated': 0,
            'tickets_synced': 0,
            'messages_synced': 0,
            'admin_set': False
        }
    
    def backup_production(self) -> bool:
        """Create a safety backup of production users table"""
        try:
            log_info("Creating production backup...")
            with self.prod_engine.connect() as conn:
                # Create backup table
                conn.execute(text("""
                    DROP TABLE IF EXISTS users_backup_sync CASCADE;
                    CREATE TABLE users_backup_sync AS SELECT * FROM users;
                    
                    DROP TABLE IF EXISTS support_tickets_backup_sync CASCADE;
                    CREATE TABLE support_tickets_backup_sync AS SELECT * FROM support_tickets;
                    
                    DROP TABLE IF EXISTS support_messages_backup_sync CASCADE;
                    CREATE TABLE support_messages_backup_sync AS SELECT * FROM support_messages;
                """))
                conn.commit()
                
            log_success("Production backup created successfully")
            return True
        except Exception as e:
            log_error(f"Backup failed: {e}")
            return False
    
    def build_user_mapping(self) -> Dict[int, int]:
        """Build telegram_id → production user ID mapping
        
        Returns dict: {dev_user_id: prod_user_id}
        """
        log_info("Building user ID reconciliation map...")
        mapping = {}
        
        with self.dev_engine.connect() as dev_conn, \
             self.prod_engine.connect() as prod_conn:
            
            # Get all dev users with their telegram_ids
            dev_users = dev_conn.execute(text("""
                SELECT id, telegram_id, username, email, is_admin
                FROM users
            """)).fetchall()
            
            # Get all prod users
            prod_users = prod_conn.execute(text("""
                SELECT id, telegram_id
                FROM users
            """)).fetchall()
            
            # Build telegram_id → prod_id lookup
            telegram_to_prod = {int(row[1]): row[0] for row in prod_users}
            
            # Map dev user IDs to prod user IDs
            for dev_id, telegram_id, username, email, is_admin in dev_users:
                telegram_id_int = int(telegram_id)
                
                if telegram_id_int in telegram_to_prod:
                    # User exists in both - map to existing prod ID
                    prod_id = telegram_to_prod[telegram_id_int]
                    mapping[dev_id] = prod_id
                    log_info(f"  Mapped dev user {dev_id} → prod user {prod_id} (@{username})")
                    
                    # Update production user data if needed
                    if is_admin:
                        prod_conn.execute(text("""
                            UPDATE users 
                            SET is_admin = true
                            WHERE id = :prod_id
                        """), {"prod_id": prod_id})
                        self.stats['admin_set'] = True
                        log_success(f"  Set admin flag for user {prod_id}")
                    
                    self.stats['users_updated'] += 1
                else:
                    # User only in dev - will create in prod with new ID
                    log_warning(f"  User @{username} (dev ID {dev_id}) not in production - will be skipped or added")
            
            prod_conn.commit()
            
            self.stats['users_preserved'] = len(prod_users)
            log_success(f"Built mapping: {len(mapping)} users matched, {len(prod_users)} total in production")
        
        return mapping
    
    def sync_support_tickets(self, user_mapping: Dict[int, int]) -> bool:
        """Sync support tickets with remapped user IDs"""
        try:
            log_info("Syncing support tickets...")
            
            with self.dev_engine.connect() as dev_conn, \
                 self.prod_engine.connect() as prod_conn:
                
                # Get dev tickets
                tickets = dev_conn.execute(text("""
                    SELECT 
                        ticket_id, user_id, subject, description, status, 
                        priority, category, created_at, resolved_at, assigned_to
                    FROM support_tickets
                    ORDER BY created_at
                """)).fetchall()
                
                for ticket in tickets:
                    ticket_id, user_id, subject, description, status, priority, category, created_at, resolved_at, assigned_to = ticket
                    
                    # Map dev user_id to prod user_id
                    if user_id not in user_mapping:
                        log_warning(f"  Skipping ticket {ticket_id} - user not in mapping")
                        continue
                    
                    prod_user_id = user_mapping[user_id]
                    prod_assigned_to = user_mapping.get(assigned_to) if assigned_to else None
                    
                    # Ensure subject has a default value if NULL
                    if not subject:
                        subject = "Support Request"
                    if not description:
                        description = "No description provided"
                    
                    # Insert or update ticket
                    prod_conn.execute(text("""
                        INSERT INTO support_tickets 
                            (ticket_id, user_id, subject, description, status, priority, category, created_at, resolved_at, assigned_to)
                        VALUES 
                            (:ticket_id, :user_id, :subject, :description, :status, :priority, :category, :created_at, :resolved_at, :assigned_to)
                        ON CONFLICT (ticket_id) 
                        DO UPDATE SET
                            subject = EXCLUDED.subject,
                            description = EXCLUDED.description,
                            status = EXCLUDED.status,
                            priority = EXCLUDED.priority,
                            category = EXCLUDED.category,
                            resolved_at = EXCLUDED.resolved_at,
                            assigned_to = EXCLUDED.assigned_to
                    """), {
                        "ticket_id": ticket_id,
                        "user_id": prod_user_id,
                        "subject": subject,
                        "description": description,
                        "status": status,
                        "priority": priority,
                        "category": category,
                        "created_at": created_at,
                        "resolved_at": resolved_at,
                        "assigned_to": prod_assigned_to
                    })
                    
                    self.stats['tickets_synced'] += 1
                
                prod_conn.commit()
            
            log_success(f"Synced {self.stats['tickets_synced']} support tickets")
            return True
            
        except Exception as e:
            log_error(f"Ticket sync failed: {e}")
            return False
    
    def sync_support_messages(self, user_mapping: Dict[int, int]) -> bool:
        """Sync support messages with remapped user IDs"""
        try:
            log_info("Syncing support messages...")
            
            with self.dev_engine.connect() as dev_conn, \
                 self.prod_engine.connect() as prod_conn:
                
                # Get dev messages
                messages = dev_conn.execute(text("""
                    SELECT 
                        m.ticket_id, m.sender_id, m.message, m.is_admin_reply, m.created_at,
                        t.ticket_id as ticket_number
                    FROM support_messages m
                    JOIN support_tickets t ON m.ticket_id = t.id
                    ORDER BY m.created_at
                """)).fetchall()
                
                for msg in messages:
                    ticket_id, sender_id, message, is_admin_reply, created_at, ticket_number = msg
                    
                    # Map sender_id
                    if sender_id not in user_mapping:
                        log_warning("  Skipping message - sender not in mapping")
                        continue
                    
                    prod_sender_id = user_mapping[sender_id]
                    
                    # Get prod ticket ID
                    prod_ticket = prod_conn.execute(text("""
                        SELECT id FROM support_tickets WHERE ticket_id = :ticket_number
                    """), {"ticket_number": ticket_number}).fetchone()
                    
                    if not prod_ticket:
                        log_warning(f"  Skipping message - ticket {ticket_number} not in production")
                        continue
                    
                    prod_ticket_id = prod_ticket[0]
                    
                    # Insert message (allow duplicates by checking if exists)
                    existing = prod_conn.execute(text("""
                        SELECT id FROM support_messages
                        WHERE ticket_id = :ticket_id 
                          AND sender_id = :sender_id
                          AND message = :message
                          AND created_at = :created_at
                    """), {
                        "ticket_id": prod_ticket_id,
                        "sender_id": prod_sender_id,
                        "message": message,
                        "created_at": created_at
                    }).fetchone()
                    
                    if not existing:
                        prod_conn.execute(text("""
                            INSERT INTO support_messages
                                (ticket_id, sender_id, message, is_admin_reply, created_at)
                            VALUES
                                (:ticket_id, :sender_id, :message, :is_admin_reply, :created_at)
                        """), {
                            "ticket_id": prod_ticket_id,
                            "sender_id": prod_sender_id,
                            "message": message,
                            "is_admin_reply": is_admin_reply,
                            "created_at": created_at
                        })
                        self.stats['messages_synced'] += 1
                
                prod_conn.commit()
            
            log_success(f"Synced {self.stats['messages_synced']} support messages")
            return True
            
        except Exception as e:
            log_error(f"Message sync failed: {e}")
            return False
    
    def verify_integrity(self) -> bool:
        """Verify data integrity after sync"""
        try:
            log_info("Verifying data integrity...")
            
            with self.prod_engine.connect() as conn:
                # Check user count
                user_count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
                if user_count < 54:
                    log_error(f"User count dropped! Expected ≥54, got {user_count}")
                    return False
                
                # Check admin exists
                admin_count = conn.execute(text("SELECT COUNT(*) FROM users WHERE is_admin = true")).scalar()
                if admin_count == 0:
                    log_error("No admin users found!")
                    return False
                
                # Check foreign key integrity
                orphan_tickets = conn.execute(text("""
                    SELECT COUNT(*) FROM support_tickets t
                    WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = t.user_id)
                """)).scalar()
                
                if orphan_tickets > 0:
                    log_error(f"Found {orphan_tickets} orphaned tickets!")
                    return False
                
                log_success(f"Integrity verified: {user_count} users, {admin_count} admins")
            
            return True
            
        except Exception as e:
            log_error(f"Verification failed: {e}")
            return False
    
    def run_sync(self) -> bool:
        """Execute the full sync process"""
        print("\n" + "="*60)
        print("🔄 SAFE DEVELOPMENT → PRODUCTION SYNC")
        print("="*60 + "\n")
        
        # Step 1: Backup
        if not self.backup_production():
            return False
        
        # Step 2: Build mapping
        user_mapping = self.build_user_mapping()
        if not user_mapping:
            log_error("Failed to build user mapping")
            return False
        
        # Step 3: Sync tickets
        if not self.sync_support_tickets(user_mapping):
            return False
        
        # Step 4: Sync messages
        if not self.sync_support_messages(user_mapping):
            return False
        
        # Step 5: Verify
        if not self.verify_integrity():
            log_error("Integrity check failed!")
            return False
        
        # Success!
        print("\n" + "="*60)
        print("✅ SYNC COMPLETED SUCCESSFULLY")
        print("="*60)
        print("\n📊 Statistics:")
        print(f"   Users preserved: {self.stats['users_preserved']}")
        print(f"   Users updated: {self.stats['users_updated']}")
        print(f"   Tickets synced: {self.stats['tickets_synced']}")
        print(f"   Messages synced: {self.stats['messages_synced']}")
        print(f"   Admin flag set: {self.stats['admin_set']}")
        print()
        
        return True
    
    def cleanup(self):
        """Close database connections"""
        self.dev_engine.dispose()
        self.prod_engine.dispose()


if __name__ == "__main__":
    try:
        sync = SafeDevToProdSync()
        success = sync.run_sync()
        sync.cleanup()
        
        if not success:
            log_error("Sync failed! Production backup tables available for rollback.")
            sys.exit(1)
        else:
            log_success("You can now safely drop backup tables if verification passed.")
            sys.exit(0)
            
    except Exception as e:
        log_error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
