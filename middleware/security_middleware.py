"""Advanced security middleware for comprehensive protection"""

import logging
import re
from typing import Dict, Any, Optional, List, Set
from datetime import datetime, timedelta
import os
from dataclasses import dataclass
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SecurityConfig:
    """Security configuration settings"""

    max_request_size: int = 10 * 1024 * 1024  # 10MB
    max_file_size: int = 5 * 1024 * 1024  # 5MB
    allowed_file_types: Set[str] = None
    max_files_per_request: int = 5
    rate_limit_per_ip: int = 100
    rate_limit_window: int = 3600  # 1 hour

    def __post_init__(self):
        if self.allowed_file_types is None:
            self.allowed_file_types = {
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".pdf",
                ".txt",
                ".doc",
                ".docx",
                ".xls",
                ".xlsx",
            }


class RequestSanitizer:
    """Advanced request sanitization system"""

    # Malicious patterns to detect
    MALICIOUS_PATTERNS = [
        # XSS patterns
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"vbscript:",
        r"on\w+\s*=",
        r"expression\s*\(",
        r"@import",
        r"<iframe[^>]*>",
        r"<object[^>]*>",
        r"<embed[^>]*>",
        # SQL injection patterns
        r"(\bUNION\b|\bSELECT\b|\bINSERT\b|\bDELETE\b|\bUPDATE\b|\bDROP\b)\s+",
        r";\s*(DROP|DELETE|TRUNCATE|ALTER)",
        r"--\s*",
        r"/\*.*?\*/",
        r"'\s*(OR|AND)\s*'.*?'\s*=\s*'",
        # Command injection patterns
        r"[;&|`]",
        r"\$\(.*?\)",
        r"`.*?`",
        r"\|\s*(rm|cat|ls|pwd|whoami|id|netstat|ps|curl|wget)",
        # Path traversal patterns
        r"\.\./+",
        r"\.\.\\+",
        r"/etc/passwd",
        r"/etc/shadow",
        r"\\windows\\system32",
        # NoSQL injection patterns
        r"\$where",
        r"\$regex",
        r"\$eval",
        r"\$mapReduce",
        # LDAP injection patterns
        r"[()&|*]",
        r"[\x00-\x1f\x7f-\x9f]",  # Control characters
    ]

    @classmethod
    def sanitize_input(cls, data: Any, aggressive: bool = True) -> Any:
        """Sanitize input data recursively"""
        if isinstance(data, str):
            return cls._sanitize_string(data, aggressive)
        elif isinstance(data, dict):
            return {
                key: cls.sanitize_input(value, aggressive)
                for key, value in data.items()
            }
        elif isinstance(data, list):
            return [cls.sanitize_input(item, aggressive) for item in data]
        else:
            return data

    @classmethod
    def _sanitize_string(cls, text: str, aggressive: bool) -> str:
        """Sanitize individual string"""
        if not isinstance(text, str):
            return str(text)

        # Basic HTML encoding
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        text = text.replace('"', "&quot;")
        text = text.replace("'", "&#x27;")

        if aggressive:
            # Remove malicious patterns
            for pattern in cls.MALICIOUS_PATTERNS:
                text = re.sub(pattern, "", text, flags=re.IGNORECASE)

            # Remove control characters
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

            # Limit length
            if len(text) > 10000:
                text = text[:10000] + "..."

        return text.strip()

    @classmethod
    def detect_malicious_patterns(cls, text: str) -> List[str]:
        """Detect malicious patterns in text"""
        detected = []

        for pattern in cls.MALICIOUS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                detected.append(f"Malicious pattern detected: {pattern[:50]}...")

        return detected


