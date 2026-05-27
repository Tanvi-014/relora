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
