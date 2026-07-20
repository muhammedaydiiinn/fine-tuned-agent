"""Merge LoRA adapter into base model and export merged_16bit."""
import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Must stay in sync with agent-backend model_runtime._ARTIFACT_SIDECAR / signature.
_ARTIFACT_SIDECAR = ".artifact_manifest.json"


def _write_artifact_sidecar(output_path: str) -> None:
    """Precompute the sha256 manifest so read-only consumers (agent-backend eval /
    deploy) skip the ~48 s re-hash of a 16 GB model. Best-effort: never fail merge."""
    try:
        root = Path(output_path).resolve()
        files = sorted(
            i for i in root.rglob("*") if i.is_file() and i.name != _ARTIFACT_SIDECAR
        )
        sig = hashlib.sha256()
        for f in files:
            st = f.stat()
            sig.update(f"{f.relative_to(root)}:{st.st_size}:{st.st_mtime_ns}\n".encode())
        entries: list[dict] = []
        digest = hashlib.sha256()
        for f in files:
            fd = hashlib.sha256()
            with f.open("rb") as h:
                for c in iter(lambda: h.read(1024 * 1024), b""):
                    fd.update(c)
            rel = str(f.relative_to(root))
            size = f.stat().st_size
            checksum = fd.hexdigest()
            digest.update(f"{rel}:{size}:{checksum}\n".encode())
            entries.append({"path": rel, "size": size, "sha256": checksum})
        manifest = {
            "valid": True, "root": str(root), "sha256": digest.hexdigest(),
            "file_count": len(entries), "files": entries,
        }
        (root / _ARTIFACT_SIDECAR).write_text(
            json.dumps({"signature": sig.hexdigest(), "manifest": manifest})
        )
        logger.info("artifact sidecar written → %s (sha=%s)", output_path, manifest["sha256"][:16])
    except Exception:  # noqa: BLE001 — sidecar is an optimization, not required
        logger.warning("artifact sidecar write failed (non-fatal)", exc_info=True)


def merge(base_model_path: str, adapter_path: str, output_path: str, training_mode: str = "real") -> dict:
    if training_mode == "mock":
        return _merge_mock(base_model_path, adapter_path, output_path)
    result = _merge_real(base_model_path, adapter_path, output_path)
    _write_artifact_sidecar(output_path)
    return result


def _merge_mock(base_model_path: str, adapter_path: str, output_path: str) -> dict:
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    cfg = {
        "base_model": base_model_path,
        "adapter": adapter_path,
        "merge_method": "mock",
    }
    (out / "config.json").write_text(json.dumps(cfg, indent=2))
    (out / "model.safetensors").write_bytes(b"")
    logger.info("mock merge done → %s", output_path)
    return {"merged_path": output_path, "mode": "mock"}


def _merge_real(base_model_path: str, adapter_path: str, output_path: str) -> dict:
    try:
        from unsloth import FastLanguageModel
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=adapter_path,
            max_seq_length=2048,
            load_in_4bit=True,
        )
        model.save_pretrained_merged(output_path, tokenizer, save_method="merged_16bit")
        logger.info("Unsloth merged_16bit → %s", output_path)
        return {"merged_path": output_path, "mode": "unsloth"}
    except ImportError:
        pass

    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    base = AutoModelForCausalLM.from_pretrained(base_model_path, device_map="cpu")
    model = PeftModel.from_pretrained(base, adapter_path)
    merged = model.merge_and_unload()
    tokenizer = AutoTokenizer.from_pretrained(adapter_path)

    Path(output_path).mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(output_path)
    tokenizer.save_pretrained(output_path)
    logger.info("peft merge_and_unload → %s", output_path)
    return {"merged_path": output_path, "mode": "peft"}
