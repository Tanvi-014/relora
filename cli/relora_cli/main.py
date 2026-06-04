#!/usr/bin/env python3
"""
relora — CLI for the Relora

Configure once:
    relora config set --url http://localhost:8000 --api-key hk_...

Then use:
    relora ingest --url https://myapp.com/hook '{"event":"test"}'
    relora status <webhook-id>
    relora dlq list
    relora dlq replay <webhook-id>
    relora dlq replay-all
    relora stats
    relora audit
    relora listen --port 4040 --forward http://localhost:3000/hook
"""
import json
import sys
import urllib.parse
import threading
import http.server
from typing import Optional

import click

from relora_cli import config as _cfg
from relora_cli.client import CLIClient


# ── Shared context ──────────────────────────────────────────────────────────

def _client(ctx: click.Context) -> CLIClient:
    cfg = ctx.ensure_object(dict)
    return CLIClient(cfg["url"], cfg.get("api_key", ""))


def _print_json(data: dict) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


# ── Root ─────────────────────────────────────────────────────────────────────

@click.group()
@click.option("--url", envvar="RELORA_URL", default=None, help="Relora base URL")
@click.option("--api-key", envvar="RELORA_API_KEY", default=None, help="API key")
@click.pass_context
def cli(ctx: click.Context, url: Optional[str], api_key: Optional[str]) -> None:
    """Relora CLI."""
    ctx.ensure_object(dict)
    loaded = _cfg.load()
    ctx.obj["url"] = url or loaded["url"]
    ctx.obj["api_key"] = api_key or loaded.get("api_key", "")


# ── config ───────────────────────────────────────────────────────────────────

@cli.group()
def config() -> None:
    """Manage CLI configuration."""


@config.command("set")
@click.option("--url", required=True, help="Relora base URL, e.g. http://localhost:8000")
@click.option("--api-key", default="", help="API key (leave empty for unauthenticated)")
def config_set(url: str, api_key: str) -> None:
    """Persist connection settings to ~/.relora/config.json."""
    _cfg.save({"url": url, "api_key": api_key})
    click.echo(f"Saved config — url={url}")


@config.command("show")
def config_show() -> None:
    """Print current config."""
    _print_json(_cfg.load())


# ── ingest ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("payload", default="{}")
@click.option("--to", "url", required=True, help="Destination URL")
@click.option("--filter", "filter_expr", default=None, help="Filter expression")
@click.option("--transform", default=None, help="JSON field map (as JSON string)")
@click.option("--idempotency-key", default=None, help="Idempotency key")
@click.option("--destination-id", default=None, help="Registered destination UUID")
@click.pass_context
def ingest(
    ctx: click.Context,
    payload: str,
    url: str,
    filter_expr: Optional[str],
    transform: Optional[str],
    idempotency_key: Optional[str],
    destination_id: Optional[str],
) -> None:
    """Ingest a webhook event through Relora.

    PAYLOAD is a JSON string, e.g. '{"event":"order.created","amount":99}'
    """
    try:
        body = json.loads(payload)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}", param_hint="PAYLOAD")

    params = [f"url={urllib.parse.quote(url, safe='')}"]
    if filter_expr:
        params.append(f"filter={urllib.parse.quote(filter_expr, safe='')}")
    if transform:
        params.append(f"transform={urllib.parse.quote(transform, safe='')}")
    if destination_id:
        params.append(f"destination_id={destination_id}")

    client = _client(ctx)
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    result = client.post(f"/api/v1/ingest", payload=body, params="&".join(params))
    _print_json(result)


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("webhook_id")
@click.pass_context
def status(ctx: click.Context, webhook_id: str) -> None:
    """Show status and delivery attempts for a webhook."""
    result = _client(ctx).get(f"/api/v1/webhooks/{webhook_id}")
    _print_json(result)


# ── stats ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show delivery statistics for your tenant."""
    result = _client(ctx).get("/api/v1/stats")
    w = result
    click.echo(f"Total:      {w.get('total_webhooks', 0)}")
    click.echo(f"Pending:    {w.get('pending_count', 0)}")
    click.echo(f"Processing: {w.get('processing_count', 0)}")
    click.echo(f"Completed:  {w.get('completed_count', 0)}")
    click.echo(f"Failed:     {w.get('failed_count', 0)}")
    click.echo(f"Success %:  {w.get('success_rate', 100.0):.1f}%")


# ── dlq ───────────────────────────────────────────────────────────────────────

@cli.group()
def dlq() -> None:
    """Inspect and manage the Dead Letter Queue."""


