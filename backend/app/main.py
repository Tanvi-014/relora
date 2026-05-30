# Backward-compatibility shim.
# All production code lives in api_main.py.
# Old deployments that reference app.main:app still work.
from app.api_main import app  # noqa: F401

__all__ = ["app"]
