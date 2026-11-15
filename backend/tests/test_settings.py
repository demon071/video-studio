"""The Settings document: provider keys/models, OCR profiles and audio mode.

The panel had nowhere to write a key before this; these tests pin the three
things that made it safe to add: the environment only *seeds* the document, the
secret never travels back to the UI, and the OCR/audio choices reach the
services through ``overlay()`` without touching any other call site.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.config import Settings, api_key
from app.schemas.subtitle import SubtitleSegment, TranscribeRequest
from app.services import model_manager, ocr_service, provider_config, stt_service


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=str(tmp_path / "data"))


def _outcome() -> stt_service.SttOutcome:
    return stt_service.SttOutcome(
        language="vi",
        duration=1.0,
        segments=[SubtitleSegment(index=1, start=0.0, end=1.0, text="xin chào")],
        provider="local",
        model="small",
        device="cpu",
    )


# ---------------------------------------------------------------------------
# Document: seed, precedence, secret hygiene
# ---------------------------------------------------------------------------
def test_environment_seeds_the_document_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "env-key")
    settings = _settings(tmp_path)

    document = provider_config.load(settings)

    assert document["providers"]["groq"]["api_key"] == "env-key"
    assert provider_config.overlay(settings).groq_api_key == "env-key"


def test_a_typed_key_wins_over_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "env-key")
    settings = _settings(tmp_path)

    provider_config.save({"providers": {"groq": {"api_key": "typed-key"}}}, settings)

    assert provider_config.overlay(settings).groq_api_key == "typed-key"
    assert provider_config.load(settings)["providers"]["groq"]["api_key"] == "typed-key"


def test_an_emptied_key_falls_back_to_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    settings = _settings(tmp_path)

    provider_config.save({"providers": {"openai": {"api_key": ""}}}, settings)

    # The document has no key, so the resolver falls through to the environment.
    assert api_key("openai", provider_config.overlay(settings)) == "env-key"


def test_model_names_reach_settings_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    provider_config.save(
        {
            "providers": {
                "gemini": {"model": "gemini-2.5-pro"},
                "openai": {"model": "gpt-4.1", "stt_model": "gpt-4o-transcribe"},
                "voicestudio": {"model": "tts-1-hd"},
            }
        },
        settings,
    )
    overlaid = provider_config.overlay(settings)

    assert overlaid.gemini_model == "gemini-2.5-pro"
    assert overlaid.openai_chat_model == "gpt-4.1"
    assert overlaid.openai_stt_model == "gpt-4o-transcribe"
    assert overlaid.voicestudio_model == "tts-1-hd"


def test_custom_base_url_reaches_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    provider_config.save({"providers": {"openai": {"base_url": "http://localhost:1234/v1"}}}, settings)

    assert provider_config.overlay(settings).openai_base_url == "http://localhost:1234/v1"


def test_describe_never_returns_the_key_itself(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    provider_config.save({"providers": {"gemini": {"api_key": "super-secret"}}}, settings)

    payload = provider_config.describe(settings)

    gemini = next(item for item in payload["providers"] if item["name"] == "gemini")
    assert gemini["api_key_set"] is True
    assert "super-secret" not in repr(payload)


def test_ocr_and_audio_choices_round_trip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    provider_config.save(
        {"ocr": {"engine": "rapidocr", "profile": "tiny"}, "audio": {"mode": "clean"}},
        settings,
    )

    assert provider_config.ocr_profile(settings) == "tiny"
    assert provider_config.audio_mode(settings) == "clean"
    assert provider_config.overlay(settings).ocr_profile == "tiny"


@pytest.mark.parametrize(
    "patch",
    [
        {"ocr": {"engine": "tesseract"}},
        {"ocr": {"profile": "huge"}},
        {"audio": {"mode": "loud"}},
    ],
)
def test_unknown_choices_are_rejected(tmp_path: Path, patch: dict) -> None:
    with pytest.raises(provider_config.ProviderConfigError):
        provider_config.save(patch, _settings(tmp_path))


def test_describe_offers_two_ocr_profiles_and_two_audio_modes(tmp_path: Path) -> None:
    payload = provider_config.describe(_settings(tmp_path))

    assert [item["name"] for item in payload["ocr"]["profiles"]] == ["small", "tiny"]
    assert payload["ocr"]["profiles"][1]["installed"] is False
    assert [item["name"] for item in payload["audio"]["modes"]] == ["fast", "clean"]
    assert payload["audio"]["separator_model"] == "uvr-mdx-inst-hq3"


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------
def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid/models")
    response = httpx.Response(status, request=request, text="nope")
    return httpx.HTTPStatusError("bad", request=request, response=response)


def test_a_rejected_key_is_reported_with_a_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    provider_config.save({"providers": {"groq": {"api_key": "k"}}}, settings)

    def reject(*_args, **_kwargs):
        raise _http_error(401)

    monkeypatch.setattr(provider_config.httpx, "get", reject)

    outcome = provider_config.test_connection("groq", settings)

    assert outcome.ok is False
    assert "401" in outcome.message
    assert "không hợp lệ" in outcome.message


def test_a_working_key_lists_the_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    provider_config.save({"providers": {"groq": {"api_key": "k"}}}, settings)
    payload = {"data": [{"id": "whisper-large-v3-turbo"}, {"id": "whisper-large-v3"}]}
    request = httpx.Request("GET", "https://example.invalid/models")
    monkeypatch.setattr(
        provider_config.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=payload, request=request),
    )

    outcome = provider_config.test_connection("groq", settings)

    assert outcome.ok is True
    assert outcome.models == ["whisper-large-v3", "whisper-large-v3-turbo"]


def test_an_override_is_tested_before_it_is_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    seen: dict[str, str] = {}

    def fake_get(url, headers=None, timeout=None):  # noqa: ANN001, ARG001
        seen["auth"] = (headers or {}).get("Authorization", "")
        return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(provider_config.httpx, "get", fake_get)

    outcome = provider_config.test_connection("openai", settings, overrides={"api_key": "unsaved"})

    assert outcome.ok is True
    assert seen["auth"] == "Bearer unsaved"
    assert provider_config.load(settings)["providers"]["openai"]["api_key"] == ""


# ---------------------------------------------------------------------------
# OCR profiles
# ---------------------------------------------------------------------------
def test_ocr_tiny_keeps_the_accurate_recogniser() -> None:
    pytest.importorskip("rapidocr")
    from rapidocr.utils.typings import ModelType  # noqa: PLC0415

    params = ocr_service._rapidocr_params("vi", "tiny", None)

    assert params["Det.model_type"] == ModelType.TINY
    assert params["Rec.model_type"] == ModelType.SMALL
    assert params["Det.lang_type"] == "vi"


def test_ocr_small_is_the_default_pair() -> None:
    pytest.importorskip("rapidocr")
    from rapidocr.utils.typings import ModelType  # noqa: PLC0415

    params = ocr_service._rapidocr_params("vi", "small", None)

    assert params["Det.model_type"] == ModelType.SMALL
    assert params["Rec.model_type"] == ModelType.SMALL


def test_korean_moves_to_the_v5_pair() -> None:
    pytest.importorskip("rapidocr")
    from rapidocr.utils.typings import ModelType, OCRVersion  # noqa: PLC0415

    params = ocr_service._rapidocr_params("ko", "tiny", None)

    assert params["Rec.ocr_version"] == OCRVersion.PPOCRV5
    assert params["Rec.lang_type"] == "korean"
    assert params["Det.lang_type"] == "multi"
    assert params["Det.model_type"] == ModelType.MOBILE


def test_japanese_has_no_tiny_detector() -> None:
    pytest.importorskip("rapidocr")
    from rapidocr.utils.typings import ModelType  # noqa: PLC0415

    params = ocr_service._rapidocr_params("ja", "tiny", None)

    assert params["Det.model_type"] == ModelType.SMALL


def test_a_managed_folder_is_only_used_once_it_has_weights(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    folder = ocr_service.model_root(settings)
    folder.mkdir(parents=True, exist_ok=True)

    assert ocr_service.managed_models(settings) is None

    (folder / "PP-OCRv6_det_tiny.onnx").write_bytes(b"x")

    assert ocr_service.managed_models(settings) == folder


# ---------------------------------------------------------------------------
# OCR device: GPU when the wheel has one, CPU otherwise
# ---------------------------------------------------------------------------
def _providers(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> None:
    monkeypatch.setattr(ocr_service, "onnx_providers", lambda: list(names))


def test_auto_picks_the_gpu_once_onnxruntime_has_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])

    assert ocr_service.resolve_device("auto") == "gpu"
    assert ocr_service.resolve_device("gpu") == "gpu"


def test_directml_counts_as_a_gpu_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Windows wheel runs on any DX12 card, NVIDIA or not.
    _providers(monkeypatch, ["DmlExecutionProvider", "CPUExecutionProvider"])

    assert ocr_service.resolve_device("auto") == "gpu"


def test_a_cpu_only_build_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch, ["CPUExecutionProvider"])

    assert ocr_service.resolve_device("auto") == "cpu"
    # Asking for the GPU is a preference, never a promise: the job runs anyway.
    assert ocr_service.resolve_device("gpu") == "cpu"


def test_no_onnxruntime_at_all_means_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch, [])

    assert ocr_service.resolve_device("auto") == "cpu"


def test_explicit_cpu_never_asks_for_the_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])

    assert ocr_service.resolve_device("cpu") == "cpu"


def test_gpu_params_ask_for_cuda_on_the_cuda_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("rapidocr")
    _providers(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])

    params = ocr_service._rapidocr_params("vi", "small", None, "gpu")

    assert params["EngineConfig.onnxruntime.use_cuda"] is True
    assert params["EngineConfig.onnxruntime.cuda_ep_cfg.device_id"] == 0
    # Asking for DirectML here would only earn an "install onnxruntime-directml"
    # warning on every run.
    assert "EngineConfig.onnxruntime.use_dml" not in params


def test_gpu_params_ask_for_directml_on_the_directml_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("rapidocr")
    _providers(monkeypatch, ["DmlExecutionProvider", "CPUExecutionProvider"])

    params = ocr_service._rapidocr_params("vi", "small", None, "gpu")

    assert params["EngineConfig.onnxruntime.use_dml"] is True
    assert "EngineConfig.onnxruntime.use_cuda" not in params


def test_cpu_params_leave_the_providers_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("rapidocr")
    _providers(monkeypatch, ["DmlExecutionProvider", "CPUExecutionProvider"])

    params = ocr_service._rapidocr_params("vi", "small", None)

    assert [key for key in params if key.startswith("EngineConfig")] == []


def test_the_pack_list_is_exclusive_and_names_its_commands() -> None:
    info = ocr_service.runtime_info()

    packs = {pack["name"]: pack for pack in info["packs"]}
    assert set(packs) == {"onnxruntime", "onnxruntime-directml", "onnxruntime-gpu"}
    assert all(pack["command"].startswith("pip install ") for pack in packs.values())
    # Exactly one build is installed: they all provide the same `onnxruntime` module.
    assert [name for name, pack in packs.items() if pack["installed"]] == (
        [info["distribution"]] if info["distribution"] else []
    )
    assert info["gpu_ready"] is bool(info["gpu_provider"])


class _Session:
    def __init__(self, providers: list[str]) -> None:
        self._providers = providers

    def get_providers(self) -> list[str]:
        return self._providers


class _Engine:
    """Stands in for a built rapidocr reader that builds its sessions lazily."""

    def __init__(self, providers: list[str], fail: bool = False) -> None:
        self.calls: list[Any] = []
        self.fail = fail
        self.text_det = SimpleNamespace(session=SimpleNamespace(session=_Session(providers)))

    def __call__(self, image: Any) -> None:
        if self.fail:
            raise RuntimeError("libcudnn is missing")
        self.calls.append(image)


def test_the_warm_up_reads_one_blank_frame() -> None:
    pytest.importorskip("numpy")
    engine = _Engine(["CUDAExecutionProvider"])

    ocr_service._probe(engine)

    assert len(engine.calls) == 1
    assert engine.calls[0].shape == (64, 320, 3)


def test_the_real_provider_is_read_from_the_detector_session() -> None:
    assert ocr_service._active_providers(_Engine(["DmlExecutionProvider"])) == ["DmlExecutionProvider"]
    # Anything unexpected answers "unknown" instead of raising mid-job.
    assert ocr_service._active_providers(object()) == []


def test_the_job_log_says_which_device_ran() -> None:
    read = ocr_service._active_providers  # any callable does

    assert ocr_service.Engine("rapidocr", "gpu", read).label == "rapidocr · GPU"
    assert ocr_service.Engine("rapidocr", "cpu", read).label == "rapidocr · CPU"


def test_the_device_survives_the_settings_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    _providers(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])

    provider_config.save({"ocr": {"device": "gpu"}}, settings)

    assert provider_config.load(settings)["ocr"]["device"] == "gpu"
    assert provider_config.overlay(settings).ocr_device == "gpu"
    described = provider_config.describe(settings)["ocr"]
    assert described["device"] == "gpu"
    assert described["device_actual"] == "gpu"
    assert described["devices"] == ["auto", "gpu", "cpu"]
    assert described["onnx_providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_a_gpu_request_without_a_provider_reports_the_cpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel must say CPU honestly instead of echoing the wish back."""
    settings = _settings(tmp_path)
    _providers(monkeypatch, ["CPUExecutionProvider"])

    provider_config.save({"ocr": {"device": "gpu"}}, settings)

    described = provider_config.describe(settings)["ocr"]
    assert described["device"] == "gpu"
    assert described["device_actual"] == "cpu"


