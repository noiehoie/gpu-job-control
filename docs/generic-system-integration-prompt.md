# Generic System Integration Prompt

Use this prompt when a Codex agent must adapt any existing system to use `gpu-job-control` for LLM, VLM, ASR, embedding, or other AI workloads.

```text
あなたはこのリポジトリの担当 Codex である。

目的は、このシステム内の LLM/VLM/ASR/embedding 処理を gpu-job-control 経由でも実行できるようにすること。

この指示は特定システム専用ではない。news-system、テキスト要約システム、翻訳システム、分析システム、OCR後処理システム、動画文字起こしシステムなど、LLM/VLM/ASR を使う任意のシステムに適用できる汎用改修指針として実装せよ。

## 絶対条件

1. 事実は必ずツールで確認する。推測で「この関数が呼ばれているはず」と断言しない。
2. 既存コードを全部読んで、LLM/VLM/ASR/embedding/要約/翻訳/分類/抽出/採点/JSON生成/semantic match に相当する呼び出し箇所を漏れなく列挙する。
3. system python / global pip install は禁止。Python は uv 管理 venv で実行する。
4. 既存の LLM 呼び出しをいきなり削除しない。gpu-job-control adapter を追加し、既存 backend は fallback として残す。
5. provider をシステム側で決め打ちしない。Modal / Ollama / RunPod / Vast の選択は gpu-job-control 側に任せる。
6. RunPod Serverless vLLM / Hub-template 新規作成には依存しない。現ローンチ方針では deferred。
7. 429 は model failure ではなく backpressure として扱う。Retry-After を尊重し、必要なら既存 fallback に逃がす。
8. 完了報告には実コマンド出力を添付する。「実装したはず」で終わらせない。
9. API token や secret をログ・テスト・fixture・README に出さない。
10. 破壊操作、DB DROP、既存データ削除、provider-side purge は行わない。

## 最重要: `/submit` と `/intake` の意味差

ここを取り違えると「job は送ったが結果が永遠に返らない」事故になる。

`POST /submit?provider=auto&execute=1`:
- 即時実行用。
- 呼び出し元がその場で LLM text を必要とする同期処理に使う。
- 成功時は response に `job`, `job_id`, `status`, `artifact_dir`, `result` が含まれ得る。
- `result.json` が存在する場合、API response の `result` に入る。
- CLI の単発要約、HTTP request に同期的に返す要約、短い smoke は原則こちらを使う。

`POST /intake`:
- 即時実行 API ではない。
- job を `buffered` として保存し、短時間 hold して group planning する入口。
- `/intake` 自体は LLM text を返さない。
- 実行には gpu-job-control 側の planner/worker が別途動いている必要がある。
- `buffered -> queued -> starting -> running -> succeeded/failed` へ進むのは worker 側の仕事。
- 大量 burst、非同期 batch、後で status polling できる処理だけに使う。

したがって adapter は必ず `sync` と `async_intake` を分けること。

## 実装すべき adapter モード

### 1. sync mode

その場で text が必要な処理用。

- `POST /submit?provider=auto&execute=1` を使う。
- provider は指定しない。`auto` のままにする。
- 成功時は response.result から text を抽出する。
- 429/backpressure、timeout、provider failure、URL/token 未設定時は既存 backend に fallback する。
- default mode は原則 `sync` にする。既存 CLI や HTTP endpoint の互換性を壊さないため。

### 2. async_intake mode

大量 burst や非同期 batch 用。

- `POST /intake` を使う。
- `/intake` response から text を直接探してはいけない。
- adapter は `job_id`, `status`, `path`, `intake state` を返す。
- 呼び出し側は gpu-job-control worker が稼働している前提で `GET /jobs/{job_id}` を polling する。
- `buffered`, `queued`, `starting`, `running` は terminal status ではない。
- `succeeded` かつ `result` が存在する時だけ text 抽出する。
- `failed`, `cancelled` は fallback または明示エラー。
- polling timeout を超えたら既存 backend に fallback するか、pending としてユーザーに返す。
- README に「async_intake は gpu-job-control worker 稼働が前提」と明記する。

環境変数例:

- `LLM_BACKEND=ollama|gpu_job`
- `GPU_JOB_SUBMIT_MODE=sync|async_intake`
- `GPU_JOB_CONTROL_URL=http://host:8765`
- `GPU_JOB_API_TOKEN=<token>`

