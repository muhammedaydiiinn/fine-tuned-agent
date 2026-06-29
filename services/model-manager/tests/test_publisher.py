from pathlib import Path

import pytest

from app.publisher import publish_directory, resolve_under


def _model_dir(path: Path, marker: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "model.bin").write_text(marker, encoding="utf-8")


def test_resolve_under_rejects_path_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_under(str(tmp_path.parent), str(tmp_path / "models"))


def test_publish_directory_atomically_replaces_target(tmp_path: Path):
    source = tmp_path / "models" / "merged" / "v2"
    target = tmp_path / "models" / "production" / "current"
    _model_dir(source, "v2")
    _model_dir(target, "v1")

    manifest = publish_directory(source, target)

    assert (target / "model.bin").read_text(encoding="utf-8") == "v2"
    assert manifest["file_count"] == 2
    assert not list(target.parent.glob(".current.backup-*"))