@dlq.command("list")
@click.option("--limit", default=20, show_default=True)
@click.pass_context
def dlq_list(ctx: click.Context, limit: int) -> None:
    """List failed webhooks in the DLQ."""
    result = _client(ctx).get(f"/api/v1/dlq?limit={limit}")
    items = result if isinstance(result, list) else result.get("items", [])
    if not items:
        click.echo("DLQ is empty.")
        return
    click.echo(f"{'ID':<38}  {'URL':<40}  {'Retries':<8}  {'Updated'}")
    click.echo("-" * 100)
    for item in items:
        click.echo(
            f"{item.get('id',''):<38}  "
            f"{item.get('destination_url','')[:40]:<40}  "
            f"{item.get('retry_count',0):<8}  "
            f"{item.get('updated_at','')}"
        )


@dlq.command("replay")
@click.argument("webhook_id")
@click.pass_context
def dlq_replay(ctx: click.Context, webhook_id: str) -> None:
    """Replay a single failed webhook immediately."""
    result = _client(ctx).post(f"/api/v1/webhooks/{webhook_id}/replay")
    _print_json(result)
    click.echo("Queued for immediate delivery.")


@dlq.command("replay-all")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def dlq_replay_all(ctx: click.Context, confirm: bool) -> None:
    """Replay all failed webhooks in the DLQ."""
    if not confirm:
        click.confirm("Replay ALL failed webhooks?", abort=True)
    result = _client(ctx).post("/api/v1/dlq/replay-all")
    _print_json(result)


@dlq.command("health")
@click.pass_context
def dlq_health(ctx: click.Context) -> None:
    """Show DLQ health score and intelligence summary."""
    result = _client(ctx).get("/api/v1/dlq/health")
    score = result.get("health_score", "?")
    status_text = result.get("status", "")
    click.echo(f"Health score: {score}/100  [{status_text}]")
    if result.get("recommendations"):
        click.echo("\nRecommendations:")
        for rec in result["recommendations"]:
            click.echo(f"  • {rec}")


# ── audit ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--resource-type", default=None, help="Filter by resource type (destination, webhook, …)")
@click.option("--action", default=None, help="Filter by action (CREATE, UPDATE, DELETE, REPLAY)")
@click.option("--limit", default=20, show_default=True)
@click.pass_context
def audit(
    ctx: click.Context,
    resource_type: Optional[str],
    action: Optional[str],
    limit: int,
) -> None:
    """Show the audit log for your tenant."""
    params = f"limit={limit}"
    if resource_type:
        params += f"&resource_type={resource_type}"
    if action:
        params += f"&action={action}"
    result = _client(ctx).get(f"/api/v1/audit-log?{params}")
    entries = result.get("entries", [])
    if not entries:
        click.echo("No audit log entries found.")
        return
    for e in entries:
        click.echo(
            f"{e['created_at']}  {e['action']:<8}  {e['resource_type']:<16}  "
            f"{e.get('resource_id', '')[:36]}  ip={e.get('ip_address','?')}"
        )


# ── listen ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--port", default=4040, show_default=True, help="Local port to listen on")
@click.option("--forward", "forward_url", required=True, help="Forward requests to this URL")
@click.option("--ingest-to", "ingest_url", default=None, help="Also ingest forwarded events into Relora")
@click.pass_context
def listen(ctx: click.Context, port: int, forward_url: str, ingest_url: Optional[str]) -> None:
    """Start a local listener that forwards webhooks to your app.

    Useful for local development: point your webhook provider at
    http://localhost:<port> and Relora will forward every POST to --forward.

    With --ingest-to, each request is also stored in Relora for replay/DLQ.
    """
    import http.server
    import urllib.request as _req

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # suppress default access log

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            # Forward to the target app
            fwd = urllib.request.Request(
                forward_url,
                data=body,
                headers={k: v for k, v in self.headers.items()
                          if k.lower() not in ("host", "content-length")},
                method="POST",
            )
            try:
                with urllib.request.urlopen(fwd, timeout=10) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            except Exception:
                code = 0

            click.echo(f"  → {forward_url}  [{code}]  {len(body)}b")

            if ingest_url:
                client = _client(ctx)
                try:
                    client.post(
                        "/api/v1/ingest",
                        payload=json.loads(body) if body else {},
                        params=f"url={urllib.parse.quote(ingest_url, safe='')}",
                    )
                    click.echo(f"    ✓ ingested into Relora")
                except Exception as exc:
                    click.echo(f"    ✗ ingest failed: {exc}")

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"forwarded"}')

    server = http.server.HTTPServer(("", port), _Handler)
    click.echo(f"Listening on http://localhost:{port}")
    click.echo(f"Forwarding to: {forward_url}")
    if ingest_url:
        click.echo(f"Ingesting to:  {ingest_url}")
    click.echo("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
