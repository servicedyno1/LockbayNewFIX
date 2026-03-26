"""
Persistent Logging System
Implements file-based logging with rotation and monitoring capabilities
"""

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

class PersistentLogger:
    """Enhanced logging system with file persistence and monitoring"""
    
    def __init__(self, log_dir: str = "/tmp/bot_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.loggers = {}
        self.setup_main_logger()
    
    def setup_main_logger(self):
        """Setup main application logger with file rotation"""
        main_log_file = self.log_dir / "bot_main.log"
        
        # Create rotating file handler (10MB max, keep 5 files)
        file_handler = logging.handlers.RotatingFileHandler(
            main_log_file,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        
        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Configure root logger - prevent duplicate handlers
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        # Clear existing handlers to prevent duplicates
        if root_logger.handlers:
            root_logger.handlers.clear()
            
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
        logging.info(f"✅ Persistent logging enabled: {main_log_file}")
    
    def create_performance_logger(self):
        """Create dedicated performance metrics logger"""
        perf_log_file = self.log_dir / "performance.log"
        
        perf_logger = logging.getLogger('performance')
        perf_logger.setLevel(logging.INFO)
        
        # Performance log handler
        perf_handler = logging.handlers.RotatingFileHandler(
            perf_log_file,
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3
        )
        
        perf_formatter = logging.Formatter(
            '%(asctime)s - PERF - %(message)s'
        )
        perf_handler.setFormatter(perf_formatter)
        perf_logger.addHandler(perf_handler)
        
        self.loggers['performance'] = perf_logger
        logging.info(f"✅ Performance logging enabled: {perf_log_file}")
    
    def create_error_logger(self):
        """Create dedicated error logger for debugging"""
        error_log_file = self.log_dir / "errors.log"
        
        error_logger = logging.getLogger('errors')
        error_logger.setLevel(logging.ERROR)
        
        error_handler = logging.handlers.RotatingFileHandler(
            error_log_file,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        
        error_formatter = logging.Formatter(
            '%(asctime)s - ERROR - %(name)s - %(levelname)s - %(message)s\n'
            'File: %(pathname)s:%(lineno)d\n'
            'Function: %(funcName)s\n'
            '%(exc_text)s\n' + '-'*80 + '\n'
        )
        error_handler.setFormatter(error_formatter)
        error_logger.addHandler(error_handler)
        
        self.loggers['errors'] = error_logger
        logging.info(f"✅ Error logging enabled: {error_log_file}")
    
    def log_performance_metric(self, metric_name: str, value: float, unit: str = ""):
        """Log performance metrics"""
        if 'performance' not in self.loggers:
            self.create_performance_logger()
        
        timestamp = datetime.utcnow().isoformat()
        metric_msg = f"{metric_name}: {value:.3f}{unit} at {timestamp}"
        
        self.loggers['performance'].info(metric_msg)
    
    def log_startup_metrics(self, metrics: Dict[str, Any]):
        """Log startup performance metrics"""
        if 'performance' not in self.loggers:
            self.create_performance_logger()
        
        perf_logger = self.loggers['performance']
        perf_logger.info("=== STARTUP METRICS ===")
        
        for metric_name, value in metrics.items():
            if isinstance(value, (int, float)):
                perf_logger.info(f"{metric_name}: {value:.3f}")
            else:
                perf_logger.info(f"{metric_name}: {value}")
        
        perf_logger.info("=====================")
    
    def get_log_stats(self) -> Dict[str, Any]:
        """Get logging statistics"""
        stats = {
            'log_directory': str(self.log_dir),
            'log_files': [],
            'total_size_mb': 0
        }
        
        if self.log_dir.exists():
            for log_file in self.log_dir.glob("*.log*"):
                size_mb = log_file.stat().st_size / (1024 * 1024)
                stats['log_files'].append({
                    'name': log_file.name,
                    'size_mb': round(size_mb, 2),
                    'modified': datetime.fromtimestamp(log_file.stat().st_mtime).isoformat()
                })
                stats['total_size_mb'] += size_mb
        
        stats['total_size_mb'] = round(stats['total_size_mb'], 2)
        return stats

# Global logger instance
_persistent_logger = None

def get_persistent_logger() -> PersistentLogger:
    """Get global persistent logger instance"""
    global _persistent_logger
    if _persistent_logger is None:
        _persistent_logger = PersistentLogger()
        _persistent_logger.create_performance_logger()
        _persistent_logger.create_error_logger()
    return _persistent_logger

def log_performance(metric_name: str, value: float, unit: str = ""):
    """Quick function to log performance metrics"""
    logger = get_persistent_logger()
    logger.log_performance_metric(metric_name, value, unit)

def setup_persistent_logging():
    """Setup persistent logging system"""
    logger = get_persistent_logger()
    logging.info("🗄️ Persistent logging system initialized")
    return logger