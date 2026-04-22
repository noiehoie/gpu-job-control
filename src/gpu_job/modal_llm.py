from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import time

import modal


MODAL_LLM_PYTHON_VERSION = "3.11"
MODAL_LLM_PACKAGES = ["torch", "transformers", "accelerate", "sentencepiece", "huggingface_hub"]
MODAL_LLM_POST_INSTALL_COMMANDS: list[str] = []
MODAL_LLM_CACHE_VOLUME_NAME = "gpu-job-modal-llm-cache"
MODAL_LLM_CACHE_MOUNT = "/cache"
MODAL_LLM_HF_HOME = f"{MODAL_LLM_CACHE_MOUNT}/huggingface"
MODAL_LLM_HF_HUB_CACHE = f"{MODAL_LLM_HF_HOME}/hub"
image = (
    modal.Image.debian_slim(python_version=MODAL_LLM_PYTHON_VERSION)
    .pip_install(*MODAL_LLM_PACKAGES)
    .run_commands(*MODAL_LLM_POST_INSTALL_COMMANDS)
    .env(
        {
            "HF_HOME": MODAL_LLM_HF_HOME,
            "HF_HUB_CACHE": MODAL_LLM_HF_HUB_CACHE,
            "TRANSFORMERS_CACHE": f"{MODAL_LLM_HF_HOME}/transformers",
        }
    )
)
app = modal.App("gpu-job-modal-llm")
model_cache_volume = modal.Volume.from_name(MODAL_LLM_CACHE_VOLUME_NAME, create_if_missing=True)

DEFAULT_HEAVY_MODEL = "Qwen/Qwen2.5-32B-Instruct"
CANARY_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_CONTEXT_LIMITS = {
    DEFAULT_HEAVY_MODEL: 32768,
    CANARY_MODEL: 32768,
    "Qwen/Qwen2.5-7B-Instruct": 32768,
}
MODEL_ALIASES = {
    "claude-sonnet-4-6": DEFAULT_HEAVY_MODEL,
    "claude-sonnet-4.6": DEFAULT_HEAVY_MODEL,
    "claude-sonnet": DEFAULT_HEAVY_MODEL,
    "claude-haiku-4-5-20251001": DEFAULT_HEAVY_MODEL,
    "claude-haiku-4.5": DEFAULT_HEAVY_MODEL,
    "claude-haiku": DEFAULT_HEAVY_MODEL,
    "sonnet": DEFAULT_HEAVY_MODEL,
    "haiku": DEFAULT_HEAVY_MODEL,
}


