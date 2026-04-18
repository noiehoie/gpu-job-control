from __future__ import annotations

from pathlib import Path
import json
import sys
import time

import modal


MODAL_LLM_PYTHON_VERSION = "3.11"
MODAL_LLM_PACKAGES = ["torch", "transformers", "accelerate", "sentencepiece"]
MODAL_LLM_POST_INSTALL_COMMANDS = [
    "python -m pip install --no-build-isolation gptqmodel",
]
image = (
    modal.Image.debian_slim(python_version=MODAL_LLM_PYTHON_VERSION)
    .pip_install(*MODAL_LLM_PACKAGES)
    .run_commands(*MODAL_LLM_POST_INSTALL_COMMANDS)
)
app = modal.App("gpu-job-modal-llm")

DEFAULT_HEAVY_MODEL = "Qwen/Qwen3-32B-AWQ"
CANARY_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_ALIASES = {
    "claude-sonnet-4-6": DEFAULT_HEAVY_MODEL,
    "claude-sonnet-4.6": DEFAULT_HEAVY_MODEL,
    "claude-sonnet": DEFAULT_HEAVY_MODEL,
    "sonnet": DEFAULT_HEAVY_MODEL,
}


def _prompt(job: dict) -> str:
    metadata = job.get("metadata") or {}
    payload = metadata.get("input") if isinstance(metadata, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    prompt = str(payload.get("prompt") or "")
    system_prompt = str(payload.get("system_prompt") or "")
    if system_prompt:
        return f"{system_prompt}\n\n{prompt}"
    if prompt:
        return prompt
    input_uri = str(job.get("input_uri") or "")
    return input_uri.removeprefix("text://")


def _max_tokens(job: dict) -> int:
    payload = (job.get("metadata") or {}).get("input") or {}
    try:
        return max(1, min(int(payload.get("max_tokens") or 512), 2048))
    except (TypeError, ValueError):
        return 512


def _model_name(job: dict) -> str:
    requested = str(job.get("model") or "").strip()
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    routing = metadata.get("routing") if isinstance(metadata.get("routing"), dict) else {}
    quality_required = bool(routing.get("quality_requires_gpu"))
    normalized = requested.lower()
    if normalized in MODEL_ALIASES:
        return MODEL_ALIASES[normalized]
    if not requested:
        return DEFAULT_HEAVY_MODEL if quality_required else CANARY_MODEL
    if quality_required and normalized in {"qwen/qwen2.5-0.5b-instruct", "qwen2.5-0.5b-instruct"}:
        raise ValueError(f"quality_requires_gpu job cannot run on canary model: {requested}")
    if requested.startswith("Qwen/"):
        return requested
    if "7b" in requested.lower():
        return "Qwen/Qwen2.5-7B-Instruct"
    if quality_required:
        raise ValueError(f"unsupported quality model alias for modal llm worker: {requested}")
    return CANARY_MODEL


def _model_context_limit(model: object) -> int | None:
    config = getattr(model, "config", None)
    for name in ("max_position_embeddings", "max_sequence_length", "seq_length"):
        value = getattr(config, name, None)
        if isinstance(value, int) and value > 0:
            return value
    return None


@app.function(image=image, gpu="A100", timeout=1800)
def run_llm(job: dict) -> dict:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    started = time.time()
    model_name = _model_name(job)
    prompt = _prompt(job)
    max_tokens = _max_tokens(job)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype="auto",
        device_map="auto",
    )
    messages = [{"role": "user", "content": prompt}]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer([text], return_tensors="pt").to(model.device)
    input_tokens = int(inputs.input_ids.shape[1])
    context_limit = _model_context_limit(model)
    if context_limit is not None and input_tokens + max_tokens > context_limit:
        raise ValueError(
            f"prompt exceeds model context: input_tokens={input_tokens} "
            f"max_new_tokens={max_tokens} context_limit={context_limit} model={model_name}"
        )
    generated = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    new_tokens = generated[:, inputs.input_ids.shape[1] :]
    output = tokenizer.batch_decode(new_tokens, skip_special_tokens=True)[0]
    return {
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "provider": "modal",
        "model": model_name,
        "text": output,
        "input_tokens": input_tokens,
        "output_chars": len(output),
        "runtime_seconds": round(time.time() - started, 3),
    }


@app.local_entrypoint()
def main(job_json: str, artifact_dir: str):
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    stdout = ""
    stderr = ""
    try:
        job = json.loads(Path(job_json).read_text())
        result = run_llm.remote(job)
        stdout = f"modal llm completed model={result.get('model')} chars={result.get('output_chars')}\n"
        ok = bool(result.get("text"))
    except Exception as exc:
        result = {"text": "", "error": str(exc), "provider": "modal"}
        stderr = str(exc)
        ok = False
    metrics = {
        "job_id": result.get("job_id"),
        "provider": "modal",
        "model": result.get("model"),
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result.get("runtime_seconds"),
        "input_tokens": result.get("input_tokens"),
        "output_chars": result.get("output_chars"),
    }
    verify = {
        "ok": ok,
        "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "missing": [],
        "checks": {"text_nonempty": ok},
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "stdout.log").write_text(stdout)
    (out / "stderr.log").write_text(stderr)
    sys.exit(0 if ok else 1)
