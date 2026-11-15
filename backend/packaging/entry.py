"""PyInstaller entry point for the ShortVideoAI engine.

Identical behaviour to ``python -m app.main``; kept as a tiny module so the
frozen binary never has to import itself as a package.
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
    # Required before anything that may spawn workers (onnxruntime, ctranslate2).
    multiprocessing.freeze_support()

    from app.main import main as run_engine

    run_engine()


if __name__ == "__main__":
    sys.exit(main())
