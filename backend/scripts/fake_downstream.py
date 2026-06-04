from collections import defaultdict
from typing import Any, Dict

from fastapi import FastAPI, Request, Response, status


app = FastAPI(title="Relora Fake Downstream")
received_events: list[Dict[str, Any]] = []
flaky_counts: defaultdict[str, int] = defaultdict(int)


async def capture_event(request: Request, endpoint: str) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {"_raw_body": (await request.body()).decode("utf-8", errors="replace")}

    event = {
        "endpoint": endpoint,
        "payload": payload,
        "headers": dict(request.headers),
    }
    received_events.append(event)
    return event


@app.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy"}


@app.post("/ok")
async def ok(request: Request) -> Dict[str, Any]:
    event = await capture_event(request, "ok")
    return {
        "received": True,
        "endpoint": event["endpoint"],
        "total_received": len(received_events),
    }


@app.post("/fail")
async def fail(request: Request) -> Response:
    await capture_event(request, "fail")
    return Response(
        content='{"error":"intentional failure for Relora demo"}',
        media_type="application/json",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@app.post("/flaky/{key}")
async def flaky(key: str, request: Request) -> Response:
    await capture_event(request, f"flaky/{key}")
    flaky_counts[key] += 1

    if flaky_counts[key] == 1:
        return Response(
            content='{"error":"first attempt intentionally failed"}',
            media_type="application/json",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        content='{"received":true,"mode":"flaky recovered"}',
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )


@app.get("/events")
async def events() -> Dict[str, Any]:
    return {
        "total": len(received_events),
        "events": received_events[-25:],
    }


@app.post("/reset")
async def reset() -> Dict[str, bool]:
    received_events.clear()
    flaky_counts.clear()
    return {"reset": True}
