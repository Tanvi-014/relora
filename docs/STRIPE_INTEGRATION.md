# Stripe Webhook Integration with Relora

This guide shows how to forward Stripe webhooks through Relora for reliable delivery and retry handling.

## Prerequisites

- Relora instance running (local or deployed)
- Stripe account (test mode)
- Stripe CLI installed (optional, for local testing)

## Step 1: Get Your Relora URL

If running locally: `http://localhost:8000`
If deployed: Use your deployed URL (e.g., `https://relora.yourdomain.com`)

## Step 2: Configure Relora for Stripe

Relora supports Stripe signature verification out of the box. Set your Stripe webhook secret:

```bash
# Set the Stripe webhook secret in your environment
export STRIPE_WEBHOOK_SECRET="whsec_your_stripe_webhook_secret_here"
```

Or add to your `.env` file:
```
STRIPE_WEBHOOK_SECRET=whsec_your_stripe_webhook_secret_here
```

## Step 3: Configure Stripe Webhook

### Option A: Using Stripe Dashboard

1. Go to Stripe Dashboard → Developers → Webhooks
2. Click "Add endpoint"
3. Set endpoint URL to: `https://your-relora-url.com/api/v1/ingest?signature_provider=stripe&url=https://your-downstream-url.com/webhook`
4. Select events to send (e.g., `payment_intent.succeeded`, `customer.created`)
5. Copy the webhook signing secret
6. Set the secret in Relora configuration (Step 2)

### Option B: Using Stripe CLI (Local Testing)

```bash
# Forward Stripe events to Relora
stripe listen --forward-to http://localhost:8000/api/v1/ingest\?signature_provider=stripe\&url=http://localhost:3000/webhook

# Trigger a test event
stripe trigger payment_intent.succeeded
```

## Step 4: Verify Integration

1. Trigger a Stripe event (via dashboard or CLI)
2. Check Relora dashboard at `/` to see the webhook in the queue
3. Verify the webhook was delivered to your downstream URL
4. Check delivery attempts and status in the webhook details panel

## Example: Fan-out to Multiple Destinations

You can forward Stripe events to multiple downstream URLs:

```
https://your-relora-url.com/api/v1/ingest?signature_provider=stripe&url=https://analytics.example.com/webhook&urls=https://slack.example.com/webhook,https://email.example.com/webhook
```

## Example: Filter Stripe Events

Only forward specific event types:

```
https://your-relora-url.com/api/v1/ingest?signature_provider=stripe&url=https://downstream.com/webhook&filter=event.type=='payment_intent.succeeded'
```

## Example: Transform Stripe Payload

Extract specific fields from Stripe events:

```
https://your-relora-url.com/api/v1/ingest?signature_provider=stripe&url=https://downstream.com/webhook&transform={"amount":"data.object.amount","customer":"data.object.customer"}
```

## Benefits of Using Relora with Stripe

- **Reliability**: Automatic retry with exponential backoff
- **Visibility**: Dashboard to track all webhook deliveries
- **Dead Letter Queue**: Failed webhooks are preserved for manual inspection
- **Fan-out**: Send events to multiple destinations simultaneously
- **Filtering**: Only forward events that match your criteria
- **Transformations**: Extract and reshape payload data before delivery
- **Alerts**: Get notified when webhooks fail (Slack/Email integration)

## Troubleshooting

### Signature Verification Failed

- Ensure `STRIPE_WEBHOOK_SECRET` is set correctly in Relora
- Check that the webhook secret matches the one in Stripe dashboard
- Verify the event is being sent with the correct signature

### Webhooks Not Delivering

- Check Relora dashboard for webhook status
- Verify downstream URL is accessible
- Check destination URL allowlist if `ALLOW_PRIVATE_DESTINATIONS=false`
- Review delivery attempts in webhook details panel

### Rate Limiting

If you're hitting rate limits, adjust `RATE_LIMIT_PER_MINUTE` in Relora configuration.
