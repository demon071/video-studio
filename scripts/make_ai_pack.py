"""Package the `full` engine into a downloadable AI pack (``.zip``).

    .venv\\Scripts\\python.exe scripts\\make_ai_pack.py ^
        --engine build\\dist-full\\engine ^
        --out release\\ShortVideoAI_0.1.0_ai-pack-full.zip

The archive mirrors the folder the app installs into::

    ai-pack.json                manifest (version, profile, sizes)
    engine/shortvideoai-engine.exe
    engine/_internal/...

The engine extracts it with :mod:`zipfile` on the user's machine, so ZIP64 is
used automatically when the archive passes 4 GB of entries. Run this through
``scripts/make_ai_pack.ps1`` (``npm run build:pack``) so the version, SHA256 and
output folder stay consistent with the installer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ENGINE_EXE = "shortvideoai-engine.exe"
INTERNAL_FOLDER = "_internal"
MANIFEST_NAME = "ai-pack.json"
SCHEMA = 1


def _human(bytes_count: int) -> str:
    value = float(bytes_count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _app_version(root: Path) -> str:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
        return str(data.get("version") or "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


def _torch_version(engine: Path) -> str | None:
    """Best effort: the frozen bundle ships torch's ``version.py`` as data."""
    try:
        text = (engine / INTERNAL_FOLDER / "torch" / "version.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", text)
    return match.group(1) if match else None


def _collect(engine: Path) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for path in sorted(engine.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(engine).as_posix()
        entries.append((path, f"engine/{relative}"))
    return entries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the ShortVideoAI AI pack archive")
    parser.add_argument("--engine", required=True, help="folder holding shortvideoai-engine.exe + _internal")
    parser.add_argument("--out", required=True, help="target .zip path")
    parser.add_argument("--version", default="", help="app version (default: root package.json)")
    parser.add_argument("--level", type=int, default=6, help="deflate level 1-9 (default 6)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing archive")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    engine = Path(args.engine).resolve()
    out = Path(args.out).resolve()
    if not out.is_absolute():
        out = (Path.cwd() / args.out).resolve()

    if not (engine / ENGINE_EXE).is_file():
        print(f"[FAIL] {engine / ENGINE_EXE} is missing - build the full engine first:", file=sys.stderr)
        print("       npm run build:backend:full -- -DistPath build\\dist-full -SkipSmokeTest", file=sys.stderr)
        return 1
    if not (engine / INTERNAL_FOLDER).is_dir():
        print(f"[FAIL] {engine / INTERNAL_FOLDER} is missing - not a onedir bundle", file=sys.stderr)
        return 1
    if out.exists() and not args.force:
        print(f"[FAIL] {out} already exists (use --force to replace it)", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    entries = _collect(engine)
    total = sum(path.stat().st_size for path, _ in entries)
    version = (args.version or _app_version(root)).strip()
    level = max(1, min(9, args.level))

    manifest = {
        "schema": SCHEMA,
        "product": "ShortVideoAI",
        "version": version,
        "profile": "full",
        "created": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "files": len(entries),
        "bytes": total,
        "python": platform.python_version(),
    }
    torch_version = _torch_version(engine)
    if torch_version:
        manifest["torch"] = torch_version

    print(f"  AI pack: {version} (full)")
    print(f"  source : {engine}  ({len(entries)} files, {_human(total)})")
    print(f"  output : {out}")
    print(f"  deflate level {level} - a 3.5 GB bundle takes several minutes")

    started = time.monotonic()
    written = 0
    next_report = 0.1
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=level, allowZip64=True) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
        for index, (path, arcname) in enumerate(entries, start=1):
            archive.write(path, arcname)
            written += path.stat().st_size
            fraction = written / total if total else 1.0
            if fraction >= next_report:
                print(f"    {fraction * 100:5.1f}%  {_human(written)}  ({index}/{len(entries)})", flush=True)
                next_report = min(1.0, next_report + 0.1)

    elapsed = time.monotonic() - started
    size = out.stat().st_size
    print("")
    print(f"  done in {elapsed:.1f}s")
    print(f"  archive : {_human(size)}  ({size / total * 100:.0f}% of the raw bundle)")
    print(f"  sha256  : {_sha256(out)}")
    print("")
    print("  Publish this file and point SVA_AI_PACK_URL at it (backend/.env), or")
    print("  hand it to users who install it from the Model tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