`LLM_BACKEND=gpu_job` だけで `/intake` を選んではいけない。同期/非同期は別の概念である。

## gpu-job-control 接続仕様

- URL は `GPU_JOB_CONTROL_URL` から読む。
- token は `GPU_JOB_API_TOKEN` から読む。
- backend 切替は既存設定に合わせる。新規に足すなら `LLM_BACKEND=gpu_job` または同等の明示設定にする。
- 認証ヘッダは以下のどちらかを使う:
  - `Authorization: Bearer <GPU_JOB_API_TOKEN>`
  - `X-GPU-Job-Token: <GPU_JOB_API_TOKEN>`
- 状態確認は `GET /guard`, `GET /queue`, `GET /intake`, `GET /jobs/{job_id}` を使う。

## 実装タスク

1. 現状調査
   - LLM/VLM/ASR/embedding を呼んでいる全ファイルを `rg` で探す。
   - 直接 API 呼び出し、SDK 呼び出し、Ollama/OpenAI/Anthropic/ローカルモデル呼び出し、独自 wrapper を全て列挙する。
   - それぞれについて以下を表にする:
     - ファイル
     - 関数
     - 用途
     - 入力サイズの見込み
     - 出力形式
     - JSON strict が必要か
     - timeout
     - 既存 fallback
     - 並列実行される可能性
     - 重要度

2. 共通 adapter を実装
   - システム固有処理に直接 gpu-job-control HTTP 呼び出しを埋め込まない。
   - 例: `src/gpu_jobs/adapters.py`, `src/llm/gpu_job_adapter.py`, `src/integrations/gpu_job.py` のような単一責務の adapter を作る。
   - adapter は最低限以下を提供する:
     - `call_llm_via_gpu_job(...)`
     - 必要なら `call_vlm_via_gpu_job(...)`
     - 必要なら `call_embedding_via_gpu_job(...)`
     - `submit_mode` または `mode` 引数: `sync` / `async_intake`
   - 既存の呼び出し側はこの adapter を経由するようにする。

3. job contract
   LLM 要約・翻訳・分類・抽出・JSON生成は原則 `job_type="llm_heavy"` とする。

   最小 job 例:

   {
     "job_type": "llm_heavy",
     "input_uri": "text://<short description or prompt>",
     "output_uri": "local://<system-name>/<task-family>",
     "worker_image": "auto",
     "gpu_profile": "llm_heavy",
     "model": "<logical-model-name>",
     "limits": {
       "max_runtime_minutes": 60,
       "max_cost_usd": 2,
       "max_output_gb": 1
     },
     "verify": {
       "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]
     },
     "metadata": {
       "source_system": "<this-system-name>",
       "task_family": "<summary|translate|classify|extract|match|json_generation>",
       "purpose": "<human-readable purpose>",
       "input": {
         "prompt": "<prompt>",
         "system_prompt": "<system prompt if any>",
         "max_tokens": 1024,
         "temperature": 0
       },
       "routing": {
         "estimated_input_tokens": 1000,
         "estimated_cpu_runtime_seconds": 600,
         "estimated_gpu_runtime_seconds": 60,
         "batch_size": 1,
         "burst_size": 1,
         "deadline_seconds": 1800,
         "quality_requires_gpu": false
       }
     }
   }

4. routing metadata を必ず入れる
   - `source_system`: このシステム名
   - `task_family`: 要約、翻訳、分類、抽出、semantic_match など
   - `estimated_input_tokens`: 文字数から概算してよい。未指定にしない。
   - `estimated_cpu_runtime_seconds`: 既存 backend での見込み時間
   - `estimated_gpu_runtime_seconds`: GPU backend での見込み時間
   - `deadline_seconds`: その処理が待てる最大時間
   - `quality_requires_gpu`: 品質上 GPU/高性能モデルが必要な時だけ true
   - `burst_size`: 呼び出し側で分かる並列数。分からない単発は 1。
   - `batch_size`: 同一性質の job がまとまる数。分からない単発は 1。

