"""Model resolvers must follow the layout ``download()`` writes.

``download()`` puts every file in ``models/<kind>/<catalogue key>/``; a resolver
that looks at ``models/<kind>/<file>`` claims "not installed" while the Models
panel says the opposite.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services import model_manager


def _settings(tmp_path: Path) -> Settings:
    return Settings(models_dir=str(tmp_path / "models"))


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_llm_path_follows_the_catalogue_folder(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    spec = model_manager.require("qwen2.5-3b-instruct-q4_k_m")
    expected = _touch(model_manager.model_dir(spec, settings) / "qwen2.5-3b-instruct-q4_k_m.gguf")

    assert model_manager.llm_path(settings) == expected
    assert model_manager.status(spec, settings).downloaded


def test_llm_path_accepts_a_hand_copied_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    flat = _touch(settings.models_path / "llm" / settings.llm_model_file)

    assert model_manager.llm_path(settings) == flat


def test_llm_path_is_none_when_absent(tmp_path: Path) -> None:
    assert model_manager.llm_path(_settings(tmp_path)) is None


def test_the_omnivoice_entry_has_no_weights_to_download(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    spec = model_manager.require("omnivoice-v1")

    # Nothing to fetch: the torch runtime comes from pip, so the catalogue only
    # carries the install hint and the Model tab never offers a download.
    assert spec.files == ()
    assert spec.snapshot_repo is None
    assert spec.pip_hint == "pip install omnivoice"
    assert model_manager.status(spec, settings).pip_hint == "pip install omnivoice"
