from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

class DeliveryAttemptResponse(BaseModel):
    id: UUID
    webhook_id: UUID
    attempt_number: int
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    attempted_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WebhookResponse(BaseModel):
    id: UUID
    tenant_id: str
    event_id: str
    destination_url: str
    idempotency_key: Optional[str] = None
    status: str
    retry_count: int
    max_retries: int
    next_attempt_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WebhookDetailResponse(WebhookResponse):
    payload: Any
    headers: Dict[str, str]
    attempts: List[DeliveryAttemptResponse] = []

    model_config = ConfigDict(from_attributes=True)

class DashboardStats(BaseModel):
    total_webhooks: int
    pending_count: int
    processing_count: int
    completed_count: int
    failed_count: int  # DLQ
    success_rate: float


class DestinationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=8)
    description: Optional[str] = None
    is_enabled: bool = True
    max_retries: int = Field(5, ge=0, le=100)
    backoff_base_seconds: int = Field(30, ge=1, le=86400)
    ordering_key_field: Optional[str] = None
    transform_type: str = Field("none", pattern="^(none|json_map|javascript)$")
    transform_code: Optional[str] = None
    transform_map: Optional[Dict[str, Any]] = None
    filter_expression: Optional[str] = Field(None, max_length=500)
    webhook_secret: Optional[str] = None
    custom_headers: Dict[str, str] = Field(default_factory=dict)


class DestinationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    url: Optional[str] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=0, le=100)
    backoff_base_seconds: Optional[int] = Field(None, ge=1, le=86400)
    ordering_key_field: Optional[str] = None
    transform_type: Optional[str] = Field(None, pattern="^(none|json_map|javascript)$")
    transform_code: Optional[str] = None
    transform_map: Optional[Dict[str, Any]] = None
    filter_expression: Optional[str] = Field(None, max_length=500)
    webhook_secret: Optional[str] = None
    custom_headers: Optional[Dict[str, str]] = None


class AlertConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    channel_type: str = Field(..., pattern="^(slack|email)$")
    config: Dict[str, Any]
    enabled: Optional[bool] = True


class AlertConfigUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class AlertConfigResponse(BaseModel):
    id: UUID
    tenant_id: str
    name: str
    channel_type: str
    config: Dict[str, Any]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

