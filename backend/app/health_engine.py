"""
DLQ Health Scoring Engine for Relora

Calculates a health score between 0-100 based on multiple factors:
- DLQ Size
- DLQ Growth Rate
- Oldest DLQ Event
- Failure Diversity
- Success Rate
- Circuit Breaker State
- Replay Success Rate
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Webhook, DeliveryAttempt, Destination, CircuitState, TrendState, Project


class HealthEngine:
    """Calculates DLQ health scores and provides health metrics."""
    
    @staticmethod
    async def calculate_dlq_health_score(
        db: AsyncSession,
        project_id: Optional[str] = None,
    ) -> Dict:
        """
        Calculate overall DLQ health score (0-100).
        
        Score factors:
        - DLQ Size (weight: 20%)
        - DLQ Growth Rate (weight: 30%)
        - Oldest DLQ Event (weight: 15%)
        - Failure Diversity (weight: 10%)
        - Success Rate (weight: 15%)
        - Circuit Breaker State (weight: 10%)
        
        Returns:
            Dict with health score and component scores
        """
        # Get metrics
        dlq_size = await HealthEngine._get_dlq_size(db, project_id)
        growth_metrics = await HealthEngine._get_growth_metrics(db, project_id)
        oldest_event = await HealthEngine._get_oldest_dlq_event(db, project_id)
        failure_diversity = await HealthEngine._get_failure_diversity(db, project_id)
        success_rate = await HealthEngine._get_success_rate(db, project_id)
        circuit_state = await HealthEngine._get_circuit_state(db, project_id)
        
        # Calculate component scores (0-100)
        size_score = HealthEngine._calculate_size_score(dlq_size)
        growth_score = HealthEngine._calculate_growth_score(growth_metrics)
        age_score = HealthEngine._calculate_age_score(oldest_event)
        diversity_score = HealthEngine._calculate_diversity_score(failure_diversity)
        success_score = HealthEngine._calculate_success_score(success_rate)
        circuit_score = HealthEngine._calculate_circuit_score(circuit_state)
        
        # Calculate weighted overall score
        overall_score = (
            size_score * 0.20 +
            growth_score * 0.30 +
            age_score * 0.15 +
            diversity_score * 0.10 +
            success_score * 0.15 +
            circuit_score * 0.10
        )
        
        # Determine health status
        health_status = HealthEngine._get_health_status(overall_score)
        
        return {
            "overall_score": round(overall_score, 1),
            "health_status": health_status,
            "components": {
                "dlq_size": {
                    "value": dlq_size,
                    "score": size_score,
                    "weight": 0.20,
                },
                "growth_rate": {
                    "value": growth_metrics,
                    "score": growth_score,
                    "weight": 0.30,
                },
                "oldest_event": {
                    "value": oldest_event,
                    "score": age_score,
                    "weight": 0.15,
                },
                "failure_diversity": {
                    "value": failure_diversity,
                    "score": diversity_score,
                    "weight": 0.10,
                },
                "success_rate": {
                    "value": success_rate,
                    "score": success_score,
                    "weight": 0.15,
                },
                "circuit_state": {
                    "value": circuit_state,
                    "score": circuit_score,
                    "weight": 0.10,
                },
            },
        }
    
    @staticmethod
    async def _get_dlq_size(db: AsyncSession, project_id: Optional[str]) -> int:
        """Get current DLQ size (number of failed webhooks)."""
        query = select(func.count(Webhook.id)).where(
            Webhook.status == "failed", Webhook.is_simulation == False  # noqa: E712
        )
        if project_id:
            query = query.where(Webhook.tenant_id == project_id)
        
        result = await db.execute(query)
        return result.scalar() or 0
    
    @staticmethod
    async def _get_growth_metrics(db: AsyncSession, project_id: Optional[str]) -> Dict:
        """Get growth metrics for different time windows."""
        now = datetime.now(timezone.utc)
        
        time_windows = {
            "15m": now - timedelta(minutes=15),
            "1h": now - timedelta(hours=1),
            "6h": now - timedelta(hours=6),
            "24h": now - timedelta(hours=24),
        }
        
        growth_metrics = {}
        for window_name, window_start in time_windows.items():
            query = select(func.count(Webhook.id)).where(
                and_(
                    Webhook.status == "failed",
                    Webhook.is_simulation == False,  # noqa: E712
                    Webhook.created_at >= window_start,
                )
            )
            if project_id:
                query = query.where(Webhook.tenant_id == project_id)
            
            result = await db.execute(query)
            growth_metrics[window_name] = result.scalar() or 0
        
        return growth_metrics
    
    @staticmethod
    async def _get_oldest_dlq_event(db: AsyncSession, project_id: Optional[str]) -> Optional[datetime]:
        """Get the timestamp of the oldest DLQ event."""
        query = select(Webhook.created_at).where(
            Webhook.status == "failed", Webhook.is_simulation == False  # noqa: E712
        ).order_by(Webhook.created_at.asc())
        if project_id:
            query = query.where(Webhook.tenant_id == project_id)
        
        result = await db.execute(query)
        return result.scalar()
    
    @staticmethod
    async def _get_failure_diversity(db: AsyncSession, project_id: Optional[str]) -> int:
        """Get the number of distinct failure categories."""
        query = select(func.count(func.distinct(DeliveryAttempt.failure_category))).where(
            DeliveryAttempt.failure_category.isnot(None)
        )
        if project_id:
            # Join with webhooks to filter by project
            query = query.join(Webhook, DeliveryAttempt.webhook_id == Webhook.id).where(
                Webhook.tenant_id == project_id
            )
        
        result = await db.execute(query)
        return result.scalar() or 0
    
    @staticmethod
    async def _get_success_rate(db: AsyncSession, project_id: Optional[str]) -> float:
        """Get the overall success rate (completed / total)."""
        ns = Webhook.is_simulation == False  # noqa: E712
        total_query = select(func.count(Webhook.id)).where(ns)
        completed_query = select(func.count(Webhook.id)).where(Webhook.status == "completed", ns)

        if project_id:
            total_query = total_query.where(Webhook.tenant_id == project_id)
            completed_query = completed_query.where(Webhook.tenant_id == project_id)
        
        total_result = await db.execute(total_query)
        total = total_result.scalar() or 0
        
        if total == 0:
            return 100.0  # No webhooks = healthy
        
        completed_result = await db.execute(completed_query)
        completed = completed_result.scalar() or 0
        
        return (completed / total) * 100
    
    @staticmethod
    async def _get_circuit_state(db: AsyncSession, project_id: Optional[str]) -> Dict:
        """Get circuit breaker state information."""
        query = select(Destination).join(
            Project, Project.id == Destination.project_id
        )
        if project_id:
            query = query.where(Project.api_key == project_id)
        
        result = await db.execute(query)
        destinations = result.scalars().all()
        
        total = len(destinations)
        if total == 0:
            return {"total": 0, "open": 0, "half_open": 0, "closed": 0}
        
        open_count = sum(1 for d in destinations if d.circuit_state == CircuitState.OPEN.value)
        half_open_count = sum(1 for d in destinations if d.circuit_state == CircuitState.HALF_OPEN.value)
        closed_count = sum(1 for d in destinations if d.circuit_state == CircuitState.CLOSED.value)
        
        return {
            "total": total,
            "open": open_count,
            "half_open": half_open_count,
            "closed": closed_count,
        }
    
    @staticmethod
    def _calculate_size_score(dlq_size: int) -> float:
        """Calculate size score (0-100)."""
        # 0-100 events: 100 points
        # 100-1000 events: 80-100 points
        # 1000-10000 events: 40-80 points
        # 10000+ events: 0-40 points
        
        if dlq_size == 0:
            return 100.0
        elif dlq_size < 100:
            return 100.0
        elif dlq_size < 1000:
            return 100.0 - ((dlq_size - 100) / 900) * 20
        elif dlq_size < 10000:
            return 80.0 - ((dlq_size - 1000) / 9000) * 40
        else:
            return max(0.0, 40.0 - ((dlq_size - 10000) / 10000) * 40)
    
    @staticmethod
    def _calculate_growth_score(growth_metrics: Dict) -> float:
        """Calculate growth score (0-100)."""
        # Prioritize growth over absolute size
        # Stable or decreasing: 100 points
        # Slow growth: 80-100 points
        # Moderate growth: 40-80 points
        # Rapid growth: 0-40 points
        
        rate_15m = growth_metrics.get("15m", 0)
        rate_1h = growth_metrics.get("1h", 0)
        rate_6h = growth_metrics.get("6h", 0)
        rate_24h = growth_metrics.get("24h", 0)
        
        # Calculate growth acceleration
        if rate_15m == 0 and rate_1h == 0:
            return 100.0  # Stable
        elif rate_15m < 10:
            return 90.0  # Very slow growth
        elif rate_15m < 50:
            return 80.0  # Slow growth
        elif rate_15m < 100:
            return 60.0  # Moderate growth
        elif rate_15m < 500:
            return 40.0  # Rapid growth
        else:
            return 0.0  # Explosive growth
    
    @staticmethod
    def _calculate_age_score(oldest_event: Optional[datetime]) -> float:
        """Calculate age score (0-100)."""
        if oldest_event is None:
            return 100.0  # No DLQ events = healthy
        
        age = datetime.now(timezone.utc) - oldest_event
        age_hours = age.total_seconds() / 3600
        
        # < 1 hour: 100 points
        # 1-6 hours: 80-100 points
        # 6-24 hours: 40-80 points
        # 24-72 hours: 20-40 points
        # > 72 hours: 0-20 points
        
        if age_hours < 1:
            return 100.0
        elif age_hours < 6:
            return 100.0 - ((age_hours - 1) / 5) * 20
        elif age_hours < 24:
            return 80.0 - ((age_hours - 6) / 18) * 40
        elif age_hours < 72:
            return 40.0 - ((age_hours - 24) / 48) * 20
        else:
            return max(0.0, 20.0 - ((age_hours - 72) / 72) * 20)
    
    @staticmethod
    def _calculate_diversity_score(diversity: int) -> float:
        """Calculate diversity score (0-100)."""
        # Fewer failure types = better (easier to fix)
        # 1 type: 100 points
        # 2-3 types: 80-100 points
        # 4-5 types: 60-80 points
        # 6-10 types: 40-60 points
        # 10+ types: 0-40 points
        
        if diversity == 0:
            return 100.0  # No failures
        elif diversity == 1:
            return 100.0
        elif diversity <= 3:
            return 100.0 - ((diversity - 1) / 2) * 20
        elif diversity <= 5:
            return 80.0 - ((diversity - 3) / 2) * 20
        elif diversity <= 10:
            return 60.0 - ((diversity - 5) / 5) * 20
        else:
            return max(0.0, 40.0 - ((diversity - 10) / 10) * 40)
    
    @staticmethod
    def _calculate_success_score(success_rate: float) -> float:
        """Calculate success score (0-100)."""
        # > 95%: 100 points
        # 90-95%: 80-100 points
        # 80-90%: 60-80 points
        # 70-80%: 40-60 points
        # < 70%: 0-40 points
        
        if success_rate >= 95:
            return 100.0
        elif success_rate >= 90:
            return 80.0 + ((success_rate - 90) / 5) * 20
        elif success_rate >= 80:
            return 60.0 + ((success_rate - 80) / 10) * 20
        elif success_rate >= 70:
            return 40.0 + ((success_rate - 70) / 10) * 20
        else:
            return max(0.0, (success_rate / 70) * 40)
    
    @staticmethod
    def _calculate_circuit_score(circuit_state: Dict) -> float:
        """Calculate circuit breaker score (0-100)."""
        total = circuit_state.get("total", 0)
        if total == 0:
            return 100.0  # No destinations = healthy
        
        open_count = circuit_state.get("open", 0)
        half_open_count = circuit_state.get("half_open", 0)
        
        # All closed: 100 points
        # Some half-open: 80-100 points
        # Some open: 0-80 points
        
        if open_count == 0 and half_open_count == 0:
            return 100.0
        elif open_count == 0:
            # Only half-open
            return 100.0 - (half_open_count / total) * 20
        else:
            # Some open
            return max(0.0, 80.0 - (open_count / total) * 80)
    
    @staticmethod
    def _get_health_status(score: float) -> str:
        """Get health status from score."""
        if score >= 80:
            return "HEALTHY"
        elif score >= 60:
            return "WARNING"
        elif score >= 40:
            return "DEGRADED"
        elif score >= 20:
            return "UNHEALTHY"
        else:
            return "CRITICAL"
