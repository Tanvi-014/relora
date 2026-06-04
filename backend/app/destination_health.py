"""
Destination Health Analysis Module for Hermes

Provides per-destination health analysis including:
- Success Rate
- Failure Rate
- Circuit State
- DLQ Count
- Oldest Failure
- Mean Latency
- Retry Success Rate
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Webhook, DeliveryAttempt, Destination, CircuitState, 
    DestinationHealth, TrendState
)


class DestinationHealthAnalyzer:
    """Analyzes health metrics for individual destinations."""
    
    @staticmethod
    async def get_destination_health(
        db: AsyncSession,
        destination_id: str,
    ) -> Dict:
        """
        Get comprehensive health report for a destination.
        
        Returns:
            Dict with health metrics and status
        """
        # Get destination info
        result = await db.execute(
            select(Destination).where(Destination.id == destination_id)
        )
        destination = result.scalar_one_or_none()
        
        if not destination:
            return {"error": "Destination not found"}
        
        # Calculate metrics
        success_rate = await DestinationHealthAnalyzer._get_success_rate(db, destination_id)
        failure_rate = 100 - success_rate
        circuit_state = destination.circuit_state
        dlq_count = await DestinationHealthAnalyzer._get_dlq_count(db, destination_id)
        oldest_failure = await DestinationHealthAnalyzer._get_oldest_failure(db, destination_id)
        mean_latency = await DestinationHealthAnalyzer._get_mean_latency(db, destination_id)
        retry_success_rate = await DestinationHealthAnalyzer._get_retry_success_rate(db, destination_id)
        
        # Calculate overall health status
        health_status = DestinationHealthAnalyzer._calculate_health_status(
            success_rate, failure_rate, circuit_state, dlq_count, oldest_failure
        )
        
        # Get recent failures for context
        recent_failures = await DestinationHealthAnalyzer._get_recent_failures(db, destination_id, limit=5)
        
        return {
            "destination_id": str(destination.id),
            "destination_name": destination.name,
            "destination_url": destination.url,
            "health_status": health_status,
            "metrics": {
                "success_rate": round(success_rate, 2),
                "failure_rate": round(failure_rate, 2),
                "circuit_state": circuit_state,
                "circuit_failure_count": destination.circuit_failure_count,
                "circuit_opened_at": destination.circuit_opened_at.isoformat() if destination.circuit_opened_at else None,
                "dlq_count": dlq_count,
                "oldest_failure": oldest_failure.isoformat() if oldest_failure else None,
                "mean_latency_ms": mean_latency,
                "retry_success_rate": round(retry_success_rate, 2),
            },
            "recent_failures": recent_failures,
        }
    
    @staticmethod
    async def _get_success_rate(db: AsyncSession, destination_id: str) -> float:
        """Calculate success rate for a destination."""
        total_query = select(func.count(Webhook.id)).where(
            Webhook.destination_id == destination_id
        )
        completed_query = select(func.count(Webhook.id)).where(
            and_(
                Webhook.destination_id == destination_id,
                Webhook.status == "completed",
            )
        )
        
        total_result = await db.execute(total_query)
        total = total_result.scalar() or 0
        
        if total == 0:
            return 100.0  # No webhooks = healthy
        
        completed_result = await db.execute(completed_query)
        completed = completed_result.scalar() or 0
        
        return (completed / total) * 100
    
    @staticmethod
    async def _get_dlq_count(db: AsyncSession, destination_id: str) -> int:
        """Get number of failed webhooks for a destination."""
        query = select(func.count(Webhook.id)).where(
            and_(
                Webhook.destination_id == destination_id,
                Webhook.status == "failed",
            )
        )
        
        result = await db.execute(query)
        return result.scalar() or 0
    
    @staticmethod
    async def _get_oldest_failure(db: AsyncSession, destination_id: str) -> Optional[datetime]:
        """Get the timestamp of the oldest failure for a destination."""
        query = select(Webhook.created_at).where(
            and_(
                Webhook.destination_id == destination_id,
                Webhook.status == "failed",
            )
        ).order_by(Webhook.created_at.asc())
        
        result = await db.execute(query)
        return result.scalar()
    
    @staticmethod
    async def _get_mean_latency(db: AsyncSession, destination_id: str) -> Optional[float]:
        """Calculate mean latency for successful deliveries."""
        query = select(func.avg(DeliveryAttempt.duration_ms)).where(
            and_(
                DeliveryAttempt.webhook_id == Webhook.id,
                Webhook.destination_id == destination_id,
                DeliveryAttempt.status_code == 200,
                DeliveryAttempt.duration_ms.isnot(None),
            )
        )
        
        result = await db.execute(query)
        return result.scalar()
    
    @staticmethod
    async def _get_retry_success_rate(db: AsyncSession, destination_id: str) -> float:
        """Calculate retry success rate (webhooks that succeeded after retry)."""
        # Webhooks that succeeded after at least one retry
        retry_success_query = select(func.count(Webhook.id)).where(
            and_(
                Webhook.destination_id == destination_id,
                Webhook.status == "completed",
                Webhook.retry_count > 0,
            )
        )
        
        # Total webhooks that had retries
        total_retry_query = select(func.count(Webhook.id)).where(
            and_(
                Webhook.destination_id == destination_id,
                Webhook.retry_count > 0,
            )
        )
        
        retry_success_result = await db.execute(retry_success_query)
        retry_success = retry_success_result.scalar() or 0
        
        total_retry_result = await db.execute(total_retry_query)
        total_retry = total_retry_result.scalar() or 0
        
        if total_retry == 0:
            return 100.0  # No retries = healthy
        
        return (retry_success / total_retry) * 100
    
    @staticmethod
    async def _get_recent_failures(
        db: AsyncSession, 
        destination_id: str, 
        limit: int = 5
    ) -> list:
        """Get recent failures for a destination."""
        query = select(Webhook).where(
            and_(
                Webhook.destination_id == destination_id,
                Webhook.status == "failed",
            )
        ).order_by(Webhook.created_at.desc()).limit(limit)
        
        result = await db.execute(query)
        webhooks = result.scalars().all()
        
        failures = []
        for webhook in webhooks:
            # Get the last delivery attempt
            attempt_query = select(DeliveryAttempt).where(
                DeliveryAttempt.webhook_id == webhook.id
            ).order_by(DeliveryAttempt.attempt_number.desc()).limit(1)
            
            attempt_result = await db.execute(attempt_query)
            attempt = attempt_result.scalar_one_or_none()
            
            failures.append({
                "webhook_id": str(webhook.id),
                "event_id": webhook.event_id,
                "created_at": webhook.created_at.isoformat(),
                "retry_count": webhook.retry_count,
                "status_code": attempt.status_code if attempt else None,
                "error_message": attempt.error_message if attempt else None,
                "failure_category": attempt.failure_category if attempt else None,
            })
        
        return failures
    
    @staticmethod
    def _calculate_health_status(
        success_rate: float,
        failure_rate: float,
        circuit_state: str,
        dlq_count: int,
        oldest_failure: Optional[datetime],
    ) -> str:
        """
        Calculate overall health status for a destination.
        
        Returns:
            One of: HEALTHY, DEGRADED, UNHEALTHY, CRITICAL
        """
        # Critical conditions
        if circuit_state == CircuitState.OPEN.value:
            return DestinationHealth.CRITICAL.value
        
        if success_rate < 50:
            return DestinationHealth.CRITICAL.value
        
        if dlq_count > 1000:
            return DestinationHealth.CRITICAL.value
        
        # Unhealthy conditions
        if success_rate < 70:
            return DestinationHealth.UNHEALTHY.value
        
        if dlq_count > 500:
            return DestinationHealth.UNHEALTHY.value
        
        if circuit_state == CircuitState.HALF_OPEN.value:
            return DestinationHealth.UNHEALTHY.value
        
        # Degraded conditions
        if success_rate < 90:
            return DestinationHealth.DEGRADED.value
        
        if dlq_count > 100:
            return DestinationHealth.DEGRADED.value
        
        if oldest_failure:
            age = datetime.now(timezone.utc) - oldest_failure
            if age > timedelta(hours=24):
                return DestinationHealth.DEGRADED.value
        
        # Healthy
        return DestinationHealth.HEALTHY.value
    
    @staticmethod
    async def get_all_destinations_health(
        db: AsyncSession,
        project_id: Optional[str] = None,
    ) -> list:
        """
        Get health status for all destinations.
        
        Returns:
            List of destination health reports
        """
        query = select(Destination)
        if project_id:
            query = query.where(Destination.project_id == project_id)
        
        result = await db.execute(query)
        destinations = result.scalars().all()
        
        health_reports = []
        for destination in destinations:
            health = await DestinationHealthAnalyzer.get_destination_health(
                db, str(destination.id)
            )
            health_reports.append(health)
        
        # Sort by health status (critical first)
        health_status_order = {
            DestinationHealth.CRITICAL.value: 0,
            DestinationHealth.UNHEALTHY.value: 1,
            DestinationHealth.DEGRADED.value: 2,
            DestinationHealth.HEALTHY.value: 3,
        }
        
        health_reports.sort(key=lambda x: health_status_order.get(x.get("health_status", "HEALTHY"), 3))
        
        return health_reports
    
    @staticmethod
    async def get_top_failing_destinations(
        db: AsyncSession,
        project_id: Optional[str] = None,
        limit: int = 10,
    ) -> list:
        """
        Get destinations with the most failures, sorted by impact score.
        
        Impact score considers:
        - Failure count
        - Failure rate
        - Severity of failures
        - Recency of failures
        
        Returns:
            List of destinations with impact scores
        """
        query = select(
            Destination.id,
            Destination.name,
            Destination.url,
            func.count(Webhook.id).label("failure_count"),
        ).join(
            Webhook, Destination.id == Webhook.destination_id
        ).where(
            Webhook.status == "failed"
        )
        
        if project_id:
            query = query.where(Destination.project_id == project_id)
        
        query = query.group_by(Destination.id).order_by(
            func.count(Webhook.id).desc()
        ).limit(limit)
        
        result = await db.execute(query)
        rows = result.all()
        
        destinations = []
        for row in rows:
            # Calculate impact score
            destination_id = row.id
            failure_count = row.failure_count
            
            # Get additional metrics
            total_count = await DestinationHealthAnalyzer._get_total_webhooks(db, destination_id)
            failure_rate = (failure_count / total_count * 100) if total_count > 0 else 0
            
            # Get failure severity
            severity_score = await DestinationHealthAnalyzer._get_severity_score(db, destination_id)
            
            # Get recency score
            recency_score = await DestinationHealthAnalyzer._get_recency_score(db, destination_id)
            
            # Calculate impact score (0-100)
            impact_score = (
                (failure_count * 0.3) +
                (failure_rate * 0.3) +
                (severity_score * 0.2) +
                (recency_score * 0.2)
            )
            
            destinations.append({
                "destination_id": str(destination_id),
                "name": row.name,
                "url": row.url,
                "failure_count": failure_count,
                "failure_rate": round(failure_rate, 2),
                "impact_score": round(impact_score, 2),
            })
        
        # Sort by impact score
        destinations.sort(key=lambda x: x["impact_score"], reverse=True)
        
        return destinations
    
    @staticmethod
    async def _get_total_webhooks(db: AsyncSession, destination_id: str) -> int:
        """Get total webhook count for a destination."""
        query = select(func.count(Webhook.id)).where(
            Webhook.destination_id == destination_id
        )
        
        result = await db.execute(query)
        return result.scalar() or 0
    
    @staticmethod
    async def _get_severity_score(db: AsyncSession, destination_id: str) -> float:
        """Calculate severity score based on failure categories."""
        # Get failure categories
        query = select(
            DeliveryAttempt.failure_category,
            func.count(DeliveryAttempt.id).label("count"),
        ).join(
            Webhook, DeliveryAttempt.webhook_id == Webhook.id
        ).where(
            and_(
                Webhook.destination_id == destination_id,
                DeliveryAttempt.failure_category.isnot(None),
            )
        ).group_by(DeliveryAttempt.failure_category)
        
        result = await db.execute(query)
        rows = result.all()
        
        severity_weights = {
            "AUTHENTICATION": 100,
            "AUTHORIZATION": 90,
            "DNS": 95,
            "SSL": 95,
            "NETWORK": 70,
            "TIMEOUT": 60,
            "SERVER_ERROR": 50,
            "RATE_LIMITING": 40,
            "CLIENT_ERROR": 30,
            "CONFIGURATION": 50,
            "TRANSFORM": 20,
            "FILTER": 20,
            "CIRCUIT_BREAKER": 60,
            "UNKNOWN": 10,
        }
        
        total_count = sum(row.count for row in rows)
        if total_count == 0:
            return 0.0
        
        weighted_sum = sum(
            row.count * severity_weights.get(row.failure_category, 10)
            for row in rows
        )
        
        return (weighted_sum / total_count)
    
    @staticmethod
    async def _get_recency_score(db: AsyncSession, destination_id: str) -> float:
        """Calculate recency score based on when failures occurred."""
        # Get most recent failure
        query = select(Webhook.created_at).where(
            and_(
                Webhook.destination_id == destination_id,
                Webhook.status == "failed",
            )
        ).order_by(Webhook.created_at.desc()).limit(1)
        
        result = await db.execute(query)
        most_recent = result.scalar()
        
        if not most_recent:
            return 0.0
        
        age = datetime.now(timezone.utc) - most_recent
        age_minutes = age.total_seconds() / 60
        
        # More recent = higher score
        if age_minutes < 5:
            return 100.0
        elif age_minutes < 15:
            return 80.0
        elif age_minutes < 60:
            return 60.0
        elif age_minutes < 360:
            return 40.0
        else:
            return 20.0
