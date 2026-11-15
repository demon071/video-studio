"""The alternative providers: free web translators, cloud STT, OCR and dubbing.

Every new route is either pure (the signing, chunking and merging helpers) or
delegates to one seam, so both halves can be pinned without a network call.
"""

from __future__ import annotations

import base64

import pytest

from app.config import Settings
from app.schemas.subtitle import DubbingRequest, SubtitleSegment, TranscribeRequest, TranslateRequest
from app.services import (
    capcut_client,
    ocr_service,
    omnivoice_tts,
    provider_config,
    stt_service,
    translate_service,
    tts_service,
    voicestudio_tts,
    web_translate,
)


def _settings(tmp_path) -> Settings:
    return Settings(data_dir=str(tmp_path / "data"))


# ---------------------------------------------------------------------------
# Free web translators
# ---------------------------------------------------------------------------
def test_plan_chunks_respects_the_engine_limits() -> None:
    texts = [f"line {number}" for number in range(30)]

    google = web_translate.plan_chunks(texts, provider="google")
    bing = web_translate.plan_chunks(texts, provider="bing")

    assert all(len(chunk) <= web_translate.GOOGLE_MAX_CUES for chunk in google)
    assert all(len(chunk) <= web_translate.BING_MAX_CUES for chunk in bing)
    assert sum(len(chunk) for chunk in google) == len(texts)
    assert sum(len(chunk) for chunk in bing) == len(texts)
    # Bing answers fewer cues per request, so the same lines need more batches.
    assert len(bing) > len(google)


def test_plan_chunks_counts_characters_not_only_cues() -> None:
    texts = ["x" * 900, "y" * 900, "z" * 900]

    chunks = web_translate.plan_chunks(texts, provider="bing")

    assert len(chunks) == 3


def test_split_answer_round_trips_the_delimiter() -> None:
    answer = web_translate.DELIMITER.join(["xin chào", "tạm biệt"])

    assert web_translate.split_answer(answer, 2) == ["xin chào", "tạm biệt"]


def test_split_answer_refuses_an_answer_that_lost_the_separator() -> None:
    assert web_translate.split_answer("xin chào tạm biệt", 2) is None
    assert web_translate.split_answer(web_translate.DELIMITER.join(["a", "b", "c"]), 2) is None


def test_google_json_and_mobile_pages_are_both_readable() -> None:
    payload = [[["xin chào", "你好", None, None]], None, "zh-CN"]
    page = '<div class="result-container">t&#7841;m bi&#7879;t</div>'

    assert web_translate.google_text(payload) == "xin chào"
    assert web_translate.google_mobile_text(page) == "tạm biệt"


def test_web_translation_never_touches_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_translate(texts, *, provider, source, target):  # noqa: ANN001
        seen.update(provider=provider, source=source, target=target)
        return [text.upper() for text in texts]

    monkeypatch.setattr(translate_service.web_translate, "translate_texts", fake_translate)
    monkeypatch.setattr(
        translate_service, "_complete_local", lambda *args, **kwargs: pytest.fail("the LLM must not run")
    )

    outcome = translate_service.translate(
        [SubtitleSegment(index=1, start=0.0, end=1.0, text="xin chào")],
        provider="google",
        target_language="en",
        settings=Settings(),
    )

    assert outcome.segments[0].translation == "XIN CHÀO"
    assert seen == {"provider": "google", "source": "auto", "target": "en"}


def test_web_translation_keeps_the_source_text_when_a_batch_comes_back_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(translate_service.web_translate, "translate_texts", lambda *args, **kwargs: [""])

    outcome = translate_service.translate(
        [SubtitleSegment(index=1, start=0.0, end=1.0, text="你好")],
        provider="google",
        settings=Settings(),
    )

    assert outcome.segments[0].translation == "你好"


def test_web_translation_reports_a_dead_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise web_translate.WebTranslateError("offline")

    monkeypatch.setattr(translate_service.web_translate, "translate_texts", boom)

    with pytest.raises(translate_service.TranslateError):
        translate_service.translate(
            [SubtitleSegment(index=1, start=0.0, end=1.0, text="你好")],
            provider="bing",
            settings=Settings(),
        )


