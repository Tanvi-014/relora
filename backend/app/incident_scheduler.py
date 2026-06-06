"""
Incident Scheduler for Relora

Automatically detects and creates incidents based on:
- DLQ growth exceeding threshold
- Health score dropping below threshold
- Circuit breaker opening
- Failure rate spikes

Runs periodically to check system health and create incidents proactively.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session
from app.health_engine import HealthEngine
from app.incident_engine import IncidentEngine
from app.models import (
    Incident, IncidentState, Webhook, Destination,
    CircuitState, TrendState, FailureSeverity,
    DestinationReliabilitySnapshot, WeeklyInsightReport,
)

configure_logging = None
logger = logging.getLogger("relora.incident_scheduler")


class IncidentScheduler:
    """Periodically checks system health and creates incidents automatically."""

    # Thresholds for automatic incident creation
    DLQ_GROWTH_THRESHOLD_15M = 50  # events in 15 minutes
    DLQ_GROWTH_THRESHOLD_1H = 200   # events in 1 hour
    HEALTH_SCORE_THRESHOLD = 60      # health score below this triggers incident
    FAILURE_RATE_THRESHOLD = 10      # 10% failure rate triggers incident

    # Cooldown: don't re-fire the same error_rate alert more than once per hour
    _error_rate_alert_cooldown: dict  # config_id -> last_fired datetime

    def __init__(self):
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self._error_rate_alert_cooldown = {}
    
    async def start(self):
        """Start the incident scheduler."""
        if self.running:
            return
        
        self.running = True
        self.task = asyncio.create_task(self._run_scheduler())
        logger.info("Incident scheduler started")
    
    async def stop(self):
        """Stop the incident scheduler."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Incident scheduler stopped")
    
    async def _run_scheduler(self):
        """Main scheduler loop."""
        _tick = 0
        while self.running:
            try:
                await self._check_system_health()
                await self._auto_resolve_incidents()
                # Compute daily reliability snapshots once per hour (every 12th tick)
                if _tick % 12 == 0:
                    await self._compute_reliability_snapshots()
                # Generate weekly insight reports once per day on Mondays (every 288th tick = 24h)
                if _tick % 288 == 0:
                    await self._generate_weekly_insight_reports()
            except Exception as e:
                logger.error(f"Error in incident scheduler: {e}")
            _tick += 1
            # Run every 5 minutes
            await asyncio.sleep(300)
    
    async def _check_system_health(self):
        """Check system health and create incidents if thresholds are exceeded."""
        async with async_session() as db:
            # Check health score for all projects
            await self._check_health_scores(db)

            # Check DLQ growth rates
            await self._check_dlq_growth(db)

            # Check circuit breaker states
            await self._check_circuit_breakers(db)

            # Check failure rates
            await self._check_failure_rates(db)

            # Fire error_rate_threshold alerts
            await self._check_error_rate_alerts(db)
    
    async def _check_health_scores(self, db: AsyncSession):
        """Check health scores and create incidents if below threshold."""
        try:
            # Get all projects
            result = await db.execute(select(func.distinct(Webhook.project_id)).where(
                Webhook.project_id.isnot(None)
            ))
            project_ids = result.scalars().all()
            
            for project_id in project_ids:
                health_data = await HealthEngine.calculate_dlq_health_score(db, str(project_id))
                
                if health_data["overall_score"] < self.HEALTH_SCORE_THRESHOLD:
                    # Check if there's already an incident for low health
                    signature = f"low_health_{project_id}"
                    
                    existing = await db.execute(
                        select(Incident).where(
                            and_(
                                Incident.project_id == project_id,
                                Incident.incident_signature == signature,
                                Incident.state == IncidentState.OPEN.value,
                            )
                        )
                    )
                    existing_incident = existing.scalar_one_or_none()
                    
                    if not existing_incident:
                        # Create new incident for low health
                        incident = Incident(
                            id=UUID(),
                            project_id=project_id,
                            incident_signature=signature,
                            state=IncidentState.OPEN.value,
                            failure_category="SYSTEM_HEALTH",
                            failure_subcategory="LOW_HEALTH_SCORE",
                            root_cause=f"Overall health score dropped to {health_data['overall_score']}",
                            affected_webhook_count=health_data["components"]["dlq_size"]["value"],
                            first_seen_at=datetime.now(timezone.utc),
                            last_seen_at=datetime.now(timezone.utc),
                            trend_state=TrendState.STABLE.value,
                            severity=FailureSeverity.HIGH.value if health_data["overall_score"] < 40 else FailureSeverity.MEDIUM.value,
                            recoverability="manual",
                            recommended_action="Investigate DLQ size, growth rate, and failure diversity",
                            expected_recovery_difficulty="medium",
                        )
                        db.add(incident)
                        await db.commit()
                        logger.warning(f"Created incident for low health score in project {project_id}")
        
        except Exception as e:
            logger.error(f"Error checking health scores: {e}")
    
    async def _check_dlq_growth(self, db: AsyncSession):
        """Check DLQ growth rates and create incidents if thresholds exceeded."""
        try:
            now = datetime.now(timezone.utc)
            
            # Check 15-minute growth
            fifteen_min_ago = now - timedelta(minutes=15)
            result_15m = await db.execute(
                select(
                    Webhook.project_id,
                    func.count(Webhook.id).label("count"),
                ).where(
                    and_(
                        Webhook.status == "failed",
                        Webhook.created_at >= fifteen_min_ago,
                        Webhook.project_id.isnot(None),
                    )
                ).group_by(Webhook.project_id)
            )
            
            for row in result_15m:
                if row.count >= self.DLQ_GROWTH_THRESHOLD_15M:
                    signature = f"dlq_growth_15m_{row.project_id}"
                    
                    existing = await db.execute(
                        select(Incident).where(
                            and_(
                                Incident.project_id == row.project_id,
                                Incident.incident_signature == signature,
                                Incident.state == IncidentState.OPEN.value,
                            )
                        )
                    )
                    existing_incident = existing.scalar_one_or_none()
                    
                    if not existing_incident:
                        incident = Incident(
                            id=UUID(),
                            project_id=row.project_id,
                            incident_signature=signature,
                            state=IncidentState.OPEN.value,
                            failure_category="SYSTEM_HEALTH",
                            failure_subcategory="RAPID_DLQ_GROWTH",
                            root_cause=f"DLQ grew by {row.count} events in 15 minutes",
                            affected_webhook_count=row.count,
                            first_seen_at=datetime.now(timezone.utc),
                            last_seen_at=datetime.now(timezone.utc),
                            trend_state=TrendState.RAPID_GROWTH.value,
                            severity=FailureSeverity.CRITICAL.value,
                            recoverability="manual",
                            recommended_action="Investigate root cause immediately - system is experiencing rapid failure accumulation",
                            expected_recovery_difficulty="high",
                        )
                        db.add(incident)
                        await db.commit()
                        logger.warning(f"Created incident for rapid DLQ growth in project {row.project_id}")
        
        except Exception as e:
            logger.error(f"Error checking DLQ growth: {e}")
    
    async def _check_circuit_breakers(self, db: AsyncSession):
        """Check circuit breaker states and create incidents if any are open."""
        try:
            result = await db.execute(
                select(Destination).where(
                    Destination.circuit_state == CircuitState.OPEN.value
                )
            )
            open_destinations = result.scalars().all()
            
            for dest in open_destinations:
                signature = f"circuit_open_{dest.id}"
                
                existing = await db.execute(
                    select(Incident).where(
                        and_(
                            Incident.project_id == dest.project_id,
                            Incident.destination_id == dest.id,
                            Incident.incident_signature == signature,
                            Incident.state == IncidentState.OPEN.value,
                        )
                    )
                )
                existing_incident = existing.scalar_one_or_none()
                
                if not existing_incident:
                    incident = Incident(
                        id=UUID(),
                        project_id=dest.project_id,
                        destination_id=dest.id,
                        incident_signature=signature,
                        state=IncidentState.OPEN.value,
                        failure_category="CIRCUIT_BREAKER",
                        failure_subcategory="CIRCUIT_OPEN",
                        root_cause=f"Circuit breaker opened for destination {dest.name}",
                        affected_webhook_count=dest.circuit_failure_count,
                        first_seen_at=dest.circuit_opened_at or datetime.now(timezone.utc),
                        last_seen_at=datetime.now(timezone.utc),
                        trend_state=TrendState.STABLE.value,
                        severity=FailureSeverity.HIGH.value,
                        recoverability="automatic",
                        recommended_action="Wait for circuit to recover or investigate destination health",
                        expected_recovery_difficulty="low",
                    )
                    db.add(incident)
                    await db.commit()
                    logger.warning(f"Created incident for open circuit breaker on destination {dest.name}")
        
        except Exception as e:
            logger.error(f"Error checking circuit breakers: {e}")
    
    async def _check_failure_rates(self, db: AsyncSession):
        """Check failure rates per destination and create incidents if thresholds exceeded."""
        try:
            # Calculate failure rate per destination
            result = await db.execute(
                select(
                    Destination.id,
                    Destination.project_id,
                    Destination.name,
                    func.count(Webhook.id).label("total"),
                    func.sum(func.case((Webhook.status == "failed", 1), else_=0)).label("failed"),
                ).join(
                    Webhook, Destination.id == Webhook.destination_id
                ).where(
                    Webhook.destination_id.isnot(None)
                ).group_by(Destination.id, Destination.project_id, Destination.name)
            )
            
            for row in result:
                total = row.total or 0
                failed = row.failed or 0
                
                if total > 0:
                    failure_rate = (failed / total) * 100
                    
                    if failure_rate >= self.FAILURE_RATE_THRESHOLD:
                        signature = f"high_failure_rate_{row.id}"
                        
                        existing = await db.execute(
                            select(Incident).where(
                                and_(
                                    Incident.project_id == row.project_id,
                                    Incident.destination_id == row.id,
                                    Incident.incident_signature == signature,
                                    Incident.state == IncidentState.OPEN.value,
                                )
                            )
                        )
                        existing_incident = existing.scalar_one_or_none()
                        
                        if not existing_incident:
                            incident = Incident(
                                id=UUID(),
                                project_id=row.project_id,
                                destination_id=row.id,
                                incident_signature=signature,
                                state=IncidentState.OPEN.value,
                                failure_category="SYSTEM_HEALTH",
                                failure_subcategory="HIGH_FAILURE_RATE",
                                root_cause=f"Failure rate {failure_rate:.1f}% exceeds threshold for destination {row.name}",
                                affected_webhook_count=failed,
                                first_seen_at=datetime.now(timezone.utc),
                                last_seen_at=datetime.now(timezone.utc),
                                trend_state=TrendState.STABLE.value,
                                severity=FailureSeverity.HIGH.value,
                                recoverability="manual",
                                recommended_action="Investigate destination health and failure patterns",
                                expected_recovery_difficulty="medium",
                            )
                            db.add(incident)
                            await db.commit()
                            logger.warning(f"Created incident for high failure rate on destination {row.name}")
        
        except Exception as e:
            logger.error(f"Error checking failure rates: {e}")
    
    async def _check_error_rate_alerts(self, db: AsyncSession):
        """Fire alerts for tenants whose 24h success rate has dropped below their configured threshold."""
        from app.models import AlertConfig
        from app.alerts import _send_slack_alert, _send_email_alert, _send_sms_alert, _send_webhook_alert
        try:
            now = datetime.now(timezone.utc)
            cooldown_seconds = 3600  # 1 hour between repeated firings for the same config

            result = await db.execute(
                select(AlertConfig).where(
                    AlertConfig.enabled == True,
                    AlertConfig.error_rate_threshold.isnot(None),
                )
            )
            configs = result.scalars().all()
            if not configs:
                return

            # Group by tenant to avoid recalculating per-config
            tenant_rates: dict[str, float] = {}
            for config in configs:
                tid = config.tenant_id
                if tid not in tenant_rates:
                    row = await db.execute(
                        select(
                            func.count(Webhook.id).label("total"),
                            func.sum(
                                func.case((Webhook.status == "completed", 1), else_=0)
                            ).label("completed"),
                        ).where(
                            Webhook.tenant_id == tid,
                            Webhook.updated_at >= text("NOW() - INTERVAL '24 hours'"),
                            Webhook.status.in_(["completed", "failed"]),
                        )
                    )
                    r = row.fetchone()
                    total = r.total or 0
                    completed = r.completed or 0
                    tenant_rates[tid] = round(completed / total * 100, 2) if total > 0 else 100.0

                current_rate = tenant_rates[tid]
                threshold = float(config.error_rate_threshold)
                if current_rate >= threshold:
                    continue

                # Check cooldown
                last_fired = self._error_rate_alert_cooldown.get(str(config.id))
                if last_fired and (now - last_fired).total_seconds() < cooldown_seconds:
                    continue

                alert_data = {
                    "webhook_id": "—",
                    "event_id": "—",
                    "destination_url": "Multiple destinations",
                    "retry_count": 0,
                    "last_error": f"24h success rate {current_rate:.1f}% is below your threshold of {threshold:.1f}%",
                    "tenant_id": tid,
                }
                try:
                    if config.channel_type == "slack":
                        await _send_slack_alert(config, alert_data)
                    elif config.channel_type == "email":
                        await _send_email_alert(config, alert_data)
                    elif config.channel_type == "sms":
                        await _send_sms_alert(config, alert_data)
                    elif config.channel_type == "webhook":
                        await _send_webhook_alert(config, alert_data)
                    self._error_rate_alert_cooldown[str(config.id)] = now
                    logger.warning(
                        "Error rate alert fired for tenant %s: %.1f%% < %.1f%%",
                        tid, current_rate, threshold,
                    )
                except Exception as e:
                    logger.error("Error rate alert delivery failed for config %s: %s", config.id, e)
        except Exception as e:
            logger.error(f"Error checking error rate alerts: {e}")

    async def _auto_resolve_incidents(self):
        """Automatically resolve incidents that have recovered."""
        try:
            async with async_session() as db:
                await IncidentEngine.auto_resolve_incidents(db)
                logger.info("Auto-resolved recovered incidents")
        except Exception as e:
            logger.error(f"Error auto-resolving incidents: {e}")

    async def _compute_reliability_snapshots(self):
        """Compute yesterday's reliability snapshot for every active destination."""
        from sqlalchemy import text
        try:
            async with async_session() as db:
                result = await db.execute(select(Destination.id))
                dest_ids = result.scalars().all()
                today = datetime.now(timezone.utc).date()
                for dest_id in dest_ids:
                    # Skip if snapshot for today already exists
                    existing = await db.execute(
                        select(DestinationReliabilitySnapshot).where(
                            DestinationReliabilitySnapshot.destination_id == dest_id,
                            DestinationReliabilitySnapshot.date >= datetime.now(timezone.utc).replace(
                                hour=0, minute=0, second=0, microsecond=0
                            ),
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue
                    row = await db.execute(
                        text("""
                        SELECT
                            COUNT(w.id)                                           AS total,
                            COUNT(w.id) FILTER (WHERE w.status='completed')       AS successful,
                            COUNT(w.id) FILTER (WHERE w.status='failed')          AS failed,
                            AVG(da.duration_ms)                                   AS avg_latency,
                            PERCENTILE_CONT(0.95) WITHIN GROUP
                                (ORDER BY da.duration_ms)                         AS p95_latency
                        FROM webhooks w
                        LEFT JOIN delivery_attempts da ON da.webhook_id = w.id
                        WHERE w.destination_id = :did
                          AND w.updated_at >= NOW() - INTERVAL '24 hours'
                        """),
                        {"did": dest_id},
                    )
                    r = row.fetchone()
                    if not r or not r.total:
                        continue
                    total = r.total or 0
                    successful = r.successful or 0
                    db.add(DestinationReliabilitySnapshot(
                        destination_id=dest_id,
                        date=datetime.now(timezone.utc),
                        total_deliveries=total,
                        successful_deliveries=successful,
                        failed_deliveries=r.failed or 0,
                        avg_latency_ms=float(r.avg_latency) if r.avg_latency else None,
                        p95_latency_ms=float(r.p95_latency) if r.p95_latency else None,
                        success_rate=round(successful / total * 100, 2) if total else None,
                    ))
                await db.commit()
                logger.info("Reliability snapshots computed for %d destinations", len(dest_ids))
        except Exception as e:
            logger.error(f"Error computing reliability snapshots: {e}")


    async def _generate_weekly_insight_reports(self):
        """Generate a weekly insight report for every active project if none exists for this week."""
        from app.insights_engine import InsightsEngine, week_bounds
        from app.ai_intelligence import generate_insight_summary
        from sqlalchemy import text
        try:
            async with async_session() as db:
                ws, _ = week_bounds()
                # Get all unique tenant_ids that have activity
                result = await db.execute(
                    text("SELECT DISTINCT tenant_id FROM webhooks WHERE tenant_id IS NOT NULL AND tenant_id != 'anonymous'")
                )
                tenant_ids = [r[0] for r in result.fetchall()]

                for tid in tenant_ids:
                    # Skip if already generated for this week
                    existing = await db.execute(
                        select(WeeklyInsightReport).where(
                            WeeklyInsightReport.tenant_id == tid,
                            WeeklyInsightReport.week_start == ws,
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    try:
                        metrics = await InsightsEngine.generate_report(db, tid, ws)
                        ai_summary = await generate_insight_summary(metrics["report_data"])
                        report = WeeklyInsightReport(
                            tenant_id=tid,
                            week_start=metrics["week_start"],
                            week_end=metrics["week_end"],
                            grade=metrics["grade"],
                            reliability_score=metrics["reliability_score"],
                            score_delta=metrics["score_delta"],
                            report_data=metrics["report_data"],
                            ai_summary=ai_summary,
                        )
                        db.add(report)
                        await db.commit()
                        logger.info("Auto-generated weekly insight report for tenant %s", tid)
                    except Exception as e:
                        logger.error("Failed to generate insight report for tenant %s: %s", tid, e)
                        await db.rollback()
        except Exception as e:
            logger.error(f"Error generating weekly insight reports: {e}")


# Global scheduler instance
incident_scheduler = IncidentScheduler()


async def start_incident_scheduler():
    """Start the incident scheduler (called from main.py)."""
    await incident_scheduler.start()


async def stop_incident_scheduler():
    """Stop the incident scheduler (called from main.py)."""
    await incident_scheduler.stop()
