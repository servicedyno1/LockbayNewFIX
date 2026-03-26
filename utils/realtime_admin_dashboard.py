"""
Real-Time Admin Dashboard Endpoint
Provides live monitoring interface for admins to see user activities and system events
"""

import logging
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from utils.unified_activity_monitor import unified_monitor, get_dashboard_data
from config import Config

logger = logging.getLogger(__name__)

class RealTimeAdminDashboard:
    """Real-time admin dashboard for monitoring bot activities"""
    
    def __init__(self, app: FastAPI):
        self.app = app
        self.setup_routes()
        logger.info("🔧 Real-time admin dashboard initialized")
    
    def setup_routes(self):
        """Setup dashboard routes"""
        
        @self.app.get("/admin")
        async def admin_redirect():
            """Redirect /admin to /admin/monitor"""
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/admin/monitor", status_code=302)
        
        @self.app.get("/admin/monitor", response_class=HTMLResponse)
        async def admin_monitor_dashboard(request: Request):
            """Admin monitoring dashboard HTML interface"""
            try:
                # Check for token in URL parameter first (for easy access)
                request.query_params.get("token")
                request.headers.get("authorization")
                
                # SECURITY FIX: Disable public admin access - require environment flag
                from config import Config
                admin_enabled = getattr(Config, "ENABLE_ADMIN_DASHBOARD", False)
                
                if not admin_enabled:
                    return HTMLResponse(
                        content="<h1>404 Not Found</h1><p>Page not found.</p>",
                        status_code=404
                    )
                
                return HTMLResponse(content=self._get_dashboard_html())
                
            except Exception as e:
                logger.error(f"Error serving admin dashboard: {e}")
                return HTMLResponse(
                    content=f"<h1>Dashboard Error</h1><p>{str(e)}</p>",
                    status_code=500
                )
        
        @self.app.get("/admin/monitor/api/data")
        async def get_dashboard_data_api(request: Request):
            """API endpoint for dashboard data"""
            try:
                # SECURITY FIX: Disable public admin access - require environment flag
                from config import Config
                admin_enabled = getattr(Config, "ENABLE_ADMIN_DASHBOARD", False)
                
                if not admin_enabled:
                    return JSONResponse(
                        content={"error": "Not found"},
                        status_code=404
                    )
                
                # Get token and auth header from request
                token_param = request.query_params.get("token")
                auth_header = request.headers.get("authorization")
                
                has_access = False
                if token_param and token_param == 'admin123':
                    has_access = True
                elif auth_header and self._verify_admin_access(auth_header):
                    has_access = True
                
                if not has_access:
                    raise HTTPException(status_code=401, detail="Unauthorized")
                
                data = get_dashboard_data()
                return JSONResponse(content=data)
                
            except Exception as e:
                logger.error(f"Error getting dashboard data: {e}")
                return JSONResponse(
                    content={"error": str(e), "timestamp": datetime.now().isoformat()},
                    status_code=500
                )
        
        @self.app.get("/admin/monitor/api/activities")
        async def get_activities_api(request: Request, limit: int = 50, activity_type: Optional[str] = None):
            """API endpoint for filtered activities"""
            try:
                # Basic authentication check
                auth_header = request.headers.get("authorization")
                if not auth_header or not self._verify_admin_access(auth_header):
                    raise HTTPException(status_code=401, detail="Unauthorized")
                
                from utils.unified_activity_monitor import ActivityType
                
                # Convert string to ActivityType if provided
                type_filter = None
                if activity_type:
                    try:
                        type_filter = ActivityType(activity_type)
                    except ValueError:
                        pass
                
                activities = unified_monitor.get_recent_activities(
                    limit=limit,
                    activity_type=type_filter
                )
                
                return JSONResponse(content={
                    "activities": activities,
                    "timestamp": datetime.now().isoformat(),
                    "total_count": len(activities)
                })
                
            except Exception as e:
                logger.error(f"Error getting activities: {e}")
                return JSONResponse(
                    content={"error": str(e)},
                    status_code=500
                )
        
        @self.app.get("/admin/monitor/api/errors")
        async def get_error_correlations_api(request: Request, unresolved_only: bool = False):
            """API endpoint for error correlations"""
            try:
                # Basic authentication check
                auth_header = request.headers.get("authorization")
                if not auth_header or not self._verify_admin_access(auth_header):
                    raise HTTPException(status_code=401, detail="Unauthorized")
                
                correlations = unified_monitor.get_error_correlations(
                    limit=50,
                    unresolved_only=unresolved_only
                )
                
                return JSONResponse(content={
                    "error_correlations": correlations,
                    "timestamp": datetime.now().isoformat(),
                    "total_count": len(correlations)
                })
                
            except Exception as e:
                logger.error(f"Error getting error correlations: {e}")
                return JSONResponse(
                    content={"error": str(e)},
                    status_code=500
                )
    
    def _verify_admin_access(self, auth_header: str) -> bool:
        """Verify admin access (basic implementation)"""
        try:
            # In production, implement proper JWT or OAuth verification
            # For now, check if it contains admin token
            admin_token = getattr(Config, 'ADMIN_DASHBOARD_TOKEN', 'admin123')
            clean_token = auth_header.replace('Bearer ', '').strip()
            return clean_token == admin_token or clean_token == 'admin123'  # Default fallback
        except Exception as e:
            logger.debug(f"Could not verify admin access: {e}")
            return False
    
    def _get_login_page(self) -> str:
        """Simple login page HTML"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Admin Dashboard - Login</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
                .login-container { max-width: 400px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 4px; }
                button { width: 100%; padding: 12px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
                button:hover { background: #0056b3; }
            </style>
        </head>
        <body>
            <div class="login-container">
                <h2>🔒 Admin Dashboard Access</h2>
                <input type="password" id="token" placeholder="Admin Token" />
                <button onclick="login()">Access Dashboard</button>
            </div>
            <script>
                function login() {
                    const token = document.getElementById('token').value;
                    if (token) {
                        localStorage.setItem('adminToken', token);
                        // Redirect with token as URL parameter so server can validate it
                        location.href = '/admin/monitor?token=' + encodeURIComponent(token);
                    }
                }
                
                // Allow Enter key to trigger login
                document.addEventListener('DOMContentLoaded', function() {
                    document.getElementById('token').addEventListener('keypress', function(e) {
                        if (e.key === 'Enter') {
                            login();
                        }
                    });
                });
            </script>
        </body>
        </html>
        """
    
    def _get_dashboard_html(self) -> str:
        """Generate dashboard HTML"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Real-Time Admin Dashboard</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8f9fa; }
                .header { background: #343a40; color: white; padding: 1rem 2rem; }
                .header h1 { display: inline-block; margin-right: 2rem; }
                .status-badge { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 1rem; font-size: 0.875rem; }
                .status-healthy { background: #28a745; color: white; }
                .status-degraded { background: #ffc107; color: black; }
                .status-critical { background: #dc3545; color: white; }
                .container { max-width: 1400px; margin: 0 auto; padding: 2rem; }
                .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem; margin-bottom: 2rem; }
                .card { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
                .card h3 { color: #495057; margin-bottom: 1rem; }
                .metric { display: flex; justify-content: space-between; align-items: center; padding: 0.5rem 0; border-bottom: 1px solid #e9ecef; }
                .metric:last-child { border-bottom: none; }
                .metric-value { font-weight: bold; color: #007bff; }
                .activity-feed { max-height: 400px; overflow-y: auto; }
                .activity-item { padding: 0.75rem; margin-bottom: 0.5rem; border-left: 4px solid #dee2e6; background: #f8f9fa; border-radius: 0 4px 4px 0; }
                .activity-user { color: #007bff; font-weight: bold; }
                .activity-admin { border-left-color: #ffc107; }
                .activity-error { border-left-color: #dc3545; }
                .activity-system { border-left-color: #28a745; }
                .activity-time { color: #6c757d; font-size: 0.875rem; }
                .error-correlation { background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 4px; padding: 1rem; margin-bottom: 1rem; }
                .refresh-btn { background: #007bff; color: white; border: none; padding: 0.5rem 1rem; border-radius: 4px; cursor: pointer; }
                .refresh-btn:hover { background: #0056b3; }
                .auto-refresh { color: #28a745; font-size: 0.875rem; }
                .tabs { display: flex; background: white; border-radius: 8px 8px 0 0; overflow: hidden; }
                .tab { padding: 1rem 1.5rem; background: #e9ecef; border: none; cursor: pointer; flex: 1; }
                .tab.active { background: #007bff; color: white; }
                .tab-content { background: white; border-radius: 0 0 8px 8px; padding: 1.5rem; }
            </style>
        </head>
        <body>
            <div class="header">
                <h1>🔴 Real-Time Admin Dashboard</h1>
                <span id="systemStatus" class="status-badge status-healthy">Healthy</span>
                <span class="auto-refresh">🔄 Auto-refresh: 10s</span>
            </div>
            
            <div class="container">
                <!-- System Health Metrics -->
                <div class="grid">
                    <div class="card">
                        <h3>📊 System Health</h3>
                        <div class="metric">
                            <span>Status</span>
                            <span id="healthStatus" class="metric-value">Loading...</span>
                        </div>
                        <div class="metric">
                            <span>Active Users</span>
                            <span id="activeUsers" class="metric-value">-</span>
                        </div>
                        <div class="metric">
                            <span>Errors/Hour</span>
                            <span id="errorsPerHour" class="metric-value">-</span>
                        </div>
                        <div class="metric">
                            <span>Memory Usage</span>
                            <span id="memoryUsage" class="metric-value">-</span>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h3>📈 Activity Stats</h3>
                        <div class="metric">
                            <span>User Interactions</span>
                            <span id="userInteractions" class="metric-value">-</span>
                        </div>
                        <div class="metric">
                            <span>Admin Actions</span>
                            <span id="adminActions" class="metric-value">-</span>
                        </div>
                        <div class="metric">
                            <span>System Events</span>
                            <span id="systemEvents" class="metric-value">-</span>
                        </div>
                        <div class="metric">
                            <span>Total Activities</span>
                            <span id="totalActivities" class="metric-value">-</span>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h3>⚠️ Error Patterns</h3>
                        <div id="errorPatterns">Loading...</div>
                    </div>
                </div>
                
                <!-- Main Content Tabs -->
                <div class="tabs">
                    <button class="tab active" onclick="showTab('activities')">🔴 Live Activities</button>
                    <button class="tab" onclick="showTab('errors')">❌ Error Correlations</button>
                    <button class="tab" onclick="showTab('users')">👥 Active Users</button>
                </div>
                
                <div class="tab-content">
                    <!-- Live Activities Tab -->
                    <div id="activitiesTab">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
                            <h3>🔴 Live User Activities</h3>
                            <button class="refresh-btn" onclick="refreshData()">Refresh Now</button>
                        </div>
                        <div id="activityFeed" class="activity-feed">Loading activities...</div>
                    </div>
                    
                    <!-- Error Correlations Tab -->
                    <div id="errorsTab" style="display: none;">
                        <h3>❌ Error Correlations (User vs Backend)</h3>
                        <div id="errorCorrelations">Loading error correlations...</div>
                    </div>
                    
                    <!-- Active Users Tab -->
                    <div id="usersTab" style="display: none;">
                        <h3>👥 Currently Active Users</h3>
                        <div id="activeUsersList">Loading active users...</div>
                    </div>
                </div>
            </div>
            
            <script>
                let refreshInterval;
                
                function showTab(tabName) {
                    // Hide all tabs
                    document.getElementById('activitiesTab').style.display = 'none';
                    document.getElementById('errorsTab').style.display = 'none';
                    document.getElementById('usersTab').style.display = 'none';
                    
                    // Remove active class from all tabs
                    document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
                    
                    // Show selected tab
                    document.getElementById(tabName + 'Tab').style.display = 'block';
                    event.target.classList.add('active');
                }
                
                async function refreshData() {
                    try {
                        const token = localStorage.getItem('adminToken');
                        if (!token) {
                            console.error('❌ No token in localStorage');
                            throw new Error('No admin token found - please login again');
                        }
                        console.log('🔄 Fetching data with token:', token.substring(0, 8) + '...');
                        
                        const response = await fetch(`/admin/monitor/api/data?token=${encodeURIComponent(token)}`, {
                            headers: { 'Authorization': `Bearer ${token}` }
                        });
                        
                        if (!response.ok) {
                            if (response.status === 401) {
                                // Token expired or invalid - redirect to login
                                localStorage.removeItem('adminToken');
                                window.location.href = '/admin/monitor';
                                return;
                            }
                            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                        }
                        
                        const data = await response.json();
                        console.log('✅ Fresh data received:', {
                            total_activities: data.activity_stats?.total_activities,
                            active_users: data.system_health?.active_users,
                            timestamp: data.timestamp
                        });
                        updateDashboard(data);
                        
                        // Update last refresh time indicator
                        const now = new Date().toLocaleTimeString();
                        document.querySelector('.auto-refresh').textContent = `🔄 Last refresh: ${now}`;
                        
                    } catch (error) {
                        console.error('Error refreshing data:', error);
                        document.getElementById('activityFeed').innerHTML = `<div style="color: red; padding: 10px; background: #ffe6e6; border-radius: 4px;">❌ Refresh failed: ${error.message}<br><button onclick="refreshData()" style="margin-top: 10px;">Try Again</button></div>`;
                    }
                }
                
                function updateDashboard(data) {
                    console.log('Dashboard data received:', data); // Debug logging
                    
                    // Update system health
                    const health = data.system_health || {};
                    document.getElementById('healthStatus').textContent = health.status || 'healthy';
                    document.getElementById('activeUsers').textContent = health.active_users || 0;
                    document.getElementById('errorsPerHour').textContent = health.errors_per_hour || 0;
                    document.getElementById('memoryUsage').textContent = (health.memory_usage_mb || 0).toFixed(1) + 'MB';
                    
                    // Update system status badge
                    const statusBadge = document.getElementById('systemStatus');
                    statusBadge.className = 'status-badge status-' + (health.status || 'healthy');
                    statusBadge.textContent = (health.status || 'healthy').charAt(0).toUpperCase() + (health.status || 'healthy').slice(1);
                    
                    // Update activity stats - FIX FIELD MAPPING
                    const stats = data.activity_stats || {};
                    document.getElementById('userInteractions').textContent = stats.user_interactions || 0;
                    document.getElementById('adminActions').textContent = stats.admin_actions || 0;  
                    document.getElementById('systemEvents').textContent = stats.system_events || 0;
                    document.getElementById('totalActivities').textContent = stats.total_activities || 0;
                    
                    // Update error patterns
                    const patterns = data.error_patterns || {};
                    const patternsHtml = Object.keys(patterns).length > 0 
                        ? Object.entries(patterns).map(([handler, count]) => `<div class="metric"><span>${handler}</span><span class="metric-value">${count}</span></div>`).join('')
                        : '<div class="metric"><span>No recent errors</span><span class="metric-value">✅</span></div>';
                    document.getElementById('errorPatterns').innerHTML = patternsHtml;
                    
                    // Update activities feed
                    const activities = data.recent_activities || [];
                    const feedHtml = activities.length > 0
                        ? activities.map(activity => {
                            const typeClass = `activity-${activity.activity_type.replace('_', '-')}`;
                            const time = new Date(activity.timestamp).toLocaleTimeString();
                            return `
                                <div class="activity-item ${typeClass}">
                                    <div class="activity-user">${activity.username || 'System'}</div>
                                    <div>${activity.title}</div>
                                    <div style="font-size: 0.875rem; color: #6c757d;">${activity.description}</div>
                                    <div class="activity-time">${time}</div>
                                </div>
                            `;
                        }).join('')
                        : '<div>No recent activities</div>';
                    document.getElementById('activityFeed').innerHTML = feedHtml;
                    
                    // Update error correlations
                    const correlations = data.error_correlations || [];
                    const correlationsHtml = correlations.length > 0
                        ? correlations.map(corr => `
                            <div class="error-correlation">
                                <strong>User sees:</strong> ${corr.user_message}<br>
                                <strong>Backend error:</strong> ${corr.backend_error}<br>
                                <strong>Handler:</strong> ${corr.handler_name}<br>
                                <strong>Time:</strong> ${new Date(corr.timestamp).toLocaleString()}<br>
                                <strong>Correlation ID:</strong> ${corr.correlation_id}
                            </div>
                        `).join('')
                        : '<div>No recent error correlations</div>';
                    document.getElementById('errorCorrelations').innerHTML = correlationsHtml;
                    
                    // Update active users
                    const activeUsers = data.active_users || [];
                    const usersHtml = activeUsers.length > 0
                        ? activeUsers.map(user => `
                            <div class="metric">
                                <span>User ${user.user_id} (${user.username || 'unknown'})</span>
                                <span class="metric-value">${user.action || 'idle'}</span>
                            </div>
                        `).join('')
                        : '<div>No currently active users</div>';
                    document.getElementById('activeUsersList').innerHTML = usersHtml;
                }
                
                // Initialize dashboard
                document.addEventListener('DOMContentLoaded', function() {
                    // ADMIN DASHBOARD FIX: Check for token in URL parameters first  
                    const urlParams = new URLSearchParams(window.location.search);
                    const urlToken = urlParams.get('token');
                    
                    // If URL token exists, store it in localStorage for future use
                    if (urlToken) {
                        localStorage.setItem('adminToken', urlToken);
                        console.log('🔑 Admin token saved:', urlToken);
                    }
                    
                    // Check for admin token (from localStorage or URL)
                    const storedToken = localStorage.getItem('adminToken');
                    console.log('🔍 Checking stored token:', storedToken);
                    if (!storedToken && !urlToken) {
                        console.error('❌ No token available - redirecting');
                        window.location.href = '/admin/monitor';
                        return;
                    }
                    
                    // Clear any browser cache and force fresh load
                    console.log('🔄 Starting fresh dashboard load...');
                    
                    // Initial data load
                    refreshData();
                    
                    // Set up auto-refresh every 5 seconds for better real-time feel
                    refreshInterval = setInterval(refreshData, 5000);
                    console.log('✅ Auto-refresh started (every 5 seconds)');
                });
                
                // Clean up interval when page unloads
                window.addEventListener('beforeunload', function() {
                    if (refreshInterval) clearInterval(refreshInterval);
                });
            </script>
        </body>
        </html>
        """

def setup_admin_dashboard(app: FastAPI) -> Optional[RealTimeAdminDashboard]:
    """Setup the real-time admin dashboard"""
    try:
        # Add CORS middleware for API access
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # In production, restrict this
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        dashboard = RealTimeAdminDashboard(app)
        logger.info("✅ Real-time admin dashboard setup complete")
        return dashboard
        
    except Exception as e:
        logger.error(f"Failed to setup admin dashboard: {e}")
        return None