# ---------------------------------------------------------------------------
# CapCut signing
# ---------------------------------------------------------------------------
def test_body_stub_is_the_md5_of_the_exact_body() -> None:
    assert capcut_client.body_stub('{"a":1}') == "bb6cb5c68df4652941caf652a366f2d8"


def test_sign_header_ignores_the_query_string() -> None:
    first = capcut_client.sign_header(
        "https://host/lv/v1/common_task/new?a=1", "8.7.0", "1700000000", "42", "3"
    )
    same = capcut_client.sign_header(
        "https://host/lv/v1/common_task/new?b=2", "8.7.0", "1700000000", "42", "3"
    )
    other = capcut_client.sign_header(
        "https://host/lv/v1/common_task/query", "8.7.0", "1700000000", "42", "3"
    )

    assert first == same
    assert first != other


def test_rsa_signature_is_a_full_size_pkcs1_block() -> None:
    modulus, exponent = capcut_client.rsa_public_numbers()
    raw = base64.b64decode(capcut_client.rsa_encrypt_pkcs1v15("appid:359289"))

    assert exponent == 65537
    assert len(raw) == (modulus.bit_length() + 7) // 8
    # The plaintext never appears in the ciphertext.
    assert b"appid" not in raw


def test_tts_payload_sign_covers_the_ssml() -> None:
    first = capcut_client.tts_payload_sign("<speak>a</speak>", "{}", "1", "359289")
    second = capcut_client.tts_payload_sign("<speak>b</speak>", "{}", "1", "359289")

    assert first != second


def test_resolve_resource_id_maps_the_catalogue_and_falls_back() -> None:
    assert capcut_client.resolve_resource_id("BV421_vivn_streaming") == "7252594014782755330"
    assert capcut_client.resolve_resource_id("capcut:BV562_streaming") == "7483736254694035984"
    assert capcut_client.resolve_resource_id("someone_vivn") == "7252594014782755330"
    assert capcut_client.resolve_resource_id("someone") == "7102355709945188865"


def test_ssml_escapes_the_line_and_carries_the_resource_id() -> None:
    ssml = capcut_client._ssml('a < b & "c"', voice="BV562_streaming", rate=1.1)

    assert "a &lt; b &amp; &quot;c&quot;" in ssml
    assert 'resource_id="7483736254694035984"' in ssml
    assert 'rate="1.10"' in ssml


def test_rate_factory_understands_the_edge_percentage() -> None:
    assert tts_service._rate_factor("+10%") == pytest.approx(1.1)
    assert tts_service._rate_factor("-10%") == pytest.approx(0.9)
    assert tts_service._rate_factor(None) == 1.0
    assert tts_service._rate_factor("fastest") == 1.0


# ---------------------------------------------------------------------------
# OCR frames -> cues
# ---------------------------------------------------------------------------
def test_crop_filter_covers_the_requested_band() -> None:
    assert ocr_service._crop("bottom", 0.3) == "crop=iw:ih*0.300:0:ih*0.700"
    assert ocr_service._crop("top", 0.3) == "crop=iw:ih*0.300:0:0"
    assert ocr_service._crop("full", 0.3) == ""


def test_merge_observations_glues_consecutive_samples_of_one_line() -> None:
    observations = [(0.0, "你好"), (0.5, "你好"), (1.0, "你好"), (1.5, "再见"), (2.0, "再见")]

    cues = ocr_service.merge_observations(observations, fps=2.0)

    assert [text for _, _, text in cues] == ["你好", "再见"]
    assert cues[0][0] == 0.0
    assert cues[0][1] == pytest.approx(1.25)


def test_merge_observations_drops_a_line_seen_once() -> None:
    observations = [(0.0, "KÊNH PHIM"), (0.5, "你好"), (1.0, "你好")]

    cues = ocr_service.merge_observations(observations, fps=2.0)

    assert [text for _, _, text in cues] == ["你好"]


