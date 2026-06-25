"""Artifact manifest helpers for reproducible training outputs."""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any


def file_manifest(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"path": str(path), "size": size, "sha256": digest.hexdigest()}


def directory_manifest(path_value: str) -> dict[str, Any]:
    root = Path(path_value)
    files = [
        file_manifest(str(file)) | {"path": str(file.relative_to(root))}
        for file in sorted(item for item in root.rglob("*") if item.is_file())
    ]
    digest = hashlib.sha256()
    for file in files:
        digest.update(
            f"{file['path']}:{file['size']}:{file['sha256']}\n".encode()
        )
    return {
        "root": str(root),
        "file_count": len(files),
        "sha256": digest.hexdigest(),
        "files": files,
    }


def publish_directory(source_path: str, target_path: str) -> dict[str, Any]:
    """Atomically publish a directory tree to a stable runtime path."""
    source = Path(source_path)
    target = Path(target_path)

    if not source.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    staging = target.parent / f".{target.name}.staging-{token}"
    backup = target.parent / f".{target.name}.backup-{token}"

    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging)

    try:
        if target.exists():
            target.replace(backup)
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)

    return directory_manifest(str(target))
