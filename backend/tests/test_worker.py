from app.config import settings
from app.worker import calculate_backoff_seconds


def test_calculate_backoff_seconds(monkeypatch):
    monkeypatch.setattr(settings, "BACKOFF_BASE_SECONDS", 15)

    assert calculate_backoff_seconds(0) == 15
    assert calculate_backoff_seconds(1) == 30
    assert calculate_backoff_seconds(4) == 240
