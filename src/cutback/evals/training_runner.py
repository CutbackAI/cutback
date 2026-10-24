"""Isolated offline HF/PEFT subprocess. No dataset-supplied code, commands or network models."""

import argparse
import json
import time
from pathlib import Path


def train(config_path: Path):
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    config = json.loads(config_path.read_text(encoding="utf-8"))
    spec = config["specification"]
    started = time.monotonic()
    torch.manual_seed(spec["seed"])
    torch.set_num_threads(1)
    device = spec["device"]
    if device == "cuda" and (not torch.cuda.is_available() or torch.cuda.device_count() != 1):
        raise ValueError("ONE_EXPLICIT_GPU_REQUIRED")
    tokenizer = AutoTokenizer.from_pretrained(
        config["base_model"], local_files_only=True, trust_remote_code=False
    )
    if tokenizer.eos_token_id is None:
        raise ValueError("LOCAL_TOKENIZER_EOS_REQUIRED")
    base = AutoModelForCausalLM.from_pretrained(
        config["base_model"],
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        dtype=torch.float32,
    )
    base.config.use_cache = False
    model = get_peft_model(
        base,
        LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=spec["lora_rank"],
            lora_alpha=2 * spec["lora_rank"],
            lora_dropout=0,
            target_modules="all-linear",
            bias="none",
        ),
    )
    model.to(device)
    records = json.loads(Path(config["records"]).read_text(encoding="utf-8"))
    sequences = []
    for case in records:
        # Target-only loss: inputs are conditioning, not teacher labels. Test/validation
        # examples never enter this subprocess. No truncation silently changes labels.
        prompt = tokenizer.encode(case["input"] + "\n", add_special_tokens=False)
        target = tokenizer.encode(case["target"], add_special_tokens=False) + [
            tokenizer.eos_token_id
        ]
        if not target or len(prompt) + len(target) > spec["max_sequence_tokens"]:
            raise ValueError("TRAINING_EXAMPLE_EXCEEDS_FROZEN_SEQUENCE_BOUND")
        sequences.append((prompt + target, [-100] * len(prompt) + target))
    if not sequences:
        raise ValueError("NO_AUTHORIZED_TRAINING_EXAMPLES")
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=spec["learning_rate"]
    )
    model.train()
    losses = []
    for step in range(spec["max_steps"]):
        if time.monotonic() - started >= spec["max_wall_seconds"]:
            raise TimeoutError("TRAINING_WALL_CAP")
        tokens, labels = sequences[step % len(sequences)]
        input_ids = torch.tensor([tokens], device=device)
        loss = model(
            input_ids=input_ids,
            attention_mask=torch.ones_like(input_ids),
            labels=torch.tensor([labels], device=device),
        ).loss
        if not torch.isfinite(loss):
            raise ValueError("NONFINITE_TRAINING_LOSS")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        losses.append(float(loss.detach().cpu()))
    output = Path(config["output"])
    model.save_pretrained(output, safe_serialization=True)
    result = {
        "steps": len(losses),
        "training_examples": len(sequences),
        "first_training_loss": losses[0],
        "last_training_loss": losses[-1],
        "semantic_quality": "NOT_VERIFIED",
        "heldout_evaluation_required": True,
    }
    (output / "metrics.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    train(parser.parse_args().config)
