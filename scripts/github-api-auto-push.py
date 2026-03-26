#!/usr/bin/env python3
"""
GitHub API-based Auto-Push Monitor for LockBay
Uses GitHub REST API directly to push changes without Git commands
"""

import os
import time
import requests
import threading
import hashlib
import base64
from datetime import datetime
from typing import Dict, Optional

class GitHubAPIPushMonitor:
    def __init__(self, 
                 repo_owner: str = "Moxxcompany2",
                 repo_name: str = "lockbaynew", 
                 branch: str = "main",
                 push_delay: int = 60,
                 max_push_interval: int = 600):
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.branch = branch
        self.push_delay = push_delay
        self.max_push_interval = max_push_interval
        
        # Working directory
        self.workspace_path = os.path.abspath(".")
        
        # State tracking
        self.last_change_time = None
        self.last_push_time = datetime.now()
        self.pending_changes = False
        self.running = False
        self.push_lock = threading.Lock()
        
        # GitHub API
        self.github_token = None
        self.session = requests.Session()
        self.base_url = "https://api.github.com"
        
        # Files and directories to ignore
        self.ignore_patterns = {
            '.git/', '.cache/', '__pycache__/', '.pytest_cache/', 'node_modules/',
            '.replit', 'replit.nix', '.DS_Store', '*.pyc', '*.pyo', '*.pyd',
            '.coverage', 'coverage.xml', '*.log', 'logs/', '.env', '.env.local',
            'attached_assets/', 'backup/', 'backups/', 'archive/', 'tmp/', '/tmp/',
            '.pythonlibs/', '.venv/', 'venv/', 'env/', 'ENV/', 'htmlcov/', 'htmlcov_100_percent_final/',
            'coverage_reports/', '*.db', '*.sqlite', '*.sqlite3', 'webhook_queue/webhook_inbox/webhook_events.db'
        }
        
        # Track file content hashes
        self.file_hashes = {}
        
    def get_github_token(self) -> Optional[str]:
        """Get GitHub access token from Replit connection"""
        try:
            hostname = os.getenv('REPLIT_CONNECTORS_HOSTNAME')
            x_replit_token = (
                f"repl {os.getenv('REPL_IDENTITY')}" if os.getenv('REPL_IDENTITY') else
                f"depl {os.getenv('WEB_REPL_RENEWAL')}" if os.getenv('WEB_REPL_RENEWAL') else
                None
            )
            
            if not hostname or not x_replit_token:
                print("❌ Missing Replit connection environment variables")
                return None
            
            url = f"https://{hostname}/api/v2/connection?include_secrets=true&connector_names=github"
            headers = {
                'Accept': 'application/json',
                'X_REPLIT_TOKEN': x_replit_token
            }
            
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                print(f"❌ Failed to get GitHub connection: {response.status_code}")
                return None
            
            data = response.json()
            items = data.get('items', [])
            
            if not items:
                print("❌ No GitHub connection found")
                return None
            
            connection = items[0]
            settings = connection.get('settings', {})
            
            # Try different token locations
            token = (
                settings.get('access_token') or
                settings.get('oauth', {}).get('credentials', {}).get('access_token')
            )
            
            if not token:
                print("❌ No GitHub access token found in connection")
                return None
                
            return token
            
        except Exception as e:
            print(f"❌ Error getting GitHub token: {e}")
            return None
    
    def should_ignore_file(self, file_path: str) -> bool:
        """Check if file should be ignored"""
        relative_path = os.path.relpath(file_path, self.workspace_path)
        
        for pattern in self.ignore_patterns:
            if pattern.endswith('/'):
                if relative_path.startswith(pattern) or f"/{pattern}" in relative_path:
                    return True
            elif pattern.startswith('*.'):
                if relative_path.endswith(pattern[1:]):
                    return True
            else:
                if pattern in relative_path:
                    return True
        
        return False
    
    def get_file_content(self, file_path: str) -> Optional[bytes]:
        """Get file content as bytes"""
        try:
            if not os.path.exists(file_path) or os.path.isdir(file_path):
                return None
            with open(file_path, 'rb') as f:
                return f.read()
        except Exception:
            return None
    
    def get_file_hash(self, content: bytes) -> str:
        """Get SHA1 hash of file content (GitHub uses SHA1)"""
        return hashlib.sha1(content).hexdigest()
    
    def scan_workspace_files(self) -> Dict[str, bytes]:
        """Scan workspace and return all non-ignored files with content"""
        files = {}
        
        for root, dirs, filenames in os.walk(self.workspace_path):
            # Skip ignored directories
            dirs[:] = [d for d in dirs if not self.should_ignore_file(os.path.join(root, d))]
            
            for filename in filenames:
                full_path = os.path.join(root, filename)
                
                if self.should_ignore_file(full_path):
                    continue
                
                relative_path = os.path.relpath(full_path, self.workspace_path)
                content = self.get_file_content(full_path)
                
                if content is not None:
                    files[relative_path] = content
        
        return files
    
    def has_changes(self) -> bool:
        """Check if there are changes since last scan"""
        current_files = self.scan_workspace_files()
        
        # Compare with cached hashes but don't update them yet
        current_hashes = {
            path: self.get_file_hash(content) 
            for path, content in current_files.items()
        }
        
        return current_hashes != self.file_hashes
    
    def update_file_hashes_after_push(self):
        """Update file hashes after successful push"""
        current_files = self.scan_workspace_files()
        self.file_hashes = {
            path: self.get_file_hash(content) 
            for path, content in current_files.items()
        }
    
    def github_api_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> Optional[dict]:
        """Make authenticated GitHub API request"""
        if not self.github_token:
            return None
        
        url = f"{self.base_url}{endpoint}"
        headers = {
            'Authorization': f'token {self.github_token}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'LockBay-AutoPush/1.0'
        }
        
        try:
            if method.upper() == 'GET':
                response = self.session.get(url, headers=headers)
            elif method.upper() == 'POST':
                response = self.session.post(url, headers=headers, json=data)
            elif method.upper() == 'PUT':
                response = self.session.put(url, headers=headers, json=data)
            elif method.upper() == 'PATCH':
                response = self.session.patch(url, headers=headers, json=data)
            else:
                print(f"❌ Unsupported method: {method}")
                return None
            
            if response.status_code not in [200, 201]:
                print(f"❌ GitHub API error {response.status_code}: {response.text}")
                return None
            
            return response.json()
            
        except Exception as e:
            print(f"❌ GitHub API request failed: {e}")
            return None
    
    def get_latest_commit_sha(self) -> Optional[str]:
        """Get the SHA of the latest commit on the branch"""
        endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/git/refs/heads/{self.branch}"
        result = self.github_api_request('GET', endpoint)
        
        if result and 'object' in result:
            return result['object']['sha']
        return None  # Empty repository case
    
    def get_tree_sha(self, commit_sha: str) -> Optional[str]:
        """Get tree SHA from commit"""
        endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/git/commits/{commit_sha}"
        result = self.github_api_request('GET', endpoint)
        
        if result and 'tree' in result:
            return result['tree']['sha']
        return None
    
    def create_blobs(self, files: Dict[str, bytes]) -> Dict[str, str]:
        """Create blobs for all files and return path->sha mapping"""
        blob_shas = {}
        
        for file_path, content in files.items():
            # Create blob
            blob_data = {
                'content': base64.b64encode(content).decode('utf-8'),
                'encoding': 'base64'
            }
            
            endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/git/blobs"
            result = self.github_api_request('POST', endpoint, blob_data)
            
            if result and 'sha' in result:
                blob_shas[file_path] = result['sha']
            else:
                print(f"❌ Failed to create blob for {file_path}")
        
        return blob_shas
    
    def create_tree(self, blob_shas: Dict[str, str], base_tree_sha: Optional[str] = None) -> Optional[str]:
        """Create new tree with all files"""
        tree_items = []
        
        for file_path, blob_sha in blob_shas.items():
            tree_items.append({
                'path': file_path,
                'mode': '100644',  # Regular file
                'type': 'blob',
                'sha': blob_sha
            })
        
        tree_data = {'tree': tree_items}
        
        # Add base_tree only if we have one (not for initial commit)
        if base_tree_sha:
            tree_data['base_tree'] = base_tree_sha
        
        endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/git/trees"
        result = self.github_api_request('POST', endpoint, tree_data)
        
        if result and 'sha' in result:
            return result['sha']
        return None
    
    def create_commit(self, tree_sha: str, parent_commit_sha: Optional[str], message: str) -> Optional[str]:
        """Create new commit"""
        commit_data = {
            'message': message,
            'tree': tree_sha,
            'author': {
                'name': 'LockBay Bot',
                'email': 'lockbay@moxxcompany.com',
                'date': datetime.utcnow().isoformat() + 'Z'
            },
            'committer': {
                'name': 'LockBay Bot', 
                'email': 'lockbay@moxxcompany.com',
                'date': datetime.utcnow().isoformat() + 'Z'
            }
        }
        
        # Add parents only if we have a parent commit (not for initial commit)
        if parent_commit_sha:
            commit_data['parents'] = [parent_commit_sha]
        
        endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/git/commits"
        result = self.github_api_request('POST', endpoint, commit_data)
        
        if result and 'sha' in result:
            return result['sha']
        return None
    
    def update_branch_reference(self, new_commit_sha: str, is_initial_commit: bool = False) -> bool:
        """Update or create branch to point to new commit"""
        ref_data = {
            'sha': new_commit_sha
        }
        
        if is_initial_commit:
            # Create new reference for initial commit
            ref_data['ref'] = f'refs/heads/{self.branch}'
            endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/git/refs"
            result = self.github_api_request('POST', endpoint, ref_data)
        else:
            # Update existing reference
            ref_data['force'] = False
            endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/git/refs/heads/{self.branch}"
            result = self.github_api_request('PATCH', endpoint, ref_data)
        
        return result is not None
    
    def push_changes_via_api(self) -> bool:
        """Push all changes using GitHub API"""
        with self.push_lock:
            print(f"\n⚡ GitHub API push triggered at {datetime.now().strftime('%H:%M:%S')}")
            
            # Check if we have changes
            if not self.has_changes():
                print("📄 No changes detected, skipping push")
                return True
            
            # Get all current files
            print("📂 Scanning workspace files...")
            current_files = self.scan_workspace_files()
            
            if not current_files:
                print("📄 No files found, skipping push")
                return True
            
            print(f"📦 Found {len(current_files)} files to sync")
            
            # Check if repository is empty
            print("🔍 Getting latest commit...")
            latest_commit_sha = self.get_latest_commit_sha()
            is_initial_commit = latest_commit_sha is None
            
            base_tree_sha = None
            if not is_initial_commit:
                # Get base tree for existing repository
                print("🌳 Getting base tree...")
                base_tree_sha = self.get_tree_sha(latest_commit_sha)
                if not base_tree_sha:
                    print("❌ Failed to get base tree")
                    return False
            else:
                print("🌱 Empty repository detected - creating initial commit")
            
            # Create blobs for all files
            print("🔧 Creating blobs...")
            blob_shas = self.create_blobs(current_files)
            
            if not blob_shas:
                print("❌ Failed to create blobs")
                return False
            
            print(f"✅ Created {len(blob_shas)} blobs")
            
            # Create new tree
            print("🌳 Creating new tree...")
            new_tree_sha = self.create_tree(blob_shas, base_tree_sha)
            if not new_tree_sha:
                print("❌ Failed to create tree")
                return False
            
            # Create commit
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if is_initial_commit:
                commit_message = f"Initial commit via GitHub API: {len(blob_shas)} files - {timestamp}"
            else:
                commit_message = f"Auto-commit via GitHub API: Changes detected - {timestamp}"
            
            print(f"💾 Creating commit: {commit_message}")
            new_commit_sha = self.create_commit(new_tree_sha, latest_commit_sha, commit_message)
            if not new_commit_sha:
                print("❌ Failed to create commit")
                return False
            
            # Update or create branch reference
            if is_initial_commit:
                print(f"🌱 Creating {self.branch} branch...")
            else:
                print(f"🚀 Updating {self.branch} branch...")
            
            if not self.update_branch_reference(new_commit_sha, is_initial_commit):
                print("❌ Failed to update/create branch")
                return False
            
            print("✅ Successfully pushed to GitHub via API!")
            self.last_push_time = datetime.now()
            self.pending_changes = False
            
            # Update file hashes after successful push
            self.update_file_hashes_after_push()
            
            return True
    
    def monitor_changes(self):
        """Monitor for changes and trigger pushes"""
        while self.running:
            try:
                current_time = datetime.now()
                
                # Check for changes
                if self.has_changes():
                    if not self.pending_changes:
                        print(f"📝 Changes detected at {current_time.strftime('%H:%M:%S')}")
                        self.pending_changes = True
                        self.last_change_time = current_time
                    
                    # Check if we should push
                    time_since_change = (current_time - self.last_change_time).total_seconds() if self.last_change_time else float('inf')
                    time_since_push = (current_time - self.last_push_time).total_seconds()
                    
                    should_push = (
                        time_since_change >= self.push_delay or 
                        time_since_push >= self.max_push_interval
                    )
                    
                    if should_push and self.pending_changes:
                        self.push_changes_via_api()
                
                # Sleep before next scan
                time.sleep(5)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ Error in change monitor: {e}")
                time.sleep(10)
    
    def start_monitoring(self):
        """Start the GitHub API auto-push monitor"""
        if self.running:
            print("⚠️ Monitor already running")
            return
        
        print("🚀 Starting GitHub API Auto-Push Monitor")
        print(f"📂 Repository: {self.repo_owner}/{self.repo_name}")
        print(f"🌿 Branch: {self.branch}")
        print(f"📁 Workspace: {self.workspace_path}")
        print(f"⏱️ Push delay: {self.push_delay} seconds after last change")
        print(f"🔄 Max interval: {self.max_push_interval} seconds between pushes")
        print("=" * 60)
        
        # Get GitHub token
        print("🔑 Authenticating with GitHub...")
        self.github_token = self.get_github_token()
        
        if not self.github_token:
            print("❌ Failed to get GitHub access token")
            return
        
        print("✅ GitHub authentication successful")
        
        # Test API access
        print("🧪 Testing GitHub API access...")
        repo_info = self.github_api_request('GET', f"/repos/{self.repo_owner}/{self.repo_name}")
        
        if not repo_info:
            print("❌ Failed to access repository")
            return
        
        print(f"✅ Repository access confirmed: {repo_info.get('full_name', 'unknown')}")
        
        # Initialize file tracking
        print("📊 Initializing file tracking...")
        self.has_changes()  # This populates file_hashes
        print(f"📈 Tracking {len(self.file_hashes)} files")
        
        self.running = True
        
        # Start monitoring in background thread
        monitor_thread = threading.Thread(target=self.monitor_changes, daemon=True)
        monitor_thread.start()
        
        print("✅ GitHub API auto-push monitor started! Press Ctrl+C to stop.")
        
        try:
            # Keep main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Stopping GitHub API auto-push monitor...")
            self.running = False
            
            # Final push if there are pending changes
            if self.pending_changes:
                print("📤 Final push of pending changes...")
                self.push_changes_via_api()
            
            print("✅ GitHub API auto-push monitor stopped")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="GitHub API Auto-Push Monitor")
    parser.add_argument("--delay", type=int, default=60, 
                       help="Seconds to wait after last change before pushing (default: 60)")
    parser.add_argument("--max-interval", type=int, default=600,
                       help="Maximum seconds between pushes (default: 600)")
    parser.add_argument("--repo-owner", default="Moxxcompany2",
                       help="GitHub repository owner (default: Moxxcompany2)")
    parser.add_argument("--repo-name", default="lockbaynew",
                       help="GitHub repository name (default: lockbaynew)")
    parser.add_argument("--branch", default="main",
                       help="Branch to push to (default: main)")
    
    args = parser.parse_args()
    
    # Create and start monitor
    monitor = GitHubAPIPushMonitor(
        repo_owner=args.repo_owner,
        repo_name=args.repo_name,
        branch=args.branch,
        push_delay=args.delay,
        max_push_interval=args.max_interval
    )
    
    monitor.start_monitoring()

if __name__ == "__main__":
    main()