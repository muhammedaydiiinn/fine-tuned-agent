"""Merge LoRA adapter into base model and export merged_16bit."""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def merge(base_model_path: str, adapter_path: str, output_path: str, training_mode: str = "real") -> dict:
    if training_mode == "mock":
        return _merge_mock(base_model_path, adapter_path, output_path)
    return _merge_real(base_model_path, adapter_path, output_path)


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
