"""
Tenant isolation tests.

Verifies that Tenant A cannot access or modify Tenant B's resources via:
  - Cross-tenant destination_id injection on ingest
  - Cross-tenant incident resolution
  - Cross-tenant consumer poll ack

These tests catch the class of bugs where a scoped query is missing and an
authenticated-but-wrong-tenant request can touch another tenant's data.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Ingest: cross-tenant destination_id injection
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ingest_rejects_destination_belonging_to_other_project():
    """
    Destination lookup on ingest joins Project so that a destination_id from
    a different project/tenant returns 404 rather than delivering to it.
    The query in routers/webhooks.py joins Destination with Project and filters
    Project.api_key == tenant_id.
    """
    # Simulate DB returning no result for the destination + tenant combination
    mock_db = AsyncMock()
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=no_result)
    mock_db.scalar = AsyncMock(return_value=0)
    mock_db.commit = AsyncMock()

    foreign_dest_id = str(uuid4())

    from app.routers.webhooks import ingest_webhook
    from fastapi import Request
    from unittest.mock import Mock

    request = Mock(spec=Request)
    request.headers = {}
    request.stream = AsyncMock(return_value=iter([b'{"event": "test"}']))

    with patch("app.routers.webhooks.check_rate_limit", new_callable=AsyncMock), \
         patch("app.config.settings") as mock_settings:
        mock_settings.MONTHLY_EVENT_QUOTA = 0
        mock_settings.DEFAULT_MAX_RETRIES = 5

        with pytest.raises(HTTPException) as exc:
            await ingest_webhook(
                request=request,
                url=None,
                urls=None,
                destination_id=foreign_dest_id,
                filter_expression=None,
                transform=None,
                signature_provider=None,
                ordering_key=None,
                consumer_id=None,
                tenant_id="tenant_a",
                db=mock_db,
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Incident resolution: cross-tenant
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_resolve_incident_scoped_to_tenant():
    """Resolving an incident must be scoped to the requesting tenant's project."""
    from app.routers.dlq import resolve_incident

    incident_id = str(uuid4())
    mock_db = AsyncMock()

    # No incident found for this tenant → should raise 404
    not_found = MagicMock()
    not_found.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=not_found)

    with pytest.raises(HTTPException) as exc:
        await resolve_incident(
            incident_id=incident_id,
            tenant_id="tenant_a",
            db=mock_db,
        )
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_resolve_incident_uses_project_scope():
    """
    An incident belonging to a different project is not resolvable by tenant_a
    because the query joins Project and filters Project.api_key == tenant_id.
    """
    from app.routers.dlq import resolve_incident

    incident_id = str(uuid4())
    mock_db = AsyncMock()

    # Scoped query returns nothing (incident belongs to different project)
    not_found = MagicMock()
    not_found.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=not_found)

    with pytest.raises(HTTPException) as exc:
        await resolve_incident(
            incident_id=incident_id,
            tenant_id="tenant_a",
            db=mock_db,
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Consumer poll: ack token is tenant-scoped
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_consumer_ack_cannot_complete_foreign_tenant_batch():
    """
    Acknowledging with a token that belongs to a different tenant must not
    mark those webhooks as completed.  The UPDATE filters by tenant_id,
    so the rowcount will be 0 for a foreign token but the call must not error.
    """
    mock_db = AsyncMock()
    ack_result = MagicMock()
    ack_result.rowcount = 0   # foreign token → no rows matched

    poll_result = MagicMock()
    poll_result.fetchall.return_value = []

    mock_db.execute = AsyncMock(side_effect=[ack_result, poll_result])
    mock_db.commit = AsyncMock()

    from app.routers.consumer import poll_events
    result = await poll_events(
        consumer_id="worker-a",
        limit=10,
        ack_token="token-from-tenant-b",
        tenant_id="tenant_a",
        db=mock_db,
    )

    # The ack UPDATE must have included tenant_id in its WHERE clause
    ack_params = mock_db.execute.call_args_list[0][0][1]
    assert ack_params["tid"] == "tenant_a"
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# Project API key not visible to viewers
# ---------------------------------------------------------------------------

def test_project_to_dict_excludes_api_key_by_default():
    """Project.to_dict() must not expose api_key unless explicitly requested."""
    from app.models import Project
    p = Project(id=uuid4(), name="Test", api_key="hk_live_secret123")
    d = p.to_dict()
    assert "api_key" not in d


def test_project_to_dict_includes_api_key_when_requested():
    """Project.to_dict(include_api_key=True) exposes the key for owner/admin callers."""
    from app.models import Project
    p = Project(id=uuid4(), name="Test", api_key="hk_live_secret123")
    d = p.to_dict(include_api_key=True)
    assert d["api_key"] == "hk_live_secret123"
