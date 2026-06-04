# GitHub Webhook Integration with Relora

This guide shows how to forward GitHub webhooks through Relora for reliable delivery and retry handling.

## Prerequisites

- Relora instance running (local or deployed)
- GitHub account
- A GitHub repository

## Step 1: Get Your Relora URL

If running locally: `http://localhost:8000` (use ngrok for external access)
If deployed: Use your deployed URL (e.g., `https://relora.yourdomain.com`)

## Step 2: Configure Relora for GitHub

Relora supports GitHub signature verification out of the box. Set your GitHub webhook secret:

```bash
# Set the GitHub webhook secret in your environment
export GITHUB_WEBHOOK_SECRET="your_github_webhook_secret_here"
```

Or add to your `.env` file:
```
GITHUB_WEBHOOK_SECRET=your_github_webhook_secret_here
```

## Step 3: Configure GitHub Webhook

### Using GitHub Repository Settings

1. Go to your GitHub repository
2. Navigate to Settings → Webhooks
3. Click "Add webhook"
4. Set Payload URL to: `https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://your-downstream-url.com/webhook`
5. Content type: `application/json`
6. Secret: Enter a secret (you'll need to set this in Relora too)
7. Select events to trigger:
   - Push events
   - Pull request events
   - Issue events
   - Or select "Send me everything"
8. Click "Add webhook"
9. Copy the webhook secret
10. Set the secret in Relora configuration (Step 2)

### Using GitHub CLI

```bash
# Add webhook to repository
gh webhook create --repo your-org/your-repo \
  --url "https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://downstream.com/webhook" \
  --secret your_webhook_secret \
  --events push,pull_request,issues
```

## Step 4: Verify Integration

1. Make a change to your repository (push, create PR, etc.)
2. Check Relora dashboard at `/` to see the webhook in the queue
3. Verify the webhook was delivered to your downstream URL
4. Check delivery attempts and status in the webhook details panel

## Example: Fan-out to Multiple Destinations

Forward GitHub events to multiple downstream URLs:

```
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://analytics.example.com/webhook&urls=https://slack.example.com/webhook,https://email.example.com/webhook
```

## Example: Filter GitHub Events

Only forward specific event types:

```
# Only push events to main branch
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://downstream.com/webhook&filter=ref=='refs/heads/main'

# Only pull request events
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://downstream.com/webhook&filter=event_type=='pull_request'
```

## Example: Transform GitHub Payload

Extract specific fields from GitHub events:

```
# Extract repository name and action
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://downstream.com/webhook&transform={"repo":"repository.name","action":"action","sender":"sender.login"}

# Extract PR information
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://downstream.com/webhook&transform={"pr_number":"pull_request.number","title":"pull_request.title","state":"pull_request.state"}
```

## Common Use Cases

### CI/CD Notifications

Forward GitHub events to your CI/CD system:

```
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://ci.example.com/github-webhook
```

### Slack Notifications

Send GitHub events to Slack:

```
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK
```

### Analytics Pipeline

Send GitHub events to your analytics system:

```
https://your-relora-url.com/api/v1/ingest?signature_provider=github&url=https://analytics.example.com/github-events
```

## Benefits of Using Relora with GitHub

- **Reliability**: Automatic retry with exponential backoff
- **Visibility**: Dashboard to track all webhook deliveries
- **Dead Letter Queue**: Failed webhooks are preserved for manual inspection
- **Fan-out**: Send events to multiple destinations simultaneously
- **Filtering**: Only forward events that match your criteria
- **Transformations**: Extract and reshape payload data before delivery
- **Alerts**: Get notified when webhooks fail (Slack/Email integration)

## Troubleshooting

### Signature Verification Failed

- Ensure `GITHUB_WEBHOOK_SECRET` is set correctly in Relora
- Check that the webhook secret matches the one in GitHub repository settings
- Verify the event is being sent with the correct signature

### Webhooks Not Delivering

- Check Relora dashboard for webhook status
- Verify downstream URL is accessible
- Check destination URL allowlist if `ALLOW_PRIVATE_DESTINATIONS=false`
- Review delivery attempts in webhook details panel

### Local Development with ngrok

For local development, use ngrok to expose your local Relora instance:

```bash
# Start ngrok
ngrok http 8000

# Use the ngrok URL in GitHub webhook configuration
# e.g., https://abc123.ngrok.io/api/v1/ingest?signature_provider=github&url=...
```

### Rate Limiting

If you're hitting rate limits, adjust `RATE_LIMIT_PER_MINUTE` in Relora configuration.
