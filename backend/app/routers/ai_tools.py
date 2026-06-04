from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

from app.ai_intelligence import analyze_payload, suggest_filter, suggest_transform
from app.auth import get_tenant_from_auth
from app.config import settings

router = APIRouter()


@router.post("/api/v1/ai/analyze-payload")
async def ai_analyze(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled. Set ENABLE_AI_FEATURES=true and ANTHROPIC_API_KEY.")
    payload = body.get("payload", body)
    result = await analyze_payload(payload)
    return result


@router.post("/api/v1/ai/suggest-filter")
async def ai_suggest_filter(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled")
    expr = await suggest_filter(body.get("description", ""), body.get("sample_payload", {}))
    return {"expression": expr}


@router.post("/api/v1/ai/suggest-transform")
async def ai_suggest_transform(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled")
    code = await suggest_transform(body.get("description", ""), body.get("sample_payload", {}))
    return {"transform_code": code}
