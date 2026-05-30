"""
Claude-powered payload analysis for schema detection, filter suggestions,
and natural-language → filter expression conversion.
"""
import json
import logging
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger("hermes.ai")

_SYSTEM_ANALYZE = """You are a webhook payload analyzer. Analyze the provided JSON payload and return ONLY valid JSON with no other text, using this exact schema:
{
  "provider": "stripe|github|shopify|twilio|custom|unknown",
  "event_type": "string",
  "confidence": 0.0,
  "filter_suggestions": [
    {"expression": "event.type == 'payment.succeeded'", "description": "Human-readable description"}
  ],
  "field_mapping_suggestions": [
    {"from": "data.object.id", "to": "id", "description": "Primary identifier"}
  ],
  "schema_summary": "Brief description of what this event represents",
  "key_fields": ["list", "of", "important", "field.paths"]
}"""

_SYSTEM_FILTER = (
    "Convert the user's natural-language description into a webhook filter expression. "
    "Return ONLY the expression string, nothing else. Use dot notation for nested fields. "
    "Supported operators: ==, !=, >, <, >=, <="
)

_SYSTEM_TRANSFORM = (
    "Generate a JavaScript transform function that reshapes the webhook payload. "
    "Return ONLY the function body (no 'function' keyword, no outer braces). "
    "The function receives `payload` and must return a new object. "
    "Example: return { id: payload.data.object.id, amount: payload.data.object.amount };"
)


async def analyze_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.ENABLE_AI_FEATURES or not settings.ANTHROPIC_API_KEY:
        return _empty_analysis()
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=_SYSTEM_ANALYZE,
            messages=[{
                "role": "user",
                "content": f"Analyze this webhook payload:\n{json.dumps(payload, indent=2)}"
            }],
        )
        return json.loads(response.content[0].text)
    except Exception as exc:
        logger.warning("AI analyze_payload failed: %s", exc)
        return _empty_analysis()


async def suggest_filter(description: str, sample_payload: Dict[str, Any]) -> str:
    if not settings.ENABLE_AI_FEATURES or not settings.ANTHROPIC_API_KEY:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=_SYSTEM_FILTER,
            messages=[{
                "role": "user",
                "content": (
                    f"Payload structure:\n{json.dumps(sample_payload, indent=2)}\n\n"
                    f"Filter description: {description}"
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("AI suggest_filter failed: %s", exc)
        return ""


async def suggest_transform(description: str, sample_payload: Dict[str, Any]) -> str:
    if not settings.ENABLE_AI_FEATURES or not settings.ANTHROPIC_API_KEY:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=_SYSTEM_TRANSFORM,
            messages=[{
                "role": "user",
                "content": (
                    f"Sample payload:\n{json.dumps(sample_payload, indent=2)}\n\n"
                    f"Transform description: {description}"
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("AI suggest_transform failed: %s", exc)
        return ""


def _empty_analysis() -> Dict[str, Any]:
    return {
        "provider": "unknown",
        "event_type": "",
        "confidence": 0.0,
        "filter_suggestions": [],
        "field_mapping_suggestions": [],
        "schema_summary": "AI features not enabled",
        "key_fields": [],
    }