def test_an_unknown_device_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(provider_config.ProviderConfigError, match="Thiết bị OCR"):
        provider_config.save({"ocr": {"device": "tpu"}}, settings)

    assert provider_config.load(settings)["ocr"]["device"] == ""


def test_the_tiny_pair_installs_flat_next_to_the_engine_folder(tmp_path: Path) -> None:
    settings = Settings(models_dir=str(tmp_path / "models"))
    spec = model_manager.require("ocr-ppocrv6-tiny")

    assert model_manager.model_dir(spec, settings) == settings.models_path / "ocr"
    assert model_manager.status(spec, settings).downloaded is False

    for item in spec.files:
        target = model_manager.model_dir(spec, settings) / item.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")

    assert model_manager.status(spec, settings).downloaded is True
    assert ocr_service.managed_models(settings) is not None


def test_omnivoice_reports_the_pip_command_instead_of_a_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(models_dir=str(tmp_path / "models"))
    spec = model_manager.require("omnivoice-v1")
    # The developer's venv may already ship the runtime; pretend it is absent so
    # this test says the same thing on every machine.
    monkeypatch.setattr(model_manager, "_package_available", lambda _name: False)

    assert model_manager.status(spec, settings).pip_hint == "pip install omnivoice"
    with pytest.raises(model_manager.ModelError, match="pip install omnivoice"):
        model_manager.download(spec.key, settings=settings)


