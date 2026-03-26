#!/usr/bin/env python3
"""
Startup Cleanup Utilities for LockBay Bot
Ensures clean startup by removing stale processes and locks
"""

import os
import time
import psutil
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class StartupCleaner:
    """Comprehensive startup cleanup for bot processes"""
    
    def __init__(self):
        self.bot_process_patterns = [
            'production_start.py',
            'main.py',
            'lockbay',
            'escrowprototype'
        ]
        
    def find_stale_processes(self) -> List[dict]:
        """Find potentially stale bot processes"""
        stale_processes = []
        current_pid = os.getpid()
        
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time', 'status']):
                try:
                    if proc.info['pid'] == current_pid:
                        continue
                        
                    # Check if it matches our bot patterns
                    if proc.info['cmdline']:
                        cmdline_str = ' '.join(proc.info['cmdline'])
                        
                        if any(pattern in cmdline_str for pattern in self.bot_process_patterns):
                            # Check if it's been running for a while (more than 5 minutes)
                            create_time = proc.info['create_time']
                            if time.time() - create_time > 300:  # 5 minutes
                                stale_processes.append({
                                    'pid': proc.info['pid'],
                                    'cmdline': cmdline_str,
                                    'age_minutes': (time.time() - create_time) / 60,
                                    'status': proc.info['status']
                                })
                                
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                    
        except Exception as e:
            logger.warning(f"Error scanning for stale processes: {e}")
            
        return stale_processes
    
    def cleanup_temp_files(self):
        """Clean up temporary bot files"""
        temp_patterns = [
            '/tmp/lockbay_bot.*',
            '/tmp/bot_logs',
            '/tmp/*.pid',
            '/tmp/*.lock'
        ]
        
        try:
            import glob
            for pattern in temp_patterns:
                for file_path in glob.glob(pattern):
                    try:
                        if os.path.isfile(file_path):
                            # Check if file is older than 10 minutes
                            if time.time() - os.path.getmtime(file_path) > 600:
                                os.unlink(file_path)
                                logger.info(f"Cleaned up stale temp file: {file_path}")
                        elif os.path.isdir(file_path):
                            # For directories, check if empty and old
                            if not os.listdir(file_path) and time.time() - os.path.getmtime(file_path) > 600:
                                os.rmdir(file_path)
                                logger.info(f"Cleaned up empty temp directory: {file_path}")
                                
                    except Exception as e:
                        logger.debug(f"Could not clean {file_path}: {e}")
                        
        except Exception as e:
            logger.warning(f"Error during temp file cleanup: {e}")
    
    def check_port_conflicts(self, target_port: int = 5000) -> List[dict]:
        """Check for processes using our target port"""
        port_conflicts = []
        
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    # Get connections using the method, not as an attribute
                    connections = proc.connections()
                    
                    for conn in connections:
                        if hasattr(conn, 'laddr') and conn.laddr and conn.laddr.port == target_port:
                            port_conflicts.append({
                                'pid': proc.info['pid'],
                                'name': proc.info['name'],
                                'port': target_port,
                                'address': f"{conn.laddr.ip}:{conn.laddr.port}"
                            })
                            
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                except AttributeError:
                    # Some processes may not have connections method
                    continue
                    
        except Exception as e:
            logger.warning(f"Error checking port conflicts: {e}")
            
        return port_conflicts
    
    def perform_startup_cleanup(self, force_cleanup: bool = False) -> Dict[str, Any]:
        """Perform comprehensive startup cleanup"""
        cleanup_report: Dict[str, Any] = {
            'stale_processes_found': 0,
            'stale_processes_cleaned': 0,
            'temp_files_cleaned': 0,
            'port_conflicts_found': 0,
            'port_conflicts_resolved': 0,
            'errors': []
        }
        
        try:
            logger.info("🧹 Starting comprehensive startup cleanup...")
            
            # 1. Find stale processes
            stale_processes = self.find_stale_processes()
            cleanup_report['stale_processes_found'] = len(stale_processes)
            
            if stale_processes:
                logger.warning(f"Found {len(stale_processes)} stale bot processes")
                for proc_info in stale_processes:
                    logger.info(f"  - PID {proc_info['pid']}: {proc_info['cmdline']} (age: {proc_info['age_minutes']:.1f}min)")
            
            # 2. Check port conflicts
            port_conflicts = self.check_port_conflicts()
            cleanup_report['port_conflicts_found'] = len(port_conflicts)
            
            if port_conflicts:
                logger.warning(f"Found {len(port_conflicts)} processes using port 5000")
                for conflict in port_conflicts:
                    logger.info(f"  - PID {conflict['pid']} ({conflict['name']}) on {conflict['address']}")
            
            # 3. Clean up temp files (always safe)
            self.cleanup_temp_files()
            
            # 4. If force cleanup or conflicts detected, attempt resolution
            if force_cleanup or port_conflicts or stale_processes:
                logger.info("Attempting process cleanup...")
                
                # Combine stale processes and port conflicts
                processes_to_clean = set()
                
                for proc in stale_processes:
                    processes_to_clean.add(proc['pid'])
                
                for conflict in port_conflicts:
                    processes_to_clean.add(conflict['pid'])
                
                # Attempt to terminate conflicting processes
                for pid in processes_to_clean:
                    try:
                        proc = psutil.Process(pid)
                        proc.terminate()
                        
                        # Wait up to 3 seconds for graceful termination
                        try:
                            proc.wait(timeout=3)
                            cleanup_report['stale_processes_cleaned'] += 1
                            logger.info(f"Successfully terminated process {pid}")
                        except psutil.TimeoutExpired:
                            # Force kill if needed
                            proc.kill()
                            cleanup_report['stale_processes_cleaned'] += 1
                            logger.warning(f"Force-killed process {pid}")
                            
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        logger.debug(f"Could not terminate process {pid}: {e}")
                        cleanup_report['errors'].append(f"Process {pid}: {str(e)}")
            
            logger.info("✅ Startup cleanup completed")
            return cleanup_report
            
        except Exception as e:
            logger.error(f"Error during startup cleanup: {e}")
            cleanup_report['errors'].append(str(e))
            return cleanup_report

# Global instance
startup_cleaner = StartupCleaner()