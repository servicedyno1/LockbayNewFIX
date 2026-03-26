"""
Connection Pool Monitoring Dashboard
Real-time web dashboard for monitoring database connection pool performance
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any
from dataclasses import dataclass
from collections import deque
import threading
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

logger = logging.getLogger(__name__)


@dataclass
class DashboardMetrics:
    """Dashboard metrics snapshot"""
    timestamp: datetime
    pool_stats: Dict[str, Any]
    performance_metrics: Dict[str, Any]
    ssl_health: Dict[str, Any]
    scaling_events: List[Dict[str, Any]]
    alerts: List[Dict[str, Any]]


class ConnectionPoolDashboard:
    """Real-time connection pool monitoring dashboard"""
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.app = FastAPI(title="Connection Pool Monitor", version="1.0.0")
        
        # WebSocket connections for real-time updates
        self.websocket_connections = set()
        self.dashboard_data = deque(maxlen=1000)
        self.alert_history = deque(maxlen=100)
        
        # Dashboard configuration
        self.config = {
            'update_interval': 5,  # seconds
            'retention_minutes': 60,
            'alert_thresholds': {
                'acquisition_time_warning': 100,  # ms
                'acquisition_time_critical': 500,  # ms
                'utilization_warning': 80,  # %
                'utilization_critical': 95,  # %
                'error_rate_warning': 5,  # %
                'error_rate_critical': 15,  # %
            }
        }
        
        # Dashboard state
        self._running = True
        self._last_update = datetime.utcnow()
        self._metrics_lock = threading.Lock()
        
        # Setup routes
        self._setup_routes()
        
        # Start background tasks
        asyncio.create_task(self._dashboard_update_loop())
        asyncio.create_task(self._websocket_broadcast_loop())
        
        logger.info(f"📊 Connection Pool Dashboard initialized on port {port}")
    
    def _setup_routes(self):
        """Setup FastAPI routes"""
        
        @self.app.get("/", response_class=HTMLResponse)
        async def dashboard_home():
            return self._get_dashboard_html()
        
        @self.app.get("/api/metrics")
        async def get_metrics():
            return self._get_current_metrics()
        
        @self.app.get("/api/history")
        async def get_history(minutes: int = 30):
            return self._get_metrics_history(minutes)
        
        @self.app.get("/api/pool-stats")
        async def get_pool_stats():
            return self._get_pool_statistics()
        
        @self.app.get("/api/ssl-health")
        async def get_ssl_health():
            return self._get_ssl_health_info()
        
        @self.app.get("/api/scaling-events")
        async def get_scaling_events():
            return self._get_scaling_events()
        
        @self.app.get("/api/alerts")
        async def get_alerts():
            return list(self.alert_history)
        
        @self.app.get("/api/export/{format}")
        async def export_data(format: str, hours: int = 1):
            if format.lower() == 'json':
                return self._export_dashboard_data(hours)
            else:
                return {"error": "Unsupported format"}
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self._handle_websocket(websocket)
        
        @self.app.get("/health")
        async def health_check():
            return {
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "connected_clients": len(self.websocket_connections),
                "last_update": self._last_update.isoformat()
            }
    
    async def _handle_websocket(self, websocket: WebSocket):
        """Handle WebSocket connections for real-time updates"""
        await websocket.accept()
        self.websocket_connections.add(websocket)
        
        try:
            # Send initial data
            initial_data = self._get_current_metrics()
            await websocket.send_json({
                "type": "initial_data",
                "data": initial_data
            })
            
            # Keep connection alive
            while True:
                try:
                    # Wait for client messages (ping/pong, preferences, etc.)
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                    
                    if message.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif message.get("type") == "get_metrics":
                        current_data = self._get_current_metrics()
                        await websocket.send_json({
                            "type": "metrics_update",
                            "data": current_data
                        })
                        
                except asyncio.TimeoutError:
                    # Send ping to check if client is still connected
                    await websocket.send_json({"type": "ping"})
                    
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self.websocket_connections.discard(websocket)
    
    async def _dashboard_update_loop(self):
        """Main dashboard data update loop"""
        logger.info("📊 Starting dashboard update loop...")
        
        while self._running:
            try:
                await asyncio.sleep(self.config['update_interval'])
                
                # Collect current metrics
                current_metrics = await self._collect_dashboard_metrics()
                
                # Store in dashboard data
                with self._metrics_lock:
                    self.dashboard_data.append(current_metrics)
                    
                    # Check for alerts
                    alerts = self._check_for_alerts(current_metrics)
                    for alert in alerts:
                        self.alert_history.append(alert)
                
                self._last_update = datetime.utcnow()
                
                logger.debug(f"📊 Dashboard updated with {len(alerts)} new alerts")
                
            except Exception as e:
                logger.error(f"Error in dashboard update loop: {e}")
                await asyncio.sleep(30)
    
    async def _websocket_broadcast_loop(self):
        """Broadcast updates to connected WebSocket clients"""
        logger.info("📡 Starting WebSocket broadcast loop...")
        
        while self._running:
            try:
                await asyncio.sleep(self.config['update_interval'])
                
                if self.websocket_connections and self.dashboard_data:
                    # Get latest data
                    latest_data = self._get_current_metrics()
                    
                    # Broadcast to all connected clients
                    message = {
                        "type": "metrics_update",
                        "timestamp": datetime.utcnow().isoformat(),
                        "data": latest_data
                    }
                    
                    # Send to all connected WebSocket clients
                    disconnected = set()
                    for websocket in self.websocket_connections:
                        try:
                            await websocket.send_json(message)
                        except Exception as e:
                            logger.debug(f"WebSocket send failed: {e}")
                            disconnected.add(websocket)
                    
                    # Remove disconnected clients
                    for websocket in disconnected:
                        self.websocket_connections.discard(websocket)
                
            except Exception as e:
                logger.error(f"Error in WebSocket broadcast loop: {e}")
                await asyncio.sleep(30)
    
    async def _collect_dashboard_metrics(self) -> DashboardMetrics:
        """Collect all metrics for the dashboard"""
        try:
            # Get pool statistics
            pool_stats = self._get_pool_statistics()
            
            # Get performance metrics
            performance_metrics = self._get_performance_metrics()
            
            # Get SSL health
            ssl_health = self._get_ssl_health_info()
            
            # Get scaling events
            scaling_events = self._get_scaling_events()[-10:]  # Last 10 events
            
            # Get recent alerts
            alerts = list(self.alert_history)[-5:]  # Last 5 alerts
            
            return DashboardMetrics(
                timestamp=datetime.utcnow(),
                pool_stats=pool_stats,
                performance_metrics=performance_metrics,
                ssl_health=ssl_health,
                scaling_events=scaling_events,
                alerts=alerts
            )
            
        except Exception as e:
            logger.error(f"Error collecting dashboard metrics: {e}")
            return DashboardMetrics(
                timestamp=datetime.utcnow(),
                pool_stats={},
                performance_metrics={},
                ssl_health={},
                scaling_events=[],
                alerts=[]
            )
    
    def _check_for_alerts(self, metrics: DashboardMetrics) -> List[Dict[str, Any]]:
        """Check for alert conditions"""
        alerts = []
        now = datetime.utcnow()
        
        try:
            pool_stats = metrics.pool_stats
            performance_metrics = metrics.performance_metrics
            
            # Check acquisition time
            avg_acquisition_time = pool_stats.get('avg_acquisition_time_ms', 0)
            if avg_acquisition_time >= self.config['alert_thresholds']['acquisition_time_critical']:
                alerts.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'severity': 'critical',
                    'type': 'acquisition_time',
                    'message': f"Critical connection acquisition time: {avg_acquisition_time:.1f}ms",
                    'metric_value': avg_acquisition_time,
                    'threshold': self.config['alert_thresholds']['acquisition_time_critical']
                })
            elif avg_acquisition_time >= self.config['alert_thresholds']['acquisition_time_warning']:
                alerts.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'severity': 'warning',
                    'type': 'acquisition_time',
                    'message': f"High connection acquisition time: {avg_acquisition_time:.1f}ms",
                    'metric_value': avg_acquisition_time,
                    'threshold': self.config['alert_thresholds']['acquisition_time_warning']
                })
            
            # Check utilization
            current_utilization = pool_stats.get('current_utilization', 0)
            if current_utilization >= self.config['alert_thresholds']['utilization_critical']:
                alerts.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'severity': 'critical',
                    'type': 'utilization',
                    'message': f"Critical pool utilization: {current_utilization:.1f}%",
                    'metric_value': current_utilization,
                    'threshold': self.config['alert_thresholds']['utilization_critical']
                })
            elif current_utilization >= self.config['alert_thresholds']['utilization_warning']:
                alerts.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'severity': 'warning',
                    'type': 'utilization',
                    'message': f"High pool utilization: {current_utilization:.1f}%",
                    'metric_value': current_utilization,
                    'threshold': self.config['alert_thresholds']['utilization_warning']
                })
            
            # Check error rate
            error_rate = performance_metrics.get('current_error_rate', 0) * 100
            if error_rate >= self.config['alert_thresholds']['error_rate_critical']:
                alerts.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'severity': 'critical',
                    'type': 'error_rate',
                    'message': f"Critical error rate: {error_rate:.1f}%",
                    'metric_value': error_rate,
                    'threshold': self.config['alert_thresholds']['error_rate_critical']
                })
            elif error_rate >= self.config['alert_thresholds']['error_rate_warning']:
                alerts.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'severity': 'warning',
                    'type': 'error_rate',
                    'message': f"High error rate: {error_rate:.1f}%",
                    'metric_value': error_rate,
                    'threshold': self.config['alert_thresholds']['error_rate_warning']
                })
                
        except Exception as e:
            logger.error(f"Error checking for alerts: {e}")
        
        return alerts
    
    def _get_pool_statistics(self) -> Dict[str, Any]:
        """Get current pool statistics from all available pools"""
        stats = {
            'timestamp': datetime.utcnow().isoformat(),
            'pools': {}
        }
        
        # Try to get dynamic pool stats
        try:
            from utils.dynamic_database_pool_manager import get_dynamic_pool_stats
            dynamic_stats = get_dynamic_pool_stats()
            stats['pools']['dynamic'] = dynamic_stats
            
            # Use dynamic pool as primary source for summary stats
            stats.update({
                'pool_size': dynamic_stats.get('current_pool_size', 0),
                'pool_checked_out': dynamic_stats.get('pool_checked_out', 0),
                'pool_overflow': dynamic_stats.get('pool_overflow', 0),
                'warmed_sessions': dynamic_stats.get('warmed_sessions', 0),
                'current_utilization': dynamic_stats.get('current_utilization', 0),
                'avg_acquisition_time_ms': dynamic_stats.get('avg_acquisition_time_ms', 0),
                'workload_pattern': dynamic_stats.get('workload_pattern', 'unknown'),
                'scaling_events': dynamic_stats.get('scaling_events', 0),
                'last_scaling': dynamic_stats.get('last_scaling')
            })
            
        except ImportError:
            logger.debug("Dynamic pool not available")
        
        # Try to get standard pool stats
        try:
            from utils.database_pool_manager import database_pool
            standard_stats = database_pool.get_pool_statistics()
            stats['pools']['standard'] = standard_stats
            
            # If no dynamic pool, use standard pool for summary
            if 'dynamic' not in stats['pools']:
                stats.update({
                    'pool_size': standard_stats.get('pool_size', 0),
                    'pool_checked_out': standard_stats.get('pool_checked_out', 0),
                    'pool_overflow': standard_stats.get('pool_overflow', 0),
                    'warmed_sessions': standard_stats.get('warmed_sessions', 0),
                    'avg_acquisition_time_ms': standard_stats.get('avg_connection_time', 0) * 1000
                })
                
        except (ImportError, AttributeError):
            logger.debug("Standard pool not available")
        
        return stats
    
    def _get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        try:
            from utils.connection_pool_performance_metrics import get_real_time_performance_metrics
            return get_real_time_performance_metrics()
        except ImportError:
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'current_performance': {},
                'error': 'Performance metrics not available'
            }
    
    def _get_ssl_health_info(self) -> Dict[str, Any]:
        """Get SSL health information"""
        try:
            from utils.ssl_connection_monitor import get_ssl_health_summary
            return get_ssl_health_summary()
        except ImportError:
            try:
                from utils.proactive_ssl_health_manager import get_ssl_health_report
                return get_ssl_health_report()
            except ImportError:
                return {
                    'timestamp': datetime.utcnow().isoformat(),
                    'overall_health': 'unknown',
                    'error': 'SSL health monitoring not available'
                }
    
    def _get_scaling_events(self) -> List[Dict[str, Any]]:
        """Get scaling events history"""
        try:
            from utils.dynamic_database_pool_manager import get_scaling_history
            return get_scaling_history()
        except ImportError:
            return []
    
    def _get_current_metrics(self) -> Dict[str, Any]:
        """Get current dashboard metrics"""
        with self._metrics_lock:
            if not self.dashboard_data:
                return {
                    'timestamp': datetime.utcnow().isoformat(),
                    'error': 'No data available'
                }
            
            latest = self.dashboard_data[-1]
            return {
                'timestamp': latest.timestamp.isoformat(),
                'pool_stats': latest.pool_stats,
                'performance_metrics': latest.performance_metrics,
                'ssl_health': latest.ssl_health,
                'scaling_events': latest.scaling_events,
                'alerts': latest.alerts,
                'connected_clients': len(self.websocket_connections)
            }
    
    def _get_metrics_history(self, minutes: int) -> List[Dict[str, Any]]:
        """Get metrics history for specified time period"""
        cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)
        
        with self._metrics_lock:
            history = [
                {
                    'timestamp': metrics.timestamp.isoformat(),
                    'pool_stats': metrics.pool_stats,
                    'performance_metrics': metrics.performance_metrics,
                    'ssl_health': metrics.ssl_health
                }
                for metrics in self.dashboard_data
                if metrics.timestamp >= cutoff_time
            ]
        
        return history
    
    def _export_dashboard_data(self, hours: int) -> Dict[str, Any]:
        """Export dashboard data"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        with self._metrics_lock:
            export_data = {
                'export_timestamp': datetime.utcnow().isoformat(),
                'period_hours': hours,
                'total_data_points': 0,
                'metrics_history': [],
                'alert_history': [],
                'configuration': self.config
            }
            
            # Export metrics history
            for metrics in self.dashboard_data:
                if metrics.timestamp >= cutoff_time:
                    export_data['metrics_history'].append({
                        'timestamp': metrics.timestamp.isoformat(),
                        'pool_stats': metrics.pool_stats,
                        'performance_metrics': metrics.performance_metrics,
                        'ssl_health': metrics.ssl_health,
                        'scaling_events': metrics.scaling_events
                    })
            
            # Export alert history
            for alert in self.alert_history:
                if alert['timestamp'] >= cutoff_time:
                    export_data['alert_history'].append({
                        **alert,
                        'timestamp': alert['timestamp'].isoformat()
                    })
            
            export_data['total_data_points'] = len(export_data['metrics_history'])
        
        return export_data
    
    def _get_dashboard_html(self) -> str:
        """Generate the dashboard HTML"""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Connection Pool Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f7; color: #1d1d1f; line-height: 1.4;
        }
        .header { 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; padding: 1.5rem; text-align: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .header h1 { font-size: 2rem; font-weight: 600; margin-bottom: 0.5rem; }
        .status-indicator { 
            display: inline-block; width: 12px; height: 12px; border-radius: 50%;
            margin-left: 10px; animation: pulse 2s infinite;
        }
        .status-healthy { background: #30d158; }
        .status-warning { background: #ff9f0a; }
        .status-critical { background: #ff453a; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        
        .container { max-width: 1200px; margin: 0 auto; padding: 2rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem; }
        .card { 
            background: white; border-radius: 12px; padding: 1.5rem; box-shadow: 0 4px 20px rgba(0,0,0,0.1);
            border: 1px solid #e5e5e7; transition: transform 0.2s ease;
        }
        .card:hover { transform: translateY(-2px); }
        .card h3 { color: #1d1d1f; font-size: 1.1rem; font-weight: 600; margin-bottom: 1rem; }
        
        .metric { display: flex; justify-content: space-between; align-items: center; margin: 0.75rem 0; }
        .metric-label { color: #86868b; font-size: 0.9rem; }
        .metric-value { 
            font-weight: 600; font-size: 1.1rem; padding: 0.25rem 0.75rem; border-radius: 6px;
        }
        .metric-excellent { background: #d1f2eb; color: #0d9488; }
        .metric-good { background: #dbeafe; color: #1d4ed8; }
        .metric-warning { background: #fef3c7; color: #d97706; }
        .metric-critical { background: #fee2e2; color: #dc2626; }
        
        .chart-container { position: relative; height: 300px; margin: 1rem 0; }
        
        .alerts { margin-top: 1rem; }
        .alert { 
            padding: 0.75rem; margin: 0.5rem 0; border-radius: 8px; border-left: 4px solid;
            font-size: 0.9rem; display: flex; justify-content: space-between; align-items: center;
        }
        .alert-warning { background: #fef3c7; border-color: #f59e0b; color: #92400e; }
        .alert-critical { background: #fee2e2; border-color: #ef4444; color: #991b1b; }
        .alert-time { font-size: 0.8rem; opacity: 0.7; }
        
        .connection-status { color: #30d158; font-weight: 600; }
        .footer { text-align: center; margin-top: 2rem; color: #86868b; font-size: 0.9rem; }
        
        .loading { text-align: center; padding: 2rem; color: #86868b; }
        .error { color: #dc2626; text-align: center; padding: 2rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Connection Pool Monitor <span id="status-indicator" class="status-indicator status-healthy"></span></h1>
        <p>Real-time database connection pool performance monitoring</p>
        <div class="connection-status" id="connection-status">Connected</div>
    </div>
    
    <div class="container">
        <div id="loading" class="loading">Loading dashboard data...</div>
        <div id="error" class="error" style="display: none;"></div>
        
        <div id="dashboard" style="display: none;">
            <div class="grid">
                <!-- Pool Statistics Card -->
                <div class="card">
                    <h3>📊 Pool Statistics</h3>
                    <div class="metric">
                        <span class="metric-label">Pool Size</span>
                        <span class="metric-value metric-good" id="pool-size">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Active Connections</span>
                        <span class="metric-value metric-excellent" id="active-connections">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Utilization</span>
                        <span class="metric-value metric-excellent" id="utilization">0%</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Warmed Sessions</span>
                        <span class="metric-value metric-good" id="warmed-sessions">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Workload Pattern</span>
                        <span class="metric-value metric-excellent" id="workload-pattern">unknown</span>
                    </div>
                </div>
                
                <!-- Performance Metrics Card -->
                <div class="card">
                    <h3>⚡ Performance Metrics</h3>
                    <div class="metric">
                        <span class="metric-label">Avg Acquisition Time</span>
                        <span class="metric-value metric-excellent" id="avg-acquisition-time">0ms</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Total Acquisitions</span>
                        <span class="metric-value metric-good" id="total-acquisitions">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Error Rate</span>
                        <span class="metric-value metric-excellent" id="error-rate">0%</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Memory Usage</span>
                        <span class="metric-value metric-excellent" id="memory-usage">0MB</span>
                    </div>
                </div>
                
                <!-- SSL Health Card -->
                <div class="card">
                    <h3>🔐 SSL Health</h3>
                    <div class="metric">
                        <span class="metric-label">Overall Health</span>
                        <span class="metric-value metric-excellent" id="ssl-health-status">healthy</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Errors (1h)</span>
                        <span class="metric-value metric-excellent" id="ssl-errors">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Recoveries (1h)</span>
                        <span class="metric-value metric-good" id="ssl-recoveries">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Error Rate</span>
                        <span class="metric-value metric-excellent" id="ssl-error-rate">0%</span>
                    </div>
                </div>
                
                <!-- Charts Card -->
                <div class="card" style="grid-column: 1 / -1;">
                    <h3>📈 Performance Trends</h3>
                    <div class="chart-container">
                        <canvas id="performance-chart"></canvas>
                    </div>
                </div>
                
                <!-- Alerts Card -->
                <div class="card" style="grid-column: 1 / -1;">
                    <h3>🚨 Active Alerts</h3>
                    <div id="alerts-container">
                        <p style="color: #30d158; text-align: center; padding: 1rem;">No active alerts - system healthy!</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>Last updated: <span id="last-update">Never</span> | Connected clients: <span id="connected-clients">0</span></p>
    </div>

    <script>
        class ConnectionPoolDashboard {
            constructor() {
                this.ws = null;
                this.chart = null;
                this.metricsHistory = [];
                this.reconnectAttempts = 0;
                this.maxReconnectAttempts = 5;
                this.init();
            }
            
            init() {
                this.connectWebSocket();
                this.initChart();
            }
            
            connectWebSocket() {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${protocol}//${window.location.host}/ws`;
                
                try {
                    this.ws = new WebSocket(wsUrl);
                    
                    this.ws.onopen = () => {
                        console.log('WebSocket connected');
                        this.updateConnectionStatus('Connected', 'healthy');
                        this.reconnectAttempts = 0;
                    };
                    
                    this.ws.onmessage = (event) => {
                        const message = JSON.parse(event.data);
                        this.handleWebSocketMessage(message);
                    };
                    
                    this.ws.onclose = () => {
                        console.log('WebSocket disconnected');
                        this.updateConnectionStatus('Disconnected', 'critical');
                        this.scheduleReconnect();
                    };
                    
                    this.ws.onerror = (error) => {
                        console.error('WebSocket error:', error);
                        this.updateConnectionStatus('Error', 'critical');
                    };
                    
                } catch (error) {
                    console.error('Failed to connect WebSocket:', error);
                    this.scheduleReconnect();
                }
            }
            
            handleWebSocketMessage(message) {
                if (message.type === 'initial_data' || message.type === 'metrics_update') {
                    this.updateDashboard(message.data);
                } else if (message.type === 'ping') {
                    this.ws.send(JSON.stringify({ type: 'pong' }));
                }
            }
            
            scheduleReconnect() {
                if (this.reconnectAttempts < this.maxReconnectAttempts) {
                    const delay = Math.pow(2, this.reconnectAttempts) * 1000;
                    this.reconnectAttempts++;
                    
                    setTimeout(() => {
                        console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
                        this.connectWebSocket();
                    }, delay);
                } else {
                    this.showError('Connection failed after multiple attempts. Please refresh the page.');
                }
            }
            
            updateConnectionStatus(status, severity) {
                const statusElement = document.getElementById('connection-status');
                const indicatorElement = document.getElementById('status-indicator');
                
                statusElement.textContent = status;
                indicatorElement.className = `status-indicator status-${severity}`;
            }
            
            updateDashboard(data) {
                this.hideLoading();
                this.hideError();
                this.showDashboard();
                
                // Update pool statistics
                this.updatePoolStats(data.pool_stats || {});
                
                // Update performance metrics
                this.updatePerformanceMetrics(data.performance_metrics || {});
                
                // Update SSL health
                this.updateSSLHealth(data.ssl_health || {});
                
                // Update alerts
                this.updateAlerts(data.alerts || []);
                
                // Update footer info
                document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
                document.getElementById('connected-clients').textContent = data.connected_clients || 0;
                
                // Store for chart
                this.metricsHistory.push({
                    timestamp: new Date(),
                    ...data
                });
                
                // Keep only last 20 data points for chart
                if (this.metricsHistory.length > 20) {
                    this.metricsHistory.shift();
                }
                
                this.updateChart();
            }
            
            updatePoolStats(stats) {
                document.getElementById('pool-size').textContent = stats.pool_size || 0;
                document.getElementById('active-connections').textContent = stats.pool_checked_out || 0;
                document.getElementById('warmed-sessions').textContent = stats.warmed_sessions || 0;
                document.getElementById('workload-pattern').textContent = stats.workload_pattern || 'unknown';
                
                const utilization = stats.current_utilization || 0;
                const utilizationEl = document.getElementById('utilization');
                utilizationEl.textContent = `${utilization.toFixed(1)}%`;
                utilizationEl.className = `metric-value ${this.getUtilizationClass(utilization)}`;
            }
            
            updatePerformanceMetrics(metrics) {
                const current = metrics.current_performance || {};
                
                const avgTime = current.avg_latency_ms || stats.avg_acquisition_time_ms || 0;
                const avgTimeEl = document.getElementById('avg-acquisition-time');
                avgTimeEl.textContent = `${avgTime.toFixed(1)}ms`;
                avgTimeEl.className = `metric-value ${this.getLatencyClass(avgTime)}`;
                
                document.getElementById('total-acquisitions').textContent = 
                    metrics.total_metrics_collected || current.total_acquisitions || 0;
                
                const errorRate = (current.error_count_last_minute || 0) / Math.max(metrics.recent_metrics_count || 1, 1) * 100;
                const errorRateEl = document.getElementById('error-rate');
                errorRateEl.textContent = `${errorRate.toFixed(1)}%`;
                errorRateEl.className = `metric-value ${this.getErrorRateClass(errorRate)}`;
                
                const memoryUsage = current.memory_usage_mb || 0;
                document.getElementById('memory-usage').textContent = `${memoryUsage.toFixed(1)}MB`;
            }
            
            updateSSLHealth(health) {
                const overallHealth = health.overall_health || 'unknown';
                const healthEl = document.getElementById('ssl-health-status');
                healthEl.textContent = overallHealth;
                healthEl.className = `metric-value ${this.getSSLHealthClass(overallHealth)}`;
                
                const metrics = health.metrics || {};
                document.getElementById('ssl-errors').textContent = metrics.errors_last_hour || 0;
                document.getElementById('ssl-recoveries').textContent = metrics.recoveries_last_hour || 0;
                
                const sslErrorRate = metrics.error_rate_percentage || 0;
                const sslErrorRateEl = document.getElementById('ssl-error-rate');
                sslErrorRateEl.textContent = `${sslErrorRate.toFixed(1)}%`;
                sslErrorRateEl.className = `metric-value ${this.getErrorRateClass(sslErrorRate)}`;
            }
            
            updateAlerts(alerts) {
                const container = document.getElementById('alerts-container');
                
                if (!alerts || alerts.length === 0) {
                    container.innerHTML = '<p style="color: #30d158; text-align: center; padding: 1rem;">No active alerts - system healthy!</p>';
                    return;
                }
                
                container.innerHTML = alerts.map(alert => `
                    <div class="alert alert-${alert.severity}">
                        <span>${alert.message}</span>
                        <span class="alert-time">${new Date(alert.timestamp).toLocaleTimeString()}</span>
                    </div>
                `).join('');
            }
            
            initChart() {
                const ctx = document.getElementById('performance-chart').getContext('2d');
                this.chart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: [],
                        datasets: [
                            {
                                label: 'Acquisition Time (ms)',
                                data: [],
                                borderColor: '#667eea',
                                backgroundColor: 'rgba(102, 126, 234, 0.1)',
                                tension: 0.3
                            },
                            {
                                label: 'Utilization (%)',
                                data: [],
                                borderColor: '#764ba2',
                                backgroundColor: 'rgba(118, 75, 162, 0.1)',
                                tension: 0.3
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: { beginAtZero: true }
                        },
                        plugins: {
                            legend: { position: 'top' }
                        }
                    }
                });
            }
            
            updateChart() {
                if (!this.chart || this.metricsHistory.length === 0) return;
                
                const labels = this.metricsHistory.map(h => h.timestamp.toLocaleTimeString());
                const acquisitionTimes = this.metricsHistory.map(h => {
                    const stats = h.pool_stats || {};
                    const perf = h.performance_metrics?.current_performance || {};
                    return perf.avg_latency_ms || stats.avg_acquisition_time_ms || 0;
                });
                const utilizations = this.metricsHistory.map(h => (h.pool_stats || {}).current_utilization || 0);
                
                this.chart.data.labels = labels;
                this.chart.data.datasets[0].data = acquisitionTimes;
                this.chart.data.datasets[1].data = utilizations;
                this.chart.update();
            }
            
            getLatencyClass(latency) {
                if (latency < 50) return 'metric-excellent';
                if (latency < 100) return 'metric-good';
                if (latency < 200) return 'metric-warning';
                return 'metric-critical';
            }
            
            getUtilizationClass(utilization) {
                if (utilization < 60) return 'metric-excellent';
                if (utilization < 80) return 'metric-good';
                if (utilization < 95) return 'metric-warning';
                return 'metric-critical';
            }
            
            getErrorRateClass(errorRate) {
                if (errorRate < 1) return 'metric-excellent';
                if (errorRate < 5) return 'metric-good';
                if (errorRate < 10) return 'metric-warning';
                return 'metric-critical';
            }
            
            getSSLHealthClass(health) {
                if (health === 'healthy' || health === 'excellent') return 'metric-excellent';
                if (health === 'good') return 'metric-good';
                if (health === 'warning') return 'metric-warning';
                return 'metric-critical';
            }
            
            showDashboard() {
                document.getElementById('dashboard').style.display = 'block';
            }
            
            hideLoading() {
                document.getElementById('loading').style.display = 'none';
            }
            
            showError(message) {
                const errorEl = document.getElementById('error');
                errorEl.textContent = message;
                errorEl.style.display = 'block';
            }
            
            hideError() {
                document.getElementById('error').style.display = 'none';
            }
        }
        
        // Initialize dashboard when page loads
        document.addEventListener('DOMContentLoaded', () => {
            new ConnectionPoolDashboard();
        });
    </script>
</body>
</html>
        """
    
    async def start_server(self):
        """Start the dashboard server"""
        try:
            config = uvicorn.Config(
                app=self.app,
                host="0.0.0.0",
                port=self.port,
                log_level="info"
            )
            server = uvicorn.Server(config)
            
            logger.info(f"🚀 Connection Pool Dashboard starting on port {self.port}")
            await server.serve()
            
        except Exception as e:
            logger.error(f"Error starting dashboard server: {e}")
    
    def shutdown(self):
        """Shutdown the dashboard"""
        logger.info("📊 Shutting down Connection Pool Dashboard...")
        self._running = False
        
        # Close all WebSocket connections
        for websocket in list(self.websocket_connections):
            try:
                asyncio.create_task(websocket.close())
            except Exception as e:
                logger.debug(f"Could not close websocket connection: {e}")
                pass
        
        self.websocket_connections.clear()


# Global dashboard instance
dashboard_instance = None


def start_dashboard(port: int = 8080):
    """Start the connection pool dashboard"""
    global dashboard_instance
    
    if dashboard_instance is None:
        dashboard_instance = ConnectionPoolDashboard(port)
    
    return dashboard_instance


def get_dashboard_metrics() -> Dict[str, Any]:
    """Get current dashboard metrics"""
    if dashboard_instance:
        return dashboard_instance._get_current_metrics()
    return {"error": "Dashboard not initialized"}


def export_dashboard_data(hours: int = 1) -> Dict[str, Any]:
    """Export dashboard data"""
    if dashboard_instance:
        return dashboard_instance._export_dashboard_data(hours)
    return {"error": "Dashboard not initialized"}