# ---------------------------------------------------------------------------
# Clean audio mode
# ---------------------------------------------------------------------------
def test_clean_mode_transcribes_the_separated_vocal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    vocal = tmp_path / "vocals.wav"
    vocal.write_bytes(b"x" * 128)
    used: dict[str, Path] = {}

    monkeypatch.setattr(stt_service, "_cleaned_wav", lambda *a, **k: vocal)
    monkeypatch.setattr(stt_service, "_cached_wav", lambda *a, **k: pytest.fail("full mix used"))

    def fake_local(wav: Path, *_args, **_kwargs) -> stt_service.SttOutcome:
        used["wav"] = wav
        return _outcome()

    monkeypatch.setattr(stt_service, "_transcribe_local", fake_local)

    result = stt_service.transcribe(
        TranscribeRequest(video_path=str(source), provider="local", audio_mode="clean"),
        settings=settings,
    )

    assert used["wav"] == vocal
    assert result.segments[0].text == "xin chào"


def test_clean_mode_degrades_to_the_full_mix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"x" * 128)
    used: dict[str, Path] = {}

    def broken(*_args, **_kwargs):
        raise RuntimeError("audio-separator is not installed")

    monkeypatch.setattr(stt_service, "_cleaned_wav", broken)
    monkeypatch.setattr(stt_service, "_cached_wav", lambda *a, **k: mix)

    def fake_local(wav: Path, *_args, **_kwargs) -> stt_service.SttOutcome:
        used["wav"] = wav
        return _outcome()

    monkeypatch.setattr(stt_service, "_transcribe_local", fake_local)

    stt_service.transcribe(
        TranscribeRequest(video_path=str(source), provider="local", audio_mode="clean"),
        settings=settings,
    )

    assert used["wav"] == mix


