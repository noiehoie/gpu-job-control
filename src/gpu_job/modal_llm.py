from __future__ import annotations

from pathlib import Path
import json
import time

import modal


image = modal.Image.debian_slim(python_version="3.12").pip_install("torch", "transformers", "accelerate", "sentencepiece")
app = modal.App("gpu-job-modal-llm")


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
    requested = str(job.get("model") or "")
    if requested.startswith("Qwen/"):
        return requested
    if "7b" in requested.lower():
        return "Qwen/Qwen2.5-7B-Instruct"
    return "Qwen/Qwen2.5-0.5B-Instruct"


@app.function(image=image, gpu="L4", timeout=1800)
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
    generated = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    new_tokens = generated[:, inputs.input_ids.shape[1] :]
    output = tokenizer.batch_decode(new_tokens, skip_special_tokens=True)[0]
    return {
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "provider": "modal",
        "model": model_name,
        "text": output,
        "input_tokens": int(inputs.input_ids.shape[1]),
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
