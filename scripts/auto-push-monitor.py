#!/usr/bin/env python3
"""
Automatic Real-time GitHub Push Monitor for LockBay
Monitors file changes and automatically pushes to GitHub repository
"""

import os
import time
import subprocess
import threading
from datetime import datetime
from typing import Optional
import hashlib

class AutoPushMonitor:
    def __init__(self, 
                 repo_path: str = ".", 
                 push_delay: int = 30,
                 max_push_interval: int = 300):
        self.repo_path = os.path.abspath(repo_path)
        self.push_delay = push_delay  # Seconds to wait after last change before pushing
        self.max_push_interval = max_push_interval  # Maximum seconds between pushes
        self.last_change_time = None
        self.last_push_time = datetime.now()
        self.pending_changes = False
        self.running = False
        self.push_lock = threading.Lock()
        
        # Files and directories to ignore
        self.ignore_patterns = {
            '.git/', '.cache/', '__pycache__/', '.pytest_cache/', 'node_modules/',
            '.replit', 'replit.nix', '.DS_Store', '*.pyc', '*.pyo', '*.pyd',
            '.coverage', 'coverage.xml', '*.log', 'logs/', '.env', '.env.local',
            'attached_assets/', 'backup/', 'backups/', 'archive/'
        }
        
        # Track file hashes to detect real changes
        self.file_hashes = {}
        self.update_file_hashes()
    
    def should_ignore_file(self, file_path: str) -> bool:
        """Check if file should be ignored based on patterns"""
        relative_path = os.path.relpath(file_path, self.repo_path)
        
        for pattern in self.ignore_patterns:
            if pattern.endswith('/'):
                # Directory pattern
                if relative_path.startswith(pattern) or f"/{pattern}" in relative_path:
                    return True
            elif pattern.startswith('*.'):
                # Extension pattern
                if relative_path.endswith(pattern[1:]):
                    return True
            else:
                # Exact match or substring
                if pattern in relative_path:
                    return True
        
        return False
    
    def get_file_hash(self, file_path: str) -> Optional[str]:
        """Get MD5 hash of file content"""
        try:
            if not os.path.exists(file_path) or os.path.isdir(file_path):
                return None
            
            with open(file_path, 'rb') as f:
                content = f.read()
                return hashlib.md5(content).hexdigest()
        except Exception:
            return None
    
    def update_file_hashes(self):
        """Update hash cache for all tracked files"""
        new_hashes = {}
        
        try:
            # Get list of tracked files from git
            result = subprocess.run(
                ["git", "ls-files"], 
                cwd=self.repo_path,
                capture_output=True, 
                text=True, 
                check=True
            )
            
            tracked_files = result.stdout.strip().split('\n')
            
            for relative_file in tracked_files:
                if not relative_file:
                    continue
                    
                full_path = os.path.join(self.repo_path, relative_file)
                file_hash = self.get_file_hash(full_path)
                
                if file_hash:
                    new_hashes[relative_file] = file_hash
            
        except subprocess.CalledProcessError:
            # Fallback: scan directory manually
            for root, dirs, files in os.walk(self.repo_path):
                # Skip ignored directories
                dirs[:] = [d for d in dirs if not self.should_ignore_file(os.path.join(root, d))]
                
                for file in files:
                    full_path = os.path.join(root, file)
                    
                    if self.should_ignore_file(full_path):
                        continue
                    
                    relative_path = os.path.relpath(full_path, self.repo_path)
                    file_hash = self.get_file_hash(full_path)
                    
                    if file_hash:
                        new_hashes[relative_path] = file_hash
        
        self.file_hashes = new_hashes
    
    def has_real_changes(self) -> bool:
        """Check if there are real content changes (not just file system events)"""
        current_hashes = {}
        
        # Get current hashes
        for relative_file in self.file_hashes.keys():
            full_path = os.path.join(self.repo_path, relative_file)
            current_hash = self.get_file_hash(full_path)
            if current_hash:
                current_hashes[relative_file] = current_hash
        
        # Also check for new files
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"], 
                cwd=self.repo_path,
                capture_output=True, 
                text=True, 
                check=True
            )
            
            if result.stdout.strip():
                # There are git changes
                return True
                
        except subprocess.CalledProcessError:
            pass
        
        # Compare hashes
        if current_hashes != self.file_hashes:
            self.file_hashes = current_hashes
            return True
        
        return False
    
    def run_git_command(self, args: list) -> bool:
        """Run a git command and return success status"""
        try:
            subprocess.run(
                ["git"] + args, 
                cwd=self.repo_path,
                check=True, 
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"Git command failed: {' '.join(['git'] + args)}")
            if hasattr(e, 'stderr') and e.stderr:
                print(f"Error: {e.stderr.decode()}")
            return False
    
    def auto_commit_and_push(self) -> bool:
        """Automatically stage, commit, and push changes"""
        with self.push_lock:
            print(f"\n⚡ Auto-push triggered at {datetime.now().strftime('%H:%M:%S')}")
            
            # Check for real changes
            if not self.has_real_changes():
                print("📄 No real changes detected, skipping push")
                return True
            
            # Stage all changes
            print("📦 Staging changes...")
            if not self.run_git_command(["add", "."]):
                print("❌ Failed to stage changes")
                return False
            
            # Check if there are staged changes
            try:
                result = subprocess.run(
                    ["git", "diff", "--cached", "--name-only"],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    check=True
                )
                
                if not result.stdout.strip():
                    print("📄 No staged changes, skipping commit")
                    return True
                    
            except subprocess.CalledProcessError:
                pass
            
            # Generate commit message
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_message = f"Auto-commit: Changes detected - {timestamp}"
            
            # Commit changes
            print(f"💾 Committing: {commit_message}")
            if not self.run_git_command(["commit", "-m", commit_message]):
                print("❌ Failed to commit changes")
                return False
            
            # Get current branch
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    check=True
                )
                branch = result.stdout.strip()
            except subprocess.CalledProcessError:
                branch = "main"  # fallback
            
            # Push to GitHub
            print(f"🚀 Pushing to GitHub ({branch})...")
            if not self.run_git_command(["push", "origin", branch]):
                print("❌ Failed to push to GitHub")
                return False
            
            print("✅ Successfully pushed to GitHub!")
            self.last_push_time = datetime.now()
            self.pending_changes = False
            
            # Update file hashes after successful push
            self.update_file_hashes()
            
            return True
    
    def scan_for_changes(self):
        """Scan for file changes and trigger push if needed"""
        while self.running:
            try:
                current_time = datetime.now()
                
                # Check if we have real changes
                if self.has_real_changes():
                    if not self.pending_changes:
                        print(f"📝 Changes detected at {current_time.strftime('%H:%M:%S')}")
                        self.pending_changes = True
                        self.last_change_time = current_time
                    
                    # Check if enough time has passed since last change
                    time_since_change = (current_time - self.last_change_time).total_seconds()
                    time_since_push = (current_time - self.last_push_time).total_seconds()
                    
                    should_push = (
                        time_since_change >= self.push_delay or 
                        time_since_push >= self.max_push_interval
                    )
                    
                    if should_push and self.pending_changes:
                        self.auto_commit_and_push()
                
                # Sleep before next scan
                time.sleep(5)  # Check every 5 seconds
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ Error in change scanner: {e}")
                time.sleep(10)  # Wait longer on error
    
    def start_monitoring(self):
        """Start the automatic push monitoring"""
        if self.running:
            print("⚠️ Monitor already running")
            return
        
        print("🚀 Starting Automatic GitHub Push Monitor")
        print(f"📂 Monitoring: {self.repo_path}")
        print(f"⏱️ Push delay: {self.push_delay} seconds after last change")
        print(f"🔄 Max interval: {self.max_push_interval} seconds between pushes")
        print("=" * 50)
        
        # Check if we're in a git repository
        if not os.path.exists(os.path.join(self.repo_path, ".git")):
            print("❌ Not a git repository")
            return
        
        # Check git status
        try:
            subprocess.run(
                ["git", "status"], 
                cwd=self.repo_path,
                check=True,
                capture_output=True
            )
        except subprocess.CalledProcessError:
            print("❌ Git repository has issues")
            return
        
        self.running = True
        
        # Start monitoring in background thread
        monitor_thread = threading.Thread(target=self.scan_for_changes, daemon=True)
        monitor_thread.start()
        
        print("✅ Auto-push monitor started! Press Ctrl+C to stop.")
        
        try:
            # Keep main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Stopping auto-push monitor...")
            self.running = False
            
            # Final push if there are pending changes
            if self.pending_changes:
                print("📤 Final push of pending changes...")
                self.auto_commit_and_push()
            
            print("✅ Auto-push monitor stopped")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Automatic Real-time GitHub Push Monitor")
    parser.add_argument("--delay", type=int, default=30, 
                       help="Seconds to wait after last change before pushing (default: 30)")
    parser.add_argument("--max-interval", type=int, default=300,
                       help="Maximum seconds between pushes (default: 300)")
    parser.add_argument("--repo-path", default=".",
                       help="Path to git repository (default: current directory)")
    
    args = parser.parse_args()
    
    # Create and start monitor
    monitor = AutoPushMonitor(
        repo_path=args.repo_path,
        push_delay=args.delay,
        max_push_interval=args.max_interval
    )
    
    monitor.start_monitoring()

if __name__ == "__main__":
    main()