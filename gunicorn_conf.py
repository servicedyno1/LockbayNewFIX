"""
Gunicorn Configuration for LockBay Telegram Bot
Production-grade worker management with uvicorn workers
"""
import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
backlog = 2048

# Worker processes
workers = 4  # Use 4 workers to handle concurrent requests (reserves 1 CPU for system)
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 10000  # Restart workers after 10k requests to prevent memory leaks
max_requests_jitter = 1000  # Add randomness to prevent all workers restarting at once
timeout = 120  # 2 minutes for long-running webhook operations
keepalive = 30

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "lockbay_telegram_bot"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if needed in future)
# keyfile = None
# certfile = None

# DO NOT preload app - each worker needs its own bot instance with separate event loop
preload_app = False

# Worker lifecycle hooks
def on_starting(server):
    """Called just before the master process is initialized."""
    print("🚀 Gunicorn master process starting...")

def on_reload(server):
    """Called when the server is reloaded."""
    print("🔄 Gunicorn reloading workers...")

def when_ready(server):
    """Called just after the server is started."""
    print(f"✅ Gunicorn ready with {workers} uvicorn workers on {bind}")

def pre_fork(server, worker):
    """Called just before a worker is forked."""
    pass

def post_fork(server, worker):
    """Called just after a worker has been forked."""
    print(f"🔧 Worker {worker.pid} started")

def pre_exec(server):
    """Called just before a new master process is forked."""
    print("🔄 Pre-exec: Forking new master process...")

def worker_int(worker):
    """Called when a worker receives the INT or QUIT signal."""
    print(f"⚠️ Worker {worker.pid} received interrupt signal")

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    print(f"❌ Worker {worker.pid} aborted")

def worker_exit(server, worker):
    """Called just after a worker has been exited."""
    print(f"👋 Worker {worker.pid} exited")