def test_merge_observations_splits_on_a_long_gap() -> None:
    observations = [(0.0, "你好"), (0.5, "你好"), (9.0, "你好"), (9.5, "你好")]

    assert len(ocr_service.merge_observations(observations, fps=2.0)) == 2


def test_merge_observations_tolerates_jitter_and_keeps_the_most_complete_reading() -> None:
    observations = [(0.0, "xin chào"), (0.5, "xin chao")]

    cues = ocr_service.merge_observations(observations, fps=2.0)

    assert len(cues) == 1
    assert cues[0][2] == "xin chào"


def test_merge_observations_keeps_a_short_word_whose_diacritics_came_and_went() -> None:
    # "ừm" and "um" are two edits apart on two characters: only the deaccented
    # comparison keeps them in one cue instead of splitting the line in two.
    observations = [(0.0, "Ừm"), (0.33, "Um"), (0.66, "ừm")]

    cues = ocr_service.merge_observations(observations, fps=3.0)

    assert len(cues) == 1
    # 0.0 to the last sample (0.66) plus half a sample interval.
    assert cues[0][:2] == (0.0, 0.827)


def test_merge_observations_never_glues_two_different_lines() -> None:
    observations = [(0.0, "Em làm mẹ"), (0.33, "Em làm mẹ"), (0.66, "anh làm bố")]

    cues = ocr_service.merge_observations(observations, fps=3.0)

    assert [text for _, _, text in cues] == ["Em làm mẹ"]


def test_ocr_defaults_keep_the_accents_inside_the_scanned_band() -> None:
    settings = Settings()

    # A band that starts at 66% clips the marks above the first line.
    assert settings.ocr_band >= 0.40
    # Three samples a second let a one-second cue survive the two-frame rule.
    assert settings.ocr_fps >= 3.0


def test_the_scan_rectangle_crops_only_the_selected_area() -> None:
    crop = ocr_service._region_crop((10.0, 60.0, 90.0, 95.0))

    assert crop == "crop=iw*0.8000:ih*0.3500:iw*0.1000:ih*0.6000"
    # The whole frame needs no filter at all.
    assert ocr_service._region_crop((0.0, 0.0, 100.0, 100.0)) == ""


def test_a_stored_region_beats_the_legacy_position_and_band() -> None:
    settings = Settings(ocr_position="full", ocr_band=1.0, ocr_region="8,62,92,96")

    assert provider_config.resolved_region(settings) == (8.0, 62.0, 92.0, 96.0)


def test_without_a_stored_region_the_scan_falls_back_to_position_and_band() -> None:
    bottom = Settings(ocr_position="bottom", ocr_band=0.45, ocr_region="")
    top = Settings(ocr_position="top", ocr_band=0.3, ocr_region="")
    full = Settings(ocr_position="full", ocr_region="")

    assert provider_config.resolved_region(bottom) == (0.0, 55.0, 100.0, 100.0)
    assert provider_config.resolved_region(top) == (0.0, 0.0, 100.0, 30.0)
    assert provider_config.resolved_region(full) == (0.0, 0.0, 100.0, 100.0)


def test_a_broken_stored_region_never_breaks_the_job() -> None:
    settings = Settings(ocr_position="bottom", ocr_band=0.45, ocr_region="0,55,100")

    assert provider_config.resolved_region(settings) == (0.0, 55.0, 100.0, 100.0)


def test_the_region_round_trips_through_providers_json(tmp_path) -> None:
    settings = _settings(tmp_path)

    provider_config.save({"ocr": {"region": [12.5, 60, 88, 94]}}, settings)
    document = provider_config.describe(settings)["ocr"]

    assert document["region"] == [12.5, 60.0, 88.0, 94.0]
    assert document["region_stored"] is True
    assert provider_config.overlay(settings).ocr_region == "12.5,60,88,94"

    # An empty string goes back to the position/band default.
    provider_config.save({"ocr": {"region": ""}}, settings)
    assert provider_config.describe(settings)["ocr"]["region_stored"] is False


