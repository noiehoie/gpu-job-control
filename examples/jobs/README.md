# Job Examples

These JSON files are safe, public examples. They use placeholder URIs, placeholder registry names, and deterministic limits.

## Local Examples

- `embedding.local.json`: zero-spend local smoke path for embedding-style jobs.
- `llm-heavy.local.json`: local LLM-style execution contract.
- `pdf-ocr.local.json`: OCR/VLM-shaped local contract.
- `vlm-ocr.local.json`: vision/OCR-shaped local contract.

## External Provider Examples

- `asr.example.json`: generic ASR job contract.
- `asr.bulk.json`: batch ASR shape for amortized startup decisions.
- `asr.modal.json`: Modal ASR contract.
- `llm-heavy.modal.json`: Modal GPU LLM contract.
- `llm-heavy.runpod.json`: RunPod serverless LLM contract.
- `smoke.modal.json`: Modal smoke job.
- `smoke.vast.json`: Vast smoke job.

## Authoring Rules

- Keep examples free of live provider IDs, private endpoints, private IP addresses, or credentials.
- Use `metadata.routing` to declare expected size, deadline, burst size, and quality constraints.
- Keep `limits.max_cost_usd` explicit even for examples.
- Include `verify.required_files` whenever the worker is expected to produce artifacts.
