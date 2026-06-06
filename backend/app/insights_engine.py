"""
Weekly Insight Report engine.
Collects a full week of reliability metrics and builds a structured, narrative report.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("relora.insights")


def week_bounds(reference: Optional[datetime] = None):
    """Return (week_start, week_end) for the ISO week containing `reference` (Monday-based)."""
    now = reference or datetime.now(timezone.utc)
    days_since_monday = now.weekday()
    week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=7)
    return week_start, week_end


def compute_grade(success_rate: float) -> str:
    if success_rate >= 99.5: return "A+"
    if success_rate >= 98.0: return "A"
    if success_rate >= 96.0: return "A-"
    if success_rate >= 94.0: return "B+"
    if success_rate >= 91.0: return "B"
    if success_rate >= 88.0: return "B-"
    if success_rate >= 84.0: return "C+"
    if success_rate >= 80.0: return "C"
    if success_rate >= 70.0: return "D"
    return "F"


class InsightsEngine:

    @staticmethod
    async def generate_report(
        db: AsyncSession,
        tenant_id: str,
        week_start: Optional[datetime] = None,
    ) -> dict:
        ws, we = week_bounds(week_start)
        prev_ws = ws - timedelta(days=7)
        now_utc = datetime.now(timezone.utc)

        # ── Delivery totals ──────────────────────────────────────────────────
        row = await db.execute(
            text("""
            SELECT
                COUNT(*)                                       AS total,
                COUNT(*) FILTER (WHERE status = 'completed')  AS completed,
                COUNT(*) FILTER (WHERE status = 'failed')     AS failed
            FROM webhooks
            WHERE tenant_id = :tid
              AND created_at >= :ws AND created_at < :we
            """),
            {"tid": tenant_id, "ws": ws, "we": we},
        )
        d = row.fetchone()
        total     = int(d.total     or 0)
        completed = int(d.completed or 0)
        failed    = int(d.failed    or 0)
        success_rate = round(completed / total * 100, 2) if total else 100.0

        # ── Previous week ────────────────────────────────────────────────────
        prev_row = await db.execute(
            text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                COUNT(*)                                      AS total
            FROM webhooks
            WHERE tenant_id = :tid
              AND created_at >= :pws AND created_at < :ws
            """),
            {"tid": tenant_id, "pws": prev_ws, "ws": ws},
        )
        p = prev_row.fetchone()
        prev_total        = int(p.total     or 0)
        prev_completed    = int(p.completed or 0)
        prev_success_rate = (
            round(prev_completed / prev_total * 100, 2) if prev_total else None
        )
        score_delta = (
            round(success_rate - prev_success_rate, 2)
            if prev_success_rate is not None else None
        )

        # ── P95 latency this week and last week ──────────────────────────────
        p95_row = await db.execute(
            text("""
            SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY da.duration_ms) AS p95
            FROM delivery_attempts da
            JOIN webhooks w ON w.id = da.webhook_id
            WHERE w.tenant_id = :tid
              AND da.attempted_at >= :ws AND da.attempted_at < :we
              AND da.duration_ms IS NOT NULL
            """),
            {"tid": tenant_id, "ws": ws, "we": we},
        )
        p95_ms = int(p95_row.scalar() or 0)

        prev_p95_row = await db.execute(
            text("""
            SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY da.duration_ms) AS p95
            FROM delivery_attempts da
            JOIN webhooks w ON w.id = da.webhook_id
            WHERE w.tenant_id = :tid
              AND da.attempted_at >= :pws AND da.attempted_at < :ws
              AND da.duration_ms IS NOT NULL
            """),
            {"tid": tenant_id, "pws": prev_ws, "ws": ws},
        )
        prev_p95_ms = int(prev_p95_row.scalar() or 0)

        # ── Per-destination stats ────────────────────────────────────────────
        dest_row = await db.execute(
            text("""
            SELECT
                d.name,
                d.url,
                COUNT(w.id)                                       AS total,
                COUNT(w.id) FILTER (WHERE w.status = 'completed') AS completed,
                COUNT(w.id) FILTER (WHERE w.status = 'failed')    AS failed,
                AVG(da.duration_ms)                               AS avg_latency,
                MODE() WITHIN GROUP (ORDER BY da.failure_category) AS top_failure_category
            FROM destinations d
            JOIN projects p ON p.id = d.project_id
            LEFT JOIN webhooks w ON w.destination_id = d.id
                AND w.created_at >= :ws AND w.created_at < :we
            LEFT JOIN delivery_attempts da ON da.webhook_id = w.id
            WHERE p.api_key = :key
            GROUP BY d.id, d.name, d.url
            ORDER BY total DESC NULLS LAST
            LIMIT 20
            """),
            {"key": tenant_id, "ws": ws, "we": we},
        )
        dest_stats = []
        for r in dest_row.fetchall():
            t  = int(r.total     or 0)
            c  = int(r.completed or 0)
            f  = int(r.failed    or 0)
            sr = round(c / t * 100, 1) if t else 100.0
            dest_stats.append({
                "name": r.name,
                "url":  r.url,
                "total": t,
                "successful": c,
                "failed":     f,
                "success_rate": sr,
                "avg_latency_ms": round(float(r.avg_latency)) if r.avg_latency else None,
                "top_failure_category": r.top_failure_category,
            })

        active_dests   = [d for d in dest_stats if d["total"] > 0]
        top_successes  = sorted(active_dests, key=lambda x: -x["success_rate"])[:3]
        biggest_issues = sorted(
            [d for d in active_dests if d["failed"] > 0],
            key=lambda x: x["success_rate"],
        )[:3]

        # ── Incidents this week ──────────────────────────────────────────────
        inc_row = await db.execute(
            text("""
            SELECT
                i.state, i.failure_category, i.severity,
                i.affected_webhook_count, i.first_seen_at, i.resolved_at,
                d.name AS dest_name
            FROM incidents i
            JOIN projects p ON p.id = i.project_id
            LEFT JOIN destinations d ON d.id = i.destination_id
            WHERE p.api_key = :key
              AND (
                (i.first_seen_at >= :ws AND i.first_seen_at < :we)
                OR (i.resolved_at >= :ws AND i.resolved_at < :we)
              )
            ORDER BY i.first_seen_at DESC
            LIMIT 20
            """),
            {"key": tenant_id, "ws": ws, "we": we},
        )
        inc_rows = inc_row.fetchall()
        incidents_opened   = sum(1 for r in inc_rows if r.first_seen_at and r.first_seen_at >= ws)
        incidents_resolved = sum(1 for r in inc_rows if r.resolved_at and r.resolved_at >= ws)
        critical_incidents = sum(1 for r in inc_rows if r.severity in ("critical", "high"))

        # ── Replay recovery ──────────────────────────────────────────────────
        replay_row = await db.execute(
            text("""
            SELECT
                COUNT(*)                          AS jobs,
                COALESCE(SUM(processed_count), 0) AS recovered
            FROM replay_jobs rj
            JOIN projects p ON p.id = rj.project_id
            WHERE p.api_key = :key
              AND rj.created_at >= :ws AND rj.created_at < :we
              AND rj.status = 'completed'
            """),
            {"key": tenant_id, "ws": ws, "we": we},
        )
        rr = replay_row.fetchone()
        replay_jobs      = int(rr.jobs      or 0)
        events_recovered = int(rr.recovered or 0)

        # Reliability rate without replay (what it would have been if no recovery)
        rate_without_replay = None
        if events_recovered > 0 and total > 0:
            recovered_without = completed - events_recovered
            rate_without_replay = round(max(0.0, recovered_without / total * 100), 2)

        # ── Schema drift ────────────────────────────────────────────────────
        drift_cnt = int(
            (await db.execute(
                text("SELECT COUNT(*) FROM schema_changes WHERE tenant_id=:tid AND detected_at>=:ws AND detected_at<:we"),
                {"tid": tenant_id, "ws": ws, "we": we},
            )).scalar() or 0
        )

        # ── New destinations ─────────────────────────────────────────────────
        new_dests_row = await db.execute(
            text("""
            SELECT d.name FROM destinations d
            JOIN projects p ON p.id = d.project_id
            WHERE p.api_key = :key AND d.created_at >= :ws AND d.created_at < :we
            LIMIT 5
            """),
            {"key": tenant_id, "ws": ws, "we": we},
        )
        new_dest_names = [r.name for r in new_dests_row.fetchall()]

        # ── Current healthy streak ────────────────────────────────────────────
        streak_row = await db.execute(
            text("""
            SELECT MAX(i.last_seen_at) AS last_critical
            FROM incidents i
            JOIN projects p ON p.id = i.project_id
            WHERE p.api_key = :key
              AND i.severity IN ('critical', 'high')
              AND i.last_seen_at < :ws
            """),
            {"key": tenant_id, "ws": ws},
        )
        last_critical = streak_row.scalar()
        if last_critical is None:
            streak_days  = None
            streak_label = "No critical incidents on record"
        else:
            streak_days = (ws - last_critical).days
            if streak_days <= 0:
                streak_label = "Critical incident resolved recently"
            elif streak_days == 1:
                streak_label = "1 day without a critical incident"
            else:
                streak_label = f"{streak_days} days without a critical incident"

        # ── Longest all-time healthy streak ──────────────────────────────────
        longest_days = await _compute_longest_streak(db, tenant_id, now_utc)

        # ── Best week ever (from archived reports) ───────────────────────────
        best_week = await _get_best_week(db, tenant_id, ws)

        # ── Enriched What Changed ────────────────────────────────────────────
        what_changed = _build_what_changed(
            score_delta=score_delta,
            prev_success_rate=prev_success_rate,
            success_rate=success_rate,
            p95_ms=p95_ms,
            prev_p95_ms=prev_p95_ms,
            inc_rows=inc_rows,
            ws=ws,
            incidents_resolved=incidents_resolved,
            events_recovered=events_recovered,
            rate_without_replay=rate_without_replay,
            drift_cnt=drift_cnt,
            new_dest_names=new_dest_names,
        )

        # ── Deterministic recommendations ────────────────────────────────────
        recommendations = _build_recommendations(
            score_delta=score_delta,
            success_rate=success_rate,
            biggest_issues=biggest_issues,
            incidents_opened=incidents_opened,
            incidents_resolved=incidents_resolved,
            events_recovered=events_recovered,
            streak_days=streak_days,
        )

        report_data = {
            "week_start": ws.isoformat(),
            "week_end":   we.isoformat(),
            "overview": {
                "total_deliveries":      total,
                "successful_deliveries": completed,
                "failed_deliveries":     failed,
                "success_rate":          success_rate,
                "prev_week_success_rate": prev_success_rate,
                "p95_latency_ms":        p95_ms if p95_ms else None,
                "prev_p95_latency_ms":   prev_p95_ms if prev_p95_ms else None,
            },
            "incidents": {
                "opened":   incidents_opened,
                "resolved": incidents_resolved,
                "critical": critical_incidents,
            },
            "streaks": {
                "current_days":  streak_days,
                "streak_label":  streak_label,
                "longest_days":  longest_days,
                "best_week":     best_week,
            },
            "what_changed": what_changed,
            "replay": {
                "jobs":               replay_jobs,
                "events_recovered":   events_recovered,
                "rate_without_replay": rate_without_replay,
            },
            "destinations": {
                "top_successes":  top_successes,
                "biggest_issues": biggest_issues,
            },
            "recommendations": recommendations,
        }

        return {
            "week_start":        ws,
            "week_end":          we,
            "grade":             compute_grade(success_rate),
            "reliability_score": success_rate,
            "score_delta":       score_delta,
            "report_data":       report_data,
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _compute_longest_streak(db: AsyncSession, tenant_id: str, now_utc: datetime) -> Optional[int]:
    """Find the longest all-time gap between critical/high incidents (days)."""
    try:
        inc_history = await db.execute(
            text("""
            SELECT i.first_seen_at,
                   COALESCE(i.resolved_at, NOW()) AS resolved_at
            FROM incidents i
            JOIN projects p ON p.id = i.project_id
            WHERE p.api_key = :key
              AND i.severity IN ('critical', 'high')
            ORDER BY i.first_seen_at ASC
            """),
            {"key": tenant_id},
        )
        incidents = inc_history.fetchall()
        if not incidents:
            return None

        start_row = await db.execute(
            text("SELECT MIN(created_at) FROM webhooks WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        project_start = start_row.scalar()
        if not project_start:
            return None

        gaps = []
        first_gap = (incidents[0].first_seen_at - project_start).days
        if first_gap > 0:
            gaps.append(first_gap)

        for i in range(1, len(incidents)):
            gap = (incidents[i].first_seen_at - incidents[i - 1].resolved_at).days
            if gap > 0:
                gaps.append(gap)

        trailing = (now_utc - incidents[-1].resolved_at).days
        if trailing > 0:
            gaps.append(trailing)

        return max(gaps) if gaps else 0
    except Exception:
        return None


async def _get_best_week(db: AsyncSession, tenant_id: str, exclude_ws: datetime) -> Optional[dict]:
    """Return the best-ever week (score, week_start) from archived reports."""
    try:
        row = await db.execute(
            text("""
            SELECT reliability_score, week_start
            FROM weekly_insight_reports
            WHERE tenant_id = :tid
            ORDER BY reliability_score DESC
            LIMIT 1
            """),
            {"tid": tenant_id},
        )
        best = row.fetchone()
        if not best:
            return None
        ws_dt = best.week_start
        we_dt = ws_dt + timedelta(days=6)
        label = f"{ws_dt.strftime('%b %-d')} – {we_dt.strftime('%b %-d, %Y')}"
        return {"score": float(best.reliability_score), "week_start": ws_dt.isoformat(), "week_label": label}
    except Exception:
        return None


def _build_what_changed(
    *,
    score_delta,
    prev_success_rate,
    success_rate,
    p95_ms,
    prev_p95_ms,
    inc_rows,
    ws,
    incidents_resolved,
    events_recovered,
    rate_without_replay,
    drift_cnt,
    new_dest_names,
) -> list:
    items = []

    # Reliability shift — headline + explanation
    if score_delta is not None and abs(score_delta) >= 0.5:
        direction  = "improved" if score_delta > 0 else "dropped"
        impact     = "positive" if score_delta > 0 else "negative"
        severity   = "significantly " if abs(score_delta) >= 3 else ""
        headline   = f"Reliability {severity}{direction} {abs(score_delta):.1f}% this week"
        prev_str   = f"{prev_success_rate:.1f}%" if prev_success_rate is not None else "last week"
        explanation = (
            f"Success rate moved from {prev_str} to {success_rate:.1f}%. "
            + (
                f"This improvement brings the project to its best score in recent weeks."
                if score_delta > 0 else
                f"This decline is worth investigating before it becomes a persistent trend."
            )
        )
        items.append({"type": "reliability_shift", "impact": impact,
                      "headline": headline, "explanation": explanation})

    # Latency shift (only if notable — >20% change)
    if p95_ms and prev_p95_ms and prev_p95_ms > 0:
        latency_delta_pct = (p95_ms - prev_p95_ms) / prev_p95_ms * 100
        if abs(latency_delta_pct) >= 20:
            direction = "increased" if latency_delta_pct > 0 else "decreased"
            impact    = "negative" if latency_delta_pct > 0 else "positive"
            items.append({
                "type": "latency_shift", "impact": impact,
                "headline": f"P95 delivery latency {direction} {abs(latency_delta_pct):.0f}%",
                "explanation": f"P95 latency went from {prev_p95_ms}ms to {p95_ms}ms. "
                               + ("Slower deliveries can indicate destination or network issues."
                                  if latency_delta_pct > 0 else
                                  "Faster deliveries indicate improved destination or network performance."),
            })

    # Incidents opened this week
    for r in inc_rows[:4]:
        if r.first_seen_at and r.first_seen_at >= ws:
            dest_part    = f" on {r.dest_name}" if r.dest_name else ""
            cat_display  = (r.failure_category or "Unknown").replace("_", " ").title()
            severity_disp = (r.severity or "unknown").lower()
            impact = "negative" if r.severity in ("critical", "high") else "neutral"
            headline = f"{cat_display} incident opened{dest_part}"
            explanation = (
                f"A {severity_disp}-severity {cat_display.lower()} incident "
                f"affecting {r.affected_webhook_count} webhooks"
                + (f" on {r.dest_name}" if r.dest_name else "")
                + " was detected this week."
            )
            items.append({"type": "incident", "impact": impact,
                          "headline": headline, "explanation": explanation})

    # Incidents resolved
    if incidents_resolved > 0:
        pl = "s" if incidents_resolved > 1 else ""
        items.append({
            "type": "resolved", "impact": "positive",
            "headline": f"{incidents_resolved} incident{pl} resolved this week",
            "explanation": f"The team closed {incidents_resolved} open incident{pl}. "
                           "Resolved incidents stop accumulating affected deliveries.",
        })

    # Replay recovery
    if events_recovered > 0:
        saved = ""
        if rate_without_replay is not None:
            diff = round(success_rate - rate_without_replay, 1)
            saved = f" Without replay, reliability would have been {rate_without_replay:.1f}% — {diff} points lower."
        items.append({
            "type": "replay", "impact": "positive",
            "headline": f"{events_recovered:,} failed deliveries recovered via replay",
            "explanation": f"Replay jobs successfully re-delivered {events_recovered:,} events that had previously failed.{saved}",
        })

    # Schema drift
    if drift_cnt > 0:
        pl = "s" if drift_cnt > 1 else ""
        items.append({
            "type": "schema_drift", "impact": "neutral",
            "headline": f"{drift_cnt} schema drift event{pl} detected",
            "explanation": f"The payload structure changed {drift_cnt} time{pl} this week. "
                           "Schema drift can indicate breaking changes from upstream providers.",
        })

    # New destinations
    if new_dest_names:
        n = len(new_dest_names)
        pl = "s" if n > 1 else ""
        names_str = ", ".join(new_dest_names[:2]) + ("…" if n > 2 else "")
        items.append({
            "type": "new_destination", "impact": "neutral",
            "headline": f"{n} new destination{pl} added — {names_str}",
            "explanation": f"{n} new delivery endpoint{pl} configured this week. "
                           "Monitor initial reliability closely as traffic ramps up.",
        })

    if not items:
        items.append({
            "type": "stable", "impact": "positive",
            "headline": "Stable week — no significant changes",
            "explanation": "No reliability shifts, incidents, or notable events detected. "
                           "Consistency like this is a strong signal of a healthy system.",
        })

    return items


def _build_recommendations(
    *,
    score_delta,
    success_rate,
    biggest_issues,
    incidents_opened,
    incidents_resolved,
    events_recovered,
    streak_days,
) -> list:
    recs = []

    # Critical reliability drop
    if score_delta is not None and score_delta < -5:
        recs.append({
            "priority": "high",
            "title": "Investigate the reliability drop urgently",
            "body": f"Reliability dropped {abs(score_delta):.1f}% this week. Identify the root cause before this becomes a persistent trend — check circuit breakers, DLQ depth, and recent deployment changes.",
        })

    # Destinations needing attention
    for d in biggest_issues[:2]:
        if d["success_rate"] < 85:
            cat = d.get("top_failure_category") or "failures"
            cat_display = cat.replace("_", " ").title() if cat else "delivery failures"
            recs.append({
                "priority": "high" if d["success_rate"] < 70 else "medium",
                "title": f"Investigate {d['name']} ({d['success_rate']:.0f}% success rate)",
                "body": f"This destination logged {d['failed']} failures this week, primarily {cat_display.lower()}. Review endpoint health, credentials, and retry configuration.",
            })

    # Open incidents accumulating
    unresolved = incidents_opened - incidents_resolved
    if unresolved > 0:
        recs.append({
            "priority": "medium",
            "title": f"Close {unresolved} unresolved incident{'s' if unresolved > 1 else ''}",
            "body": "Open incidents can mask new problems as they accumulate. Review each one, document findings, and close or escalate.",
        })

    # High replay dependency
    if events_recovered > 100:
        recs.append({
            "priority": "medium",
            "title": "Reduce replay dependency",
            "body": f"{events_recovered:,} deliveries required replay recovery this week. High replay volume signals persistent delivery failures — address root causes rather than relying on replay as a safety net.",
        })

    # Celebrate and sustain a strong streak
    if streak_days is not None and streak_days >= 7 and success_rate >= 95 and not recs:
        recs.append({
            "priority": "low",
            "title": f"Sustain the {streak_days}-day streak",
            "body": "Your reliability is strong. Keep it up by reviewing alert thresholds, monitoring destination health trends, and scheduling a proactive review of any circuit breakers approaching their failure threshold.",
        })

    return recs[:3]
