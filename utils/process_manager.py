#!/usr/bin/env python3
"""
Singleton Process Manager for LockBay Telegram Bot
Prevents duplicate processes and handles graceful shutdowns
"""

import os
import sys
import time
import signal
import socket
import psutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class ProcessManager:
    """Singleton process manager to prevent duplicate bot instances"""
    
    def __init__(self, process_name: str = "lockbay_bot"):
        self.process_name = process_name
        self.pid_file = Path(f"/tmp/{process_name}.pid")
        self.lock_file = Path(f"/tmp/{process_name}.lock")
        self.current_pid = os.getpid()
        self.is_locked = False
        
    def acquire_lock(self) -> bool:
        """Acquire exclusive process lock"""
        try:
            # Check if lock file exists and process is still running
            if self.lock_file.exists():
                try:
                    with open(self.lock_file, 'r') as f:
                        existing_pid = int(f.read().strip())
                    
                    # Check if the process is still running
                    if psutil.pid_exists(existing_pid):
                        logger.warning(f"Another instance is running (PID: {existing_pid})")
                        return False
                    else:
                        logger.info(f"Cleaning up stale lock file for PID {existing_pid}")
                        self.lock_file.unlink()
                except (ValueError, FileNotFoundError):
                    logger.info("Cleaning up corrupted lock file")
                    self.lock_file.unlink(missing_ok=True)
            
            # Create new lock file
            with open(self.lock_file, 'w') as f:
                f.write(str(self.current_pid))
            
            # Create PID file
            with open(self.pid_file, 'w') as f:
                f.write(str(self.current_pid))
            
            self.is_locked = True
            logger.info(f"Process lock acquired for PID {self.current_pid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to acquire process lock: {e}")
            return False
    
    def release_lock(self):
        """Release process lock and cleanup files"""
        try:
            if self.is_locked:
                self.lock_file.unlink(missing_ok=True)
                self.pid_file.unlink(missing_ok=True)
                self.is_locked = False
                logger.info(f"Process lock released for PID {self.current_pid}")
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")
    
    def cleanup_existing_processes(self, port: int = 5000):
        """Cleanup existing bot processes that might be blocking the port"""
        try:
            # Find processes using the target port
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['cmdline'] and any('production_start.py' in cmd for cmd in proc.info['cmdline']):
                        if proc.pid != self.current_pid:
                            logger.info(f"Found existing bot process: PID {proc.pid}")
                            
                            # Check if it's using our port
                            try:
                                connections = proc.connections()
                                using_port = any(conn.laddr.port == port for conn in connections if conn.laddr)
                                
                                if using_port:
                                    logger.warning(f"Terminating process {proc.pid} using port {port}")
                                    proc.terminate()
                                    proc.wait(timeout=5)  # Wait up to 5 seconds
                                    
                            except (psutil.AccessDenied, psutil.TimeoutExpired):
                                logger.warning(f"Could not cleanly terminate process {proc.pid}")
                                try:
                                    proc.kill()
                                except psutil.NoSuchProcess:
                                    pass
                                    
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                    
        except Exception as e:
            logger.error(f"Error during process cleanup: {e}")
    
    def is_port_available(self, port: int, host: str = '0.0.0.0') -> bool:
        """Check if port is available for binding"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                logger.info(f"Port {port} is available")
                return True
        except OSError as e:
            logger.warning(f"Port {port} is not available: {e}")
            return False
    
    def setup_signal_handlers(self):
        """Setup graceful shutdown signal handlers"""
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            logger.info(f"Received {signal_name} signal, shutting down gracefully...")
            
            # Set shutdown flag instead of immediately exiting
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # Schedule graceful shutdown in the event loop
                loop.create_task(self._graceful_shutdown())
            except RuntimeError:
                # No running loop, just release lock and exit
                logger.warning("No async loop running, performing immediate shutdown")
                self.release_lock()
                sys.exit(0)
        
        # Register signal handlers
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        # Handle SIGUSR1 for reload requests
        def reload_handler(signum, frame):
            logger.info("Received reload signal, preparing for restart...")
            self.release_lock()
            # Allow a brief moment for cleanup
            time.sleep(1)
            sys.exit(0)
            
        signal.signal(signal.SIGUSR1, reload_handler)
        logger.info("Signal handlers registered for graceful shutdown")
    
    async def _graceful_shutdown(self):
        """Perform graceful async shutdown"""
        import asyncio
        
        logger.info("🔄 Starting graceful async shutdown...")
        
        try:
            # Get the global application instance if available
            try:
                import main
                application = main.get_application_instance()
                if application:
                    logger.info("📱 Shutting down Telegram Application...")
                    await application.stop()
                    await application.shutdown()
                    logger.info("✅ Telegram Application shut down gracefully")
                else:
                    logger.warning("⚠️ No Telegram Application instance found")
            except Exception as app_error:
                logger.warning(f"⚠️ Error shutting down Telegram Application: {app_error}")
            
            # Cancel any remaining tasks
            tasks = [task for task in asyncio.all_tasks() if not task.done()]
            if tasks:
                logger.info(f"🔄 Cancelling {len(tasks)} remaining tasks...")
                for task in tasks:
                    task.cancel()
                
                # Wait briefly for tasks to cancel
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.0)
                    logger.info("✅ All tasks cancelled gracefully")
                except asyncio.TimeoutError:
                    logger.warning("⚠️ Some tasks did not cancel within timeout")
                except Exception as cancel_error:
                    logger.warning(f"⚠️ Error during task cancellation: {cancel_error}")
            
        except Exception as shutdown_error:
            logger.error(f"❌ Error during graceful shutdown: {shutdown_error}")
        finally:
            # Always release the lock and exit
            logger.info("🔓 Releasing process lock...")
            self.release_lock()
            logger.info("✅ Graceful shutdown complete")
            
            # Exit after a brief delay to ensure logs are written
            import asyncio
            await asyncio.sleep(0.1)
            sys.exit(0)
    
    def ensure_singleton(self, port: int = 5000, max_retries: int = 3) -> bool:
        """Ensure only one instance runs - complete singleton setup"""
        logger.info(f"Ensuring singleton process for {self.process_name}")
        
        # Setup signal handlers first
        self.setup_signal_handlers()
        
        for attempt in range(max_retries):
            try:
                # Try to acquire lock
                if self.acquire_lock():
                    # Check port availability
                    if self.is_port_available(port):
                        logger.info(f"✅ Singleton process established (attempt {attempt + 1})")
                        return True
                    else:
                        # Port is busy, try cleanup
                        logger.info(f"Port {port} busy, attempting cleanup (attempt {attempt + 1})")
                        self.cleanup_existing_processes(port)
                        time.sleep(2)  # Wait for cleanup
                        continue
                else:
                    # Another process is running, try cleanup
                    logger.info(f"Lock unavailable, attempting cleanup (attempt {attempt + 1})")
                    self.cleanup_existing_processes(port)
                    time.sleep(2)  # Wait for cleanup
                    continue
                    
            except Exception as e:
                logger.error(f"Singleton setup attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
        
        logger.error(f"Failed to establish singleton after {max_retries} attempts")
        return False

# Global instance
process_manager = ProcessManager("lockbay_bot")