5. 並列・burst 対応
   - 大量並列が予想される処理だけ `async_intake` を検討する。
   - 1件ずつ同期結果が必要な処理で `/intake` を使わない。
   - 呼び出し側が burst_size を渡せない場合でも、gpu-job-control 側 intake の observed burst に任せる設計にする。
   - 429 が返った場合:
     - retry_after_seconds / Retry-After を読む。
     - すぐ同じ provider に連打しない。
     - 既存 fallback があるなら fallback へ移る。
     - ログには backpressure として記録する。

6. fallback
   - `GPU_JOB_CONTROL_URL` または token が未設定なら、既存 backend をそのまま使う。
   - gpu-job-control が 429/backpressure を返した場合、既存 backend に fallback できるなら fallback する。
   - gpu-job-control が timeout/provider failure を返した場合、呼び出し用途に応じて fallback する。
   - JSON strict 出力が必要な処理では、fallback 後も JSON validation を必ず行う。

7. 出力パース
   - gpu-job-control の結果から text を取り出す処理を adapter に閉じ込める。
   - sync mode では response.result を最優先で見る。
   - async_intake mode では `/jobs/{job_id}` の terminal response だけを見る。
   - 想定候補:
     - `text`
     - `response`
     - `output`
     - `generated_text`
     - `answer`
     - OpenAI style `choices[0].message.content`
   - JSON を要求する処理では、JSON parse と schema/required keys の検証を呼び出し側で行う。

8. timeout
   - 短文要約・翻訳: 120-300秒程度
   - 長文要約・大規模分析: 600-3600秒程度
   - caller の HTTP timeout は job の deadline と整合させる。
   - 長時間処理は同期 HTTP で待ち続けず、async_intake / queue / status polling に寄せる。

9. テスト
   - adapter の単体テストを追加する。
   - HTTP client は mock し、最低限以下をテストする:
     - token header が付く
     - sync mode が `/submit?provider=auto&execute=1` を呼ぶ
     - sync mode 成功時に response.result から text を抽出する
     - async_intake mode が `/intake` を呼ぶ
     - async_intake mode は `/intake` response から text を抽出しない
     - async_intake polling で `buffered` / `queued` を terminal と扱わない
     - async_intake polling で `succeeded` + result から text を抽出する
     - 429 backpressure
     - 500 provider failure
     - timeout
     - GPU_JOB_CONTROL_URL 未設定時の fallback
     - JSON strict 処理が壊れない
   - 既存 LLM 呼び出しの挙動を壊していないことをテストする。

10. smoke
   実環境 token がある場合のみ、以下を実行してよい。
   token がない場合は skip し、skip 理由を報告する。

   - `GET $GPU_JOB_CONTROL_URL/guard`
   - sync mode で小さい `llm_heavy` smoke job を `POST /submit?provider=auto&execute=1`
   - 結果の status, provider, artifact_count, verify.ok を確認
   - smoke 後に再度 `GET /guard`
   - async_intake mode は gpu-job-control worker が稼働している環境でだけ smoke する

11. 完了報告
   完了報告は以下の順に書く:
   - 結論
   - 変更ファイル一覧
   - 発見した LLM/VLM/ASR/embedding 呼び出し箇所一覧
   - adapter 仕様
   - sync / async_intake の使い分け
   - fallback 仕様
   - 実コマンド出力
   - smoke 実行結果または skip 理由
   - 残リスク
   - gpu-job-control 側への改善要望

実装時の注意:
- 個別システム専用の provider 判定ロジックを増やすな。
- 「短文だから Ollama」「長文だから Modal」などをシステム側で決め打ちしない。
- システム側は job metadata を正確に渡す。判断は gpu-job-control に任せる。
- 既存コードの設計に合わせるが、LLM 呼び出しは必ず一箇所の adapter に集約する。
- 環境変数名、API token、URL、モデル名は設定で差し替え可能にする。
- secret 値をログに出すな。
- 完了前に必ずテストを実行し、実出力を貼る。
```
