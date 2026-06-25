import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from jobs.artifacts import begin_directory_publication, publish_directory


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_publish_directory_copies_source_tree_into_stable_target(tmp_path):
    source = tmp_path / "merged" / "candidate-a"
    target = tmp_path / "candidates" / "current"
    _write_tree(
        source,
        {
            "config.json": '{"model":"a"}',
            "weights/model.bin": "aaa",
        },
    )

    manifest = publish_directory(str(source), str(target))

    assert (target / "config.json").read_text(encoding="utf-8") == '{"model":"a"}'
    assert (target / "weights" / "model.bin").read_text(encoding="utf-8") == "aaa"
    assert manifest["root"] == str(target)
    assert manifest["file_count"] == 2


def test_publish_directory_replaces_existing_runtime_tree(tmp_path):
    source = tmp_path / "merged" / "candidate-b"
    target = tmp_path / "candidates" / "current"
    _write_tree(source, {"config.json": '{"model":"b"}', "model.bin": "bbb"})
    _write_tree(target, {"stale.txt": "old"})

    publish_directory(str(source), str(target))

    assert not (target / "stale.txt").exists()
    assert (target / "config.json").read_text(encoding="utf-8") == '{"model":"b"}'
    assert (target / "model.bin").read_text(encoding="utf-8") == "bbb"


def test_publish_directory_restores_backup_on_swap_failure(tmp_path):
    source = tmp_path / "merged" / "candidate-c"
    target = tmp_path / "candidates" / "current"
    _write_tree(source, {"model.bin": "new"})
    _write_tree(target, {"model.bin": "original"})

    original_replace = Path.replace

    def fail_on_staging_replace(self, other):
        # Let the backup rename succeed, fail on staging → target swap.
        if "staging" in self.name:
            raise OSError("simulated swap failure")
        return original_replace(self, other)

    with patch.object(Path, "replace", fail_on_staging_replace):
        with pytest.raises(OSError):
            publish_directory(str(source), str(target))

    # Original target must be restored from backup.
    assert target.exists()
    assert (target / "model.bin").read_text(encoding="utf-8") == "original"


def test_publish_directory_raises_when_source_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="source directory does not exist"):
        publish_directory(str(tmp_path / "nonexistent"), str(tmp_path / "target"))


def test_publish_directory_manifest_contains_sha256_and_file_count(tmp_path):
    source = tmp_path / "src"
    _write_tree(source, {"a.bin": "x", "b.bin": "y"})

    manifest = publish_directory(str(source), str(tmp_path / "dst"))

    assert manifest["file_count"] == 2
    assert len(manifest["sha256"]) == 64
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])


def test_directory_publication_can_rollback_after_successful_swap(tmp_path):
    source = tmp_path / "merged" / "candidate-d"
    target = tmp_path / "candidates" / "current"
    _write_tree(source, {"model.bin": "new"})
    _write_tree(target, {"model.bin": "original"})

    publication = begin_directory_publication(str(source), str(target))
    assert (target / "model.bin").read_text(encoding="utf-8") == "new"

    publication.rollback()

    assert (target / "model.bin").read_text(encoding="utf-8") == "original"
    assert not list(target.parent.glob(".current.backup-*"))


def test_directory_publication_finalize_removes_backup(tmp_path):
    source = tmp_path / "merged" / "candidate-e"
    target = tmp_path / "candidates" / "current"
    _write_tree(source, {"model.bin": "new"})
    _write_tree(target, {"model.bin": "original"})

    publication = begin_directory_publication(str(source), str(target))
    publication.finalize()

    assert (target / "model.bin").read_text(encoding="utf-8") == "new"
    assert not list(target.parent.glob(".current.backup-*"))


def test_manifest_failure_restores_previous_target(tmp_path):
    source = tmp_path / "merged" / "candidate-f"
    target = tmp_path / "candidates" / "current"
    _write_tree(source, {"model.bin": "new"})
    _write_tree(target, {"model.bin": "original"})

    with patch("jobs.artifacts.directory_manifest", side_effect=OSError("hash failed")):
        with pytest.raises(OSError, match="hash failed"):
            begin_directory_publication(str(source), str(target))

    assert (target / "model.bin").read_text(encoding="utf-8") == "original"
    assert not list(target.parent.glob(".current.backup-*"))
    assert not list(target.parent.glob(".current.staging-*"))
