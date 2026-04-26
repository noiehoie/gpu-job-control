# External System Self-Modify Prompt v1

Prompt for asking another software system, coding agent, or AI CLI to modify
itself so it calls `gpu-job-control` through the public caller contract.

- Prompt version: `external-system-self-modify-prompt-v1`
- Product commit used for this prompt: `a8eaa77`
- Caller contract: `gpu-job-caller-request-v1`
- Product invariant: `routing_by_module_enabled=false`

```text
あなたは「GPU 実行を内包する既存ソフトウェアシステム」の自律改修エージェントである。

目的:
このシステムを改修し、GPU を必要とする処理を provider 固有実装ではなく、外部製品 gpu-job-control の public caller contract だけで実行できるようにせよ。

絶対条件:
1. RunPod / Vast.ai / Modal / その他 cloud GPU provider の SDK、REST API、CLI、queue、endpoint、pod、instance、worker group を caller 側の GPU 実行パスから直接呼んではならない。
2. hidden local fallback を実装してはならない。gpu-job-control が失敗、timeout、429、artifact verification failure を返した場合、ローカル GPU、ローカル CPU、Ollama、OpenAI、別 API へ黙って逃がしてはならない。
3. LLM runtime routing を使ってはならない。operation 選択、provider 選択、lane 選択、model 選択、limits 調整を会話的判断、曖昧スコア、自然言語解釈、hidden heuristic で決めてはならない。
4. provider credential、API key、secret、endpoint token を caller request payload に入れてはならない。
5. `provider_module_id` を新規実装の routing key として使ってはならない。lane を明示する必要がある場合だけ `preferences.execution_lane_id` を使う。
6. `routing_by_module_enabled` を true にする設計、前提、説明を入れてはならない。製品側 invariant は `routing_by_module_enabled=false` である。
7. backend selector を実装する場合、許可値は closed enum にし、未設定・未知値・typo は fail-closed にせよ。default を `ollama`、`local`、`api`、`openai`、provider 直 API などへ落としてはならない。

Single Source of Truth:
- contract_version: `gpu-job-caller-request-v1`
- caller schema: `schemas/gpu-job-caller-request.schema.json` または public endpoint `GET /schemas/caller-request`
- operation catalog: `config/operation-catalog.json` または public endpoint `GET /catalog/operations`
- public API endpoint set:
  - `POST /validate`
  - `POST /route`
  - `POST /plan`
  - `POST /submit`
  - `GET /jobs/{job_id}`
  - `GET /verify/{job_id}`
- public auth:
  - `Authorization: Bearer <token>` または `X-GPU-Job-Token: <token>`

実 product API 事前確認:
1. 実行前に、あなたが接続している `GPU_JOB_API_BASE` が最新版 product API を指していることを確認せよ。
2. `GET /schemas/caller-request` と `GET /catalog/operations` を実 API から取得し、`gpu-job-caller-request-v1` と `smoke.gpu` が存在することを確認せよ。
3. canonical request を `POST /validate` に送り、旧 execution-job fields の missing error (`job_type`, `input_uri`, `output_uri`, `worker_image`, `gpu_profile`) が返った場合は、caller 実装の欠陥と即断してはならない。接続先 product API が古い checkout / 古い process / 誤った service を指している可能性を報告し、API base、response、時刻を提示せよ。
4. static local schema だけを正本にしてはならない。実 API の schema/catalog と同期してから判断せよ。

実装すべき構造:
1. 既存システム内の GPU を必要とする処理を棚卸しする。
2. provider 直呼び、local fallback、provider 固有 env、provider 固有 SDK、provider 固有 CLI を GPU 実行パスから除去する。
3. システム内の業務ロジックから GPU 実行意図を作る internal request 型を定義する。
4. internal request から canonical caller request JSON へ変換する deterministic compiler を 1 箇所に実装する。
5. caller request を送る thin transport client を 1 箇所に実装する。
6. status / result / verify を読み、既存システムの成功・失敗モデルへ戻す result adapter を 1 箇所に実装する。
7. 送信前 validation を必ず実行し、validation failure では product に送らず structured local error を返す。

必須 request shape:
必ず次の top-level fields を持つ JSON object を生成せよ。

{
  "contract_version": "gpu-job-caller-request-v1",
  "operation": "<closed operation id>",
  "input": {
    "uri": "<explicit machine-readable URI>",
    "parameters": {}
  },
  "output_expectation": {
    "target_uri": "<explicit target URI>",
    "format": "<expected output format>",
    "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]
  },
  "limits": {
    "max_runtime_minutes": <positive integer>,
    "max_cost_usd": <positive finite number>,
    "max_output_gb": <positive finite number>
  },
  "idempotency": {
    "key": "<stable key; same caller intent and same input must reuse the same key>"
  },
  "caller": {
    "system": "<this system name>",
    "operation": "<this system operation name>",
    "request_id": "<unique request id>",
    "version": "<this system version>"
  },
  "trace_context": {},
  "preferences": {}
}

`trace_context` と `preferences` は任意だが、使う場合も schema と operation catalog に従うこと。

禁止 top-level fields:
以下を caller request の top-level に出してはならない。

- `job_type`
- `input_uri`
- `output_uri`
- `worker_image`
- `gpu_profile`
- `provider`
- `provider_job_id`

これらは gpu-job-control 内部の deterministic compiler が生成する execution-job 側の概念である。

operation 選択規則:
1. `operation` は operation catalog に存在する closed id のみ使う。
2. ASR なら `asr.transcribe` または `asr.transcribe_diarize` を使う。
3. LLM 生成なら `llm.generate` を使う。
4. embedding なら `embedding.embed` を使う。
5. OCR なら `ocr.document` または `ocr.image` を使う。
6. 上記の named operation に入らない bounded GPU workload だけ `gpu.container.run` を使う。
7. free-form `job_type` や caller 独自 operation 名を product に送ってはならない。
8. caller 側で `operation -> gpu_profile`、`operation -> provider`、`operation -> worker_image` の内部 execution-job 変換を実装してはならない。その変換は gpu-job-control 側の deterministic compiler の責務である。

LLM production-quality 規則:
`llm.generate` を production-quality 外部 GPU workload として送る場合、`preferences` に次を入れよ。

{
  "quality_tier": "production_quality",
  "quality_requires_gpu": true,
  "local_fixed_resource_policy": "unsuitable",
  "model_size_class": "at_least_70b"
}

または `model_size_billion_parameters >= 70` を明示せよ。
27B/32B 級は smoke / development / degraded には使ってよいが、production-quality 外部 GPU evidence として扱ってはならない。

lane 指定規則:
通常は caller 側で lane を指定しない。gpu-job-control が deterministic policy で処理する。
特定 lane の canary、診断、承認済み運用が必要な場合だけ、`preferences.execution_lane_id` に次の closed enum のいずれかを指定してよい。

- `modal_function`
- `runpod_pod`
- `runpod_serverless`
- `vast_instance`
- `vast_pyworker_serverless`

未知 lane、precondition 不足、health/capability/startup/endpoint/policy failure は fail-closed とする。caller 側で別 lane へ hidden fallback してはならない。

`gpu.container.run` の必須規則:
1. `input.parameters.workload` を必ず持つ。
2. workload は bounded でなければならない。
3. `limits.max_runtime_minutes` は正の整数、`max_cost_usd` と `max_output_gb` は有限の正の数でなければならない。
4. `output_expectation.required_files` は非空でなければならない。
5. 5 lane examples と同型の request を生成できなければならない。

参照すべき 5 lane examples:
- `examples/caller-requests/gpu.container.run.modal_function.json`
- `examples/caller-requests/gpu.container.run.runpod_pod.json`
- `examples/caller-requests/gpu.container.run.runpod_serverless.json`
- `examples/caller-requests/gpu.container.run.vast_instance.json`
- `examples/caller-requests/gpu.container.run.vast_pyworker_serverless.json`

送信前 validation:
product へ送る前に必ず local validation を実行せよ。最低限、次を検査する。

1. 必須 top-level fields がすべてある。
2. 必須 nested fields がすべてある。
3. `contract_version == "gpu-job-caller-request-v1"`。
4. `operation` が operation catalog の closed id である。
5. `input.uri` と `output_expectation.target_uri` が空でない。
6. `output_expectation.required_files` が非空である。
7. `limits.max_runtime_minutes` が正の整数（positive integer）であり、`max_cost_usd` と `max_output_gb` が有限の正の数（finite positive numbers）である。
8. `idempotency.key` が空でない。
9. `caller.system`, `caller.operation`, `caller.request_id`, `caller.version` が空でない。
10. 禁止 top-level fields が存在しない。
11. `preferences.execution_lane_id` がある場合、closed enum かつ operation catalog の allowed_lanes に含まれる。
12. backend selector がある場合、許可値の closed enum に含まれる。未設定 default や未知値 fallback が存在しない。
13. `caller.version` と、入力に存在する `trace_context` が product request へ欠落なく伝搬される。

validation failure の場合:
- product に送らない。
- provider を呼ばない。
- local fallback しない。
- structured local error を返す。

response handling:
1. 送信 request body を記録する。ただし secrets は記録しない。
2. `/submit` の戻り値から product job id を記録する。
3. `/jobs/{job_id}` だけで status を読む。
4. terminal state を deterministic に map する。
5. 成功扱いにする前に `/verify/{job_id}` を呼ぶ。
6. artifact verification failure は job 成功として扱わない。
7. 429 は backpressure として扱い、`Retry-After` があれば尊重する。
8. timeout / provider error / artifact failure / validation failure を区別して既存システムの error model に戻す。
9. `planned`、`accepted`、`queued`、`running` は成功ではない。また、`/submit` において `execute=true`（または製品同等物）が不在または false の場合、`planned` 状態で停止する可能性があり、これは最終的な E2E success ではない。caller migration の contract path 確認としては使えるが、製品利用可能性の最終 E2E success として報告してはならない。
10. 最終 E2E success は terminal success (`succeeded` または product が返す同等 terminal success) かつ `/verify/{job_id}` が `ok:true` の場合だけである。
11. `/verify` が `ok:false` で missing artifacts を返した場合、job が未実行なら fail-closed として正しいが、最終 E2E success ではない。

実装成果物:
以下を作成または更新せよ。

1. GPU job integration module
   - internal request -> canonical caller request compiler
   - local validator
   - transport client
   - result adapter
2. 既存 GPU 呼び出し箇所の差し替え
   - provider 直呼び削除
   - hidden local fallback 削除
   - runtime LLM routing 削除
3. tests
   - valid caller request generation
   - invalid request fail-closed
   - forbidden top-level fields rejection
   - same input -> same idempotency key
   - same input -> same canonical request
   - 429/backpressure mapping
   - artifact verification failure mapping
   - no provider direct call in caller path
   - 5 lane examples equivalent shape for `gpu.container.run`
   - unknown backend selector fails closed
   - no default local/Ollama/OpenAI/API fallback exists
   - `caller.version` and `trace_context` pass through unchanged
   - caller does not generate `gpu_profile`, `worker_image`, `provider`, or `job_type`
4. docs or README update
   - how this system calls gpu-job-control
   - required env vars for product API base URL and token
   - failure behavior
   - no direct provider calls / no hidden fallback statement

検証コマンド:
このシステムの実際の test runner に合わせて実行し、実出力を報告せよ。最低限、次を満たす検証を用意する。

1. unit tests for request compiler and validator
2. integration or mocked transport test for `/validate`, `/submit`, `/jobs/{job_id}`, `/verify/{job_id}`
3. static search proving no provider direct call remains in the caller GPU execution path
4. generated request sample validation against the caller schema
5. one dry-run or mocked end-to-end flow:
   native task -> canonical request -> submit/status/verify -> native result
6. one real product API contract check:
   `GET /schemas/caller-request` -> `GET /catalog/operations` -> `POST /validate`
7. one real executed E2E smoke when credentials and policy allow it:
   `POST /submit` with `execute=true` (or product equivalent) -> `GET /jobs/{job_id}` until terminal success -> `GET /verify/{job_id}` returns `ok:true`
8. artifact existence check for successful executed E2E:
   `result.json`, `metrics.json`, `verify.json`, `stdout.log`, `stderr.log`, plus product manifest/record files when available

完了報告様式:
作業後、次の形式で報告せよ。実コマンド出力を必ず含めること。

## gpu-job-control caller integration report

Status: GO / NO-GO

Changed files:
- ...

Implemented:
- deterministic compiler: yes/no
- local schema/catalog validation: yes/no
- transport client: yes/no
- result adapter: yes/no
- artifact verification: yes/no
- idempotency: yes/no
- no provider direct call in caller path: yes/no
- no hidden local fallback: yes/no
- no LLM runtime routing: yes/no

Supported operations:
- ...

Lane behavior:
- default: no caller lane selection
- explicit `preferences.execution_lane_id`: supported yes/no
- supported lane examples: modal_function / runpod_pod / runpod_serverless / vast_instance / vast_pyworker_serverless

Verification output:
```text
<paste actual command outputs here>
```

Product API evidence:
- API base URL:
- schema endpoint status/output:
- operation catalog status/output:
- validate output:
- submit output:
- jobs output:
- verify output:
- artifact file listing:

GO criteria:
- all tests pass
- generated request validates against `gpu-job-caller-request-v1`
- invalid requests fail closed before send
- no provider direct call remains in caller GPU execution path
- no hidden local fallback remains
- artifact verification is required before success
- real product API `/validate` accepts a canonical request
- final executed E2E, if claimed, reaches terminal success and `/verify` returns `ok:true`

NO-GO if any of these remain:
- provider direct call in caller GPU path
- hidden local fallback
- LLM runtime routing
- request can be sent without validation
- missing idempotency key
- missing valid limits (runtime as integer, cost/output as finite positive numbers)
- artifact verification is optional or absent
- production-quality `llm.generate` can use under-70B model without degraded/smoke/development label
- backend selector has a default local/Ollama/OpenAI/API/provider-direct fallback
- unknown backend selector value does not fail closed
- `caller.version` or `trace_context` is dropped
- caller code maps operation to `gpu_profile`, `worker_image`, `provider`, or `job_type`
- report claims final E2E success while the job is only `planned`, `accepted`, `queued`, or `running`
- report claims final E2E success while `/verify` is `ok:false`
- report claims product compiler is missing based only on old execution-job missing-field errors, without confirming the live product API version/schema/catalog
```
