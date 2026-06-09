"""
Incident Engine for Relora

Aggregates failures into incidents, manages incident lifecycle,
and provides root cause analysis.
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Incident, IncidentState, Webhook, DeliveryAttempt, 
    Destination, FailureSeverity, FailureRecoverability, TrendState
)
from app.failure_classifier import FailureClassifier


class IncidentEngine:
    """Manages incident creation, aggregation, and lifecycle."""
    
    @staticmethod
    async def get_or_create_incident(
        db: AsyncSession,
        project_id: str,
        destination_id: Optional[str],
        error_signature: str,
        failure_category: str,
        failure_subcategory: str,
    ) -> Incident:
        """
        Get existing incident or create a new one for this failure pattern.
        
        Incidents are grouped by:
        - project_id
        - destination_id
        - error_signature
        
        This groups similar failures together into incidents.
        """
        # Look for existing open incident with same signature
        result = await db.execute(
            select(Incident).where(
                and_(
                    Incident.project_id == project_id,
                    Incident.destination_id == destination_id if destination_id else True,
                    Incident.incident_signature == error_signature,
                    Incident.state == IncidentState.OPEN.value,
                )
            )
        )
        incident = result.scalar_one_or_none()
        
        if incident:
            # Update existing incident
            incident.affected_webhook_count += 1
            incident.last_seen_at = datetime.now(timezone.utc)
            
            # Update growth rates
            await IncidentEngine._update_growth_rates(db, incident)
            
            # Get recommendation
            recommendation = FailureClassifier.get_recommendation(
                failure_category, failure_subcategory
            )
            incident.recommended_action = recommendation.get("suggested_fix")
            incident.expected_recovery_difficulty = recommendation.get("expected_recovery_difficulty")
            
            await db.commit()
            await db.refresh(incident)
            return incident
        
        # Create new incident
        incident = Incident(
            id=uuid.uuid4(),
            project_id=project_id,
            destination_id=destination_id,
            incident_signature=error_signature,
            state=IncidentState.OPEN.value,
            failure_category=failure_category,
            failure_subcategory=failure_subcategory,
            root_cause=IncidentEngine._generate_root_cause(failure_category, failure_subcategory),
            affected_webhook_count=1,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            trend_state=TrendState.STABLE.value,
            severity=IncidentEngine._determine_severity(failure_category),
            recoverability=IncidentEngine._determine_recoverability(failure_category),
        )
        
        # Get recommendation
        recommendation = FailureClassifier.get_recommendation(
            failure_category, failure_subcategory
        )
        incident.recommended_action = recommendation.get("suggested_fix")
        incident.expected_recovery_difficulty = recommendation.get("expected_recovery_difficulty")
        
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        return incident
    
    @staticmethod
    async def _update_growth_rates(db: AsyncSession, incident: Incident):
        """Calculate growth rates for the incident."""
        now = datetime.now(timezone.utc)
        
        # Get webhook counts for different time windows
        time_windows = [
            ("15m", now - timedelta(minutes=15)),
            ("1h", now - timedelta(hours=1)),
            ("6h", now - timedelta(hours=6)),
            ("24h", now - timedelta(hours=24)),
        ]
        
        for window_name, window_start in time_windows:
            result = await db.execute(
                select(func.count(Webhook.id)).where(
                    and_(
                        Webhook.project_id == incident.project_id,
                        Webhook.destination_id == incident.destination_id if incident.destination_id else True,
                        Webhook.status == "failed",
                        Webhook.created_at >= window_start,
                    )
                )
            )
            count = result.scalar() or 0
            
            if window_name == "15m":
                incident.growth_rate_15m = count
            elif window_name == "1h":
                incident.growth_rate_1h = count
            elif window_name == "6h":
                incident.growth_rate_6h = count
            elif window_name == "24h":
                incident.growth_rate_24h = count
        
        # Determine trend state
        incident.trend_state = IncidentEngine._classify_trend(
            incident.growth_rate_15m,
            incident.growth_rate_1h,
            incident.growth_rate_6h,
            incident.growth_rate_24h,
        )
    
    @staticmethod
    def _classify_trend(
        rate_15m: int,
        rate_1h: int,
        rate_6h: int,
        rate_24h: int,
    ) -> str:
        """Classify the growth trend based on rates."""
        # Calculate growth acceleration
        if rate_15m > 100:
            return TrendState.EXPLOSIVE_GROWTH.value
        elif rate_1h > 500:
            return TrendState.RAPID_GROWTH.value
        elif rate_6h > 1000:
            return TrendState.MODERATE_GROWTH.value
        elif rate_24h > 2000:
            return TrendState.SLOW_GROWTH.value
        else:
            return TrendState.STABLE.value
    
    @staticmethod
    def _generate_root_cause(category: str, subcategory: str) -> str:
        """Generate human-readable root cause description."""
        root_causes = {
            "AUTHENTICATION": "Authentication credentials are invalid or expired",
            "AUTHORIZATION": "Insufficient permissions to access the destination",
            "RATE_LIMITING": "Destination is rate limiting requests",
            "CLIENT_ERROR": "Client-side error in request format or content",
            "SERVER_ERROR": "Destination service is experiencing internal failures",
            "NETWORK": "Network connectivity issues preventing delivery",
            "TIMEOUT": "Destination is responding too slowly",
            "DNS": "DNS resolution failed for destination hostname",
            "SSL": "SSL/TLS certificate validation failed",
            "TRANSFORM": "Payload transformation or mapping failed",
            "FILTER": "Event filter expression evaluation failed",
            "CIRCUIT_BREAKER": "Circuit breaker is open due to repeated failures",
            "CONFIGURATION": "Destination or webhook configuration error",
            "UNKNOWN": "Unknown failure cause",
        }
        
        return root_causes.get(category, "Unknown failure cause")
    
    @staticmethod
    def _determine_severity(category: str) -> str:
        """Determine severity based on failure category."""
        severity_map = {
            "AUTHENTICATION": FailureSeverity.CRITICAL.value,
            "AUTHORIZATION": FailureSeverity.HIGH.value,
            "RATE_LIMITING": FailureSeverity.HIGH.value,
            "CLIENT_ERROR": FailureSeverity.MEDIUM.value,
            "SERVER_ERROR": FailureSeverity.HIGH.value,
            "NETWORK": FailureSeverity.HIGH.value,
            "TIMEOUT": FailureSeverity.HIGH.value,
            "DNS": FailureSeverity.CRITICAL.value,
            "SSL": FailureSeverity.CRITICAL.value,
            "TRANSFORM": FailureSeverity.MEDIUM.value,
            "FILTER": FailureSeverity.MEDIUM.value,
            "CIRCUIT_BREAKER": FailureSeverity.HIGH.value,
            "CONFIGURATION": FailureSeverity.HIGH.value,
            "UNKNOWN": FailureSeverity.MEDIUM.value,
        }
        
        return severity_map.get(category, FailureSeverity.MEDIUM.value)
    
    @staticmethod
    def _determine_recoverability(category: str) -> str:
        """Determine recoverability based on failure category."""
        recoverability_map = {
            "AUTHENTICATION": FailureRecoverability.MANUAL.value,
            "AUTHORIZATION": FailureRecoverability.MANUAL.value,
            "RATE_LIMITING": FailureRecoverability.AUTOMATIC.value,
            "CLIENT_ERROR": FailureRecoverability.MANUAL.value,
            "SERVER_ERROR": FailureRecoverability.AUTOMATIC.value,
            "NETWORK": FailureRecoverability.AUTOMATIC.value,
            "TIMEOUT": FailureRecoverability.AUTOMATIC.value,
            "DNS": FailureRecoverability.MANUAL.value,
            "SSL": FailureRecoverability.MANUAL.value,
            "TRANSFORM": FailureRecoverability.MANUAL.value,
            "FILTER": FailureRecoverability.MANUAL.value,
            "CIRCUIT_BREAKER": FailureRecoverability.AUTOMATIC.value,
            "CONFIGURATION": FailureRecoverability.MANUAL.value,
            "UNKNOWN": FailureRecoverability.UNLIKELY.value,
        }
        
        return recoverability_map.get(category, FailureRecoverability.MANUAL.value)
    
    @staticmethod
    async def auto_resolve_incidents(db: AsyncSession, project_id: Optional[str] = None):
        """
        Automatically resolve incidents that have recovered.
        
        An incident is considered resolved when:
        - No new failures for the signature in the last hour
        - Growth rates are stable
        """
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        
        # Get open incidents
        query = select(Incident).where(Incident.state == IncidentState.OPEN.value)
        if project_id:
            query = query.where(Incident.project_id == project_id)
        
        result = await db.execute(query)
        incidents = result.scalars().all()
        
        for incident in incidents:
            # Check if there have been any new failures in the last hour
            recent_failures = await db.execute(
                select(func.count(Webhook.id)).where(
                    and_(
                        Webhook.project_id == incident.project_id,
                        Webhook.destination_id == incident.destination_id if incident.destination_id else True,
                        Webhook.status == "failed",
                        Webhook.created_at >= one_hour_ago,
                    )
                )
            )
            recent_count = recent_failures.scalar() or 0
            
            # If no recent failures and trend is stable, resolve the incident
            if recent_count == 0 and incident.trend_state == TrendState.STABLE.value:
                incident.state = IncidentState.RESOLVED.value
                incident.resolved_at = now
                incident.resolution_notes = "Automatically resolved - no new failures for 1 hour"
        
        await db.commit()
    
    @staticmethod
    async def get_incident_summary(db: AsyncSession, project_id: Optional[str] = None) -> Dict:
        """
        Get summary of all incidents.
        
        Returns:
            Dict with incident counts and statistics
        """
        query = select(Incident)
        if project_id:
            query = query.where(Incident.project_id == project_id)
        
        result = await db.execute(query)
        incidents = result.scalars().all()
        
        total_incidents = len(incidents)
        open_incidents = sum(1 for i in incidents if i.state == IncidentState.OPEN.value)
        critical_incidents = sum(
            1 for i in incidents 
            if i.state == IncidentState.OPEN.value and i.severity == FailureSeverity.CRITICAL.value
        )
        
        # Get unique destinations affected
        affected_destinations = len(set(i.destination_id for i in incidents if i.destination_id))
        
        return {
            "total_incidents": total_incidents,
            "open_incidents": open_incidents,
            "critical_incidents": critical_incidents,
            "affected_destinations": affected_destinations,
        }
    
    @staticmethod
    async def get_root_causes(db: AsyncSession, project_id: Optional[str] = None) -> List[Dict]:
        """
        Get root cause breakdown of failures.
        
        Returns:
            List of dicts with root cause information
        """
        # Only count delivery attempts whose parent webhook is still failed
        query = select(
            DeliveryAttempt.failure_category,
            DeliveryAttempt.failure_subcategory,
            func.count(DeliveryAttempt.id).label("count"),
        ).join(
            Webhook, DeliveryAttempt.webhook_id == Webhook.id
        ).where(
            DeliveryAttempt.failure_category.isnot(None),
            Webhook.status == "failed",
            Webhook.is_simulation == False,  # noqa: E712
        ).group_by(
            DeliveryAttempt.failure_category,
            DeliveryAttempt.failure_subcategory,
        ).order_by(
            func.count(DeliveryAttempt.id).desc()
        )

        if project_id:
            query = query.where(Webhook.project_id == project_id)

        result = await db.execute(query)
        rows = result.all()
        
        root_causes = []
        for row in rows:
            root_causes.append({
                "category": row.failure_category,
                "subcategory": row.failure_subcategory,
                "count": row.count,
            })
        
        return root_causes
