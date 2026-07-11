"""LoRA fine-tuning job — mock mode for local dev, real mode for GPU server."""
import json
import logging
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def train(
    dataset_path: str,
    output_adapter_path: str,
    config: dict,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """Train a LoRA adapter on the given JSONL dataset.

    config keys (all optional, fall back to defaults):
        base_model_path, lora_rank, lora_alpha, lora_dropout,
        epochs, lr, batch_size, gradient_accumulation_steps, max_seq_length,
        warmup_ratio, training_mode
    """
    mode = config.get("training_mode", "real")
    if mode == "mock":
        return _train_mock(dataset_path, output_adapter_path, config, progress_cb)
    return _train_real(dataset_path, output_adapter_path, config, progress_cb)


# ── Mock mode ────────────────────────────────────────────────────────────────

def _train_mock(
    dataset_path: str,
    output_adapter_path: str,
    config: dict,
    progress_cb: Callable[[int, int], None] | None,
) -> dict:
    with open(dataset_path, encoding="utf-8") as fh:
        rows = [l for l in fh if l.strip()]
    total_rows = len(rows)

    epochs = config.get("epochs", 3)
    total_steps = epochs * max(1, total_rows // max(1, config.get("batch_size", 4)))
    logger.info("mock train — %d rows, %d epochs, ~%d steps", total_rows, epochs, total_steps)

    for step in range(1, total_steps + 1):
        time.sleep(0.05)
        if progress_cb:
            progress_cb(step, total_steps)

    out = Path(output_adapter_path)
    out.mkdir(parents=True, exist_ok=True)
    adapter_cfg = {
        "base_model_name_or_path": config.get("base_model_path", "mock"),
        "peft_type": "LORA",
        "r": config.get("lora_rank", 16),
        "lora_alpha": config.get("lora_alpha", 32),
        "lora_dropout": config.get("lora_dropout", 0.05),
    }
    (out / "adapter_config.json").write_text(json.dumps(adapter_cfg, indent=2))
    (out / "adapter_model.safetensors").write_bytes(b"")  # placeholder
    logger.info("mock adapter saved → %s", output_adapter_path)
    return {"adapter_path": output_adapter_path, "steps": total_steps, "mode": "mock"}


# ── Real mode (GPU server with Unsloth/TRL) ──────────────────────────────────

def _train_real(
    dataset_path: str,
    output_adapter_path: str,
    config: dict,
    progress_cb: Callable[[int, int], None] | None,
) -> dict:
    # Imports deferred so mock mode never requires torch
    try:
        from unsloth import FastLanguageModel
        _UNSLOTH = True
    except ImportError:
        _UNSLOTH = False
        logger.warning("unsloth not found — falling back to transformers + peft")

    import torch
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer
    from transformers import TrainerCallback, TrainerControl, TrainerState, TrainingArguments

    base_model_path = config["base_model_path"]
    lora_rank = config.get("lora_rank", 16)
    lora_alpha = config.get("lora_alpha", 32)
    lora_dropout = config.get("lora_dropout", 0.05)
    epochs = config.get("epochs", 3)
    lr = config.get("lr", 2e-4)
    batch_size = config.get("batch_size", 4)
    grad_accum = config.get("gradient_accumulation_steps", 4)
    max_seq = config.get("max_seq_length", 2048)
    warmup = config.get("warmup_ratio", 0.05)

    target_modules = [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ]

    if _UNSLOTH:
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=base_model_path,
            max_seq_length=max_seq,
            load_in_4bit=True,
        )
        model = FastLanguageModel.get_peft_model(
            model,
            r=lora_rank,
            target_modules=target_modules,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            bias="none",
            use_gradient_checkpointing="unsloth",
        )
    else:
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model = AutoModelForCausalLM.from_pretrained(
            base_model_path, quantization_config=bnb, device_map="auto"
        )
        model = prepare_model_for_kbit_training(model)
        lora_cfg = LoraConfig(
            r=lora_rank,
            lora_alpha=lora_alpha,
            target_modules=target_modules,
            lora_dropout=lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_cfg)
        tokenizer = AutoTokenizer.from_pretrained(base_model_path)

    dataset = load_dataset("json", data_files={"train": dataset_path}, split="train")

    class ProgressCallback(TrainerCallback):
        def __init__(self, cb):
            self._cb = cb
            self._total = 0

        def on_train_begin(self, args: TrainingArguments, state: TrainerState, control: TrainerControl, **kw):
            self._total = state.max_steps

        def on_step_end(self, args: TrainingArguments, state: TrainerState, control: TrainerControl, **kw):
            if self._cb:
                self._cb(state.global_step, self._total)

    sft_args = SFTConfig(
        output_dir=output_adapter_path,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=grad_accum,
        learning_rate=lr,
        warmup_ratio=warmup,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        save_steps=0,
        report_to="none",
        max_length=max_seq,
        # Recompute activations instead of storing them — the large (~1k token)
        # system prompt otherwise blows up activation memory and OOMs on the
        # shared GPU. Non-reentrant is required with PEFT/kbit training.
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        # NOTE: assistant_only_loss (trl 1.7 response-only masking) needs the chat
        # template's {% generation %} markers, which THIS model's Qwen template
        # lacks ("template is not training-compatible"). So it is disabled; we
        # rely on a broad, fact-consistent anchor dataset to keep SFT from
        # degrading. Revisit with a patched template to re-enable masking.
        assistant_only_loss=False,
    )

    mask_responses = config.get("train_on_responses_only", True)
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=dataset,
        args=sft_args,
        callbacks=[ProgressCallback(progress_cb)],
    )
    if mask_responses and _UNSLOTH:
        try:
            from unsloth.chat_templates import train_on_responses_only as _mask
            trainer = _mask(
                trainer,
                instruction_part="<|im_start|>user\n",
                response_part="<|im_start|>assistant\n",
            )
        except Exception:
            logger.warning(
                "unsloth response-only masking unavailable — training on full sequence",
                exc_info=True,
            )

    logger.info("Starting LoRA training — %s, epochs=%d, lr=%s", base_model_path, epochs, lr)
    trainer.train()
    trainer.save_model(output_adapter_path)
    tokenizer.save_pretrained(output_adapter_path)

    logger.info("Adapter saved → %s", output_adapter_path)
    return {
        "adapter_path": output_adapter_path,
        "steps": trainer.state.global_step,
        "mode": "unsloth" if _UNSLOTH else "trl",
    }