def _prompt(job: dict) -> str:
    metadata = job.get("metadata") or {}
    payload = metadata.get("input") if isinstance(metadata, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    prompt = str(payload.get("prompt") or "")
    system_prompt = str(payload.get("system_prompt") or "")
    items = payload.get("items")
    items_text = ""
    if isinstance(items, list):
        items_text = json.dumps({"items": items}, ensure_ascii=False, sort_keys=True)
    if system_prompt:
        if prompt and items_text:
            return f"{system_prompt}\n\n{prompt}\n\nINPUT_JSON:\n{items_text}"
        if items_text:
            return f"{system_prompt}\n\nINPUT_JSON:\n{items_text}"
        return f"{system_prompt}\n\n{prompt}"
    if prompt:
        if items_text:
            return f"{prompt}\n\nINPUT_JSON:\n{items_text}"
        return prompt
    if items_text:
        return items_text
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
    if normalized.startswith("claude-"):
        return DEFAULT_HEAVY_MODEL
    if not requested:
        return DEFAULT_HEAVY_MODEL
    if quality_required and normalized in {"qwen/qwen2.5-0.5b-instruct", "qwen2.5-0.5b-instruct"}:
        raise ValueError(f"quality_requires_gpu job cannot run on canary model: {requested}")
    if requested.startswith("Qwen/"):
        return requested
    if "7b" in requested.lower():
        return "Qwen/Qwen2.5-7B-Instruct"
    raise ValueError(f"unsupported model alias for modal llm worker: {requested}")


def _model_context_limit(model: object) -> int | None:
    config = getattr(model, "config", None)
    for name in ("max_position_embeddings", "max_sequence_length", "seq_length"):
        value = getattr(config, name, None)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _known_context_limit(model_name: str) -> int | None:
    return MODEL_CONTEXT_LIMITS.get(model_name)


def _commit_cache() -> None:
    try:
        model_cache_volume.commit()
    except Exception as exc:
        raise RuntimeError("failed to commit Modal Hugging Face cache volume") from exc


def _hf_model_cache_exists(model_name: str) -> bool:
    model_dir = Path(MODAL_LLM_HF_HUB_CACHE) / f"models--{model_name.replace('/', '--')}"
    return any((model_dir / "snapshots").glob("*")) if (model_dir / "snapshots").is_dir() else False


@app.function(image=image, gpu="A100-80GB", timeout=3600, volumes={MODAL_LLM_CACHE_MOUNT: model_cache_volume})
def warm_llm_cache(model_name: str = DEFAULT_HEAVY_MODEL) -> dict:
    from huggingface_hub import snapshot_download

    started = time.time()
    os.makedirs(MODAL_LLM_HF_HOME, exist_ok=True)
    cache_hit_before_download = _hf_model_cache_exists(model_name)
    snapshot_download(model_name)
    _commit_cache()
    return {
        "provider": "modal",
        "worker_image": "gpu-job-modal-llm",
        "model": model_name,
        "cache_volume": MODAL_LLM_CACHE_VOLUME_NAME,
        "hf_home": MODAL_LLM_HF_HOME,
        "cache_hit_before_download": cache_hit_before_download,
        "cache_hit_after_download": _hf_model_cache_exists(model_name),
        "runtime_seconds": round(time.time() - started, 3),
    }


@app.function(image=image, gpu="A100-80GB", timeout=3600, volumes={MODAL_LLM_CACHE_MOUNT: model_cache_volume})
def run_llm(job: dict) -> dict:
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForCausalLM, AutoTokenizer

    started = time.time()
    model_name = _model_name(job)
    cache_hit_before_download = _hf_model_cache_exists(model_name)
    prompt = _prompt(job)
    max_tokens = _max_tokens(job)
    os.makedirs(MODAL_LLM_HF_HOME, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    _commit_cache()
    messages = [{"role": "user", "content": prompt}]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer([text], return_tensors="pt")
    input_tokens = int(inputs.input_ids.shape[1])
    context_limit = _known_context_limit(model_name)
    if context_limit is not None and input_tokens + max_tokens > context_limit:
        raise ValueError(
            f"prompt exceeds model context before weight load: input_tokens={input_tokens} "
            f"max_new_tokens={max_tokens} context_limit={context_limit} model={model_name}"
        )
    snapshot_download(model_name)
    _commit_cache()
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype="auto",
        device_map="auto",
    )
    _commit_cache()
    inputs = inputs.to(model.device)
    context_limit = _model_context_limit(model)
    if context_limit is not None and input_tokens + max_tokens > context_limit:
        raise ValueError(
            f"prompt exceeds model context: input_tokens={input_tokens} "
            f"max_new_tokens={max_tokens} context_limit={context_limit} model={model_name}"
        )
    generated = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    new_tokens = generated[:, inputs.input_ids.shape[1] :]
    output = tokenizer.batch_decode(new_tokens, skip_special_tokens=True)[0]
    gpu_name = ""
    gpu_memory_used_mb = 0
    try:
        import torch

        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_memory_used_mb = int(torch.cuda.max_memory_allocated() / (1024 * 1024))
    except Exception:
        pass
    return {
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "provider": "modal",
        "model": model_name,
        "text": output,
        "input_tokens": input_tokens,
        "output_chars": len(output),
        "runtime_seconds": round(time.time() - started, 3),
        "probe_info": {
            "provider": "modal",
            "worker_image": "gpu-job-modal-llm",
            "loaded_model_id": model_name,
            "cache_hit": cache_hit_before_download,
            "cache_volume": MODAL_LLM_CACHE_VOLUME_NAME,
            "hf_home": MODAL_LLM_HF_HOME,
            "gpu_name": gpu_name or None,
            "gpu_count": 1 if gpu_name else None,
            "gpu_memory_used_mb": gpu_memory_used_mb or None,
        },
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
        "gpu_memory_used_mb": (result.get("probe_info") or {}).get("gpu_memory_used_mb"),
        "gpu_name": (result.get("probe_info") or {}).get("gpu_name"),
        "cache_hit": (result.get("probe_info") or {}).get("cache_hit"),
    }
    probe_info = (
        result.get("probe_info")
        if isinstance(result.get("probe_info"), dict)
        else {
            "provider": "modal",
            "worker_image": "gpu-job-modal-llm",
            "loaded_model_id": result.get("model"),
            "cache_hit": None,
            "error": result.get("error"),
        }
    )
    verify = {
        "ok": ok,
        "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "missing": [],
        "checks": {"text_nonempty": ok},
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "probe_info.json").write_text(json.dumps(probe_info, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "stdout.log").write_text(stdout)
    (out / "stderr.log").write_text(stderr)
    sys.exit(0 if ok else 1)


@app.local_entrypoint()
def warm_cache(model_name: str = DEFAULT_HEAVY_MODEL, artifact_dir: str = ""):
    started = time.time()
    try:
        result = warm_llm_cache.remote(model_name)
        ok = bool(result.get("cache_hit_after_download"))
        error = ""
    except Exception as exc:
        result = {
            "provider": "modal",
            "worker_image": "gpu-job-modal-llm",
            "model": model_name,
            "cache_volume": MODAL_LLM_CACHE_VOLUME_NAME,
            "hf_home": MODAL_LLM_HF_HOME,
            "cache_hit_before_download": None,
            "cache_hit_after_download": False,
            "runtime_seconds": round(time.time() - started, 3),
            "error": str(exc),
        }
        ok = False
        error = str(exc)
    if artifact_dir:
        out = Path(artifact_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        (out / "stdout.log").write_text(
            f"modal llm warm_cache ok={ok} model={model_name} cache_hit_after_download={result.get('cache_hit_after_download')}\n"
        )
        (out / "stderr.log").write_text(error)
    print(json.dumps({"ok": ok, "result": result}, ensure_ascii=False, indent=2, sort_keys=True))
    sys.exit(0 if ok else 1)
