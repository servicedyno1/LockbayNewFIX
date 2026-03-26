#!/usr/bin/env python3
"""
Automated Git Deployment Script for LockBay
Handles committing and pushing code changes to GitHub from Replit
"""

import os
import sys
import subprocess
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

class GitDeployer:
    def __init__(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.git_dir = os.path.join(self.repo_root, ".git")
        
    def run_git_command(self, args: List[str], check_output: bool = True):
        """Execute a git command and return output (str) or success (bool)"""
        cmd = ["git"] + args
        try:
            if check_output:
                result = subprocess.run(
                    cmd, 
                    cwd=self.repo_root, 
                    capture_output=True, 
                    text=True, 
                    check=True
                )
                return result.stdout.strip()
            else:
                subprocess.run(cmd, cwd=self.repo_root, check=True)
                return True  # Return True to indicate success
        except subprocess.CalledProcessError as e:
            print(f"❌ Git command failed: {' '.join(cmd)}")
            print(f"Error: {e.stderr if hasattr(e, 'stderr') and e.stderr else str(e)}")
            return False if not check_output else None
        except Exception as e:
            print(f"❌ Unexpected error running git command: {e}")
            return False if not check_output else None
    
    def check_repo_status(self) -> bool:
        """Check if we're in a valid git repository"""
        if not os.path.exists(self.git_dir):
            print("❌ Not a git repository. Initialize with: git init")
            return False
        
        # Check if we have a remote
        remotes = self.run_git_command(["remote", "-v"])
        if not remotes:
            print("❌ No git remotes configured. Add with: git remote add origin <url>")
            return False
        
        print("✅ Git repository detected with remotes:")
        print(remotes)
        return True
    
    def get_repo_status(self) -> Dict[str, any]:
        """Get detailed repository status"""
        status = {
            "has_changes": False,
            "staged_files": [],
            "unstaged_files": [],
            "untracked_files": [],
            "current_branch": None,
            "ahead_behind": None
        }
        
        # Get current branch
        branch = self.run_git_command(["rev-parse", "--abbrev-ref", "HEAD"])
        if branch:
            status["current_branch"] = branch
        
        # Get status
        git_status = self.run_git_command(["status", "--porcelain"])
        if git_status:
            status["has_changes"] = True
            for line in git_status.split('\n'):
                if not line:
                    continue
                    
                status_code = line[:2]
                filename = line[3:]
                
                if status_code[0] in ['A', 'M', 'D', 'R', 'C']:
                    status["staged_files"].append(filename)
                elif status_code[1] in ['M', 'D']:
                    status["unstaged_files"].append(filename)
                elif status_code == '??':
                    status["untracked_files"].append(filename)
        
        # Check if we're ahead/behind remote
        if status["current_branch"]:
            try:
                ahead_behind = self.run_git_command([
                    "rev-list", "--left-right", "--count", 
                    f"HEAD...origin/{status['current_branch']}"
                ])
                if ahead_behind:
                    ahead, behind = ahead_behind.split('\t')
                    status["ahead_behind"] = {"ahead": int(ahead), "behind": int(behind)}
            except Exception as e:
                logger.debug(f"Could not check ahead/behind status: {e}")
                pass
        
        return status
    
    def stage_files(self, files: List[str] = None) -> bool:
        """Stage files for commit"""
        if files:
            for file in files:
                if not self.run_git_command(["add", file], check_output=False):
                    return False
            print(f"✅ Staged {len(files)} files")
        else:
            if not self.run_git_command(["add", "."], check_output=False):
                return False
            print("✅ Staged all changes")
        return True
    
    def commit_changes(self, message: str) -> bool:
        """Commit staged changes"""
        if not self.run_git_command(["commit", "-m", message], check_output=False):
            return False
        print(f"✅ Committed changes: {message}")
        return True
    
    def push_changes(self, branch: Optional[str] = None) -> bool:
        """Push changes to remote repository"""
        args = ["push"]
        if branch:
            args.extend(["origin", branch])
        
        if not self.run_git_command(args, check_output=False):
            return False
        
        print("✅ Successfully pushed changes to GitHub")
        return True
    
    def generate_commit_message(self, status: Dict[str, any]) -> str:
        """Generate automatic commit message based on changes"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Count changes by type
        total_files = len(status["staged_files"]) + len(status["unstaged_files"]) + len(status["untracked_files"])
        
        # Basic commit message
        if total_files == 1:
            return f"Update: Single file change - {timestamp}"
        elif total_files <= 5:
            return f"Update: {total_files} files changed - {timestamp}"
        else:
            return f"Major update: {total_files} files changed - {timestamp}"
    
    def interactive_deploy(self) -> bool:
        """Interactive deployment with user prompts"""
        print("🚀 LockBay Automated Deployment")
        print("=" * 40)
        
        # Check repository
        if not self.check_repo_status():
            return False
        
        # Get status
        status = self.get_repo_status()
        
        if not status["has_changes"]:
            print("✅ No changes detected. Repository is up to date.")
            
            # Check if we're behind remote
            if status["ahead_behind"] and status["ahead_behind"]["behind"] > 0:
                print(f"⚠️ Repository is {status['ahead_behind']['behind']} commits behind remote")
                pull = input("Pull latest changes? (y/N): ").strip().lower()
                if pull == 'y':
                    return self.run_git_command(["pull"], check_output=False)
            return True
        
        # Display changes
        print(f"\n📋 Repository Status (Branch: {status['current_branch']})")
        
        if status["staged_files"]:
            print(f"✅ Staged files ({len(status['staged_files'])}):")
            for file in status["staged_files"][:10]:  # Show first 10
                print(f"   • {file}")
            if len(status["staged_files"]) > 10:
                print(f"   ... and {len(status['staged_files']) - 10} more")
        
        if status["unstaged_files"]:
            print(f"⚠️ Unstaged changes ({len(status['unstaged_files'])}):")
            for file in status["unstaged_files"][:10]:
                print(f"   • {file}")
            if len(status["unstaged_files"]) > 10:
                print(f"   ... and {len(status['unstaged_files']) - 10} more")
        
        if status["untracked_files"]:
            print(f"❓ Untracked files ({len(status['untracked_files'])}):")
            for file in status["untracked_files"][:10]:
                print(f"   • {file}")
            if len(status["untracked_files"]) > 10:
                print(f"   ... and {len(status['untracked_files']) - 10} more")
        
        # Stage files if needed
        if status["unstaged_files"] or status["untracked_files"]:
            stage_all = input("\n📦 Stage all changes? (Y/n): ").strip().lower()
            if stage_all != 'n':
                if not self.stage_files():
                    return False
        
        # Commit message
        default_message = self.generate_commit_message(status)
        commit_message = input(f"\n💬 Commit message (default: '{default_message}'): ").strip()
        if not commit_message:
            commit_message = default_message
        
        # Commit
        if not self.commit_changes(commit_message):
            return False
        
        # Push
        push_confirm = input(f"\n🚀 Push to GitHub (branch: {status['current_branch']})? (Y/n): ").strip().lower()
        if push_confirm != 'n':
            return self.push_changes(status["current_branch"])
        
        print("✅ Changes committed but not pushed")
        return True
    
    def auto_deploy(self, commit_message: Optional[str] = None) -> bool:
        """Automated deployment without prompts"""
        print("🤖 Automated Deployment Started")
        
        if not self.check_repo_status():
            return False
        
        status = self.get_repo_status()
        
        if not status["has_changes"]:
            print("✅ No changes to deploy")
            return True
        
        # Stage all changes
        if not self.stage_files():
            return False
        
        # Use provided message or generate one
        if not commit_message:
            commit_message = self.generate_commit_message(status)
        
        # Commit and push
        if not self.commit_changes(commit_message):
            return False
        
        return self.push_changes(status["current_branch"])

def main():
    deployer = GitDeployer()
    
    # Parse command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--auto":
            message = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None
            success = deployer.auto_deploy(message)
        elif sys.argv[1] == "--status":
            deployer.check_repo_status()
            status = deployer.get_repo_status()
            print(json.dumps(status, indent=2))
            return
        elif sys.argv[1] == "--help":
            print("""
LockBay Automated Deployment Script

Usage:
  python deploy.py                    # Interactive deployment
  python deploy.py --auto [message]   # Automated deployment
  python deploy.py --status          # Show repository status
  python deploy.py --help            # Show this help

Examples:
  python deploy.py --auto "Fix payment processing bug"
  python deploy.py --auto             # Uses generated commit message
            """)
            return
        else:
            print(f"Unknown option: {sys.argv[1]}")
            print("Use --help for usage information")
            return
    else:
        success = deployer.interactive_deploy()
    
    if success:
        print("\n🎉 Deployment completed successfully!")
    else:
        print("\n❌ Deployment failed")
        sys.exit(1)

if __name__ == "__main__":
    main()