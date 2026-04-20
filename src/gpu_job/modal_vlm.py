from __future__ import annotations

from pathlib import Path
import base64
import io
import json
import time

import modal


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("torch", "torchvision", "transformers", "accelerate", "pillow", "pymupdf", "sentencepiece")
)
app = modal.App("gpu-job-modal-vlm")


def _payload(job: dict) -> dict:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    value = metadata.get("input") if isinstance(metadata, dict) else {}
    return value if isinstance(value, dict) else {}


def _prompt(job: dict) -> str:
    payload = _payload(job)
    prompt = str(payload.get("prompt") or "")
    if prompt:
        return prompt
    if job.get("job_type") == "pdf_ocr":
        return "Extract all visible text from this document page. Return concise OCR text only."
    return "Describe the image and extract all visible text. Return concise Japanese text when possible."


def _max_tokens(job: dict) -> int:
    payload = _payload(job)
    try:
        return max(1, min(int(payload.get("max_tokens") or 512), 2048))
    except (TypeError, ValueError):
        return 512


def _model_name(job: dict) -> str:
    requested = str(job.get("model") or "").strip()
    if requested and not requested.startswith("local-deterministic"):
        return requested
    return "HuggingFaceTB/SmolVLM-256M-Instruct"


def _image_from_job(job: dict):
    from PIL import Image, ImageDraw

    payload = _payload(job)
    if payload.get("image_base64"):
        raw = base64.b64decode(str(payload["image_base64"]))
        return Image.open(io.BytesIO(raw)).convert("RGB")

    input_uri = str(job.get("input_uri") or "")
    if input_uri.startswith("file://"):
        path = Path(input_uri.removeprefix("file://"))
        if path.suffix.lower() == ".pdf":
            import fitz

            doc = fitz.open(path)
            page = doc.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        return Image.open(path).convert("RGB")

    text = input_uri.removeprefix("text://") if input_uri.startswith("text://") else input_uri
    text = text or "GPU job VLM canary"
    img = Image.new("RGB", (1024, 512), "white")
    draw = ImageDraw.Draw(img)
    draw.multiline_text((32, 32), text[:800], fill="black", spacing=8)
    return img


@app.function(image=image, gpu="L4", timeout=3600)
def run_vlm(job: dict) -> dict:
    import torch
    import transformers
    from transformers import AutoProcessor

    started = time.time()
    model_name = _model_name(job)
    prompt = _prompt(job)
    max_tokens = _max_tokens(job)
    pil_image = _image_from_job(job)
    processor = AutoProcessor.from_pretrained(model_name)
    model_cls = getattr(transformers, "AutoModelForImageTextToText", None)
    if model_cls is None:
        model_cls = getattr(transformers, "AutoModelForVision2Seq", None)
    if model_cls is None:
        raise RuntimeError("transformers has no VLM auto model class")
    model = model_cls.from_pretrained(
        model_name,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto",
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    text_prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
    inputs = processor(text=text_prompt, images=[pil_image], return_tensors="pt")
    inputs = {key: value.to(model.device) for key, value in inputs.items()}
    generated = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    new_tokens = generated[:, inputs["input_ids"].shape[1] :]
    text = processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
    gpu_name = ""
    gpu_memory_used_mb = 0
    try:
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
        "text": text,
        "input_tokens": int(inputs["input_ids"].shape[1]),
        "output_chars": len(text),
        "runtime_seconds": round(time.time() - started, 3),
        "probe_info": {
            "provider": "modal",
            "worker_image": "gpu-job-modal-vlm",
            "loaded_model_id": model_name,
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
        result = run_vlm.remote(job)
        stdout = f"modal vlm completed model={result.get('model')} chars={result.get('output_chars')}\n"
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
    }
    probe_info = (
        result.get("probe_info")
        if isinstance(result.get("probe_info"), dict)
        else {
            "provider": "modal",
            "worker_image": "gpu-job-modal-vlm",
            "loaded_model_id": result.get("model"),
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
