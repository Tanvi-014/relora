"""Thin HTTP client used by CLI commands."""
import json
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


class CLIClient:
    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Relora-API-Key"] = self.api_key
        if extra:
            h.update(extra)
        return h

    def get(self, path: str) -> Any:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            headers=self._headers(),
            method="GET",
        )
        return self._call(req)

    def post(self, path: str, payload: Any = None, params: str = "") -> Any:
        data = json.dumps(payload).encode() if payload is not None else b""
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{params}"
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        return self._call(req)

    def delete(self, path: str) -> Any:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            headers=self._headers(),
            method="DELETE",
        )
        return self._call(req)

    def _call(self, req: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            print(f"Error {e.code}: {detail}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"Connection error: {exc}", file=sys.stderr)
            sys.exit(1)