@pytest.mark.parametrize("value", ["0,55,100", "0,55,100,120", "0,55,2,100", "trái,trên,phải,dưới"])
def test_an_impossible_region_is_rejected(tmp_path, value: str) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(provider_config.ProviderConfigError, match="Vùng quét"):
        provider_config.save({"ocr": {"region": value}}, settings)


def test_normalize_text_ignores_case_spacing_and_zero_width_marks() -> None:
    assert ocr_service.normalize_text("Xin  Chào\u200b") == "xinchào"


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------
def test_transcribe_routes_capcut_through_the_cloud_engine(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"00")
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"00")
    settings = _settings(tmp_path)
    seen: dict[str, object] = {}

    monkeypatch.setattr(stt_service, "_cached_wav", lambda source, settings: wav)
    monkeypatch.setattr(stt_service, "probe_duration", lambda path: 4.0)

    def fake_engine(audio, *, work_dir, report, settings):  # noqa: ANN001
        seen["audio"] = audio
        return [(0.0, 1.5, "你好"), (1.5, 3.0, "再见")]

    monkeypatch.setitem(stt_service.CLOUD_ENGINES, "capcut", fake_engine)

    result = stt_service.transcribe(
        TranscribeRequest(video_path=str(video), provider="capcut", polish=False),
        settings=settings,
    )

    assert seen["audio"] == wav
    assert result.provider == "capcut"
    assert [segment.text for segment in result.segments] == ["你好", "再见"]
    assert result.duration == 4.0


def test_transcribe_routes_ocr_to_the_frame_reader(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"00")
    settings = _settings(tmp_path)
    seen: dict[str, object] = {}

    monkeypatch.setattr(stt_service, "probe_duration", lambda path: 6.0)
    monkeypatch.setattr(
        stt_service,
        "_cached_wav",
        lambda *args, **kwargs: pytest.fail("OCR must not decode the audio"),
    )

    def fake_reader(path, *, work_dir, report, settings):  # noqa: ANN001
        seen["path"] = path
        return [(0.0, 2.0, "第一行")]

    monkeypatch.setattr(stt_service.ocr_service, "transcribe", fake_reader)

    result = stt_service.transcribe(
        TranscribeRequest(video_path=str(video), provider="ocr", polish=False),
        settings=settings,
    )

    assert seen["path"] == video
    assert result.provider == "ocr"
    assert result.model == f"ocr:{settings.ocr_engine}"
    assert result.segments[0].text == "第一行"


def test_ocr_reads_the_hardsub_language_the_user_picked(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"00")
    settings = _settings(tmp_path)
    seen: dict[str, object] = {}

    monkeypatch.setattr(stt_service, "probe_duration", lambda path: 2.0)

    def fake_reader(path, *, work_dir, report, settings):  # noqa: ANN001
        seen["language"] = settings.ocr_language
        return [(0.0, 1.0, "xin chào")]

    monkeypatch.setattr(stt_service.ocr_service, "transcribe", fake_reader)

    result = stt_service.transcribe(
        TranscribeRequest(video_path=str(video), provider="ocr", polish=False, language="vi"),
        settings=settings,
    )

    assert seen["language"] == "vi"
    assert result.language == "vi"


def test_dub_extension_and_voice_defaults_cover_the_three_providers() -> None:
    settings = Settings()

    assert tts_service.DEFAULT_PROVIDER == "capcut"
    assert tts_service._raw_extension("capcut") == "mp3"
    assert tts_service._raw_extension("omnivoice") == "wav"
    assert tts_service._raw_extension("voicestudio") == "wav"
    assert tts_service.resolve_voice("capcut", None, settings) == settings.capcut_voice
    assert tts_service.resolve_voice("omnivoice", None, settings) == omnivoice_tts.DEFAULT_VOICE
    assert tts_service.resolve_voice("voicestudio", None, settings) == settings.voicestudio_voice


