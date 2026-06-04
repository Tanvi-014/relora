"""
Test script to generate sample webhooks for DLQ Intelligence testing.
This creates some failed webhooks to test the DLQ Intelligence tab.
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta
from uuid import uuid4

# Add backend to path
sys.path.insert(0, 'backend')

from app.db import async_session, init_db
from app.models import Webhook, Destination, Project, DeliveryAttempt, CircuitState


async def create_test_data():
    """Create test webhooks with various failure states."""
    async with async_session() as db:
        await init_db()
        
        # Get or create project
        result = await db.execute(Project.__table__.select().limit(1))
        project = result.first()
        
        if not project:
            print("No project found. Please create a project first.")
            return
        
        # Get or create destination
        result = await db.execute(
            Destination.__table__.select().where(Destination.project_id == project[0]).limit(1)
        )
        dest = result.first()
        
        if not dest:
            # Create a test destination
            dest = Destination(
                id=uuid4(),
                project_id=project[0],
                name="Test Destination",
                url="https://example.com/webhook",
                max_retries=5,
                backoff_base_seconds=30,
                circuit_state=CircuitState.CLOSED.value,
                is_enabled=True
            )
            db.add(dest)
            await db.commit()
            await db.refresh(dest)
            dest_id = dest.id
        else:
            dest_id = dest[1]
        
        print(f"Using project: {project[1]}")
        print(f"Using destination: {dest_id}")
        
        # Create some failed webhooks
        failure_categories = [
            "AUTHENTICATION",
            "RATE_LIMITING", 
            "SERVER_ERROR",
            "TIMEOUT",
            "NETWORK"
        ]
        
        now = datetime.now(timezone.utc)
        
        for i in range(20):
            webhook = Webhook(
                id=uuid4(),
                project_id=project[0],
                destination_id=dest_id,
                event_id=f"event_{i}",
                tenant_id="test_tenant",
                status="failed",
                payload={"test": f"data_{i}"},
                headers={"content-type": "application/json"},
                retry_count=3,
                max_retries=5,
                created_at=now - timedelta(minutes=i*5),
                idempotency_key=f"key_{i}"
            )
            db.add(webhook)
            await db.flush()
            
            # Create delivery attempts with failures
            failure_cat = failure_categories[i % len(failure_categories)]
            attempt = DeliveryAttempt(
                id=uuid4(),
                webhook_id=webhook.id,
                attempt_number=1,
                status_code=500 if i % 2 == 0 else 401,
                error_message=f"Test error: {failure_cat}",
                attempted_at=now - timedelta(minutes=i*5),
                failure_category=failure_cat,
                failure_subcategory="test_subcategory"
            )
            db.add(attempt)
        
        await db.commit()
        print(f"Created 20 test failed webhooks")
        print("You can now test the DLQ Intelligence tab")


if __name__ == "__main__":
    asyncio.run(create_test_data())
