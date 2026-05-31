from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_create_job_and_fetch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NEWTONSPECT_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("NEWTONSPECT_DB_PATH", str(tmp_path / "storage" / "db.sqlite3"))
    app = create_app()
    client = TestClient(app)

    payload = b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00isomiso2"
    response = client.post(
        "/api/v1/jobs",
        files={"video": ("demo.mp4", payload, "video/mp4")},
        data={"quality_preset": "max_quality"},
    )
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    assert job["status"] == "queued"

    job_id = job["id"]
    details = client.get(f"/api/v1/jobs/{job_id}")
    assert details.status_code == 200
    assert details.json()["id"] == job_id


def test_chunked_websocket_upload_creates_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NEWTONSPECT_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("NEWTONSPECT_DB_PATH", str(tmp_path / "storage" / "db.sqlite3"))
    app = create_app()
    client = TestClient(app)

    payload = b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00isomiso2"
    with client.websocket_connect("/api/v1/jobs/ws/upload") as ws:
        ws.send_json({"type": "start", "filename": "chunked.mp4", "total_size": len(payload)})
        started = ws.receive_json()
        assert started["type"] == "started"

        ws.send_bytes(payload[:10])
        progress_one = ws.receive_json()
        assert progress_one["type"] == "progress"
        assert progress_one["received_bytes"] == 10

        ws.send_bytes(payload[10:])
        progress_two = ws.receive_json()
        assert progress_two["type"] == "progress"
        assert progress_two["received_bytes"] == len(payload)

        ws.send_json({"type": "finish", "options": {"quality_preset": "max_quality"}})
        completed = ws.receive_json()
        assert completed["type"] == "completed"
        job = completed["job"]
        assert job["status"] == "queued"
        assert "Unknown" in job["created_by_device"] or "/" in job["created_by_device"]

    details = client.get(f"/api/v1/jobs/{job['id']}")
    assert details.status_code == 200
    assert details.json()["id"] == job["id"]
