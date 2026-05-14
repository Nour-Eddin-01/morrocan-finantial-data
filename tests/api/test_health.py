from fastapi.testclient import TestClient
from sqlalchemy import text

from tradehub_data.api.app import create_app
from tradehub_data.db.session import get_db


class FakeDb:
    def execute(self, statement):
        assert str(statement) == str(text("select 1"))


def test_health_check():
    app = create_app()

    def override_get_db():
        yield FakeDb()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["database"] == "connected"