class FileUploadValidator:
    """Comprehensive file upload validation"""

    def __init__(self, config: SecurityConfig):
        self.config = config

        # Dangerous file signatures (magic bytes)
        self.dangerous_signatures = {
            b"\x4d\x5a": "PE executable",
            b"\x7f\x45\x4c\x46": "ELF executable",
            b"\xca\xfe\xba\xbe": "Java class file",
            b"\xfe\xed\xfa": "Mach-O executable",
            b"\x89\x50\x4e\x47": "PNG (could contain exploits)",
            b"\xff\xd8\xff": "JPEG (could contain exploits)",
            b"\x50\x4b\x03\x04": "ZIP/Office doc (could contain macros)",
        }

    def validate_file(
        self, file_data: bytes, filename: str, content_type: str
    ) -> Dict[str, Any]:
        """Comprehensive file validation"""
        result = {"valid": True, "errors": [], "warnings": [], "metadata": {}}

        # Size validation
        if len(file_data) > self.config.max_file_size:
            result["valid"] = False
            result["errors"].append(
                f"File size {len(file_data)} exceeds limit {self.config.max_file_size}"
            )

        # Extension validation
        file_ext = Path(filename).suffix.lower()
        if file_ext not in self.config.allowed_file_types:
            result["valid"] = False
            result["errors"].append(f"File type {file_ext} not allowed")

        # Filename validation
        filename_issues = self._validate_filename(filename)
        if filename_issues:
            result["errors"].extend(filename_issues)
            result["valid"] = False

        # Content validation
        content_issues = self._validate_content(file_data)
        if content_issues:
            result["warnings"].extend(content_issues)

        # MIME type validation
        expected_mime = mimetypes.guess_type(filename)[0]
        if expected_mime and content_type != expected_mime:
            result["warnings"].append(
                f"MIME type mismatch: expected {expected_mime}, got {content_type}"
            )

        # Enhanced security scanning
        security_issues = self._comprehensive_security_scan(file_data, filename)
        if security_issues:
            result["valid"] = False
            result["errors"].extend(security_issues)

        result["metadata"] = {
            "size": len(file_data),
            "extension": file_ext,
            "mime_type": content_type,
            "filename_safe": self._sanitize_filename(filename),
        }

        return result

    def _comprehensive_security_scan(self, file_data: bytes, filename: str) -> List[str]:
        """Comprehensive security scan for uploaded files"""
        issues = []
        
        # Check file signatures for dangerous content
        virus_patterns = self._scan_for_virus_patterns(file_data)
        issues.extend(virus_patterns)
        
        # Check for embedded scripts in files
        script_patterns = self._scan_for_embedded_scripts(file_data)
        issues.extend(script_patterns)
        
        # Check for suspicious file structure
        structure_issues = self._validate_file_structure(file_data, filename)
        issues.extend(structure_issues)
        
        return issues
    
    def _scan_for_embedded_scripts(self, file_data: bytes) -> List[str]:
        """Scan for embedded scripts in files"""
        issues = []
        
        try:
            # Convert to string for pattern matching (handle encoding errors)
            file_data.decode('utf-8', errors='ignore').lower()
            
            # Script patterns to detect
            script_patterns = [
                b'<script',
                b'javascript:',
                b'vbscript:',
                b'onload=',
                b'onerror=',
                b'<?php',
                b'<%',
                b'exec(',
                b'eval(',
                b'system(',
                b'shell_exec('
            ]
            
            for pattern in script_patterns:
                if pattern in file_data:
                    issues.append(f"Potentially malicious script pattern detected: {pattern.decode('utf-8', errors='ignore')}")
        
        except Exception as e:
            logger.warning(f"Error scanning for embedded scripts: {e}")
        
        return issues
    
    def _validate_file_structure(self, file_data: bytes, filename: str) -> List[str]:
        """Validate file structure for security issues"""
        issues = []
        
        # Check for polyglot files (files that are valid in multiple formats)
        if len(file_data) > 0:
            # Check if file starts with multiple format signatures
            signatures_found = 0
            for signature, description in self.dangerous_signatures.items():
                if file_data.startswith(signature):
                    signatures_found += 1
            
            if signatures_found > 1:
                issues.append("Polyglot file detected - multiple format signatures")
        
        # Check for ZIP bombs (compressed files with excessive expansion)
        if filename.lower().endswith(('.zip', '.rar', '.7z')):
            if len(file_data) < 1000 and b'\x50\x4b' in file_data:  # Small ZIP with ZIP signature
                issues.append("Potentially dangerous compressed file detected")
        
        return issues

    def _validate_filename(self, filename: str) -> List[str]:
        """Validate filename for security issues"""
        issues = []

        # Path traversal check
        if ".." in filename or "/" in filename or "\\" in filename:
            issues.append("Filename contains path traversal characters")

        # Dangerous characters
        dangerous_chars = '<>:"|?*\x00'
        if any(char in filename for char in dangerous_chars):
            issues.append("Filename contains dangerous characters")

        # Length check
        if len(filename) > 255:
            issues.append("Filename too long")

        # Reserved names (Windows)
        reserved_names = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
        }

        name_without_ext = Path(filename).stem.upper()
        if name_without_ext in reserved_names:
            issues.append("Filename is a reserved system name")

        return issues

    def _validate_content(self, file_data: bytes) -> List[str]:
        """Validate file content for suspicious patterns"""
        warnings = []

        # Check file signatures
        for signature, description in self.dangerous_signatures.items():
            if file_data.startswith(signature):
                warnings.append(f"Suspicious file signature detected: {description}")

        # Check for embedded executables
        if b"\x4d\x5a" in file_data[100:]:  # PE header not at start
            warnings.append("Embedded executable detected")

        # Check for script content in non-script files
        script_patterns = [b"<script", b"javascript:", b"vbscript:", b"<?php"]
        for pattern in script_patterns:
            if pattern.lower() in file_data.lower():
                warnings.append(
                    f"Script content detected: {pattern.decode('ascii', errors='ignore')}"
                )

        return warnings

    def _scan_for_virus_patterns(self, file_data: bytes) -> List[str]:
        """Scan for common virus/malware patterns"""
        errors = []

        # EICAR test string (antivirus test)
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        if eicar in file_data:
            errors.append("EICAR test virus signature detected")

        # Suspicious API calls in executables
        suspicious_apis = [
            b"CreateRemoteThread",
            b"WriteProcessMemory",
            b"VirtualAllocEx",
            b"SetWindowsHookEx",
            b"RegSetValueEx",
        ]

        for api in suspicious_apis:
            if api in file_data:
                errors.append(f"Suspicious API call detected: {api.decode('ascii')}")

        return errors

    def _sanitize_filename(self, filename: str) -> str:
        """Create safe filename"""
        # Remove path components
        filename = os.path.basename(filename)

        # Replace dangerous characters
        safe_filename = re.sub(r'[<>:"|?*\\/\x00-\x1f]', "_", filename)

        # Limit length
        if len(safe_filename) > 255:
            name, ext = os.path.splitext(safe_filename)
            safe_filename = name[: 255 - len(ext)] + ext

        return safe_filename


