"""
Claude-powered payload analysis for schema detection, filter suggestions,
and natural-language → filter expression conversion.
"""
import json
import logging
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger("relora.ai")

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
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
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
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
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
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
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


_SYSTEM_ADVISOR = """You are a webhook infrastructure reliability advisor for Relora. Analyze the provided telemetry and return ONLY valid JSON with this exact schema:
{
  "summary": "2-3 sentence narrative of the current infrastructure state. Be specific and use the actual numbers.",
  "recommendations": [
    {
      "severity": "critical|warn|info|ok",
      "title": "Short title, max 8 words",
      "body": "Specific, actionable 1-2 sentence recommendation. Reference exact numbers from the data.",
      "action_label": "Short button label",
      "action_page": "recovery|destinations|analytics|replay|dlq-intelligence|timeline|pipeline"
    }
  ]
}

Rules:
- Never use filler phrases like "It seems like", "I would suggest", or "Consider"
- Be direct and specific: use the actual numbers from the telemetry
- Maximum 4 recommendations total
- Use severity "critical" only for circuit breakers open or success rate below 90%
- Use severity "ok" only when there is genuinely nothing wrong — make it feel like a celebration, not a report
- If everything is healthy, one "ok" recommendation is enough
- Order by severity descending (critical first)"""


async def analyze_advisor(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.ENABLE_AI_FEATURES or not settings.ANTHROPIC_API_KEY:
        return {"summary": None, "recommendations": [], "ai_enabled": False}
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_SYSTEM_ADVISOR,
            messages=[{
                "role": "user",
                "content": f"Analyze this webhook infrastructure telemetry:\n{json.dumps(telemetry, indent=2)}",
            }],
        )
        result = json.loads(response.content[0].text)
        result["ai_enabled"] = True
        return result
    except Exception as exc:
        logger.warning("AI analyze_advisor failed: %s", exc)
        return {"summary": None, "recommendations": [], "ai_enabled": False}


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


_SYSTEM_INSIGHT_SUMMARY = """You are the head of platform reliability giving a weekly briefing to the engineering leadership team.

Tone: direct, confident, specific. Write like someone who has read every number and is now synthesizing the story behind them. No filler, no hedging, no robotic phrases like "it appears" or "it seems."

Write 3–4 sentences that tell the operational story of the week:
- Lead with the headline result — grade, score, and whether this is good or bad in context
- Name the single most consequential event or trend (not a list)
- Explain what drove it, if the data allows
- End with one forward-looking signal: what to watch, what to do, or what this week's pattern predicts

Rules:
- Use exact numbers from the report
- Reference destination names when relevant
- Do not use the word "significant" — show it in numbers instead
- Never start a sentence with "Additionally" or "Furthermore"
- Return ONLY the narrative text — no markdown, no headers, no bullets"""


async def generate_insight_summary(report_data: Dict[str, Any]) -> str:
    if not settings.ENABLE_AI_FEATURES or not settings.ANTHROPIC_API_KEY:
        return ""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=350,
            system=_SYSTEM_INSIGHT_SUMMARY,
            messages=[{
                "role": "user",
                "content": f"Generate the weekly briefing for this report:\n{json.dumps(report_data, indent=2)}",
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("AI generate_insight_summary failed: %s", exc)
        return ""


_SYSTEM_INSIGHT_QA = """You are a reliability engineer who wrote this weekly report and can answer questions about it.
You know exactly what happened, why it happened, and what to do about it.
Answer directly and specifically — reference the exact numbers from the report.
Keep answers to 2-3 sentences. Be decisive: give a clear answer, not a list of possibilities.
If the answer genuinely isn't in the report data, say so briefly and tell them where to look."""


async def ask_insight_question(report_data: Dict[str, Any], messages: list) -> str:
    if not settings.ENABLE_AI_FEATURES or not settings.ANTHROPIC_API_KEY:
        return "AI features are not enabled. Set ENABLE_AI_FEATURES=true and ANTHROPIC_API_KEY."
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=_SYSTEM_INSIGHT_QA + f"\n\nWeekly report:\n{json.dumps(report_data, indent=2)}",
            messages=messages,
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("AI ask_insight_question failed: %s", exc)
        return "Unable to answer at this time."
