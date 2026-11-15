"""OmniVoice: k2-fsa's zero-shot cloning voice for 646 languages, Vietnamese included.

``pip install omnivoice`` brings the ``OmniVoice`` class; the weights
(``k2-fsa/OmniVoice``) live in the Models folder (``models/tts/omnivoice-v1``) -
the first dubbed line downloads them when they are missing. It runs on
torch, so it shares the GPU with the recogniser - :func:`unload` drops it when the
engine stops, before the process exits.

Three modes, chosen by the voice string the AI panel sends:

* ``auto``              let the model pick a voice (no prompt at all);
* ``design:<words>``    describe the voice - "female, low pitch, british accent";
* ``clone:<path.wav>``  clone a 3-10 s sample. The transcript is optional: when
  ``omnivoice_reference_text`` is empty the model transcribes the sample itself.

``auto`` and ``design`` only *describe* a voice, they do not name a speaker: the
sampler invents a new one on every call, so a 47-line file comes out in 47
voices. Only ``clone:`` pins an identity - that is the mode a whole video should
use. The clone prompt is cached per reference file, so the sample is encoded
(and transcribed) once instead of once per line.

Plain words without a prefix are read as a description, so typing "male, deep"
in the voice box does what it says instead of failing.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

MODEL_ID = "k2-fsa/OmniVoice"
# Where the Models panel keeps the snapshot: <models>/tts/<model key>.
SNAPSHOT_FOLDER = Path("tts") / "omnivoice-v1"
SAMPLE_RATE = 24_000
DEFAULT_VOICE = "auto"
# Language tag handed to generate(): it removes the model's guesswork on
# homographs, so "vi" is what a Vietnamese dubbing job wants.
DEFAULT_LANGUAGE = "vi"
PREFIXES = ("design:", "clone:")
# Diffusion steps: 32 is the published default, fewer buys speed and costs
# clarity. Clamped because a typo in the settings must not stop the dubbing.
DEFAULT_STEPS = 32
MIN_STEPS = 4
MAX_STEPS = 64

_LOCK = threading.Lock()
_ENGINE = None
# One reference voice at a time: a prompt holds audio tokens on the GPU, so a
# library of them would eat the VRAM the recogniser needs. Keyed by file
# version, so replacing the sample mid-job rebuilds it instead of lying.
_PROMPTS: dict[tuple[str, int, int, str], object] = {}


class OmnivoiceError(RuntimeError):
    """Raised when the local OmniVoice engine cannot speak."""


def available() -> bool:
    """True when the ``omnivoice`` package can be imported."""
    try:
        import omnivoice  # noqa: F401, PLC0415 - capability probe
    except ImportError:
        return False
    return True


def resolve_voice(voice: str | None, settings: Settings) -> str:
    """``auto``, ``design:<attributes>`` or ``clone:<absolute wav path>``."""
    chosen = (voice or settings.omnivoice_voice or DEFAULT_VOICE).strip()
    if chosen.lower().startswith("omnivoice:"):
        chosen = chosen.split(":", 1)[-1].strip()
    if not chosen:
        return DEFAULT_VOICE
    lowered = chosen.lower()
    if lowered == DEFAULT_VOICE or lowered.startswith(PREFIXES):
        return chosen
    # Anything else reads as a description: the field is free text in the UI.
    return f"design:{chosen}"


def _steps(settings: Settings) -> int:
    try:
        steps = int(settings.omnivoice_steps)
    except (TypeError, ValueError):
        return DEFAULT_STEPS
    return min(max(steps, MIN_STEPS), MAX_STEPS)


def _language(settings: Settings) -> str:
    """The language tag for ``generate()``: "vi" unless the setting says otherwise."""
    return (settings.omnivoice_language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE


def _source(settings: Settings) -> str:
    """The snapshot in the models folder, fetched once when it is still missing."""
    local = settings.models_path / SNAPSHOT_FOLDER
    if (local / "config.json").is_file():
        return str(local)
    configured = (settings.omnivoice_model or MODEL_ID).strip() or MODEL_ID
    if configured != MODEL_ID or Path(configured).exists():
        # A pinned revision or a folder the user placed by hand: trust it.
        return configured
    try:
        from huggingface_hub import snapshot_download  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise OmnivoiceError("huggingface_hub is needed to fetch the OmniVoice weights") from exc
    log.info("fetching %s into %s (3.3 GB, once)", MODEL_ID, local)
    try:
        snapshot_download(
            MODEL_ID,
            local_dir=str(local),
            max_workers=8,
            allow_patterns=("*.json", "*.safetensors", "*.jinja", "*.txt"),
        )
    except Exception as exc:  # noqa: BLE001 - network / disk / auth errors
        raise OmnivoiceError(f"OmniVoice could not download {MODEL_ID}: {exc}") from exc
    return str(local)


def _target(settings: Settings) -> tuple[str, bool]:
    """Where to load the model, and whether that device is a GPU."""
    choice = (settings.omnivoice_device or "auto").strip().lower()
    try:
        import torch  # noqa: PLC0415 - only needed to answer "auto"
    except ImportError:
        return "cpu", False
    if choice == "cuda":
        return ("cuda:0", True) if torch.cuda.is_available() else ("cpu", False)
    if choice == "cpu":
        return "cpu", False
    return ("cuda:0", True) if torch.cuda.is_available() else ("cpu", False)


def load(settings: Settings):
    """Load (once) and return the cached engine."""
    global _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            return _ENGINE
        try:
            import torch  # noqa: PLC0415 - heavy optional import
            from omnivoice import OmniVoice  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise OmnivoiceError("omnivoice is not installed (pip install omnivoice)") from exc

        device, gpu = _target(settings)
        dtype = torch.float16 if gpu else torch.float32
        model_id = _source(settings)
        log.info("loading %s on %s (%s, %s steps)", model_id, device, dtype, _steps(settings))
        try:
            _ENGINE = OmniVoice.from_pretrained(model_id, device_map=device, dtype=dtype)
        except Exception as exc:  # noqa: BLE001 - download / torch errors
            raise OmnivoiceError(f"OmniVoice could not load {model_id}: {exc}") from exc
        return _ENGINE


def unload() -> None:
    """Drop the engine so its VRAM goes back to the OS before the process exits."""
    global _ENGINE
    with _LOCK:
        _ENGINE = None
        _PROMPTS.clear()
    log.debug("omnivoice cache cleared")


def _prompt(engine, reference: Path, transcript: str):
    """The clone prompt for ``reference``, built once per version of the file."""
    stat = reference.stat()
    key = (str(reference.resolve()), stat.st_mtime_ns, stat.st_size, transcript)
    cached = _PROMPTS.get(key)
    if cached is not None:
        return cached
    prompt = engine.create_voice_clone_prompt(ref_audio=str(reference), ref_text=transcript or None)
    _PROMPTS.clear()
    _PROMPTS[key] = prompt
    return prompt


def _arguments(engine, speaker: str, settings: Settings) -> dict[str, object]:
    """The ``generate()`` keyword arguments the chosen mode needs."""
    lowered = speaker.lower()
    if lowered.startswith("clone:"):
        reference = Path(speaker.split(":", 1)[1].strip().strip('"'))
        if not reference.is_file():
            raise OmnivoiceError(
                f"Voice sample not found: {reference}. Pick an existing 3-10 s wav/mp3 file."
            )
        transcript = (settings.omnivoice_reference_text or "").strip()
        return {"voice_clone_prompt": _prompt(engine, reference, transcript)}
    if lowered.startswith("design:"):
        attributes = speaker.split(":", 1)[1].strip()
        if attributes:
            return {"instruct": attributes}
        return {}
    return {}


def synthesize(
    text: str,
    target: str | Path,
    *,
    voice: str | None = None,
    settings: Settings | None = None,
) -> Path:
    """Speak ``text`` into ``target`` (WAV) and return the written path."""
    settings = settings or get_settings()
    spoken = (text or "").strip()
    if not spoken:
        raise OmnivoiceError("No text to speak")

    try:
        import numpy as np  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise OmnivoiceError("OmniVoice needs numpy and soundfile") from exc

    engine = load(settings)
    speaker = resolve_voice(voice, settings)
    arguments = _arguments(engine, speaker, settings)
    try:
        audio = engine.generate(
            text=spoken,
            language=_language(settings),
            num_step=_steps(settings),
            **arguments,
        )
    except OmnivoiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - inference errors of the engine
        raise OmnivoiceError(f"OmniVoice failed on the voice '{speaker}': {exc}") from exc

    # generate() answers a list of arrays (one per requested sentence).
    first = audio[0] if isinstance(audio, (list, tuple)) and audio else audio
    samples = np.asarray(first, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        raise OmnivoiceError("OmniVoice produced no audio")

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = int(getattr(engine, "sampling_rate", SAMPLE_RATE) or SAMPLE_RATE)
    try:
        sf.write(str(path), samples, sample_rate, subtype="PCM_16")
    except Exception as exc:  # noqa: BLE001 - disk/writer errors
        raise OmnivoiceError(f"OmniVoice could not write {path.name}: {exc}") from exc

    if not path.is_file() or path.stat().st_size < 44:
        raise OmnivoiceError("OmniVoice produced no audio")
    return path


__all__ = [
    "DEFAULT_STEPS",
    "DEFAULT_VOICE",
    "MODEL_ID",
    "SAMPLE_RATE",
    "OmnivoiceError",
    "available",
    "load",
    "resolve_voice",
    "synthesize",
    "unload",
]