class SecurityMiddleware:
    """Main security middleware class"""

    def __init__(self, config: Optional[SecurityConfig] = None):
        self.config = config or SecurityConfig()
        self.file_validator = FileUploadValidator(self.config)
        self.request_sanitizer = RequestSanitizer()
        self.blocked_ips: Set[str] = set()
        self.suspicious_activity: Dict[str, List[datetime]] = {}

    def validate_request_size(self, content_length: int) -> bool:
        """Validate request size"""
        return content_length <= self.config.max_request_size

    def sanitize_request_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize all request data"""
        return self.request_sanitizer.sanitize_input(data)

    def detect_malicious_input(self, data: Any) -> List[str]:
        """Detect malicious patterns in input"""
        if isinstance(data, str):
            return self.request_sanitizer.detect_malicious_patterns(data)
        elif isinstance(data, dict):
            issues = []
            for key, value in data.items():
                issues.extend(self.detect_malicious_input(key))
                issues.extend(self.detect_malicious_input(value))
            return issues
        elif isinstance(data, list):
            issues = []
            for item in data:
                issues.extend(self.detect_malicious_input(item))
            return issues
        else:
            return []

    def validate_file_upload(
        self, file_data: bytes, filename: str, content_type: str
    ) -> Dict[str, Any]:
        """Validate file upload"""
        return self.file_validator.validate_file(file_data, filename, content_type)

    def check_ip_reputation(self, ip_address: str) -> Dict[str, Any]:
        """Check IP address reputation"""
        if ip_address in self.blocked_ips:
            return {
                "blocked": True,
                "reason": "Previously blocked for suspicious activity",
            }

        # Check activity frequency
        now = datetime.now()
        recent_activity = self.suspicious_activity.get(ip_address, [])

        # Clean old entries
        cutoff = now - timedelta(hours=1)
        recent_activity = [
            activity for activity in recent_activity if activity > cutoff
        ]

        if len(recent_activity) > self.config.rate_limit_per_ip:
            self.blocked_ips.add(ip_address)
            return {"blocked": True, "reason": "Rate limit exceeded"}

        # Record activity
        if ip_address not in self.suspicious_activity:
            self.suspicious_activity[ip_address] = []
        self.suspicious_activity[ip_address] = recent_activity + [now]

        return {"blocked": False, "activity_count": len(recent_activity)}

    def generate_security_headers(self) -> Dict[str, str]:
        """Generate security headers for responses"""
        return {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        }

    def log_security_event(
        self, event_type: str, details: Dict[str, Any], severity: str = "medium"
    ):
        """Log security events for monitoring"""
        security_log = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "severity": severity,
            "details": details,
        }

        if severity in ["high", "critical"]:
            logger.error(f"Security Event: {security_log}")
        elif severity == "medium":
            logger.warning(f"Security Event: {security_log}")
        else:
            logger.info(f"Security Event: {security_log}")

    def get_security_metrics(self) -> Dict[str, Any]:
        """Get security metrics for monitoring"""
        now = datetime.now()
        cutoff = now - timedelta(hours=24)

        recent_activity = {}
        for ip, activities in self.suspicious_activity.items():
            recent_count = len([a for a in activities if a > cutoff])
            if recent_count > 0:
                recent_activity[ip] = recent_count

        return {
            "blocked_ips_count": len(self.blocked_ips),
            "active_ips_24h": len(recent_activity),
            "total_requests_24h": sum(recent_activity.values()),
            "top_active_ips": sorted(
                recent_activity.items(), key=lambda x: x[1], reverse=True
            )[:10],
        }


# Global security middleware instance
security_middleware = SecurityMiddleware()


def validate_request_security(data: Dict[str, Any], ip_address: str) -> Dict[str, Any]:
    """Convenience function for request security validation"""
    # Check IP reputation
    ip_check = security_middleware.check_ip_reputation(ip_address)
    if ip_check["blocked"]:
        return {"valid": False, "error": ip_check["reason"]}

    # Sanitize data
    sanitized_data = security_middleware.sanitize_request_data(data)

    # Detect malicious patterns
    malicious_patterns = security_middleware.detect_malicious_input(data)
    if malicious_patterns:
        security_middleware.log_security_event(
            "malicious_input_detected",
            {"ip": ip_address, "patterns": malicious_patterns},
            "high",
        )
        return {"valid": False, "error": "Malicious input detected"}

    return {"valid": True, "sanitized_data": sanitized_data}


def validate_file_security(
    file_data: bytes, filename: str, content_type: str
) -> Dict[str, Any]:
    """Convenience function for file security validation"""
    return security_middleware.validate_file_upload(file_data, filename, content_type)
