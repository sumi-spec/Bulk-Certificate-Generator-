from unittest.mock import MagicMock

from fastapi import status
from sqlalchemy.exc import OperationalError

from app.core.database import get_db
from app.main import app, lifespan


def test_health_check_returns_success(client, monkeypatch):
    """Verify that /health responds with 200 OK and valid JSON structure."""
    from unittest.mock import MagicMock

    import redis

    # Mock Redis ping to test fully healthy status
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: mock_redis)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["app_name"] == "Bulk Certificate Generator"
    assert data["database"] == "connected"
    assert data["redis"] == "connected"
    assert "timestamp" in data


def test_health_check_database_failure(client):
    """Verify that /health responds with 503 Service Unavailable when DB is down."""
    mock_db = MagicMock()
    mock_db.execute.side_effect = OperationalError("connection failed", None, None)

    def override_get_db_failure():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db_failure
    try:
        response = client.get("/health")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["status"] == "degraded"
        assert "disconnected" in data["database"]
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_application_lifespan():
    """Verify that the FastAPI lifespan startup and shutdown context manager executes safely."""
    async with lifespan(app):
        # Startup completed without exception
        pass
