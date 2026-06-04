"""
Failure Classification Engine for Hermes

Analyzes delivery attempt failures and classifies them into:
- Category (e.g., AUTHENTICATION, NETWORK, TIMEOUT)
- Subcategory (e.g., HTTP_401, CONNECTION_REFUSED)
- Severity (low, medium, high, critical)
- Recoverability (automatic, manual, unlikely)
"""

import re
from typing import Dict, Optional, Tuple
from app.models import FailureCategory, FailureSeverity, FailureRecoverability


class FailureClassifier:
    """Classifies webhook delivery failures into meaningful categories."""
    
    # HTTP status code mappings
    HTTP_STATUS_CATEGORIES = {
        400: (FailureCategory.CLIENT_ERROR, "HTTP_400", FailureSeverity.MEDIUM, FailureRecoverability.MANUAL),
        401: (FailureCategory.AUTHENTICATION, "HTTP_401", FailureSeverity.CRITICAL, FailureRecoverability.MANUAL),
        403: (FailureCategory.AUTHORIZATION, "HTTP_403", FailureSeverity.HIGH, FailureRecoverability.MANUAL),
        404: (FailureCategory.CLIENT_ERROR, "HTTP_404", FailureSeverity.HIGH, FailureRecoverability.MANUAL),
        405: (FailureCategory.CLIENT_ERROR, "HTTP_405", FailureSeverity.MEDIUM, FailureRecoverability.MANUAL),
        409: (FailureCategory.CLIENT_ERROR, "HTTP_409", FailureSeverity.MEDIUM, FailureRecoverability.MANUAL),
        422: (FailureCategory.CLIENT_ERROR, "HTTP_422", FailureSeverity.MEDIUM, FailureRecoverability.MANUAL),
        429: (FailureCategory.RATE_LIMITING, "HTTP_429", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC),
        500: (FailureCategory.SERVER_ERROR, "HTTP_500", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC),
        502: (FailureCategory.SERVER_ERROR, "HTTP_502", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC),
        503: (FailureCategory.SERVER_ERROR, "HTTP_503", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC),
        504: (FailureCategory.TIMEOUT, "HTTP_504", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC),
    }
    
    # Error message patterns
    ERROR_PATTERNS = {
        # Authentication errors
        r"unauthorized|invalid.*token|expired.*token|authentication.*failed": (
            FailureCategory.AUTHENTICATION, "AUTH_TOKEN_INVALID", FailureSeverity.CRITICAL, FailureRecoverability.MANUAL
        ),
        r"forbidden|access.*denied|permission.*denied": (
            FailureCategory.AUTHORIZATION, "ACCESS_DENIED", FailureSeverity.HIGH, FailureRecoverability.MANUAL
        ),
        
        # Network errors
        r"connection.*refused|connection.*reset|econnrefused": (
            FailureCategory.NETWORK, "CONNECTION_REFUSED", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC
        ),
        r"connection.*timeout|timed.*out|timeout": (
            FailureCategory.TIMEOUT, "CONNECTION_TIMEOUT", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC
        ),
        r"no.*route.*to.*host|host.*unreachable": (
            FailureCategory.NETWORK, "HOST_UNREACHABLE", FailureSeverity.HIGH, FailureRecoverability.MANUAL
        ),
        r"network.*unreachable": (
            FailureCategory.NETWORK, "NETWORK_UNREACHABLE", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC
        ),
        
        # DNS errors
        r"dns.*error|name.*not.*resolved|nodata|nxdomain": (
            FailureCategory.DNS, "DNS_RESOLUTION_FAILED", FailureSeverity.CRITICAL, FailureRecoverability.MANUAL
        ),
        
        # SSL/TLS errors
        r"ssl|tls|certificate.*expired|certificate.*invalid|handshake.*failed": (
            FailureCategory.SSL, "SSL_CERTIFICATE_ERROR", FailureSeverity.CRITICAL, FailureRecoverability.MANUAL
        ),
        
        # Transform errors
        r"transform.*error|javascript.*error|script.*error": (
            FailureCategory.TRANSFORM, "TRANSFORM_EXCEPTION", FailureSeverity.MEDIUM, FailureRecoverability.MANUAL
        ),
        
        # Filter errors
        r"filter.*error|expression.*error": (
            FailureCategory.FILTER, "FILTER_EXCEPTION", FailureSeverity.MEDIUM, FailureRecoverability.MANUAL
        ),
        
        # Configuration errors
        r"configuration.*error|invalid.*config|missing.*config": (
            FailureCategory.CONFIGURATION, "CONFIGURATION_ERROR", FailureSeverity.HIGH, FailureRecoverability.MANUAL
        ),
        
        # Circuit breaker
        r"circuit.*breaker|circuit.*open": (
            FailureCategory.CIRCUIT_BREAKER, "CIRCUIT_OPEN", FailureSeverity.HIGH, FailureRecoverability.AUTOMATIC
        ),
    }
    
    @classmethod
    def classify(
        cls,
        status_code: Optional[int] = None,
        error_message: Optional[str] = None,
        response_body: Optional[str] = None,
    ) -> Tuple[str, str, str, str, str]:
        """
        Classify a delivery attempt failure.
        
        Returns:
            Tuple of (category, subcategory, severity, recoverability, error_signature)
        """
        # Default to unknown
        category = FailureCategory.UNKNOWN.value
        subcategory = "UNKNOWN"
        severity = FailureSeverity.MEDIUM.value
        recoverability = FailureRecoverability.MANUAL.value
        
        # Combine error message and response body for analysis
        error_text = " ".join(filter(None, [error_message, response_body])).lower()
        
        # Check HTTP status code first
        if status_code and status_code in cls.HTTP_STATUS_CATEGORIES:
            category, subcategory, severity_enum, recoverability_enum = cls.HTTP_STATUS_CATEGORIES[status_code]
            category = category.value
            subcategory = subcategory
            severity = severity_enum.value
            recoverability = recoverability_enum.value
        
        # Check error message patterns
        for pattern, (cat_enum, subcat, sev_enum, rec_enum) in cls.ERROR_PATTERNS.items():
            if re.search(pattern, error_text, re.IGNORECASE):
                # Only override if we haven't already classified from HTTP status
                # or if the pattern is more specific
                if category == FailureCategory.UNKNOWN.value or cat_enum.value in [
                    FailureCategory.AUTHENTICATION.value,
                    FailureCategory.AUTHORIZATION.value,
                    FailureCategory.DNS.value,
                    FailureCategory.SSL.value,
                ]:
                    category = cat_enum.value
                    subcategory = subcat
                    severity = sev_enum.value
                    recoverability = rec_enum.value
                break
        
        # Generate error signature for grouping
        error_signature = cls._generate_signature(status_code, category, subcategory, error_text)
        
        return category, subcategory, severity, recoverability, error_signature
    
    @classmethod
    def _generate_signature(
        cls,
        status_code: Optional[int],
        category: str,
        subcategory: str,
        error_text: str,
    ) -> str:
        """
        Generate a signature for grouping similar failures.
        
        The signature should be the same for failures that have the same root cause.
        """
        parts = []
        
        if status_code:
            parts.append(f"status:{status_code}")
        
        if category != FailureCategory.UNKNOWN.value:
            parts.append(f"cat:{category}")
        
        if subcategory != "UNKNOWN":
            parts.append(f"sub:{subcategory}")
        
        # Extract key error patterns for signature
        if "connection refused" in error_text:
            parts.append("type:connection_refused")
        elif "timeout" in error_text:
            parts.append("type:timeout")
        elif "dns" in error_text:
            parts.append("type:dns")
        elif "certificate" in error_text:
            parts.append("type:certificate")
        elif "unauthorized" in error_text or "401" in str(status_code):
            parts.append("type:auth")
        elif "forbidden" in error_text or "403" in str(status_code):
            parts.append("type:forbidden")
        
        return "|".join(parts) if parts else "unknown"
    
    @classmethod
    def get_recommendation(cls, category: str, subcategory: str) -> Dict[str, str]:
        """
        Get actionable recommendations for a failure type.
        
        Returns:
            Dict with keys: cause, impact, suggested_fix, expected_recovery_difficulty
        """
        recommendations = {
            FailureCategory.AUTHENTICATION.value: {
                "HTTP_401": {
                    "cause": "Destination credentials are likely invalid or expired",
                    "impact": "All requests to this destination will fail authentication",
                    "suggested_fix": "Rotate destination API credentials or tokens",
                    "expected_recovery_difficulty": "medium",
                },
                "AUTH_TOKEN_INVALID": {
                    "cause": "Authentication token is invalid or has expired",
                    "impact": "Requests cannot be authenticated by the destination",
                    "suggested_fix": "Refresh or regenerate authentication tokens",
                    "expected_recovery_difficulty": "low",
                },
            },
            FailureCategory.AUTHORIZATION.value: {
                "HTTP_403": {
                    "cause": "Destination rejected access due to insufficient permissions",
                    "impact": "Requests are authenticated but lack necessary permissions",
                    "suggested_fix": "Check and update destination permissions/roles",
                    "expected_recovery_difficulty": "medium",
                },
                "ACCESS_DENIED": {
                    "cause": "Access to the requested resource was denied",
                    "impact": "Specific endpoints or resources cannot be accessed",
                    "suggested_fix": "Verify API permissions and resource access rights",
                    "expected_recovery_difficulty": "medium",
                },
            },
            FailureCategory.CLIENT_ERROR.value: {
                "HTTP_404": {
                    "cause": "Destination URL or endpoint no longer exists",
                    "impact": "Requests cannot reach the intended endpoint",
                    "suggested_fix": "Verify destination URL is correct and endpoint exists",
                    "expected_recovery_difficulty": "low",
                },
                "HTTP_422": {
                    "cause": "Request payload validation failed",
                    "impact": "Destination rejected the request format or content",
                    "suggested_fix": "Review and fix payload format or schema",
                    "expected_recovery_difficulty": "medium",
                },
            },
            FailureCategory.RATE_LIMITING.value: {
                "HTTP_429": {
                    "cause": "Destination is rate limiting requests",
                    "impact": "Requests are being throttled by the destination",
                    "suggested_fix": "Reduce request rate or implement backoff strategy",
                    "expected_recovery_difficulty": "low",
                },
            },
            FailureCategory.SERVER_ERROR.value: {
                "HTTP_500": {
                    "cause": "Destination service is experiencing internal failures",
                    "impact": "Destination cannot process requests reliably",
                    "suggested_fix": "Contact destination service provider or monitor for recovery",
                    "expected_recovery_difficulty": "low",
                },
                "HTTP_502": {
                    "cause": "Destination gateway or proxy error",
                    "impact": "Requests cannot reach the destination service",
                    "suggested_fix": "Monitor destination service status and retry",
                    "expected_recovery_difficulty": "low",
                },
                "HTTP_503": {
                    "cause": "Destination service is temporarily unavailable",
                    "impact": "Service is overloaded or under maintenance",
                    "suggested_fix": "Wait for service recovery and retry with backoff",
                    "expected_recovery_difficulty": "low",
                },
            },
            FailureCategory.TIMEOUT.value: {
                "HTTP_504": {
                    "cause": "Destination gateway timeout",
                    "impact": "Destination is taking too long to respond",
                    "suggested_fix": "Increase timeout or investigate destination performance",
                    "expected_recovery_difficulty": "medium",
                },
                "CONNECTION_TIMEOUT": {
                    "cause": "Connection to destination timed out",
                    "impact": "Cannot establish connection within timeout period",
                    "suggested_fix": "Check network connectivity and destination availability",
                    "expected_recovery_difficulty": "medium",
                },
            },
            FailureCategory.NETWORK.value: {
                "CONNECTION_REFUSED": {
                    "cause": "Destination refused the connection",
                    "impact": "Destination service is not accepting connections",
                    "suggested_fix": "Verify destination service is running and accessible",
                    "expected_recovery_difficulty": "medium",
                },
                "HOST_UNREACHABLE": {
                    "cause": "Network route to destination host is unavailable",
                    "impact": "Cannot reach the destination host",
                    "suggested_fix": "Check network configuration and firewall rules",
                    "expected_recovery_difficulty": "high",
                },
            },
            FailureCategory.DNS.value: {
                "DNS_RESOLUTION_FAILED": {
                    "cause": "Hostname cannot be resolved to an IP address",
                    "impact": "Destination URL hostname is invalid or DNS is misconfigured",
                    "suggested_fix": "Verify destination URL hostname and DNS configuration",
                    "expected_recovery_difficulty": "medium",
                },
            },
            FailureCategory.SSL.value: {
                "SSL_CERTIFICATE_ERROR": {
                    "cause": "SSL/TLS certificate validation failed",
                    "impact": "Secure connection cannot be established",
                    "suggested_fix": "Update SSL certificate or check certificate validity",
                    "expected_recovery_difficulty": "medium",
                },
            },
            FailureCategory.TRANSFORM.value: {
                "TRANSFORM_EXCEPTION": {
                    "cause": "JavaScript transform or payload mapping failed",
                    "impact": "Payload transformation cannot be completed",
                    "suggested_fix": "Debug and fix transform code or mapping configuration",
                    "expected_recovery_difficulty": "medium",
                },
            },
            FailureCategory.FILTER.value: {
                "FILTER_EXCEPTION": {
                    "cause": "Filter expression evaluation failed",
                    "impact": "Event filtering cannot be applied",
                    "suggested_fix": "Fix filter expression syntax or logic",
                    "expected_recovery_difficulty": "low",
                },
            },
            FailureCategory.CONFIGURATION.value: {
                "CONFIGURATION_ERROR": {
                    "cause": "Destination or webhook configuration is invalid",
                    "impact": "Webhook cannot be processed due to misconfiguration",
                    "suggested_fix": "Review and fix destination configuration",
                    "expected_recovery_difficulty": "low",
                },
            },
            FailureCategory.CIRCUIT_BREAKER.value: {
                "CIRCUIT_OPEN": {
                    "cause": "Circuit breaker is open due to repeated failures",
                    "impact": "Requests are being blocked to prevent cascading failures",
                    "suggested_fix": "Investigate root cause and wait for circuit to recover",
                    "expected_recovery_difficulty": "low",
                },
            },
        }
        
        # Get recommendation for specific category/subcategory
        if category in recommendations:
            if subcategory in recommendations[category]:
                return recommendations[category][subcategory]
        
        # Default recommendation
        return {
            "cause": "Unknown failure occurred",
            "impact": "Webhook delivery failed for unknown reasons",
            "suggested_fix": "Inspect error details and logs for more information",
            "expected_recovery_difficulty": "high",
        }
