---
name: project-hermes-phase11
description: Hermes Phase 11 production hardening — what was fixed, what architecture exists now, what remains
metadata:
  type: project
---

Phase 11 (2026-06-03) shipped all production hardening changes.

**Why:** Project needed top-1% production readiness for recruiter/founder review.

**What shipped:**
- async Anthropic client fix in ai_intelligence.py
- SSRF fix in update_destination (validate_destination_url now called on PUT too)
- Password validation (8 char min + email format) in register
- Pydantic DestinationCreate/DestinationUpdate models
- /api/v1/dashboard single-call endpoint for Overview page KPIs
- Startup recovery for stuck replay jobs
- api_main.py split into 11 routers under app/routers/
- Full Overview dashboard redesign: SVG icons, health banner, 4 KPI cards, sparkline, recent failures with inline replay

**Current architecture:**
- api_main.py is 103 lines (entry point only)
- Routers: auth, projects, destinations, webhooks, alerts, event_types, ai_tools, dlq, simulator, consumer, system
- Frontend: index.html uses SVG icons, no emoji nav; Overview calls /api/v1/dashboard

**How to apply:** When working on this project, expect the router structure. Don't look for endpoints in api_main.py — they're in routers/.

[[project-hermes-architecture]]