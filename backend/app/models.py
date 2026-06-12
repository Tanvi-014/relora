import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey,
    Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _now():
    return datetime.now(timezone.utc)


class WebhookStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FailureCategory(str, enum.Enum):
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    RATE_LIMITING = "RATE_LIMITING"
    CLIENT_ERROR = "CLIENT_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    DNS = "DNS"
    SSL = "SSL"
    TRANSFORM = "TRANSFORM"
    FILTER = "FILTER"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    CONFIGURATION = "CONFIGURATION"
    UNKNOWN = "UNKNOWN"


class FailureSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FailureRecoverability(str, enum.Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
    UNLIKELY = "unlikely"


class IncidentState(str, enum.Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"


class TrendState(str, enum.Enum):
    STABLE = "STABLE"
    SLOW_GROWTH = "SLOW_GROWTH"
    MODERATE_GROWTH = "MODERATE_GROWTH"
    RAPID_GROWTH = "RAPID_GROWTH"
    EXPLOSIVE_GROWTH = "EXPLOSIVE_GROWTH"


class DestinationHealth(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Core webhook tables
# ---------------------------------------------------------------------------

class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False, default="anonymous", index=True)
    event_id = Column(String, nullable=False, default=lambda: str(uuid.uuid4()))
    destination_url = Column(String, nullable=False)
    destination_id = Column(UUID(as_uuid=True), ForeignKey("destinations.id", ondelete="SET NULL"), nullable=True)
    payload = Column(JSONB, nullable=False)
    headers = Column(JSONB, nullable=False)
    idempotency_key = Column(String, nullable=True)
    ordering_key = Column(String, nullable=True)
    status = Column(String, nullable=False, default=WebhookStatus.PENDING.value)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=5)
    is_simulation = Column(Boolean, nullable=False, default=False)

    # Consumer polling fields
    consumer_id = Column(String, nullable=True)
    poll_ack_token = Column(String, nullable=True)

    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    attempts = relationship(
        "DeliveryAttempt",
        back_populates="webhook",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    destination = relationship("Destination", back_populates="webhooks", lazy="select")

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": self.tenant_id,
            "event_id": self.event_id,
            "destination_url": self.destination_url,
            "destination_id": str(self.destination_id) if self.destination_id else None,
            "idempotency_key": self.idempotency_key,
            "ordering_key": self.ordering_key,
            "status": self.status,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "is_simulation": self.is_simulation,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    __table_args__ = (
        Index("ix_webhooks_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_webhooks_created_at", "created_at"),
        Index("ix_webhooks_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_webhooks_event_id", "event_id"),
        Index("ix_webhooks_ordering_key", "ordering_key"),
        # Compound index for the CLAIM_QUERY NOT EXISTS subquery:
        # filters on ordering_key = ? AND status = 'processing' AND updated_at >= ?
        Index(
            "ix_webhooks_ordering_key_status",
            "ordering_key", "status",
            postgresql_where="ordering_key IS NOT NULL",
        ),
        Index(
            "ix_webhooks_tenant_destination_idempotency_key",
            "tenant_id", "destination_url", "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="chk_webhook_status",
        ),
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    response_headers = Column(JSONB, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_strategy_used = Column(String, nullable=True)
    attempted_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    # Failure classification fields
    failure_category = Column(String, nullable=True)
    failure_subcategory = Column(String, nullable=True)
    failure_severity = Column(String, nullable=True)
    failure_recoverability = Column(String, nullable=True)
    error_signature = Column(String, nullable=True)

    # Response body is zlib-compressed when > 1 KB; this flag tells readers to decompress
    response_body_compressed = Column(Boolean, nullable=False, default=False)

    webhook = relationship("Webhook", back_populates="attempts")

    def to_dict(self):
        return {
            "id": str(self.id),
            "webhook_id": str(self.webhook_id),
            "attempt_number": self.attempt_number,
            "status_code": self.status_code,
            "response_body": self.response_body,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "retry_strategy_used": self.retry_strategy_used,
            "attempted_at": self.attempted_at.isoformat() if self.attempted_at else None,
            "failure_category": self.failure_category,
            "failure_subcategory": self.failure_subcategory,
            "failure_severity": self.failure_severity,
            "failure_recoverability": self.failure_recoverability,
            "error_signature": self.error_signature,
        }

    __table_args__ = (
        Index("ix_delivery_attempts_webhook_id_attempt_number", "webhook_id", "attempt_number"),
        Index("ix_delivery_attempts_failure_category", "failure_category"),
        Index("ix_delivery_attempts_error_signature", "error_signature"),
        Index("ix_delivery_attempts_attempted_at", "attempted_at"),
    )


# ---------------------------------------------------------------------------
# Destinations registry
# ---------------------------------------------------------------------------

class Destination(Base):
    __tablename__ = "destinations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    max_retries = Column(Integer, nullable=False, default=5)
    backoff_base_seconds = Column(Integer, nullable=False, default=30)
    ordering_key_field = Column(String(255), nullable=True)
    transform_type = Column(String(20), nullable=False, default="none")  # none | json_map | javascript
    transform_code = Column(Text, nullable=True)
    transform_map = Column(JSONB, nullable=True)
    filter_expression = Column(Text, nullable=True)
    webhook_secret = Column(Text, nullable=True)
    custom_headers = Column(JSONB, nullable=False, default=dict)

    # Circuit breaker
    circuit_state = Column(String(20), nullable=False, default=CircuitState.CLOSED.value)
    circuit_failure_count = Column(Integer, nullable=False, default=0)
    circuit_opened_at = Column(DateTime(timezone=True), nullable=True)
    circuit_next_retry_at = Column(DateTime(timezone=True), nullable=True)

    # Sandbox flag — auto-created for new accounts, used in onboarding
    is_sandbox = Column(Boolean, nullable=False, default=False, server_default="false")

    # SLO
    slo_target_pct = Column(Float, nullable=True)          # e.g. 99.5 means 99.5% success rate
    slo_window_minutes = Column(Integer, nullable=False, default=60)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    project = relationship("Project", back_populates="destinations")
    webhooks = relationship("Webhook", back_populates="destination", lazy="select")

    def to_dict(self):
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "name": self.name,
            "url": self.url,
            "description": self.description,
            "is_enabled": self.is_enabled,
            "max_retries": self.max_retries,
            "backoff_base_seconds": self.backoff_base_seconds,
            "ordering_key_field": self.ordering_key_field,
            "transform_type": self.transform_type,
            "filter_expression": self.filter_expression,
            "custom_headers": self.custom_headers or {},
            "circuit_state": self.circuit_state,
            "circuit_failure_count": self.circuit_failure_count,
            "circuit_opened_at": self.circuit_opened_at.isoformat() if self.circuit_opened_at else None,
            "circuit_next_retry_at": self.circuit_next_retry_at.isoformat() if self.circuit_next_retry_at else None,
            "is_sandbox": self.is_sandbox,
            "slo_target_pct": self.slo_target_pct,
            "slo_window_minutes": self.slo_window_minutes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_destinations_project_name"),
        Index("ix_destinations_project_id", "project_id"),
    )


# ---------------------------------------------------------------------------
# Alert configuration
# ---------------------------------------------------------------------------

class AlertConfig(Base):
    __tablename__ = "alert_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False, default="anonymous")
    name = Column(String, nullable=False)
    channel_type = Column(String, nullable=False)  # slack | email
    config = Column(JSONB, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    dlq_threshold = Column(Integer, nullable=True)        # fire when DLQ depth >= this
    error_rate_threshold = Column(Numeric(5, 2), nullable=True)  # fire when success rate < this %
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    def to_dict(self):
        safe_config = {**self.config} if self.config else {}
        for k in ("password", "smtp_password"):
            if k in safe_config:
                safe_config[k] = "••••••••"
        if "webhook_url" in safe_config and len(safe_config["webhook_url"]) > 20:
            safe_config["webhook_url"] = "…" + safe_config["webhook_url"][-20:]
        return {
            "id": str(self.id),
            "tenant_id": self.tenant_id,
            "name": self.name,
            "channel_type": self.channel_type,
            "config": safe_config,
            "enabled": self.enabled,
            "dlq_threshold": self.dlq_threshold,
            "error_rate_threshold": float(self.error_rate_threshold) if self.error_rate_threshold is not None else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    __table_args__ = (
        Index("ix_alert_configs_tenant_id", "tenant_id"),
    )


# ---------------------------------------------------------------------------
# SaaS user / project / team models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    email_verified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    memberships = relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "email": self.email,
            "email_verified": self.email_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_email_verification_tokens_token", "token"),
        Index("ix_email_verification_tokens_user_id", "user_id"),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_password_reset_tokens_token", "token"),
        Index("ix_password_reset_tokens_user_id", "user_id"),
    )


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    api_key = Column(String, unique=True, nullable=False, default=lambda: f"hk_live_{uuid.uuid4().hex}")
    source_secrets = Column(JSONB, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    destinations = relationship("Destination", back_populates="project", cascade="all, delete-orphan")
    event_types = relationship("EventType", back_populates="project", cascade="all, delete-orphan")
    replay_jobs = relationship("ReplayJob", back_populates="project", cascade="all, delete-orphan")

    def to_dict(self, *, include_api_key: bool = False):
        d = {
            "id": str(self.id),
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_api_key:
            d["api_key"] = self.api_key
        return d


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String, nullable=False, default="viewer")  # owner | admin | viewer
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    user = relationship("User", back_populates="memberships")
    project = relationship("Project", back_populates="members")

    def to_dict(self):
        return {
            "project_id": str(self.project_id),
            "user_id": str(self.user_id),
            "role": self.role,
            "email": self.user.email if self.user else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Rate limit buckets (Postgres token bucket — multi-process safe)
# ---------------------------------------------------------------------------

class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key = Column(String(255), primary_key=True)
    tokens = Column(Float, nullable=False, default=60.0)
    last_refill = Column(DateTime(timezone=True), nullable=False, default=_now)
    max_tokens = Column(Float, nullable=False, default=60.0)
    refill_rate = Column(Float, nullable=False, default=1.0)  # tokens/second


# ---------------------------------------------------------------------------
# Event type catalog
# ---------------------------------------------------------------------------

class EventType(Base):
    __tablename__ = "event_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    schema = Column(JSONB, nullable=True)
    example_payload = Column(JSONB, nullable=True)
    version = Column(String(50), nullable=False, default="1")
    deprecated = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    project = relationship("Project", back_populates="event_types")

    def to_dict(self):
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "name": self.name,
            "description": self.description,
            "schema": self.schema,
            "example_payload": self.example_payload,
            "version": self.version,
            "deprecated": self.deprecated,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="uq_event_types_project_name_version"),
        Index("ix_event_types_project_id", "project_id"),
    )


# ---------------------------------------------------------------------------
# Bulk replay jobs
# ---------------------------------------------------------------------------

class ReplayJob(Base):
    __tablename__ = "replay_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    from_time = Column(DateTime(timezone=True), nullable=False)
    to_time = Column(DateTime(timezone=True), nullable=False)
    destination_id = Column(UUID(as_uuid=True), nullable=True)
    replay_rate_per_minute = Column(Integer, nullable=False, default=100)
    status = Column(String(20), nullable=False, default="pending")  # pending | running | completed | failed
    total_count = Column(Integer, nullable=False, default=0)
    processed_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    project = relationship("Project", back_populates="replay_jobs")

    def to_dict(self):
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "from_time": self.from_time.isoformat() if self.from_time else None,
            "to_time": self.to_time.isoformat() if self.to_time else None,
            "destination_id": str(self.destination_id) if self.destination_id else None,
            "replay_rate_per_minute": self.replay_rate_per_minute,
            "status": self.status,
            "total_count": self.total_count,
            "processed_count": self.processed_count,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# DLQ Intelligence - Incidents
# ---------------------------------------------------------------------------

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    destination_id = Column(UUID(as_uuid=True), ForeignKey("destinations.id", ondelete="SET NULL"), nullable=True)
    
    # Incident identification
    incident_signature = Column(String, nullable=False, index=True)  # Unique signature for grouping
    state = Column(String, nullable=False, default=IncidentState.OPEN.value)
    
    # Root cause information
    failure_category = Column(String, nullable=True)
    failure_subcategory = Column(String, nullable=True)
    root_cause = Column(Text, nullable=True)
    
    # Metrics
    affected_webhook_count = Column(Integer, nullable=False, default=0)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    
    # Trend analysis
    trend_state = Column(String, nullable=True)
    growth_rate_15m = Column(Integer, nullable=False, default=0)
    growth_rate_1h = Column(Integer, nullable=False, default=0)
    growth_rate_6h = Column(Integer, nullable=False, default=0)
    growth_rate_24h = Column(Integer, nullable=False, default=0)
    
    # Severity and recoverability
    severity = Column(String, nullable=True)
    recoverability = Column(String, nullable=True)
    
    # Recommendations
    recommended_action = Column(Text, nullable=True)
    expected_recovery_difficulty = Column(String, nullable=True)
    
    # Resolution
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    project = relationship("Project")
    destination = relationship("Destination")

    def to_dict(self):
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "destination_id": str(self.destination_id) if self.destination_id else None,
            "incident_signature": self.incident_signature,
            "state": self.state,
            "failure_category": self.failure_category,
            "failure_subcategory": self.failure_subcategory,
            "root_cause": self.root_cause,
            "affected_webhook_count": self.affected_webhook_count,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "trend_state": self.trend_state,
            "growth_rate_15m": self.growth_rate_15m,
            "growth_rate_1h": self.growth_rate_1h,
            "growth_rate_6h": self.growth_rate_6h,
            "growth_rate_24h": self.growth_rate_24h,
            "severity": self.severity,
            "recoverability": self.recoverability,
            "recommended_action": self.recommended_action,
            "expected_recovery_difficulty": self.expected_recovery_difficulty,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_notes": self.resolution_notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    __table_args__ = (
        Index("ix_incidents_project_id", "project_id"),
        Index("ix_incidents_destination_id", "destination_id"),
        Index("ix_incidents_state", "state"),
        Index("ix_incidents_first_seen_at", "first_seen_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    action = Column(String(32), nullable=False)        # CREATE UPDATE DELETE REPLAY
    resource_type = Column(String(64), nullable=False) # destination webhook alert_config project
    resource_id = Column(String, nullable=True)
    changes = Column(JSONB, nullable=True)             # {"before": {...}, "after": {...}}
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Schema drift detection
# ---------------------------------------------------------------------------

class SchemaFingerprint(Base):
    __tablename__ = "schema_fingerprints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False)
    source_key = Column(String(128), nullable=False)   # e.g. "stripe", event_type name
    fingerprint = Column(String(64), nullable=False)   # SHA-256 hex of sorted key paths
    key_structure = Column(JSONB, nullable=False)       # sorted list of dotted key paths
    sample_payload = Column(JSONB, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    event_count = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_schema_fingerprints_tenant_source", "tenant_id", "source_key", unique=True),
    )


class SchemaChange(Base):
    __tablename__ = "schema_changes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False)
    source_key = Column(String(128), nullable=False)
    old_fingerprint = Column(String(64), nullable=True)
    new_fingerprint = Column(String(64), nullable=False)
    added_keys = Column(JSONB, nullable=True)
    removed_keys = Column(JSONB, nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_schema_changes_tenant_detected", "tenant_id", "detected_at"),
    )


# ---------------------------------------------------------------------------
# Destination reliability snapshots
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Weekly Insight Reports
# ---------------------------------------------------------------------------

class WeeklyInsightReport(Base):
    __tablename__ = "weekly_insight_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False)
    week_start = Column(DateTime(timezone=True), nullable=False)
    week_end = Column(DateTime(timezone=True), nullable=False)
    grade = Column(String(4), nullable=False)
    reliability_score = Column(Float, nullable=False)
    score_delta = Column(Float, nullable=True)               # vs previous week
    report_data = Column(JSONB, nullable=False, default=dict)
    ai_summary = Column(Text, nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": self.tenant_id,
            "week_start": self.week_start.isoformat() if self.week_start else None,
            "week_end": self.week_end.isoformat() if self.week_end else None,
            "grade": self.grade,
            "reliability_score": self.reliability_score,
            "score_delta": self.score_delta,
            "report_data": self.report_data,
            "ai_summary": self.ai_summary,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }

    __table_args__ = (
        UniqueConstraint("tenant_id", "week_start", name="uq_weekly_report_tenant_week"),
        Index("ix_weekly_insight_reports_tenant_id", "tenant_id"),
    )


class DestinationReliabilitySnapshot(Base):
    __tablename__ = "destination_reliability_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    destination_id = Column(UUID(as_uuid=True), ForeignKey("destinations.id", ondelete="CASCADE"), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)   # date of this snapshot (truncated to day)
    total_deliveries = Column(Integer, nullable=False, default=0)
    successful_deliveries = Column(Integer, nullable=False, default=0)
    failed_deliveries = Column(Integer, nullable=False, default=0)
    avg_latency_ms = Column(Float, nullable=True)
    p95_latency_ms = Column(Float, nullable=True)
    success_rate = Column(Float, nullable=True)
    computed_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_reliability_snapshots_dest_date", "destination_id", "date", unique=True),
    )
