from pathlib import Path
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import Artifact


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


def test_format_capabilities_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NEWTONSPECT_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("NEWTONSPECT_DB_PATH", str(tmp_path / "storage" / "db.sqlite3"))
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/v1/jobs/capabilities/formats")
    assert response.status_code == 200
    payload = response.json()
    assert "curated" in payload
    assert any(item["format"] == "mp4" for item in payload["curated"])


def test_download_artifact_headers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NEWTONSPECT_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("NEWTONSPECT_DB_PATH", str(tmp_path / "storage" / "db.sqlite3"))
    app = create_app()
    client = TestClient(app)

    payload = b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00isomiso2"
    created = client.post(
        "/api/v1/jobs",
        files={"video": ("demo.mp4", payload, "video/mp4")},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]

    artifact_path = tmp_path / "storage" / "manual_bundle.zip"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"zip-content")

    container = app.state.container
    raw = container.repository.get_job(job_id)
    assert raw is not None
    artifacts = container.repository.decode_artifacts(raw.artifacts_json)
    artifacts.append(
        Artifact(
            name="bundle final.zip",
            kind="zip",
            path=str(artifact_path),
            mime_type="application/zip",
            created_at=datetime.now(timezone.utc),
        )
    )
    container.repository.set_artifacts(job_id, artifacts)

    response = client.get(f"/api/v1/jobs/{job_id}/artifacts/bundle%20final.zip/download")
    assert response.status_code == 200
    assert response.content == b"zip-content"
    disposition = response.headers.get("content-disposition", "")
    assert "attachment;" in disposition
    assert "filename=" in disposition
    assert "filename*=" in disposition