def test_a_retired_engine_is_rejected_instead_of_dubbing_silently() -> None:
    """Projects and requests saved with edge/piper/vieneu must fail loudly."""
    with pytest.raises(tts_service.TtsError, match="edge"):
        tts_service.resolve_voice("edge", None, Settings())


def test_omnivoice_voice_strings_split_into_the_three_modes() -> None:
    settings = Settings()

    assert omnivoice_tts.resolve_voice(None, settings) == omnivoice_tts.DEFAULT_VOICE
    assert omnivoice_tts.resolve_voice("omnivoice:auto", settings) == "auto"
    assert omnivoice_tts.resolve_voice("design:nữ, giọng trầm", settings) == "design:nữ, giọng trầm"
    assert omnivoice_tts.resolve_voice("clone:D:/mau.wav", settings) == "clone:D:/mau.wav"
    # Plain words read as a description: the voice box is free text in the UI.
    assert omnivoice_tts.resolve_voice("nam, chậm", settings) == "design:nam, chậm"


def test_omnivoice_reuses_one_clone_prompt_per_reference(tmp_path) -> None:
    """Re-encoding and re-transcribing the sample for every line would be waste."""
    sample = tmp_path / "mau.wav"
    sample.write_bytes(b"RIFF" + b"\0" * 512)

    class FakeEngine:
        def __init__(self) -> None:
            self.calls = 0

        def create_voice_clone_prompt(self, *, ref_audio, ref_text=None):
            self.calls += 1
            return f"prompt:{ref_text or 'auto'}"

    omnivoice_tts._PROMPTS.clear()
    engine = FakeEngine()
    settings = Settings(omnivoice_reference_text="xin chào")
    first = omnivoice_tts._arguments(engine, f"clone:{sample}", settings)
    second = omnivoice_tts._arguments(engine, f"clone:{sample}", settings)

    assert engine.calls == 1
    assert first["voice_clone_prompt"] == second["voice_clone_prompt"] == "prompt:xin chào"
    # auto and design describe a voice instead of naming one: no prompt involved.
    assert omnivoice_tts._arguments(engine, "auto", settings) == {}
    assert omnivoice_tts._arguments(engine, "design:nữ, trầm", settings) == {"instruct": "nữ, trầm"}
    omnivoice_tts._PROMPTS.clear()


def test_voicestudio_normalises_the_url_and_voice() -> None:
    assert voicestudio_tts.base_url(Settings(voicestudio_base_url="")) == voicestudio_tts.DEFAULT_URL
    assert (
        voicestudio_tts.base_url(Settings(voicestudio_base_url="127.0.0.1:3900"))
        == "http://127.0.0.1:3900/v1"
    )
    assert (
        voicestudio_tts.base_url(Settings(voicestudio_base_url="http://box:3900/v1/")) == "http://box:3900/v1"
    )
    assert voicestudio_tts.resolve_voice("voicestudio:alloy", Settings()) == "alloy"
    assert (
        voicestudio_tts.resolve_voice(None, Settings(voicestudio_voice="")) == voicestudio_tts.DEFAULT_VOICE
    )


def test_voicestudio_key_falls_back_to_the_omnivoice_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOICESTUDIO_API_KEY", raising=False)
    monkeypatch.setenv("OMNIVOICE_API_KEY", "server-key")

    assert voicestudio_tts.api_key(Settings(voicestudio_api_key="")) == "server-key"
    assert voicestudio_tts.api_key(Settings(voicestudio_api_key="typed")) == "typed"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
def test_requests_accept_the_new_providers() -> None:
    assert TranscribeRequest(video_path="clip.mp4", provider="bilibili").provider == "bilibili"
    assert TranscribeRequest(video_path="clip.mp4", provider="ocr").provider == "ocr"
    assert TranslateRequest(segments=[], provider="bing").provider == "bing"
    assert DubbingRequest(segments=[], provider="omnivoice").provider == "omnivoice"
    assert DubbingRequest(segments=[], provider="voicestudio").provider == "voicestudio"
    assert DubbingRequest(segments=[], provider="capcut").provider == "capcut"