def test_the_saved_mode_is_used_when_the_request_stays_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    provider_config.save({"audio": {"mode": "clean"}}, settings)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    vocal = tmp_path / "vocals.wav"
    vocal.write_bytes(b"x" * 128)
    used: dict[str, Path] = {}

    monkeypatch.setattr(stt_service, "_cleaned_wav", lambda *a, **k: vocal)
    monkeypatch.setattr(stt_service, "_cached_wav", lambda *a, **k: pytest.fail("full mix used"))

    def fake_local(wav: Path, *_args, **_kwargs) -> stt_service.SttOutcome:
        used["wav"] = wav
        return _outcome()

    monkeypatch.setattr(stt_service, "_transcribe_local", fake_local)

    stt_service.transcribe(TranscribeRequest(video_path=str(source), provider="local"), settings=settings)

    assert used["wav"] == vocal


# ---------------------------------------------------------------------------
# Offline translator: which GGUF runs
# ---------------------------------------------------------------------------
def _llm_settings(tmp_path: Path) -> Settings:
    # An isolated models_dir, otherwise the machine's real GGUFs decide `installed`.
    return Settings(data_dir=str(tmp_path / "data"), models_dir=str(tmp_path / "models"))


def test_the_offline_model_choice_round_trips(tmp_path: Path) -> None:
    settings = _llm_settings(tmp_path)

    provider_config.save({"llm": {"model": "gemma-3-4b-it-q4_k_m.gguf"}}, settings)

    assert provider_config.overlay(settings).llm_model_file == "gemma-3-4b-it-q4_k_m.gguf"
    described = provider_config.describe(settings)["llm"]
    assert described["model"] == "gemma-3-4b-it-q4_k_m.gguf"
    assert described["ready"] is False  # nothing downloaded under this models_dir
    assert [option["filename"] for option in described["options"]] == [
        "qwen2.5-3b-instruct-q4_k_m.gguf",
        "gemma-3-4b-it-q4_k_m.gguf",
    ]
    assert [option["active"] for option in described["options"]] == [False, True]


def test_an_unknown_offline_model_is_rejected_at_save(tmp_path: Path) -> None:
    settings = _llm_settings(tmp_path)

    with pytest.raises(provider_config.ProviderConfigError, match="không hợp lệ"):
        provider_config.save({"llm": {"model": "gemma-3-4b.gguf"}}, settings)


def test_an_empty_offline_choice_returns_to_the_engine_default(tmp_path: Path) -> None:
    settings = _llm_settings(tmp_path)
    provider_config.save({"llm": {"model": "gemma-3-4b-it-q4_k_m.gguf"}}, settings)

    provider_config.save({"llm": {"model": ""}}, settings)

    assert provider_config.overlay(settings).llm_model_file == settings.llm_model_file
    described = provider_config.describe(settings)["llm"]
    assert described["model"] == settings.llm_model_file
    assert [option["active"] for option in described["options"]] == [True, False]
