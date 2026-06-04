import ipaddress
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from app.config import settings


PRIVATE_HOSTNAMES = {
    "localhost",
    "host.docker.internal",
}


async def require_api_key(request: Request) -> str:
    tenants = settings.api_key_tenants
    if not tenants:
        return "anonymous"

    supplied_key = request.headers.get("X-Relora-API-Key")
    tenant_id = tenants.get(supplied_key or "")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Relora API key",
        )
    return tenant_id


def validate_destination_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Destination URL must be an absolute http:// or https:// URL",
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Destination URL must include a hostname",
        )

    allowlist = settings.destination_host_allowlist
    if allowlist and hostname not in allowlist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Destination host is not in DESTINATION_HOST_ALLOWLIST",
        )

    if settings.ALLOW_PRIVATE_DESTINATIONS:
        return url

    if hostname in PRIVATE_HOSTNAMES or hostname.endswith(".local"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Private destination hosts are disabled",
        )

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return url

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Private destination IPs are disabled",
        )

    return url
