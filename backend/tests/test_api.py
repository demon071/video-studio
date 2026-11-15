"""REST + WebSocket smoke tests (no AI weights required)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services import ocr_service, provider_config


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_engine_state(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["ffmpeg"] is True


def test_hardware_report_contains_a_plan(client: TestClient) -> None:
    payload = client.get("/api/system/hardware").json()
    assert payload["device"] in {"cuda", "cpu"}
    assert payload["whisper"]["model"]
    assert payload["whisper"]["compute_type"] in {"int8", "float16", "int8_float16"}
    assert payload["paths"]["models"]


def test_model_catalogue_is_complete(client: TestClient) -> None:
    payload = client.get("/api/models").json()
    keys = {model["key"] for model in payload["models"]}
    assert {
        "whisper-medium",
        "qwen2.5-3b-instruct-q4_k_m",
        "gemma3-4b-it-q4_k_m",
        "uvr-mdx-inst-hq3",
    } <= keys
    assert all(model["path"] for model in payload["models"])


def test_unknown_model_is_404(client: TestClient) -> None:
    assert client.post("/api/models/not-a-model/download").status_code == 404


def test_probe_missing_file_is_404(client: TestClient) -> None:
    response = client.post("/api/media/probe", json={"video_path": "Z:/does-not-exist.mp4"})
    assert response.status_code == 404


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_settings_surface_lists_providers_without_keys(client: TestClient) -> None:
    payload = client.get("/api/settings").json()

    names = [item["name"] for item in payload["providers"]]
    assert names == ["gemini", "openai", "groq", "voicestudio"]
    assert all("api_key" not in item for item in payload["providers"])
    assert payload["ocr"]["profile"] in {"small", "tiny"}
    assert payload["audio"]["mode"] in {"fast", "clean"}
    assert len(payload["llm"]["options"]) >= 2  # Qwen2.5 3B and Gemma 3 4B


def test_settings_store_the_offline_gguf(client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Picking Gemma in the panel must survive the round-trip and reach the service."""
    isolated = Settings(data_dir=str(tmp_path / "data"), models_dir=str(tmp_path / "models"))
    monkeypatch.setattr(provider_config, "get_settings", lambda: isolated)

    stored = client.put("/api/settings", json={"llm": {"model": "gemma-3-4b-it-q4_k_m.gguf"}})
    assert stored.status_code == 200
    assert stored.json()["llm"]["model"] == "gemma-3-4b-it-q4_k_m.gguf"
    assert provider_config.overlay(isolated).llm_model_file == "gemma-3-4b-it-q4_k_m.gguf"

    # A name no catalogue entry can download is refused now, not on the first translation.
    broken = client.put("/api/settings", json={"llm": {"model": "gemma-3-4b.gguf"}})
    assert broken.status_code == 400
    assert "Model dịch offline" in broken.json()["detail"]


def test_settings_reject_an_unknown_provider(client: TestClient) -> None:
    response = client.put("/api/settings", json={"providers": {"acme": {"api_key": "k"}}})
    assert response.status_code == 422


def test_settings_store_the_ocr_scan_region(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rectangle the OCR crops must survive the HTTP round-trip, not only the file."""
    isolated = Settings(data_dir=str(tmp_path / "data"))
    monkeypatch.setattr(provider_config, "get_settings", lambda: isolated)

    stored = client.put("/api/settings", json={"ocr": {"region": "10,60,90,95"}})
    assert stored.status_code == 200
    assert stored.json()["ocr"]["region"] == [10.0, 60.0, 90.0, 95.0]
    assert stored.json()["ocr"]["region_stored"] is True
    assert client.get("/api/settings").json()["ocr"]["region"] == [10.0, 60.0, 90.0, 95.0]

    # A box that is not a box is refused with the reason, never silently accepted.
    broken = client.put("/api/settings", json={"ocr": {"region": "0,60,90"}})
    assert broken.status_code == 400
    assert "Vùng quét" in broken.json()["detail"]

    # An empty string goes back to the position/band default.
    reset = client.put("/api/settings", json={"ocr": {"region": ""}}).json()["ocr"]
    assert reset["region_stored"] is False
    assert reset["region"] == [0.0, 55.0, 100.0, 100.0]


def test_settings_store_the_ocr_device(client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A GPU request must survive the round-trip and report what will really run."""
    isolated = Settings(data_dir=str(tmp_path / "data"))
    monkeypatch.setattr(provider_config, "get_settings", lambda: isolated)
    monkeypatch.setattr(ocr_service, "onnx_providers", lambda: ["CPUExecutionProvider"])

    stored = client.put("/api/settings", json={"ocr": {"device": "gpu"}})
    assert stored.status_code == 200
    assert stored.json()["ocr"]["device"] == "gpu"
    # No GPU provider in this install: the panel is told CPU, not the wish.
    assert stored.json()["ocr"]["device_actual"] == "cpu"
    assert client.get("/api/settings").json()["ocr"]["device"] == "gpu"

    broken = client.put("/api/settings", json={"ocr": {"device": "tpu"}})
    assert broken.status_code == 400
    assert "Thiết bị OCR" in broken.json()["detail"]


def test_testing_an_unknown_provider_is_404(client: TestClient) -> None:
    assert client.post("/api/settings/providers/acme/test", json={}).status_code == 404


def test_progress_socket_greets_with_hardware(client: TestClient) -> None:
    with client.websocket_connect("/ws/progress") as socket:
        hello = socket.receive_json()
    assert hello["type"] == "hello"
    assert hello["data"]["hardware"]["device"] in {"cuda", "cpu"}
    assert isinstance(hello["data"]["models"], list)
