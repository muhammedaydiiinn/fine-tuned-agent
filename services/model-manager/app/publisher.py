from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any


REQUIRED_MODEL_FILES = ("config.json",)
WEIGHT_PATTERNS = ("*.safetensors", "*.bin")


def resolve_under(path_value: str, root_value: str) -> Path:
    root = Path(root_value).resolve()
    path = Path(path_value).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"path must be inside {root}")
    return path


def inspect_artifact(path_value: str) -> dict[str, Any]:
    root = Path(path_value).resolve()
    if not root.is_dir():
        raise ValueError(f"artifact directory does not exist: {root}")
    missing = [name for name in REQUIRED_MODEL_FILES if not (root / name).is_file()]
    weights = sorted({
        file
        for pattern in WEIGHT_PATTERNS
        for file in root.glob(pattern)
        if file.is_file()
    })
    if not weights:
        missing.append("*.safetensors or *.bin")
    if missing:
        raise ValueError(f"missing required artifact files: {', '.join(missing)}")
    return directory_manifest(root)


def directory_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for file in sorted(item for item in root.rglob("*") if item.is_file()):
        file_digest = hashlib.sha256()
        size = 0
        with file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                file_digest.update(chunk)
        relative = str(file.relative_to(root))
        checksum = file_digest.hexdigest()
        digest.update(f"{relative}:{size}:{checksum}\n".encode())
        files.append({"path": relative, "size": size, "sha256": checksum})
    return {
        "root": str(root),
        "sha256": digest.hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def publish_directory(source_path: Path, target_path: Path) -> dict[str, Any]:
    if not source_path.is_dir():
        raise ValueError(f"source directory does not exist: {source_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    staging = target_path.parent / f".{target_path.name}.staging-{token}"
    backup = target_path.parent / f".{target_path.name}.backup-{token}"

    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source_path, staging)
    manifest = inspect_artifact(str(staging))

    swapped = False
    try:
        if target_path.exists():
            target_path.replace(backup)
        staging.replace(target_path)
        swapped = True
        published = directory_manifest(target_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if swapped:
            shutil.rmtree(target_path, ignore_errors=True)
        if backup.exists() and not target_path.exists():
            backup.replace(target_path)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)

    published["source_sha256"] = manifest["sha256"]
    return published
