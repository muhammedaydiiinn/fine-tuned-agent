"""Artifact manifest helpers for reproducible training outputs."""
from __future__ import annotations

import hashlib
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
