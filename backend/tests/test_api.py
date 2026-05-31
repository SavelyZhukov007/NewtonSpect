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

