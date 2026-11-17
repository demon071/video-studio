"""Import-level sanity check for the AI stack.

    .venv\\Scripts\\python.exe backend\\scripts\\verify_stack.py

Reports every optional dependency as OK / MISSING so a broken wheel is visible
before the pipeline runs, and prints the compute device the engine will use.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODULES = [
    ("numpy", "numeric foundation"),
    ("scipy", "resampling / dsp"),
    ("soundfile", "wav io"),
    ("torch", "compute backend"),
    ("faster_whisper", "offline STT"),
    ("ctranslate2", "whisper runtime"),
    ("sherpa_onnx", "offline STT (SenseVoice)"),
    ("onnxruntime", "onnx inference"),
    ("audio_separator", "vocal isolation"),
    ("llama_cpp", "offline LLM (GGUF)"),
    ("google.genai", "Gemini translation"),
    ("openai", "OpenAI / Groq-compatible API"),
]

# Dubbing works without these: CapCut needs no package, so a miss only costs
# the ability to pick OmniVoice locally.
OPTIONAL = [
    ("omnivoice", "offline TTS (OmniVoice, torch)"),
]


def _probe(name: str, purpose: str) -> bool:
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "")
        print(f"{name:18} {'OK':10} {version}  ({purpose})")
        return True
    except Exception as exc:  # noqa: BLE001 - report, never crash the probe
        print(f"{name:18} {'MISSING':10} {type(exc).__name__}: {exc}  ({purpose})")
        return False


def main() -> int:
    failures = 0
    print()
    print(f"{'module':18} {'status':10} version / detail")
    print("-" * 74)

    for name, purpose in MODULES:
        if not _probe(name, purpose):
            failures += 1

    for name, purpose in OPTIONAL:
        _probe(name, purpose)

    print("-" * 74)

    try:
        import torch

        cuda = torch.cuda.is_available()
        device = f"cuda ({torch.cuda.get_device_name(0)})" if cuda else "cpu"
        print(f"compute device : {device}")
        if cuda:
            total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            print(f"vram           : {total:.1f} GB")
            print(f"whisper plan   : {'medium / large-v3-turbo' if total >= 6 else 'small (float16)'}")
        else:
            print("whisper plan   : small / base (int8)")
    except Exception as exc:  # noqa: BLE001
        print(f"compute device : unavailable ({exc})")

    print()
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
