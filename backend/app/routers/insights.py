"""
Weekly Insight Reports API.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_intelligence import ask_insight_question, generate_insight_summary
from app.auth import get_tenant_from_auth
from app.db import get_db
from app.insights_engine import InsightsEngine, week_bounds
from app.models import WeeklyInsightReport

router = APIRouter(tags=["insights"])
logger = logging.getLogger("relora.insights_router")


# ---------------------------------------------------------------------------
# List all archived reports
# ---------------------------------------------------------------------------

@router.get("/api/v1/insights/reports")
async def list_reports(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WeeklyInsightReport)
        .where(WeeklyInsightReport.tenant_id == tenant_id)
        .order_by(desc(WeeklyInsightReport.week_start))
        .limit(52)
    )
    reports = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "week_start": r.week_start.isoformat(),
            "week_end": r.week_end.isoformat(),
            "grade": r.grade,
            "reliability_score": r.reliability_score,
            "score_delta": r.score_delta,
            "generated_at": r.generated_at.isoformat(),
        }
        for r in reports
    ]


# ---------------------------------------------------------------------------
# Current week's report (auto-generate if missing)
# ---------------------------------------------------------------------------

@router.get("/api/v1/insights/reports/current")
async def get_current_report(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    ws, _ = week_bounds()
    result = await db.execute(
        select(WeeklyInsightReport).where(
            WeeklyInsightReport.tenant_id == tenant_id,
            WeeklyInsightReport.week_start == ws,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        report = await _build_and_save(db, tenant_id, ws)
    return report.to_dict()


# ---------------------------------------------------------------------------
# Force-regenerate current week's report
# ---------------------------------------------------------------------------

@router.post("/api/v1/insights/generate")
async def generate_report(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    ws, _ = week_bounds()
    result = await db.execute(
        select(WeeklyInsightReport).where(
            WeeklyInsightReport.tenant_id == tenant_id,
            WeeklyInsightReport.week_start == ws,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()
    report = await _build_and_save(db, tenant_id, ws)
    return report.to_dict()


# ---------------------------------------------------------------------------
# Fetch a specific archived report
# ---------------------------------------------------------------------------

@router.get("/api/v1/insights/reports/{report_id}")
async def get_report(
    report_id: str,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    try:
        rid = UUID(report_id)
    except ValueError:
        raise HTTPException(400, "Invalid report ID")

    result = await db.execute(
        select(WeeklyInsightReport).where(
            WeeklyInsightReport.id == rid,
            WeeklyInsightReport.tenant_id == tenant_id,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")
    return report.to_dict()


# ---------------------------------------------------------------------------
# Ask a question about a report
# ---------------------------------------------------------------------------

@router.post("/api/v1/insights/reports/{report_id}/ask")
async def ask_about_report(
    report_id: str,
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    try:
        rid = UUID(report_id)
    except ValueError:
        raise HTTPException(400, "Invalid report ID")

    result = await db.execute(
        select(WeeklyInsightReport).where(
            WeeklyInsightReport.id == rid,
            WeeklyInsightReport.tenant_id == tenant_id,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")

    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(400, "messages array required")

    answer = await ask_insight_question(report.report_data, messages)
    return {"answer": answer}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

async def _build_and_save(db: AsyncSession, tenant_id: str, week_start: datetime) -> WeeklyInsightReport:
    metrics = await InsightsEngine.generate_report(db, tenant_id, week_start)
    ai_summary = await generate_insight_summary(metrics["report_data"])

    report = WeeklyInsightReport(
        tenant_id=tenant_id,
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
    await db.refresh(report)
    logger.info("Generated weekly insight report for tenant %s (week %s)", tenant_id, week_start.date())
    return report
