"""Runtime hook: the windowed build has no stdio, libraries still expect it.

``--noconsole`` leaves ``sys.stdout`` / ``sys.stderr`` as *None*, which makes
``tqdm``, ``uvicorn`` and some AI wheels raise ``AttributeError`` on the first
write. Pointing them at the null device keeps every third-party library happy
while the real log goes to ``%APPDATA%\\ShortVideoAI\\logs\\engine.log``.
"""

import os
import sys

if getattr(sys, "frozen", False):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
