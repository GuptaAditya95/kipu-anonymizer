"""Smoke tests for the API contract. Run: pytest -q"""
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def test_health():
    r = c.get("/v1/health", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_anonymize_cold_start_keeps_ids():
    rec = {"id": "m1", "thread_id": "soporte-cobros:1", "author": "Ana", "ts": "1",
           "text": "hola", "attachments": []}
    r = c.post("/v1/anonymize", json={"source": "chats", "records": [rec]})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["records"]] == ["m1"]


def test_anonymize_rejects_bad_source():
    assert c.post("/v1/anonymize", json={"source": "x", "records": []}).status_code == 422
