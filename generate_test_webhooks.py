"""
Generate test webhooks with failures to populate DLQ Intelligence data.
This script sends webhooks that will fail to populate the DLQ.
"""
import requests
import json
import time
from datetime import datetime

def main():
    base_url = "http://localhost:8000"
    
    # Try to register a test user first
    register_data = {
        "email": "test@example.com",
        "password": "test123"
    }
    
    try:
        resp = requests.post(f"{base_url}/api/v1/auth/register", json=register_data)
        if resp.status_code == 201:
            print("✅ Registered test user: test@example.com / test123")
        else:
            print(f"Registration failed (user may already exist): {resp.status_code}")
    except Exception as e:
        print(f"Registration error: {e}")
    
    # Login to get auth token
    login_data = {
        "email": "test@example.com",
        "password": "test123"
    }
    
    try:
        resp = requests.post(f"{base_url}/api/v1/auth/login", json=login_data)
        if resp.status_code != 200:
            print(f"Login failed with status {resp.status_code}")
            print(f"Response: {resp.text}")
            print("Please provide your actual login credentials or register a user first")
            return
        auth_data = resp.json()
        token = auth_data.get("access_token")
        headers = {"Authorization": f"Bearer {token}"}
        print("✅ Login successful")
    except Exception as e:
        print(f"Login error: {e}")
        return
    
    # Get projects
    resp = requests.get(f"{base_url}/api/v1/projects", headers=headers)
    projects_data = resp.json()
    
    # Handle response - it might be a list or dict
    if isinstance(projects_data, dict):
        projects = projects_data.get("projects", [])
    else:
        projects = projects_data if isinstance(projects_data, list) else []
    
    if not projects:
        print("No projects found. Creating a test project...")
        project_data = {"name": "Test Project"}
        resp = requests.post(f"{base_url}/api/v1/projects", json=project_data, headers=headers)
        project = resp.json()
        project_id = project["id"]
    else:
        project_id = projects[0]["id"]
        print(f"Using project: {projects[0]['name']} ({project_id})")
    
    # Get or create a destination that will fail
    resp = requests.get(f"{base_url}/api/v1/destinations", headers=headers)
    dests_data = resp.json()
    
    # Handle response - it might be a list or dict
    if isinstance(dests_data, dict):
        destinations = dests_data.get("destinations", [])
    else:
        destinations = dests_data if isinstance(dests_data, list) else []
    
    # Use an invalid URL to ensure failures
    test_dest = None
    for dest in destinations:
        if "test-fail" in dest.get("name", "").lower():
            test_dest = dest
            break
    
    if not test_dest:
        print("Creating test destination with invalid URL...")
        dest_data = {
            "name": "Test-Fail Destination",
            "url": "http://invalid-host-that-will-fail:9999/webhook",
            "http_method": "POST",
            "max_retries": 3,
            "backoff_base_seconds": 1
        }
        resp = requests.post(f"{base_url}/api/v1/destinations", json=dest_data, headers=headers)
        test_dest = resp.json()
    
    dest_id = test_dest["id"]
    print(f"Using destination: {test_dest['name']} ({dest_id})")
    
    # Send multiple webhooks that will fail
    print("\nSending test webhooks that will fail...")
    event_types = ["payment.failed", "order.cancelled", "user.signup", "subscription.expired"]
    
    for i in range(20):
        event_type = event_types[i % len(event_types)]
        payload = {
            "event_id": f"test_event_{i}",
            "event_type": event_type,
            "data": {
                "user_id": f"user_{i}",
                "amount": 100 + i,
                "timestamp": datetime.now().isoformat()
            }
        }
        
        # Send webhook using the ingest endpoint
        # The ingest endpoint expects destination as query param and payload as body
        webhook_url = f"{base_url}/api/v1/ingest?destination_id={dest_id}"
        
        try:
            resp = requests.post(webhook_url, json=payload, headers=headers)
            print(f"  [{i+1}/20] Sent webhook {payload['event_id']} - Status: {resp.status_code}")
            if resp.status_code == 200:
                result = resp.json()
                print(f"         Webhook IDs: {result.get('webhook_ids', [])}")
            else:
                print(f"         Error: {resp.text}")
            time.sleep(0.1)  # Small delay between requests
        except Exception as e:
            print(f"  [{i+1}/20] Error sending webhook: {e}")
    
    print("\n✅ Sent 20 test webhooks that should fail and populate the DLQ")
    print("Wait a few seconds for the worker to process them, then refresh the DLQ Intelligence page")

if __name__ == "__main__":
    main()
